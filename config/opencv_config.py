"""
Конфигурация OpenCV параметров для обработки видео.
"""

import cv2
import numpy as np
from typing import Dict, Tuple, Any


class OpenCVConfig:
    """Конфигурация параметров OpenCV для анализа видео Apex Legends."""
    
    def __init__(self):
        """Инициализация конфигурации OpenCV."""
        
        # Параметры обработки кадров
        self.frame_skip = 2  # Обрабатывать каждый N-й кадр для производительности
        self.resize_factor = 1.0  # Масштабирование кадров (1.0 = без изменений)
        
        # ROI области
        self.left_panel_roi = (0, 0, 225, 720)     # Левая панель команд
        self.right_panel_roi = (795, 0, 225, 720)  # Правая панель команд
        self.map_roi = (225, 0, 570, 720)          # Центральная область карты
        
        # Параметры детекции контуров
        self.contour_retrieval_mode = cv2.RETR_EXTERNAL
        self.contour_approximation = cv2.CHAIN_APPROX_SIMPLE
        self.min_contour_area = 100
        self.max_contour_area = 2000
        
        # Параметры морфологических операций
        self.morphology_kernel_size = (3, 3)
        self.morphology_kernel_type = cv2.MORPH_ELLIPSE
        
        # Параметры фильтрации цветов
        self.hsv_tolerance = 20
        self.min_saturation = 50
        self.min_value = 50
        
        # Параметры трекинга
        self.tracker_type = "CSRT"  # Тип трекера: CSRT, KCF, MIL
        self.max_lost_frames = 10
        self.roi_expansion_size = 100
        
        # Предустановленные диапазоны цветов HSV для распространенных цветов команд
        self.predefined_color_ranges = {
            "red": ((0, 100, 100), (10, 255, 255)),
            "red2": ((160, 100, 100), (179, 255, 255)),  # Красный обходит 0
            "blue": ((100, 100, 100), (130, 255, 255)),
            "green": ((40, 100, 100), (80, 255, 255)),
            "yellow": ((20, 100, 100), (40, 255, 255)),
            "orange": ((10, 100, 100), (25, 255, 255)),
            "purple": ((130, 100, 100), (160, 255, 255)),
            "cyan": ((80, 100, 100), (100, 255, 255)),
            "pink": ((140, 50, 50), (170, 255, 255)),
            "white": ((0, 0, 200), (179, 30, 255)),
            "gray": ((0, 0, 50), (179, 30, 200))
        }
        
        # Параметры размытия для различных операций
        self.gaussian_blur_kernel = (3, 3)
        self.median_blur_kernel = 3
        
        # Параметры адаптивной бинаризации
        self.adaptive_threshold_max_value = 255
        self.adaptive_threshold_method = cv2.ADAPTIVE_THRESH_GAUSSIAN_C
        self.adaptive_threshold_type = cv2.THRESH_BINARY
        self.adaptive_threshold_block_size = 11
        self.adaptive_threshold_c = 2
        
        # Параметры для OCR (используется в team_detector)
        self.ocr_preprocessing = True
        self.ocr_scale_factor = 2
        self.ocr_contrast_alpha = 2.0
        self.ocr_brightness_beta = 0
        
    def create_tracker(self) -> Any:
        """
        Создать трекер указанного типа.
        
        Returns:
            Инициализированный трекер
        """
        try:
            # Попытка использовать новый API OpenCV (4.5.1+)
            if hasattr(cv2, 'legacy') and hasattr(cv2.legacy, 'TrackerCSRT_create'):
                tracker_map = {
                    "CSRT": cv2.legacy.TrackerCSRT_create,
                    "KCF": cv2.legacy.TrackerKCF_create, 
                    "MIL": cv2.legacy.TrackerMIL_create,
                }
            else:
                # Старый API OpenCV
                tracker_map = {
                    "CSRT": getattr(cv2, 'TrackerCSRT_create', None),
                    "KCF": getattr(cv2, 'TrackerKCF_create', None),
                    "MIL": getattr(cv2, 'TrackerMIL_create', None),
                }
            
            if self.tracker_type in tracker_map and tracker_map[self.tracker_type]:
                return tracker_map[self.tracker_type]()
            else:
                # Fallback: пробуем любой доступный трекер
                for tracker_name, tracker_func in tracker_map.items():
                    if tracker_func:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning(f"Используем fallback трекер: {tracker_name}")
                        return tracker_func()
                        
                # Если ничего не работает, возвращаем None
                import logging
                logger = logging.getLogger(__name__)
                logger.error("Ни один трекер OpenCV не доступен")
                return None
                
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка создания трекера: {e}")
            return None
            
    def get_morphology_kernel(self) -> np.ndarray:
        """
        Получить ядро для морфологических операций.
        
        Returns:
            Ядро для морфологических операций
        """
        return cv2.getStructuringElement(
            self.morphology_kernel_type, 
            self.morphology_kernel_size
        )
        
    def expand_color_range(self, base_color_bgr: Tuple[int, int, int]) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        """
        Создать HSV диапазон для заданного BGR цвета.
        
        Args:
            base_color_bgr: Базовый цвет в формате BGR
            
        Returns:
            Диапазон HSV (lower, upper)
        """
        # Преобразование в HSV
        bgr_array = np.uint8([[base_color_bgr]])
        hsv_color = cv2.cvtColor(bgr_array, cv2.COLOR_BGR2HSV)[0][0]
        h, s, v = hsv_color
        
        # Создание диапазона с толерантностью
        lower_hsv = (
            max(0, h - self.hsv_tolerance),
            max(self.min_saturation, s - 50),
            max(self.min_value, v - 50)
        )
        upper_hsv = (
            min(179, h + self.hsv_tolerance),
            255,
            255
        )
        
        return (lower_hsv, upper_hsv)
        
    def preprocess_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Предварительная обработка кадра.
        
        Args:
            frame: Исходный кадр
            
        Returns:
            Обработанный кадр
        """
        processed = frame.copy()
        
        # Масштабирование если нужно
        if self.resize_factor != 1.0:
            new_width = int(processed.shape[1] * self.resize_factor)
            new_height = int(processed.shape[0] * self.resize_factor)
            processed = cv2.resize(processed, (new_width, new_height), 
                                 interpolation=cv2.INTER_AREA)
            
        # Легкое размытие для уменьшения шума
        processed = cv2.GaussianBlur(processed, self.gaussian_blur_kernel, 0)
        
        return processed
        
    def create_color_mask(self, hsv_frame: np.ndarray, 
                         color_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]]) -> np.ndarray:
        """
        Создать маску для цветового диапазона с очисткой шума.
        
        Args:
            hsv_frame: Кадр в HSV формате
            color_range: Диапазон цвета (lower_hsv, upper_hsv)
            
        Returns:
            Очищенная бинарная маска
        """
        lower_hsv, upper_hsv = color_range
        
        # Создание базовой маски
        mask = cv2.inRange(hsv_frame, np.array(lower_hsv), np.array(upper_hsv))
        
        # Морфологические операции для очистки
        kernel = self.get_morphology_kernel()
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        
        # Медианная фильтрация для дополнительной очистки
        mask = cv2.medianBlur(mask, self.median_blur_kernel)
        
        return mask
        
    def get_processing_fps_target(self, source_fps: float) -> float:
        """
        Получить целевой FPS для обработки на основе исходного FPS.
        
        Args:
            source_fps: FPS исходного видео
            
        Returns:
            Целевой FPS для обработки
        """
        if self.frame_skip <= 1:
            return source_fps
        else:
            return source_fps / self.frame_skip
            
    def should_process_frame(self, frame_number: int) -> bool:
        """
        Определить, нужно ли обрабатывать данный кадр.
        
        Args:
            frame_number: Номер кадра
            
        Returns:
            True если кадр нужно обработать
        """
        return frame_number % self.frame_skip == 0