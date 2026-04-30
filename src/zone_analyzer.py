"""
Дополнительный модуль для анализа зоны сужения в Apex Legends (опциональная функциональность).
"""

import cv2
import numpy as np
import logging
from typing import Tuple, Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ZoneInfo:
    """Информация о состоянии зоны."""
    center: Tuple[int, int]
    radius: int
    ring_number: int
    time_remaining: Optional[float]
    is_closing: bool


class ZoneAnalyzer:
    """Анализатор зоны сужения карты (Ring analysis)."""
    
    def __init__(self):
        """Инициализация анализатора зоны."""
        # Цвет границы зоны в HSV (красноватые оттенки)
        self.zone_color_ranges = [
            ((0, 100, 100), (10, 255, 255)),    # Красный
            ((160, 100, 100), (179, 255, 255))  # Красный (обходит 0)
        ]
        
        # Параметры детекции круговых контуров
        self.min_radius = 50
        self.max_radius = 400
        self.circle_threshold = 0.8
        
        # История состояний зоны
        self.zone_history: List[ZoneInfo] = []
        
    def _detect_zone_boundary(self, frame: np.ndarray) -> Optional[Tuple[int, int, int]]:
        """
        Обнаружить границу зоны на кадре.
        
        Args:
            frame: Кадр для анализа
            
        Returns:
            Кортеж (center_x, center_y, radius) или None
        """
        try:
            # Преобразование в HSV
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Создание маски для красных оттенков зоны
            combined_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
            
            for color_range in self.zone_color_ranges:
                lower, upper = color_range
                mask = cv2.inRange(hsv, np.array(lower), np.array(upper))
                combined_mask = cv2.bitwise_or(combined_mask, mask)
                
            # Морфологические операции для очистки
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
            
            # Поиск кругов с помощью Hough Transform
            circles = cv2.HoughCircles(
                combined_mask,
                cv2.HOUGH_GRADIENT,
                dp=1,
                minDist=100,
                param1=50,
                param2=30,
                minRadius=self.min_radius,
                maxRadius=self.max_radius
            )
            
            if circles is not None:
                circles = np.round(circles[0, :]).astype("int")
                
                # Выбор наиболее вероятного круга (самый большой)
                best_circle = None
                max_radius = 0
                
                for (x, y, r) in circles:
                    if r > max_radius:
                        max_radius = r
                        best_circle = (x, y, r)
                        
                return best_circle
                
        except Exception as e:
            logger.debug(f"Ошибка детекции границы зоны: {e}")
            
        return None
        
    def _detect_ring_closing_indicator(self, frame: np.ndarray) -> bool:
        """
        Определить, закрывается ли кольцо в данный момент.
        
        Args:
            frame: Кадр для анализа
            
        Returns:
            True если кольцо закрывается
        """
        try:
            # Поиск текста "RING CLOSING" на экране
            # Область где обычно появляется это уведомление
            roi = frame[500:600, 400:600]  # Примерная область
            
            # Преобразование в оттенки серого
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            
            # Увеличение контраста
            enhanced = cv2.convertScaleAbs(gray, alpha=2.0, beta=0)
            
            # Простая проверка наличия красных пикселей в области уведомления
            hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            red_mask = cv2.inRange(hsv_roi, (0, 100, 100), (10, 255, 255))
            
            red_pixels = cv2.countNonZero(red_mask)
            total_pixels = roi.shape[0] * roi.shape[1]
            red_ratio = red_pixels / total_pixels
            
            # Если много красных пикселей, возможно отображается предупреждение
            return red_ratio > 0.1
            
        except Exception as e:
            logger.debug(f"Ошибка детекции индикатора закрытия: {e}")
            
        return False
        
    def analyze_zone(self, frame: np.ndarray, frame_number: int) -> Optional[ZoneInfo]:
        """
        Проанализировать состояние зоны на кадре.
        
        Args:
            frame: Кадр для анализа
            frame_number: Номер кадра
            
        Returns:
            Информация о зоне или None
        """
        try:
            # Детекция границы зоны
            zone_boundary = self._detect_zone_boundary(frame)
            
            if zone_boundary:
                x, y, radius = zone_boundary
                
                # Определение номера кольца на основе размера (приблизительно)
                ring_number = 1
                if radius < 100:
                    ring_number = 6
                elif radius < 150:
                    ring_number = 5
                elif radius < 200:
                    ring_number = 4
                elif radius < 250:
                    ring_number = 3
                elif radius < 300:
                    ring_number = 2
                    
                # Проверка, закрывается ли кольцо
                is_closing = self._detect_ring_closing_indicator(frame)
                
                zone_info = ZoneInfo(
                    center=(x, y),
                    radius=radius,
                    ring_number=ring_number,
                    time_remaining=None,  # Пока не реализовано
                    is_closing=is_closing
                )
                
                # Добавление в историю
                self.zone_history.append(zone_info)
                
                # Ограничение размера истории
                if len(self.zone_history) > 1000:
                    self.zone_history = self.zone_history[-500:]
                    
                return zone_info
                
        except Exception as e:
            logger.error(f"Ошибка анализа зоны: {e}")
            
        return None
        
    def get_zone_statistics(self) -> Dict[str, Any]:
        """
        Получить статистику анализа зоны.
        
        Returns:
            Словарь со статистикой
        """
        if not self.zone_history:
            return {}
            
        stats = {
            "total_detections": len(self.zone_history),
            "ring_transitions": 0,
            "closing_events": 0,
            "avg_radius_by_ring": {}
        }
        
        # Анализ истории
        ring_radii = {}
        last_ring = None
        
        for zone_info in self.zone_history:
            ring_num = zone_info.ring_number
            
            # Переходы между кольцами
            if last_ring and ring_num != last_ring:
                stats["ring_transitions"] += 1
                
            # События закрытия
            if zone_info.is_closing:
                stats["closing_events"] += 1
                
            # Радиусы по кольцам
            if ring_num not in ring_radii:
                ring_radii[ring_num] = []
            ring_radii[ring_num].append(zone_info.radius)
            
            last_ring = ring_num
            
        # Средние радиусы по кольцам
        for ring_num, radii in ring_radii.items():
            stats["avg_radius_by_ring"][ring_num] = sum(radii) / len(radii)
            
        return stats
        
    def visualize_zone(self, frame: np.ndarray, zone_info: Optional[ZoneInfo]) -> np.ndarray:
        """
        Визуализировать зону на кадре.
        
        Args:
            frame: Исходный кадр
            zone_info: Информация о зоне
            
        Returns:
            Кадр с визуализацией
        """
        viz_frame = frame.copy()
        
        if zone_info:
            center = zone_info.center
            radius = zone_info.radius
            
            # Рисование границы зоны
            color = (0, 0, 255) if zone_info.is_closing else (255, 255, 0)
            cv2.circle(viz_frame, center, radius, color, 3)
            
            # Центр зоны
            cv2.circle(viz_frame, center, 5, color, -1)
            
            # Информация о кольце
            text = f"Ring {zone_info.ring_number}"
            if zone_info.is_closing:
                text += " CLOSING"
                
            cv2.putText(viz_frame, text, (center[0] - 50, center[1] - radius - 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                       
        return viz_frame
        
    def export_zone_data(self) -> Dict[str, Any]:
        """
        Экспортировать данные анализа зоны.
        
        Returns:
            Словарь с данными зоны
        """
        return {
            "zone_analysis": {
                "statistics": self.get_zone_statistics(),
                "history": [
                    {
                        "center": zone.center,
                        "radius": zone.radius,
                        "ring_number": zone.ring_number,
                        "is_closing": zone.is_closing
                    }
                    for zone in self.zone_history
                ]
            }
        }