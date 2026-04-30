"""
Модуль для сегментации видео на отдельные игры с обработкой заглушек между ними.
"""

import cv2
import numpy as np
import logging
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class GameSegment:
    """Информация о сегменте игры."""
    game_id: int
    start_frame: int
    end_frame: int
    start_timestamp: float
    end_timestamp: float
    duration: float
    confidence: float  # Уверенность в корректности сегментации


@dataclass
class IntermissionSegment:
    """Информация о заглушке между играми."""
    start_frame: int
    end_frame: int
    start_timestamp: float
    end_timestamp: float
    duration: float
    segment_type: str  # 'loading', 'break', 'unknown'


class GameSegmenter:
    """Класс для сегментации видео на отдельные игры."""
    
    def __init__(self):
        """Инициализация сегментатора игр."""
        # Параметры детекции начала/конца игр
        self.min_game_duration = 60.0  # Минимальная продолжительность игры (секунды)
        self.min_intermission_duration = 5.0  # Минимальная продолжительность заглушки
        
        # Параметры анализа кадров
        self.analysis_interval = 30  # Анализировать каждый N-й кадр
        self.stability_threshold = 10  # Количество стабильных кадров для подтверждения
        
        # Индикаторы состояния игры
        self.game_indicators = {
            'teams_visible': True,      # Видны ли команды на панелях
            'map_active': True,         # Активна ли карта в центре
            'ui_elements': True,        # Присутствуют ли UI элементы игры
            'ring_system': True         # Работает ли система зоны
        }
        
        # Индикаторы заглушки/загрузки
        self.intermission_indicators = {
            'loading_screen': False,    # Экран загрузки
            'static_image': False,      # Статичное изображение
            'sponsor_screen': False,    # Реклама/спонсоры
            'countdown': False          # Обратный отсчет
        }
        
        # История анализа кадров
        self.frame_analysis_history: List[Dict[str, Any]] = []
        
    def _analyze_frame_content(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Анализировать содержимое кадра для определения типа.
        
        Args:
            frame: Кадр для анализа
            
        Returns:
            Словарь с результатами анализа
        """
        analysis = {
            'is_game_frame': False,
            'is_intermission': False,
            'confidence': 0.0,
            'indicators': {}
        }
        
        try:
            height, width = frame.shape[:2]
            
            # Анализ панелей команд (левая и правая)
            left_panel = frame[0:height, 0:225]
            right_panel = frame[0:height, width-225:width]
            
            # Проверка наличия цветовых элементов команд
            teams_detected = self._detect_team_panels(left_panel, right_panel)
            analysis['indicators']['teams_visible'] = teams_detected
            
            # Анализ центральной области (карты)
            map_area = frame[0:height, 225:width-225]
            map_active = self._detect_active_map(map_area)
            analysis['indicators']['map_active'] = map_active
            
            # Проверка UI элементов игры
            ui_present = self._detect_game_ui(frame)
            analysis['indicators']['ui_elements'] = ui_present
            
            # Детекция экранов загрузки/заглушек
            loading_detected = self._detect_loading_screen(frame)
            analysis['indicators']['loading_screen'] = loading_detected
            
            # Детекция статичных изображений
            static_detected = self._detect_static_content(frame)
            analysis['indicators']['static_image'] = static_detected
            
            # Общая оценка типа кадра
            game_score = 0
            if teams_detected:
                game_score += 3
            if map_active:
                game_score += 3
            if ui_present:
                game_score += 2
                
            intermission_score = 0
            if loading_detected:
                intermission_score += 4
            if static_detected:
                intermission_score += 2
                
            # Определение типа кадра
            if game_score >= 5:
                analysis['is_game_frame'] = True
                analysis['confidence'] = min(1.0, game_score / 8.0)
            elif intermission_score >= 3:
                analysis['is_intermission'] = True
                analysis['confidence'] = min(1.0, intermission_score / 6.0)
            else:
                # Неопределенный кадр
                analysis['confidence'] = 0.5
                
        except Exception as e:
            logger.debug(f"Ошибка анализа кадра: {e}")
            
        return analysis
        
    def _detect_team_panels(self, left_panel: np.ndarray, right_panel: np.ndarray) -> bool:
        """Детекция панелей команд."""
        try:
            # Поиск цветовых квадратов в панелях
            for panel in [left_panel, right_panel]:
                # Преобразование в HSV для анализа цветов
                hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
                
                # Поиск насыщенных цветов (признак цветовых квадратов команд)
                saturated_mask = cv2.inRange(hsv, (0, 100, 100), (179, 255, 255))
                
                # Поиск контуров
                contours, _ = cv2.findContours(saturated_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                # Проверка наличия прямоугольных контуров подходящего размера
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if 100 <= area <= 1000:  # Размер цветового квадрата
                        x, y, w, h = cv2.boundingRect(contour)
                        aspect_ratio = w / h if h > 0 else 0
                        if 0.7 <= aspect_ratio <= 1.5:  # Примерно квадратный
                            return True
                            
        except Exception as e:
            logger.debug(f"Ошибка детекции панелей команд: {e}")
            
        return False
        
    def _detect_active_map(self, map_area: np.ndarray) -> bool:
        """Детекция активной карты в центральной области."""
        try:
            # Анализ вариации цветов и текстур в области карты
            gray = cv2.cvtColor(map_area, cv2.COLOR_BGR2GRAY)
            
            # Вычисление статистик изображения
            mean_brightness = np.mean(gray)
            std_brightness = np.std(gray)
            
            # Активная карта должна иметь разнообразие цветов и текстур
            # Загрузочные экраны обычно более однородные
            if std_brightness > 30 and 50 < mean_brightness < 200:
                return True
                
            # Дополнительная проверка через детекцию краев
            edges = cv2.Canny(gray, 50, 150)
            edge_density = np.sum(edges > 0) / (edges.shape[0] * edges.shape[1])
            
            # Активная карта должна содержать много деталей (краев)
            if edge_density > 0.05:
                return True
                
        except Exception as e:
            logger.debug(f"Ошибка детекции активной карты: {e}")
            
        return False
        
    def _detect_game_ui(self, frame: np.ndarray) -> bool:
        """Детекция UI элементов игры."""
        try:
            # Поиск характерных элементов UI (белый текст, иконки)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Поиск белых областей (текст UI)
            white_mask = cv2.inRange(gray, 200, 255)
            white_pixels = cv2.countNonZero(white_mask)
            total_pixels = frame.shape[0] * frame.shape[1]
            white_ratio = white_pixels / total_pixels
            
            # UI обычно содержит 2-8% белых пикселей для текста
            if 0.02 <= white_ratio <= 0.08:
                return True
                
        except Exception as e:
            logger.debug(f"Ошибка детекции UI: {e}")
            
        return False
        
    def _detect_loading_screen(self, frame: np.ndarray) -> bool:
        """Детекция экрана загрузки."""
        try:
            # Загрузочные экраны часто имеют темный фон с логотипами
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_brightness = np.mean(gray)
            std_brightness = np.std(gray)
            
            # Очень темный или очень яркий фон с низкой вариацией
            if (mean_brightness < 30 or mean_brightness > 220) and std_brightness < 20:
                return True
                
            # Поиск текста "LOADING", "Loading" и т.д.
            # Это упрощенная версия - в реальности можно использовать OCR
            
        except Exception as e:
            logger.debug(f"Ошибка детекции загрузки: {e}")
            
        return False
        
    def _detect_static_content(self, frame: np.ndarray) -> bool:
        """Детекция статичного контента (реклама, заставки)."""
        try:
            # Сравнение с предыдущими кадрами для обнаружения статичности
            if len(self.frame_analysis_history) >= 5:
                # Простая проверка - если последние несколько кадров очень похожи
                current_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                
                # Сравнение с предыдущим кадром
                if hasattr(self, '_previous_frame'):
                    diff = cv2.absdiff(current_gray, self._previous_frame)
                    change_ratio = np.sum(diff > 10) / (diff.shape[0] * diff.shape[1])
                    
                    # Если изменений меньше 1%, возможно статичный контент
                    if change_ratio < 0.01:
                        return True
                        
                self._previous_frame = current_gray.copy()
                
        except Exception as e:
            logger.debug(f"Ошибка детекции статичного контента: {e}")
            
        return False
        
    def segment_video(self, video_path: Path, fps: float = 30.0) -> Tuple[List[GameSegment], List[IntermissionSegment]]:
        """
        Сегментировать видео на игры и заглушки.
        
        Args:
            video_path: Путь к видео файлу
            fps: FPS видео
            
        Returns:
            Кортеж (список игр, список заглушек)
        """
        logger.info(f"Начало сегментации видео: {video_path}")
        
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Не удалось открыть видео: {video_path}")
            
        games = []
        intermissions = []
        self.frame_analysis_history.clear()
        
        try:
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            current_segment_start = 0
            current_segment_type = None  # 'game' или 'intermission'
            stable_frames_count = 0
            
            frame_number = 0
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                    
                # Анализировать только каждый N-й кадр
                if frame_number % self.analysis_interval == 0:
                    timestamp = frame_number / fps
                    analysis = self._analyze_frame_content(frame)
                    analysis['frame_number'] = frame_number
                    analysis['timestamp'] = timestamp
                    
                    self.frame_analysis_history.append(analysis)
                    
                    # Определение типа текущего кадра
                    frame_type = None
                    if analysis['is_game_frame']:
                        frame_type = 'game'
                    elif analysis['is_intermission']:
                        frame_type = 'intermission'
                        
                    # Логика сегментации
                    if current_segment_type is None:
                        # Начало первого сегмента
                        current_segment_type = frame_type
                        current_segment_start = frame_number
                        stable_frames_count = 1
                        
                    elif current_segment_type == frame_type:
                        # Продолжение текущего сегмента
                        stable_frames_count += 1
                        
                    else:
                        # Возможная смена типа сегмента
                        if stable_frames_count >= self.stability_threshold:
                            # Достаточно стабильных кадров - завершаем текущий сегмент
                            segment_end = frame_number - (stable_frames_count * self.analysis_interval)
                            segment_duration = (segment_end - current_segment_start) / fps
                            
                            if current_segment_type == 'game' and segment_duration >= self.min_game_duration:
                                games.append(GameSegment(
                                    game_id=len(games) + 1,
                                    start_frame=current_segment_start,
                                    end_frame=segment_end,
                                    start_timestamp=current_segment_start / fps,
                                    end_timestamp=segment_end / fps,
                                    duration=segment_duration,
                                    confidence=0.8  # Можно улучшить расчет
                                ))
                                logger.info(f"Найдена игра {len(games)}: {segment_duration:.1f}с")
                                
                            elif current_segment_type == 'intermission' and segment_duration >= self.min_intermission_duration:
                                intermissions.append(IntermissionSegment(
                                    start_frame=current_segment_start,
                                    end_frame=segment_end,
                                    start_timestamp=current_segment_start / fps,
                                    end_timestamp=segment_end / fps,
                                    duration=segment_duration,
                                    segment_type='unknown'  # Можно улучшить классификацию
                                ))
                                logger.info(f"Найдена заглушка: {segment_duration:.1f}с")
                                
                            # Начало нового сегмента
                            current_segment_type = frame_type
                            current_segment_start = segment_end
                            stable_frames_count = 1
                        else:
                            # Недостаточно стабильных кадров - считаем шумом
                            stable_frames_count = 1
                            
                frame_number += 1
                
            # Обработка последнего сегмента
            if current_segment_type and stable_frames_count >= self.stability_threshold:
                segment_duration = (frame_number - current_segment_start) / fps
                
                if current_segment_type == 'game' and segment_duration >= self.min_game_duration:
                    games.append(GameSegment(
                        game_id=len(games) + 1,
                        start_frame=current_segment_start,
                        end_frame=frame_number,
                        start_timestamp=current_segment_start / fps,
                        end_timestamp=frame_number / fps,
                        duration=segment_duration,
                        confidence=0.8
                    ))
                    
        finally:
            cap.release()
            
        logger.info(f"Сегментация завершена: найдено {len(games)} игр и {len(intermissions)} заглушек")
        return games, intermissions
        
    def get_game_segment(self, video_path: Path, game_id: int) -> Optional[GameSegment]:
        """
        Получить конкретный сегмент игры.
        
        Args:
            video_path: Путь к видео файлу
            game_id: ID игры
            
        Returns:
            Сегмент игры или None
        """
        games, _ = self.segment_video(video_path)
        
        for game in games:
            if game.game_id == game_id:
                return game
                
        return None
        
    def export_segments_info(self, games: List[GameSegment], 
                           intermissions: List[IntermissionSegment]) -> Dict[str, Any]:
        """
        Экспорт информации о сегментах для сохранения в JSON.
        
        Args:
            games: Список игровых сегментов
            intermissions: Список заглушек
            
        Returns:
            Словарь с информацией о сегментах
        """
        return {
            "segmentation_info": {
                "total_games": len(games),
                "total_intermissions": len(intermissions),
                "games": [
                    {
                        "game_id": game.game_id,
                        "start_frame": game.start_frame,
                        "end_frame": game.end_frame,
                        "start_timestamp": round(game.start_timestamp, 3),
                        "end_timestamp": round(game.end_timestamp, 3),
                        "duration": round(game.duration, 3),
                        "confidence": round(game.confidence, 3)
                    }
                    for game in games
                ],
                "intermissions": [
                    {
                        "start_frame": intermission.start_frame,
                        "end_frame": intermission.end_frame,
                        "start_timestamp": round(intermission.start_timestamp, 3),
                        "end_timestamp": round(intermission.end_timestamp, 3),
                        "duration": round(intermission.duration, 3),
                        "type": intermission.segment_type
                    }
                    for intermission in intermissions
                ]
            }
        }