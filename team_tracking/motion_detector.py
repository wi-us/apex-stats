"""
Модуль для определения начальной позиции стрелочки команды через анализ движения.
"""

import cv2
import numpy as np
import logging
from typing import Tuple, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MovingObject:
    """Информация о движущемся объекте."""
    positions: List[Tuple[int, int]]  # История позиций (x, y)
    bboxes: List[Tuple[int, int, int, int]]  # История bounding boxes
    movement_score: float  # Оценка активности движения
    
    def get_average_position(self) -> Tuple[int, int]:
        """Получить среднюю позицию объекта."""
        if not self.positions:
            return (0, 0)
        avg_x = int(np.mean([p[0] for p in self.positions]))
        avg_y = int(np.mean([p[1] for p in self.positions]))
        return (avg_x, avg_y)
    
    def get_latest_bbox(self) -> Optional[Tuple[int, int, int, int]]:
        """Получить последний bounding box."""
        return self.bboxes[-1] if self.bboxes else None


class MotionDetector:
    """Детектор движения для определения начальной позиции стрелочки."""
    
    def __init__(self, color_hsv_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]],
                 min_area: int = 30, max_area: int = 500, min_movement: float = 5.0,
                 map_roi: Optional[Tuple[int, int, int, int]] = None):
        """
        Инициализация детектора движения.
        
        Args:
            color_hsv_range: Диапазон цвета в HSV ((h_min, s_min, v_min), (h_max, s_max, v_max))
            min_area: Минимальная площадь контура
            max_area: Максимальная площадь контура
            min_movement: Минимальное расстояние для регистрации движения
            map_roi: Ограничение рабочей зоны (x, y, width, height). Если None, работает по всему кадру
        """
        self.color_range = color_hsv_range
        self.lower_hsv = np.array(color_hsv_range[0], dtype=np.uint8)
        self.upper_hsv = np.array(color_hsv_range[1], dtype=np.uint8)
        self.min_area = min_area
        self.max_area = max_area
        self.min_movement = min_movement
        self.map_roi = map_roi
        self.lower_lab, self.upper_lab = self._build_lab_range_from_hsv(
            color_hsv_range[0], color_hsv_range[1]
        )
        
        # История обнаруженных объектов
        self.tracked_objects: List[MovingObject] = []
        
        # Предыдущий кадр для optical flow
        self.prev_frame_gray = None

    def _hsv_to_lab(self, hsv: Tuple[int, int, int]) -> np.ndarray:
        pixel_hsv = np.array([[[hsv[0], hsv[1], hsv[2]]]], dtype=np.uint8)
        pixel_bgr = cv2.cvtColor(pixel_hsv, cv2.COLOR_HSV2BGR)
        pixel_lab = cv2.cvtColor(pixel_bgr, cv2.COLOR_BGR2LAB)
        return pixel_lab[0, 0].astype(np.int16)

    def _build_lab_range_from_hsv(
        self,
        lower_hsv: Tuple[int, int, int],
        upper_hsv: Tuple[int, int, int],
    ) -> Tuple[np.ndarray, np.ndarray]:
        lab_low = self._hsv_to_lab(lower_hsv)
        lab_high = self._hsv_to_lab(upper_hsv)

        l_min = int(max(0, min(lab_low[0], lab_high[0]) - 20))
        l_max = int(min(255, max(lab_low[0], lab_high[0]) + 20))
        a_min = int(max(0, min(lab_low[1], lab_high[1]) - 28))
        a_max = int(min(255, max(lab_low[1], lab_high[1]) + 28))
        b_min = int(max(0, min(lab_low[2], lab_high[2]) - 28))
        b_max = int(min(255, max(lab_low[2], lab_high[2]) + 28))

        return (
            np.array([l_min, a_min, b_min], dtype=np.uint8),
            np.array([l_max, a_max, b_max], dtype=np.uint8),
        )
        
    def _create_color_mask(self, frame: np.ndarray) -> np.ndarray:
        """
        Создать маску для определенного цвета.
        
        Args:
            frame: Кадр в BGR формате
            
        Returns:
            Бинарная маска
        """
        hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask_hsv = cv2.inRange(hsv_frame, self.lower_hsv, self.upper_hsv)

        lab_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        mask_lab = cv2.inRange(lab_frame, self.lower_lab, self.upper_lab)
        mask = cv2.bitwise_and(mask_hsv, mask_lab)
        if cv2.countNonZero(mask) < 8:
            mask = mask_hsv
        
        # Морфологические операции для очистки шума
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.medianBlur(mask, 3)
        
        return mask
    
    def _find_contours_in_frame(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        Найти контуры стрелочек на кадре.
        
        Args:
            frame: Кадр для анализа
            
        Returns:
            Список bounding boxes [(x, y, w, h), ...]
        """
        mask = self._create_color_mask(frame)
        
        # Дополнительная морфология для удаления текста и мелких артефактов
        kernel_large = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_large)
        
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Для первичного поиска плашки команды расширяем верхнюю границу площади.
        search_max_area = max(self.max_area * 40, self.max_area)
        bboxes = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if self.min_area <= area <= search_max_area:
                x, y, w, h = cv2.boundingRect(contour)
                
                # ПРОВЕРКА: bbox должен быть внутри MAP_ROI
                if self.map_roi:
                    map_x, map_y, map_w, map_h = self.map_roi
                    bbox_center_x = x + w // 2
                    bbox_center_y = y + h // 2
                    
                    # Проверяем что центр bbox находится внутри области карты
                    if not (map_x <= bbox_center_x < map_x + map_w and 
                           map_y <= bbox_center_y < map_y + map_h):
                        continue  # Пропускаем bbox вне области карты
                
                # Проверка пропорций под плашку команды.
                aspect_ratio = w / h if h > 0 else 0
                fill_ratio = area / max(1.0, float(w * h))
                if 0.7 <= aspect_ratio <= 10.0 and fill_ratio >= 0.22:
                    bboxes.append((x, y, w, h))
        
        return bboxes
    
    def _match_bbox_to_object(self, bbox: Tuple[int, int, int, int], 
                              max_distance: float = 50.0) -> Optional[MovingObject]:
        """
        Сопоставить bounding box с существующим отслеживаемым объектом.
        
        Args:
            bbox: Bounding box для сопоставления
            max_distance: Максимальное расстояние для сопоставления
            
        Returns:
            Соответствующий объект или None
        """
        x, y, w, h = bbox
        center_x = x + w // 2
        center_y = y + h // 2
        
        best_match = None
        min_distance = max_distance
        
        for obj in self.tracked_objects:
            if not obj.positions:
                continue
            
            last_x, last_y = obj.positions[-1]
            distance = np.sqrt((center_x - last_x)**2 + (center_y - last_y)**2)
            
            if distance < min_distance:
                min_distance = distance
                best_match = obj
        
        return best_match
    
    def _calculate_movement_score(self, positions: List[Tuple[int, int]]) -> float:
        """
        Вычислить оценку активности движения объекта.
        
        Args:
            positions: История позиций
            
        Returns:
            Оценка движения (чем выше, тем активнее)
        """
        if len(positions) < 5:  # Нужно минимум 5 позиций для анализа
            return 0.0
        
        total_distance = 0.0
        movements = []
        
        for i in range(1, len(positions)):
            x1, y1 = positions[i-1]
            x2, y2 = positions[i]
            distance = np.sqrt((x2 - x1)**2 + (y2 - y1)**2)
            total_distance += distance
            movements.append(distance)
        
        # Средняя дистанция за шаг
        avg_movement = total_distance / (len(positions) - 1)
        
        # ВАЖНО: Проверка что объект действительно движется (не статичен)
        # Статичные объекты имеют движение ~0
        if avg_movement < 1.0:  # Меньше 1 пикселя за кадр = статичный
            return 0.0
        
        # Проверка стабильности движения (не просто дрожание)
        if movements:
            movement_variance = np.var(movements)
            # Если все движения примерно одинаковы - это хорошо
            stability_score = 1.0 / (1.0 + movement_variance)
        else:
            stability_score = 0.0
        
        # Бонус за количество наблюдений (объект не исчезает)
        presence_bonus = len(positions) / 100.0
        
        # Итоговая оценка
        score = (avg_movement * 2.0) + (stability_score * 10.0) + presence_bonus
        
        return score
    
    def _is_bbox_moving(self, frame: np.ndarray, bbox: Tuple[int, int, int, int]) -> bool:
        """
        Проверить, действительно ли объект в bbox движется, используя optical flow.
        Это помогает отличить реально движущиеся стрелочки от статичных элементов карты.
        
        Args:
            frame: Текущий кадр
            bbox: Bounding box для проверки
            
        Returns:
            True если объект движется, False если статичен
        """
        if self.prev_frame_gray is None:
            return True  # Первый кадр - считаем что движется
        
        x, y, w, h = bbox
        
        # Безопасная проверка границ
        if x < 0 or y < 0 or x + w >= frame.shape[1] or y + h >= frame.shape[0]:
            return False
        
        # Минимальный размер bbox для анализа
        if w < 5 or h < 5:
            return False
        
        try:
            # Преобразуем в grayscale
            curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Создаем сетку точек в области bbox для отслеживания
            step = max(3, min(w, h) // 3)
            points = []
            
            for py in range(y + 2, min(y + h - 2, frame.shape[0]), step):
                for px in range(x + 2, min(x + w - 2, frame.shape[1]), step):
                    points.append([[float(px), float(py)]])
            
            if len(points) < 3:
                return True
            
            p0 = np.array(points, dtype=np.float32)
            
            # Рассчитываем optical flow
            p1, st, err = cv2.calcOpticalFlowPyrLK(
                self.prev_frame_gray, curr_gray, p0, None,
                winSize=(10, 10),
                maxLevel=2,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03)
            )
            
            if p1 is None or st is None:
                return True
            
            # Считаем среднее смещение успешно отслеженных точек
            good_new = p1[st == 1]
            good_old = p0[st == 1]
            
            if len(good_new) < 2:
                return True
            
            # Средняя величина смещения
            displacements = np.linalg.norm(good_new - good_old, axis=1)
            avg_displacement = np.mean(displacements)
            
            # Если смещение больше 0.3 пикселя - объект движется
            is_moving = avg_displacement > 0.3
            
            return is_moving
            
        except Exception as e:
            logger.debug(f"Ошибка при вычислении optical flow: {e}")
            return True
    
    def process_frame(self, frame: np.ndarray) -> None:
        """
        Обработать кадр и обновить информацию об отслеживаемых объектах.
        
        Args:
            frame: Кадр для обработки
        """
        # Сохраняем grayscale для следующего кадра
        curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        bboxes = self._find_contours_in_frame(frame)
        
        # Фильтрация bbox по движению (optical flow)
        moving_bboxes = []
        for bbox in bboxes:
            if self._is_bbox_moving(frame, bbox):
                moving_bboxes.append(bbox)
        
        # Сопоставление найденных moving_bboxes с существующими объектами
        matched_objects = set()
        
        for bbox in moving_bboxes:
            x, y, w, h = bbox
            center_x = x + w // 2
            center_y = y + h // 2
            
            matched_obj = self._match_bbox_to_object(bbox)
            
            if matched_obj:
                # Обновление существующего объекта
                matched_obj.positions.append((center_x, center_y))
                matched_obj.bboxes.append(bbox)
                matched_objects.add(id(matched_obj))
            else:
                # Создание нового объекта
                new_obj = MovingObject(
                    positions=[(center_x, center_y)],
                    bboxes=[bbox],
                    movement_score=0.0
                )
                self.tracked_objects.append(new_obj)
                matched_objects.add(id(new_obj))
        
        # Пересчет оценок движения для всех объектов
        for obj in self.tracked_objects:
            obj.movement_score = self._calculate_movement_score(obj.positions)
        
        # Обновляем prev_frame для следующей итерации
        self.prev_frame_gray = curr_gray
    
    def get_best_moving_object(self) -> Optional[MovingObject]:
        """
        Получить объект с наибольшей активностью движения.
        
        Returns:
            Объект с наибольшей оценкой движения или None
        """
        if not self.tracked_objects:
            return None
        
        # Фильтрация объектов с минимальным движением
        moving_objects = [obj for obj in self.tracked_objects 
                         if obj.movement_score > self.min_movement]
        
        if not moving_objects:
            logger.warning("Не найдено движущихся объектов с достаточной активностью")
            return None
        
        # Выбор объекта с максимальной оценкой
        best_object = max(moving_objects, key=lambda obj: obj.movement_score)
        
        logger.info(f"Выбран объект с оценкой движения: {best_object.movement_score:.2f}, "
                   f"количество позиций: {len(best_object.positions)}")
        
        return best_object


def find_initial_position(frames: List[np.ndarray], 
                          color_hsv_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]],
                          team_name: str = "Unknown",
                          min_area: int = 8,
                          max_area: int = 250,
                          map_roi: Optional[Tuple[int, int, int, int]] = None) -> Optional[Tuple[int, int, int, int]]:
    """
    Найти начальную позицию стрелочки команды через анализ движения.
    
    Args:
        frames: Список кадров для анализа (первые 10 секунд видео)
        color_hsv_range: Диапазон цвета команды в HSV
        team_name: Название команды (для логирования)
        min_area: Минимальная площадь контура
        max_area: Максимальная площадь контура
        map_roi: Ограничение рабочей зоны (x, y, width, height). Если None, работает по всему кадру
        
    Returns:
        Bounding box начальной позиции (x, y, w, h) или None
    """
    logger.info(f"Начало поиска начальной позиции для команды {team_name}")
    logger.info(f"HSV диапазон: {color_hsv_range}")
    logger.info(f"Площадь контура: {min_area}-{max_area}")
    if map_roi:
        logger.info(f"Рабочая зона: x={map_roi[0]}-{map_roi[0]+map_roi[2]}, y={map_roi[1]}-{map_roi[1]+map_roi[3]}")
    logger.info(f"Анализ {len(frames)} кадров...")
    
    detector = MotionDetector(color_hsv_range, min_area=min_area, max_area=max_area, 
                             min_movement=2.0, map_roi=map_roi)
    
    # Обработка всех кадров
    for i, frame in enumerate(frames):
        detector.process_frame(frame)
        
        if (i + 1) % 50 == 0:
            logger.info(f"Обработано {i + 1}/{len(frames)} кадров, "
                        f"отслеживается объектов: {len(detector.tracked_objects)}")
    
    logger.info(f"\nВсего найдено объектов: {len(detector.tracked_objects)}")
    
    # Выводим информацию о ТОП-10 объектах
    if detector.tracked_objects:
        logger.info("\n" + "="*60)
        logger.info("ТОП-10 объектов по оценке движения:")
        logger.info("="*60)
        sorted_objects = sorted(detector.tracked_objects, 
                               key=lambda obj: obj.movement_score, reverse=True)[:10]
        for i, obj in enumerate(sorted_objects, 1):
            avg_pos = obj.get_average_position()
            logger.info(f"{i:2d}. Оценка: {obj.movement_score:7.2f} | "
                       f"Позиций: {len(obj.positions):4d} | "
                       f"Средняя позиция: ({avg_pos[0]:4d}, {avg_pos[1]:4d})")
        logger.info("="*60 + "\n")
    
    # Получение лучшего движущегося объекта
    best_object = detector.get_best_moving_object()
    
    if best_object:
        # Фиксируем стартовый bbox как самый крупный из истории лучшего движущегося объекта.
        bbox = max(best_object.bboxes, key=lambda b: b[2] * b[3]) if best_object.bboxes else best_object.get_latest_bbox()
        avg_pos = best_object.get_average_position()
        
        logger.info(f"✓ Найдена начальная позиция для {team_name}:")
        logger.info(f"  - bbox: {bbox}")
        logger.info(f"  - средняя позиция: {avg_pos}")
        logger.info(f"  - оценка движения: {best_object.movement_score:.2f}")
        logger.info(f"  - количество наблюдений: {len(best_object.positions)}")
        
        # Дополнительная проверка
        if best_object.movement_score < 5.0:
            logger.warning(f"⚠ Низкая оценка движения ({best_object.movement_score:.2f}). "
                          f"Возможно, найден статичный объект вместо стрелочки!")
        
        return bbox
    else:
        logger.error(f"✗ Не удалось найти начальную позицию для команды {team_name}")
        logger.error("Возможные причины:")
        logger.error("  1. HSV диапазон неправильно настроен")
        logger.error("  2. Стрелочка не движется в первые 10 секунд")
        logger.error("  3. Цветовой фильтр захватывает только статичные объекты")
        
        return None
