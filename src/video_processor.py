"""
Основной процессор видео, координирующий все компоненты анализа.
"""

import cv2
import numpy as np
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional, Any, Generator
import threading
from queue import Queue
import json

from src.team_detector import TeamDetector, TeamInfo
from src.arrow_tracker import ArrowTrackingSystem, TrackingResult
from src.game_segmenter import GameSegmenter, GameSegment, IntermissionSegment
from config.opencv_config import OpenCVConfig

logger = logging.getLogger(__name__)


class VideoProcessingError(Exception):
    """Исключение при ошибках обработки видео."""
    pass


class ProcessingStats:
    """Статистика обработки видео."""
    
    def __init__(self):
        """Инициализация статистики."""
        self.frames_processed = 0
        self.frames_skipped = 0
        self.processing_time = 0.0
        self.start_time = time.time()
        self.teams_detected = 0
        self.successful_tracking_frames = 0
        
    def add_frame(self, processing_time: float, teams_tracked: int):
        """Добавить статистику обработки кадра."""
        self.frames_processed += 1
        self.processing_time += processing_time
        if teams_tracked > 0:
            self.successful_tracking_frames += 1
            
    def get_fps(self) -> float:
        """Получить скорость обработки в FPS."""
        elapsed = time.time() - self.start_time
        return self.frames_processed / elapsed if elapsed > 0 else 0
        
    def get_avg_processing_time(self) -> float:
        """Получить среднее время обработки кадра."""
        return self.processing_time / self.frames_processed if self.frames_processed > 0 else 0


class VideoProcessor:
    """Основной процессор видео для анализа Apex Legends."""
    
    def __init__(self, config: OpenCVConfig):
        """
        Инициализация процессора видео.
        
        Args:
            config: Конфигурация OpenCV
        """
        self.config = config
        
        # Инициализация компонентов
        self.team_detector = TeamDetector()
        self.arrow_tracker = ArrowTrackingSystem()
        self.game_segmenter = GameSegmenter()
        
        # Статистика
        self.stats = ProcessingStats()
        
        # Настройки обработки
        self.save_debug_frames = False
        self.debug_output_dir = Path("assets/reference_frames")
        self.debug_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Многопоточность
        self._processing_lock = threading.Lock()
        
        # Кеш результатов
        self._results_cache: List[TrackingResult] = []
        
    def _validate_frame(self, frame: np.ndarray) -> bool:
        """
        Проверить валидность кадра.
        
        Args:
            frame: Кадр для проверки
            
        Returns:
            True если кадр валидный
        """
        if frame is None or frame.size == 0:
            return False
            
        # Проверка размеров (ожидаем 1920x1080 или близкие пропорции)
        height, width = frame.shape[:2]
        
        if width < 800 or height < 600:
            logger.warning(f"Кадр слишком маленький: {width}x{height}")
            return False
            
        if width / height < 1.3 or width / height > 2.0:
            logger.warning(f"Необычное соотношение сторон: {width}x{height}")
            
        return True
        
    def _save_debug_frame(self, frame: np.ndarray, frame_number: int, suffix: str = ""):
        """
        Сохранить отладочный кадр.
        
        Args:
            frame: Кадр для сохранения
            frame_number: Номер кадра
            suffix: Суффикс для имени файла
        """
        if not self.save_debug_frames:
            return
            
        try:
            filename = f"frame_{frame_number:06d}{suffix}.jpg"
            filepath = self.debug_output_dir / filename
            cv2.imwrite(str(filepath), frame)
        except Exception as e:
            logger.debug(f"Ошибка сохранения отладочного кадра: {e}")
            
    def _initialize_tracking(self, frame: np.ndarray) -> Dict[str, TeamInfo]:
        """
        Инициализировать трекинг на первом кадре.
        
        Args:
            frame: Первый кадр для анализа
            
        Returns:
            Словарь найденных команд
        """
        logger.info("Инициализация детекции команд и трекинга")
        
        # Предварительная обработка кадра
        processed_frame = self.config.preprocess_frame(frame)
        
        # Детекция команд
        teams = self.team_detector.detect_teams(processed_frame, force_refresh=True)
        
        if not teams:
            raise VideoProcessingError("Команды не найдены на первом кадре")
            
        logger.info(f"Найдено {len(teams)} команд")
        
        # Получение цветовых диапазонов для трекинга
        team_colors = self.team_detector.get_team_colors_for_tracking()
        
        if not team_colors:
            raise VideoProcessingError("Цвета команд не определены")
            
        # Инициализация трекинга
        self.arrow_tracker.initialize_tracking(processed_frame, team_colors)
        
        active_teams = self.arrow_tracker.get_active_teams()
        logger.info(f"Инициализировано трекеров: {len(active_teams)}")
        
        if not active_teams:
            raise VideoProcessingError("Не удалось инициализировать трекеры")
            
        # Сохранение отладочного кадра с визуализацией
        if self.save_debug_frames:
            viz_frame = self.team_detector.visualize_detection(processed_frame)
            self._save_debug_frame(viz_frame, 0, "_teams_detected")
            
        self.stats.teams_detected = len(teams)
        return teams
        
    def _process_frame(self, frame: np.ndarray, frame_number: int, timestamp: float) -> Optional[TrackingResult]:
        """
        Обработать один кадр.
        
        Args:
            frame: Кадр для обработки
            frame_number: Номер кадра
            timestamp: Временная метка
            
        Returns:
            Результат трекинга или None при ошибке
        """
        start_time = time.time()
        
        try:
            # Проверка валидности кадра
            if not self._validate_frame(frame):
                logger.warning(f"Пропускаем невалидный кадр {frame_number}")
                return None
                
            # Предварительная обработка
            processed_frame = self.config.preprocess_frame(frame)
            
            # Обновление трекинга
            tracking_result = self.arrow_tracker.update_tracking(
                processed_frame, frame_number, timestamp
            )
            
            # Периодическое обновление детекции команд (каждые 100 кадров)
            if frame_number % 100 == 0:
                try:
                    updated_teams = self.team_detector.detect_teams(processed_frame, force_refresh=True)
                    if updated_teams:
                        logger.debug(f"Обновлена информация о командах на кадре {frame_number}")
                except Exception as e:
                    logger.debug(f"Ошибка обновления детекции команд: {e}")
                    
            # Сохранение отладочных кадров
            if self.save_debug_frames and frame_number % 30 == 0:  # Каждую секунду при 30fps
                viz_frame = self.arrow_tracker.visualize_tracking(processed_frame, tracking_result)
                self._save_debug_frame(viz_frame, frame_number, "_tracking")
                
            # Обновление статистики
            processing_time = time.time() - start_time
            teams_tracked = len(tracking_result.team_positions)
            self.stats.add_frame(processing_time, teams_tracked)
            
            # Логирование прогресса
            if frame_number % 300 == 0:  # Каждые 10 секунд при 30fps
                fps = self.stats.get_fps()
                avg_time = self.stats.get_avg_processing_time()
                logger.info(f"Кадр {frame_number}: {fps:.1f} FPS, "
                           f"среднее время обработки: {avg_time:.3f}с, "
                           f"команд отслеживается: {teams_tracked}")
                           
            return tracking_result
            
        except Exception as e:
            logger.error(f"Ошибка обработки кадра {frame_number}: {e}")
            return None
            
    def process_video(self, video_path: Path) -> List[TrackingResult]:
        """
        Обработать видео файл.
        
        Args:
            video_path: Путь к видео файлу
            
        Returns:
            Список результатов трекинга
        """
        logger.info(f"Начало обработки видео: {video_path}")
        
        # Проверка существования файла
        if not video_path.exists():
            raise VideoProcessingError(f"Видео файл не найден: {video_path}")
            
        # Очистка кеша
        self._results_cache.clear()
        
        # Открытие видео
        cap = cv2.VideoCapture(str(video_path))
        
        if not cap.isOpened():
            raise VideoProcessingError(f"Не удалось открыть видео: {video_path}")
            
        try:
            # Получение информации о видео
            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / fps if fps > 0 else 0
            
            logger.info(f"Видео: {fps:.1f} FPS, {total_frames} кадров, {duration:.1f}с")
            
            # Чтение первого кадра для инициализации
            ret, first_frame = cap.read()
            if not ret:
                raise VideoProcessingError("Не удалось прочитать первый кадр")
                
            # Инициализация трекинга
            teams = self._initialize_tracking(first_frame)
            
            # Обработка первого кадра
            first_result = self._process_frame(first_frame, 0, 0.0)
            if first_result:
                self._results_cache.append(first_result)
                
            # Обработка остальных кадров
            frame_number = 1
            
            while True:
                ret, frame = cap.read()
                
                if not ret:
                    break  # Конец видео
                    
                # Проверка, нужно ли обрабатывать этот кадр
                if not self.config.should_process_frame(frame_number):
                    self.stats.frames_skipped += 1
                    frame_number += 1
                    continue
                    
                # Вычисление временной метки
                timestamp = frame_number / fps if fps > 0 else 0
                
                # Обработка кадра
                result = self._process_frame(frame, frame_number, timestamp)
                
                if result:
                    self._results_cache.append(result)
                    
                frame_number += 1
                
                # Прерывание по Ctrl+C
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    logger.info("Обработка прервана пользователем")
                    break
                    
        finally:
            cap.release()
            cv2.destroyAllWindows()
            
        # Финальная статистика
        total_time = time.time() - self.stats.start_time
        logger.info(f"Обработка завершена за {total_time:.1f}с")
        logger.info(f"Обработано кадров: {self.stats.frames_processed}")
        logger.info(f"Пропущено кадров: {self.stats.frames_skipped}")
        logger.info(f"Успешных кадров трекинга: {self.stats.successful_tracking_frames}")
        logger.info(f"Средняя скорость: {self.stats.get_fps():.1f} FPS")
        
        return self._results_cache.copy()
        
    def process_stream(self, video_stream: Generator[bytes, None, None]) -> List[TrackingResult]:
        """
        Обработать поток видео в реальном времени.
        
        Args:
            video_stream: Генератор видеоданных
            
        Returns:
            Список результатов трекинга
        """
        logger.info("Начало потоковой обработки видео")
        
        # Очистка кеша
        self._results_cache.clear()
        
        try:
            # Буфер для накопления данных потока
            stream_buffer = b''
            frame_number = 0
            initialized = False
            
            # Обработка потоковых данных через OpenCV
            for chunk in video_stream:
                stream_buffer += chunk
                
                # Попытка декодирования кадра из буфера
                # Это упрощенная реализация - в реальности может потребоваться более сложная логика
                try:
                    # Преобразование данных в numpy array
                    nparr = np.frombuffer(stream_buffer, np.uint8)
                    
                    # Попытка декодирования изображения
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    
                    if frame is not None and self._validate_frame(frame):
                        # Инициализация на первом валидном кадре
                        if not initialized:
                            teams = self._initialize_tracking(frame)
                            initialized = True
                            
                        # Обработка кадра
                        timestamp = time.time()
                        result = self._process_frame(frame, frame_number, timestamp)
                        
                        if result:
                            self._results_cache.append(result)
                            
                        frame_number += 1
                        
                        # Очистка буфера после успешного декодирования
                        stream_buffer = b''
                        
                except Exception as e:
                    # Если не удалось декодировать, продолжаем накапливать данные
                    pass
                    
                # Ограничение размера буфера
                if len(stream_buffer) > 1024 * 1024:  # 1MB
                    stream_buffer = stream_buffer[-512*1024:]  # Оставляем последние 512KB
                    
        except Exception as e:
            logger.error(f"Ошибка потоковой обработки: {e}")
            raise VideoProcessingError(f"Ошибка потоковой обработки: {e}")
            
        logger.info(f"Потоковая обработка завершена. Обработано кадров: {frame_number}")
        return self._results_cache.copy()
        
    def enable_debug_output(self, output_dir: Optional[Path] = None):
        """
        Включить сохранение отладочных кадров.
        
        Args:
            output_dir: Директория для сохранения (по умолчанию assets/reference_frames)
        """
        self.save_debug_frames = True
        if output_dir:
            self.debug_output_dir = Path(output_dir)
            self.debug_output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Отладочные кадры будут сохраняться в: {self.debug_output_dir}")
        
    def disable_debug_output(self):
        """Отключить сохранение отладочных кадров."""
        self.save_debug_frames = False
        
    def get_processing_stats(self) -> Dict[str, Any]:
        """
        Получить статистику обработки.
        
        Returns:
            Словарь со статистикой
        """
        return {
            "frames_processed": self.stats.frames_processed,
            "frames_skipped": self.stats.frames_skipped,
            "processing_fps": self.stats.get_fps(),
            "avg_processing_time": self.stats.get_avg_processing_time(),
            "teams_detected": self.stats.teams_detected,
            "successful_tracking_frames": self.stats.successful_tracking_frames,
            "success_rate": (self.stats.successful_tracking_frames / 
                           max(1, self.stats.frames_processed)) * 100
        }
        
    def process_video_with_segmentation(self, video_path: Path, 
                                      process_all_games: bool = True,
                                      specific_game_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Обработать видео с автоматической сегментацией на отдельные игры.
        
        Args:
            video_path: Путь к видео файлу
            process_all_games: Обрабатывать все найденные игры
            specific_game_id: ID конкретной игры для обработки
            
        Returns:
            Словарь с результатами обработки всех игр
        """
        logger.info(f"Начало обработки видео с сегментацией: {video_path}")
        
        # Проверка существования файла
        if not video_path.exists():
            raise VideoProcessingError(f"Видео файл не найден: {video_path}")
            
        # Получение FPS видео
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise VideoProcessingError(f"Не удалось открыть видео: {video_path}")
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        cap.release()
        
        logger.info(f"Видео: {fps:.1f} FPS, {total_frames} кадров, {duration:.1f}с")
        
        # Сегментация видео на игры
        logger.info("Выполняется сегментация видео на отдельные игры...")
        games, intermissions = self.game_segmenter.segment_video(video_path, fps)
        
        if not games:
            raise VideoProcessingError("Игровые сегменты не найдены в видео")
            
        logger.info(f"Найдено игр: {len(games)}, заглушек: {len(intermissions)}")
        
        # Определение игр для обработки
        games_to_process = []
        if specific_game_id:
            target_game = next((g for g in games if g.game_id == specific_game_id), None)
            if target_game:
                games_to_process = [target_game]
            else:
                raise VideoProcessingError(f"Игра с ID {specific_game_id} не найдена")
        elif process_all_games:
            games_to_process = games
        else:
            # По умолчанию обрабатываем первую игру
            games_to_process = [games[0]]
            
        # Результаты обработки
        processing_results = {
            "video_info": {
                "path": str(video_path),
                "fps": fps,
                "total_frames": total_frames,
                "duration": duration
            },
            "segmentation": self.game_segmenter.export_segments_info(games, intermissions),
            "games_processed": {},
            "processing_stats": {}
        }
        
        # Обработка каждой игры
        for game_segment in games_to_process:
            logger.info(f"Обработка игры {game_segment.game_id} "
                       f"({game_segment.duration:.1f}с, кадры {game_segment.start_frame}-{game_segment.end_frame})")
            
            try:
                # Обработка конкретного сегмента
                game_results = self._process_game_segment(video_path, game_segment, fps)
                
                processing_results["games_processed"][str(game_segment.game_id)] = {
                    "segment_info": {
                        "game_id": game_segment.game_id,
                        "start_frame": game_segment.start_frame,
                        "end_frame": game_segment.end_frame,
                        "start_timestamp": game_segment.start_timestamp,
                        "end_timestamp": game_segment.end_timestamp,
                        "duration": game_segment.duration,
                        "confidence": game_segment.confidence
                    },
                    "tracking_results": game_results,
                    "teams": {},  # Будет заполнено в _process_game_segment
                    "statistics": {}  # Будет заполнено в _process_game_segment
                }
                
            except Exception as e:
                logger.error(f"Ошибка обработки игры {game_segment.game_id}: {e}")
                processing_results["games_processed"][str(game_segment.game_id)] = {
                    "error": str(e),
                    "segment_info": {
                        "game_id": game_segment.game_id,
                        "start_frame": game_segment.start_frame,
                        "end_frame": game_segment.end_frame,
                        "duration": game_segment.duration
                    }
                }
                
        # Общая статистика обработки
        processing_results["processing_stats"] = self.get_processing_stats()
        
        logger.info(f"Обработка видео завершена. Обработано игр: {len([g for g in processing_results['games_processed'].values() if 'error' not in g])}")
        
        return processing_results
        
    def _process_game_segment(self, video_path: Path, game_segment: GameSegment, fps: float) -> List[TrackingResult]:
        """
        Обработать конкретный сегмент игры.
        
        Args:
            video_path: Путь к видео файлу
            game_segment: Информация о сегменте игры
            fps: FPS видео
            
        Returns:
            Список результатов трекинга для данного сегмента
        """
        # Очистка кеша для новой игры
        self._results_cache.clear()
        
        # Открытие видео
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise VideoProcessingError(f"Не удалось открыть видео: {video_path}")
            
        try:
            # Переход к началу сегмента
            cap.set(cv2.CAP_PROP_POS_FRAMES, game_segment.start_frame)
            
            # Чтение первого кадра сегмента для инициализации
            ret, first_frame = cap.read()
            if not ret:
                raise VideoProcessingError(f"Не удалось прочитать первый кадр игры {game_segment.game_id}")
                
            # Инициализация трекинга на первом кадре сегмента
            teams = self._initialize_tracking(first_frame)
            
            # Обработка первого кадра
            first_result = self._process_frame(
                first_frame, 
                game_segment.start_frame, 
                game_segment.start_timestamp
            )
            if first_result:
                self._results_cache.append(first_result)
                
            # Обработка остальных кадров сегмента
            current_frame = game_segment.start_frame + 1
            
            while current_frame <= game_segment.end_frame:
                ret, frame = cap.read()
                
                if not ret:
                    logger.warning(f"Неожиданный конец видео на кадре {current_frame}")
                    break
                    
                # Проверка, нужно ли обрабатывать этот кадр
                if not self.config.should_process_frame(current_frame - game_segment.start_frame):
                    self.stats.frames_skipped += 1
                    current_frame += 1
                    continue
                    
                # Вычисление относительной временной метки для сегмента
                segment_timestamp = (current_frame - game_segment.start_frame) / fps if fps > 0 else 0
                
                # Обработка кадра
                result = self._process_frame(frame, current_frame, segment_timestamp)
                
                if result:
                    self._results_cache.append(result)
                    
                current_frame += 1
                
                # Прерывание по Ctrl+C
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    logger.info(f"Обработка игры {game_segment.game_id} прервана пользователем")
                    break
                    
        finally:
            cap.release()
            
        logger.info(f"Игра {game_segment.game_id} обработана: {len(self._results_cache)} кадров")
        return self._results_cache.copy()