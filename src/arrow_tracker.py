"""
Модуль для отслеживания стрелочек команд на карте с использованием цветовой фильтрации и ROI оптимизации.
"""

import cv2
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
import threading

logger = logging.getLogger(__name__)


@dataclass
class ArrowTracker:
    """Информация об индивидуальном трекере стрелочки."""
    team_id: str
    tracker: Any  # cv2.Tracker
    last_position: Tuple[int, int, int, int]  # x, y, w, h
    confidence: float = 1.0
    lost_frames: int = 0
    color_hsv_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]] = field(default=None)


@dataclass
class TrackingResult:
    """Результат отслеживания для одного кадра."""
    frame_number: int
    timestamp: float
    team_positions: Dict[str, Dict[str, Any]]  # {team_id: {x, y, confidence}}


class ArrowTrackingSystem:
    """Система отслеживания стрелочек команд на карте."""
    
    def __init__(self):
        """Инициализация системы трекинга."""
        # Область карты для анализа (исключая UI панели)
        self.map_roi = (225, 0, 570, 720)  # x, y, width, height
        
        # Параметры трекинга
        self.roi_expansion_size = 100  # Размер ROI вокруг последней позиции
        self.max_lost_frames = 10      # Максимальное количество потерянных кадров
        self.min_arrow_area = 50       # Минимальная площадь стрелочки
        self.max_arrow_area = 500      # Максимальная площадь стрелочки
        
        # Активные трекеры
        self.active_trackers: Dict[str, ArrowTracker] = {}
        
        # Блокировка для многопоточности
        self._lock = threading.Lock()
        
        # Счетчик кадров
        self._frame_count = 0
        
    def _extract_map_roi(self, frame: np.ndarray) -> np.ndarray:
        """
        Извлечь область карты из кадра (исключая UI панели).
        
        Args:
            frame: Исходный кадр
            
        Returns:
            Область карты
        """
        x, y, w, h = self.map_roi
        return frame[y:y+h, x:x+w]
        
    def _create_color_mask(self, hsv_frame: np.ndarray, 
                          color_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]]) -> np.ndarray:
        """
        Создать маску для определенного цвета в HSV пространстве.
        
        Args:
            hsv_frame: Кадр в HSV формате
            color_range: Диапазон цвета ((h_min, s_min, v_min), (h_max, s_max, v_max))
            
        Returns:
            Бинарная маска
        """
        lower_hsv, upper_hsv = color_range
        mask = cv2.inRange(hsv_frame, np.array(lower_hsv), np.array(upper_hsv))
        
        # Морфологические операции для очистки шума
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        
        # Дополнительное размытие для сглаживания
        mask = cv2.medianBlur(mask, 3)
        
        return mask
        
    def _find_arrows_by_color(self, map_frame: np.ndarray, 
                             color_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]]) -> List[Tuple[int, int, int, int]]:
        """
        Найти стрелочки определенного цвета на карте.
        
        Args:
            map_frame: Область карты
            color_range: Диапазон цвета для поиска
            
        Returns:
            Список найденных стрелочек в формате [(x, y, w, h), ...]
        """
        arrows = []
        
        try:
            # Преобразование в HSV
            hsv_frame = cv2.cvtColor(map_frame, cv2.COLOR_BGR2HSV)
            
            # Создание цветовой маски
            mask = self._create_color_mask(hsv_frame, color_range)
            
            # Поиск контуров
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                # Фильтрация по размеру
                if self.min_arrow_area <= area <= self.max_arrow_area:
                    # Получение ограничивающего прямоугольника
                    x, y, w, h = cv2.boundingRect(contour)
                    
                    # Дополнительные проверки формы
                    aspect_ratio = w / h if h > 0 else 0
                    
                    # Стрелочки обычно не слишком вытянутые
                    if 0.3 <= aspect_ratio <= 3.0:
                        # Проверка заполненности контура
                        rect_area = w * h
                        fill_ratio = area / rect_area if rect_area > 0 else 0
                        
                        if fill_ratio >= 0.3:  # Минимальная заполненность
                            arrows.append((x, y, w, h))
                            
        except Exception as e:
            logger.error(f"Ошибка поиска стрелочек по цвету: {e}")
            
        return arrows
        
    def _initialize_tracker(self, frame: np.ndarray, bbox: Tuple[int, int, int, int]) -> Optional[Any]:
        """
        Инициализировать новый трекер для стрелочки.
        
        Args:
            frame: Кадр для инициализации
            bbox: Ограничивающий прямоугольник (x, y, w, h)
            
        Returns:
            Инициализированный трекер или None при ошибке
        """
        try:
            # Пробуем создать трекер с помощью различных методов
            tracker = None
            
            # Метод 1: Новый API OpenCV 4.5.1+
            if hasattr(cv2, 'legacy') and hasattr(cv2.legacy, 'TrackerCSRT_create'):
                tracker = cv2.legacy.TrackerCSRT_create()
            # Метод 2: Старый API OpenCV
            elif hasattr(cv2, 'TrackerCSRT_create'):
                tracker = cv2.TrackerCSRT_create()
            # Метод 3: Fallback к другим трекерам
            elif hasattr(cv2, 'legacy') and hasattr(cv2.legacy, 'TrackerKCF_create'):
                tracker = cv2.legacy.TrackerKCF_create()
                logger.warning("Используем KCF трекер вместо CSRT")
            elif hasattr(cv2, 'TrackerKCF_create'):
                tracker = cv2.TrackerKCF_create()
                logger.warning("Используем KCF трекер вместо CSRT")
                
            if tracker is None:
                logger.error("Не удалось создать ни один трекер OpenCV")
                return None
            
            # Инициализация трекера
            success = tracker.init(frame, bbox)
            
            if success:
                return tracker
            else:
                logger.warning(f"Не удалось инициализировать трекер для области {bbox}")
                
        except Exception as e:
            logger.error(f"Ошибка создания трекера: {e}")
            
        return None
        
    def _update_tracker_roi(self, frame: np.ndarray, arrow_tracker: ArrowTracker) -> bool:
        """
        Обновить трекер с использованием ROI оптимизации.
        
        Args:
            frame: Текущий кадр
            arrow_tracker: Информация о трекере
            
        Returns:
            True если трекинг успешен, False иначе
        """
        try:
            # Получение области для поиска вокруг последней позиции
            last_x, last_y, last_w, last_h = arrow_tracker.last_position
            
            # Центр последней позиции
            center_x = last_x + last_w // 2
            center_y = last_y + last_h // 2
            
            # Расширенная область поиска
            roi_x = max(0, center_x - self.roi_expansion_size // 2)
            roi_y = max(0, center_y - self.roi_expansion_size // 2)
            roi_w = min(frame.shape[1] - roi_x, self.roi_expansion_size)
            roi_h = min(frame.shape[0] - roi_y, self.roi_expansion_size)
            
            # Извлечение ROI
            roi_frame = frame[roi_y:roi_y+roi_h, roi_x:roi_x+roi_w]
            
            if roi_frame.size == 0:
                return False
                
            # Обновление трекера на ROI
            success, roi_bbox = arrow_tracker.tracker.update(roi_frame)
            
            if success:
                # Преобразование координат обратно к полному кадру
                roi_x_rel, roi_y_rel, roi_w_rel, roi_h_rel = roi_bbox
                global_x = int(roi_x + roi_x_rel)
                global_y = int(roi_y + roi_y_rel)
                global_w = int(roi_w_rel)
                global_h = int(roi_h_rel)
                
                # Проверка, что координаты находятся в пределах кадра
                if (global_x >= 0 and global_y >= 0 and 
                    global_x + global_w <= frame.shape[1] and 
                    global_y + global_h <= frame.shape[0]):
                    
                    arrow_tracker.last_position = (global_x, global_y, global_w, global_h)
                    arrow_tracker.lost_frames = 0
                    
                    # Оценка confidence на основе качества трекинга
                    arrow_tracker.confidence = min(1.0, arrow_tracker.confidence + 0.05)
                    
                    return True
                    
        except Exception as e:
            logger.debug(f"Ошибка обновления трекера ROI: {e}")
            
        return False
        
    def _reinitialize_lost_tracker(self, frame: np.ndarray, arrow_tracker: ArrowTracker) -> bool:
        """
        Попытаться переинициализировать потерянный трекер.
        
        Args:
            frame: Текущий кадр
            arrow_tracker: Информация о потерянном трекере
            
        Returns:
            True если переинициализация успешна, False иначе
        """
        if not arrow_tracker.color_hsv_range:
            return False
            
        try:
            # Поиск стрелочек того же цвета в расширенной области
            last_x, last_y, last_w, last_h = arrow_tracker.last_position
            
            # Область поиска (больше чем для обновления)
            search_size = self.roi_expansion_size * 2
            search_x = max(0, last_x - search_size // 2)
            search_y = max(0, last_y - search_size // 2)
            search_w = min(frame.shape[1] - search_x, search_size)
            search_h = min(frame.shape[0] - search_y, search_size)
            
            search_frame = frame[search_y:search_y+search_h, search_x:search_x+search_w]
            
            # Поиск стрелочек по цвету
            found_arrows = self._find_arrows_by_color(search_frame, arrow_tracker.color_hsv_range)
            
            if found_arrows:
                # Выбор ближайшей стрелочки к последней позиции
                best_arrow = None
                min_distance = float('inf')
                
                for arrow_x, arrow_y, arrow_w, arrow_h in found_arrows:
                    # Преобразование координат
                    global_arrow_x = search_x + arrow_x
                    global_arrow_y = search_y + arrow_y
                    
                    # Расстояние до последней позиции
                    distance = np.sqrt((global_arrow_x - last_x)**2 + (global_arrow_y - last_y)**2)
                    
                    if distance < min_distance:
                        min_distance = distance
                        best_arrow = (global_arrow_x, global_arrow_y, arrow_w, arrow_h)
                        
                if best_arrow and min_distance < self.roi_expansion_size:
                    # Переинициализация трекера
                    new_tracker = self._initialize_tracker(frame, best_arrow)
                    if new_tracker:
                        arrow_tracker.tracker = new_tracker
                        arrow_tracker.last_position = best_arrow
                        arrow_tracker.lost_frames = 0
                        arrow_tracker.confidence = 0.7  # Уменьшенная уверенность после переинициализации
                        
                        logger.info(f"Трекер команды {arrow_tracker.team_id} переинициализирован")
                        return True
                        
        except Exception as e:
            logger.error(f"Ошибка переинициализации трекера: {e}")
            
        return False
        
    def initialize_tracking(self, frame: np.ndarray, 
                           team_colors: Dict[str, Tuple[Tuple[int, int, int], Tuple[int, int, int]]]) -> None:
        """
        Инициализировать трекинг для всех команд на первом кадре.
        
        Args:
            frame: Первый кадр для инициализации
            team_colors: Словарь цветов команд {team_id: (lower_hsv, upper_hsv)}
        """
        with self._lock:
            logger.info("Инициализация системы трекинга стрелочек")
            
            # Очистка существующих трекеров
            self.active_trackers.clear()
            
            # Извлечение области карты
            map_frame = self._extract_map_roi(frame)
            
            # Инициализация трекеров для каждой команды
            for team_id, color_range in team_colors.items():
                try:
                    # Поиск стрелочек данного цвета
                    arrows = self._find_arrows_by_color(map_frame, color_range)
                    
                    if arrows:
                        # Берем первую найденную стрелочку (можно улучшить логику выбора)
                        arrow_bbox = arrows[0]
                        
                        # Преобразование координат относительно полного кадра
                        map_x, map_y, _, _ = self.map_roi
                        full_bbox = (
                            map_x + arrow_bbox[0],
                            map_y + arrow_bbox[1],
                            arrow_bbox[2],
                            arrow_bbox[3]
                        )
                        
                        # Создание трекера
                        tracker = self._initialize_tracker(frame, full_bbox)
                        
                        if tracker:
                            arrow_tracker = ArrowTracker(
                                team_id=team_id,
                                tracker=tracker,
                                last_position=full_bbox,
                                confidence=1.0,
                                lost_frames=0,
                                color_hsv_range=color_range
                            )
                            
                            self.active_trackers[team_id] = arrow_tracker
                            logger.info(f"Инициализирован трекер для команды {team_id} в позиции {full_bbox}")
                        else:
                            logger.warning(f"Не удалось создать трекер для команды {team_id}")
                    else:
                        logger.warning(f"Стрелочки команды {team_id} не найдены на первом кадре")
                        
                except Exception as e:
                    logger.error(f"Ошибка инициализации трекера для команды {team_id}: {e}")
                    
            logger.info(f"Инициализировано {len(self.active_trackers)} трекеров")
            
    def update_tracking(self, frame: np.ndarray, frame_number: int, timestamp: float) -> TrackingResult:
        """
        Обновить трекинг на новом кадре.
        
        Args:
            frame: Текущий кадр
            frame_number: Номер кадра
            timestamp: Временная метка
            
        Returns:
            Результат трекинга
        """
        with self._lock:
            self._frame_count += 1
            team_positions = {}
            
            # Список трекеров для удаления
            trackers_to_remove = []
            
            for team_id, arrow_tracker in self.active_trackers.items():
                try:
                    # Попытка обновления трекера
                    success = self._update_tracker_roi(frame, arrow_tracker)
                    
                    if success:
                        # Успешное отслеживание
                        x, y, w, h = arrow_tracker.last_position
                        center_x = x + w // 2
                        center_y = y + h // 2
                        
                        # Преобразование координат относительно области карты
                        map_x, map_y, _, _ = self.map_roi
                        map_center_x = center_x - map_x
                        map_center_y = center_y - map_y
                        
                        team_positions[team_id] = {
                            'x': map_center_x,
                            'y': map_center_y,
                            'confidence': arrow_tracker.confidence
                        }
                        
                    else:
                        # Трекинг потерян
                        arrow_tracker.lost_frames += 1
                        arrow_tracker.confidence = max(0.1, arrow_tracker.confidence - 0.1)
                        
                        if arrow_tracker.lost_frames <= self.max_lost_frames:
                            # Попытка переинициализации
                            if self._reinitialize_lost_tracker(frame, arrow_tracker):
                                # Успешная переинициализация
                                x, y, w, h = arrow_tracker.last_position
                                center_x = x + w // 2
                                center_y = y + h // 2
                                
                                map_x, map_y, _, _ = self.map_roi
                                map_center_x = center_x - map_x
                                map_center_y = center_y - map_y
                                
                                team_positions[team_id] = {
                                    'x': map_center_x,
                                    'y': map_center_y,
                                    'confidence': arrow_tracker.confidence
                                }
                            else:
                                logger.warning(f"Команда {team_id} потеряна на {arrow_tracker.lost_frames} кадрах")
                        else:
                            # Слишком много потерянных кадров
                            logger.warning(f"Трекер команды {team_id} будет удален после {arrow_tracker.lost_frames} потерянных кадров")
                            trackers_to_remove.append(team_id)
                            
                except Exception as e:
                    logger.error(f"Ошибка обновления трекера команды {team_id}: {e}")
                    trackers_to_remove.append(team_id)
                    
            # Удаление неработающих трекеров
            for team_id in trackers_to_remove:
                del self.active_trackers[team_id]
                
            return TrackingResult(
                frame_number=frame_number,
                timestamp=timestamp,
                team_positions=team_positions
            )
            
    def get_active_teams(self) -> List[str]:
        """
        Получить список активно отслеживаемых команд.
        
        Returns:
            Список ID команд
        """
        with self._lock:
            return list(self.active_trackers.keys())
            
    def visualize_tracking(self, frame: np.ndarray, tracking_result: TrackingResult) -> np.ndarray:
        """
        Визуализировать результаты трекинга на кадре.
        
        Args:
            frame: Исходный кадр
            tracking_result: Результат трекинга
            
        Returns:
            Кадр с визуализацией
        """
        viz_frame = frame.copy()
        
        # Отметка области карты
        map_x, map_y, map_w, map_h = self.map_roi
        cv2.rectangle(viz_frame, (map_x, map_y), (map_x + map_w, map_y + map_h), (0, 255, 255), 2)
        
        # Отметка позиций команд
        for team_id, position in tracking_result.team_positions.items():
            # Преобразование координат обратно к полному кадру
            full_x = map_x + position['x']
            full_y = map_y + position['y']
            confidence = position['confidence']
            
            # Цвет метки в зависимости от уверенности
            color_intensity = int(255 * confidence)
            color = (0, color_intensity, 255 - color_intensity)
            
            # Рисование метки
            cv2.circle(viz_frame, (int(full_x), int(full_y)), 8, color, -1)
            cv2.circle(viz_frame, (int(full_x), int(full_y)), 12, color, 2)
            
            # Подпись
            text = f"#{team_id} ({confidence:.2f})"
            cv2.putText(viz_frame, text, (int(full_x) + 15, int(full_y) - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                       
        # Информация о кадре
        info_text = f"Frame: {tracking_result.frame_number}, Teams: {len(tracking_result.team_positions)}"
        cv2.putText(viz_frame, info_text, (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                   
        return viz_frame