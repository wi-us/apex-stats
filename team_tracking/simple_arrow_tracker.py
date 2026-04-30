"""
Упрощенный трекер без сложных проверок.
Только: HSV фильтр → Размер → Пропорции → Ближайший к центру ROI → Готово!
"""

import cv2
import numpy as np
import logging
from typing import Tuple, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryPoint:
    """Точка траектории."""
    x: int
    y: int
    timestamp: float
    confidence: float = 1.0


class SimpleArrowTracker:
    """Простой трекер без проверок на скачки, застревание и т.д."""
    
    def __init__(self, initial_bbox: Tuple[int, int, int, int],
                 color_hsv_range: Tuple[Tuple[int, int, int], Tuple[int, int, int]],
                 roi_size: int = 200,
                 min_area: int = 8,
                 max_area: int = 250,
                 morph_kernel_size: int = 5,
                 smoothing_window: int = 20,  # Увеличено до 15 точек (было 10)
                 outlier_threshold_ratio: float = 0.04,  # Порог 8% = 16px (было 12% = 24px)
                 map_roi: Optional[Tuple[int, int, int, int]] = None,
                 selection_strategy: str = "label_arrow",
                 calibration_duration_sec: float = 10.0,
                 forced_bbox_size: Optional[Tuple[int, int]] = None,
                 predict_seconds: float = 1.5,
                 switch_confirm_frames: int = 3,
                 max_step_px: float = 16.0,
                 forbidden_polygons: Optional[List[np.ndarray]] = None,
                 forbidden_zone_size: Optional[Tuple[float, float]] = None,
                 advanced_recovery_mode: bool = False):  # (x, y, width, height) - рабочая зона карты
        """
        Инициализация упрощенного трекера.
        
        Args:
            initial_bbox: Начальный bounding box (x, y, w, h)
            color_hsv_range: Диапазон цвета в HSV
            roi_size: Размер области поиска вокруг последней позиции
            min_area: Минимальная площадь контура
            max_area: Максимальная площадь контура
            morph_kernel_size: Размер ядра морфологии
            smoothing_window: Размер окна для анализа и сглаживания траектории
            outlier_threshold_ratio: Порог для определения выброса (доля от ROI размера)
            map_roi: Ограничение рабочей зоны (x, y, width, height). Если None, работает по всему кадру
        """
        self.last_bbox = initial_bbox
        self.color_range = color_hsv_range
        self.lower_hsv = np.array(color_hsv_range[0], dtype=np.uint8)
        self.upper_hsv = np.array(color_hsv_range[1], dtype=np.uint8)
        self.roi_size = roi_size
        self.min_area = min_area
        self.max_area = max_area
        self.morph_kernel_size = morph_kernel_size
        self.smoothing_window = smoothing_window
        self.outlier_threshold_ratio = outlier_threshold_ratio
        self.map_roi = map_roi  # Ограничение рабочей зоны
        self.selection_strategy = selection_strategy
        self.predict_seconds = max(0.0, float(predict_seconds))
        self.forbidden_polygons = forbidden_polygons or []
        self.forbidden_zone_size = forbidden_zone_size
        self.last_zone_rejected_candidates = 0
        
        # Траектория
        self.trajectory: List[TrajectoryPoint] = []
        
        # Текущая позиция точки трекинга (используется для ROI)
        self.tracking_position: Optional[Tuple[int, int]] = None
        
        # Буфер для сглаживания (последние N необработанных детекций)
        self.detection_buffer: List[Tuple[int, int]] = []
        
        # Буфер для усреднения за секунду (временное окно)
        self.second_buffer: List[Tuple[int, int, float]] = []  # (x, y, timestamp)
        self.last_saved_second: Optional[int] = None  # Последняя сохраненная секунда
        self.averaging_interval: int = 2  # Усреднять за 2 секунды
        
        # Счетчики
        self.lost_frames = 0
        self.confidence = 1.0
        
        # Счетчик успешных детекций подряд (для "липкости")
        self.consecutive_detections = 0
        self.tracking_locked = False  # Флаг "захвата" объекта
        
        # Фиксация размера bbox после калибровки
        self.is_bbox_calibrated = False
        self.calibration_frames_needed = 60  # запасной порог по кадрам
        self.calibration_duration_sec = calibration_duration_sec
        self.calibration_start_timestamp: Optional[float] = None
        self.calibration_bboxes: List[Tuple[int, int, int, int]] = []
        self.fixed_bbox_size: Optional[Tuple[int, int]] = None  # (width, height)
        self.fixed_tracking_offset: Optional[Tuple[int, int]] = None  # (dx, dy) от центра bbox до точки трекинга
        self.min_label_w = 32
        self.min_label_h = 14
        self.calibration_max_w = max(self.min_label_w, int(initial_bbox[2]))
        self.calibration_max_h = max(self.min_label_h, int(initial_bbox[3]))
        self.default_bbox_size = (
            max(self.min_label_w, int(initial_bbox[2])),
            max(self.min_label_h, int(initial_bbox[3])),
        )
        if forced_bbox_size is not None:
            fw = max(self.min_label_w, int(forced_bbox_size[0]))
            fh = max(self.min_label_h, int(forced_bbox_size[1]))
            if fw / max(1.0, float(fh)) < 1.3:
                fw = max(fw, int(round(fh * 1.8)))
            self.fixed_bbox_size = (fw, fh)
            self.fixed_tracking_offset = (0, fh // 2 + 3)
            self.is_bbox_calibrated = True

        self.stable_center: Tuple[float, float] = (
            float(initial_bbox[0] + self.default_bbox_size[0] // 2),
            float(initial_bbox[1] + self.default_bbox_size[1] // 2),
        )
        self.stable_right_x: float = float(initial_bbox[0] + self.default_bbox_size[0])
        self.center_smoothing_alpha = 0.25
        self.center_deadzone_px = 2.0
        self.max_center_step_px = max(2.0, float(max_step_px))
        self.right_edge_smoothing_alpha = 0.35
        self.right_edge_deadzone_px = 1.5
        self.max_right_edge_step_px = max(2.0, float(max_step_px))
        self.min_tracking_roi_px = 120
        self.jump_switch_threshold_px = 18.0
        self.identity_iou_gate = 0.10
        self.identity_right_gate_px = max(8.0, self.max_right_edge_step_px * 1.4)
        self.pending_center: Optional[Tuple[float, float]] = None
        self.pending_center_hits = 0
        self.pending_required_hits = max(1, int(switch_confirm_frames))
        self.last_detected_timestamp: Optional[float] = None
        self.track_state: str = "tracked"
        self.state_reason: str = "init"
        self.last_mask_mode: str = "hsv+lab"
        self.mask_too_sparse_count = 0
        self.shape_reject_count = 0
        self.zone_gate_reject_count = 0
        self.roi_expand_px = 0
        self.max_roi_expand_px = 400
        self.roi_expand_step_px = 100
        self.low_conf_stall_sec_threshold = 5.0
        self.stall_deadzone_px = 2.0
        self.last_stall_point: Optional[Tuple[float, float]] = None
        self.last_stall_timestamp: Optional[float] = None
        self.recording_warmup_sec = 30.0
        self.stable_tracking_start_ts: Optional[float] = None
        self.team6_mode_enabled = bool(advanced_recovery_mode)
        self.recording_enabled = not self.team6_mode_enabled
        self._confirmed_observations: List[Tuple[float, float, float, float]] = []  # ts, cx, cy, rx
        self.lower_lab, self.upper_lab = self._build_lab_range_from_hsv(
            color_hsv_range[0], color_hsv_range[1]
        )

    def _append_confirmed_observation(self, timestamp: float, center_x: float, center_y: float, right_x: float) -> None:
        self._confirmed_observations.append((timestamp, center_x, center_y, right_x))
        if len(self._confirmed_observations) > 12:
            self._confirmed_observations.pop(0)

    def _predict_from_motion_model(self, timestamp: float) -> Optional[Tuple[float, float, float]]:
        if len(self._confirmed_observations) < 2:
            return None
        t2, cx2, cy2, rx2 = self._confirmed_observations[-1]
        t1, cx1, cy1, rx1 = self._confirmed_observations[-2]
        dt = max(1e-6, t2 - t1)
        dt_future = max(0.0, timestamp - t2)
        vx = (cx2 - cx1) / dt
        vy = (cy2 - cy1) / dt
        vr = (rx2 - rx1) / dt
        px = cx2 + vx * dt_future
        py = cy2 + vy * dt_future
        pr = rx2 + vr * dt_future
        return (px, py, pr)

    def _is_forbidden_tracking_point(self, global_x: float, global_y: float) -> bool:
        if not self.forbidden_polygons or self.forbidden_zone_size is None or self.map_roi is None:
            return False

        zone_w, zone_h = self.forbidden_zone_size
        if zone_w <= 1e-6 or zone_h <= 1e-6:
            return False

        map_x, map_y, map_w, map_h = self.map_roi
        if map_w <= 0 or map_h <= 0:
            return False

        local_x = ((global_x - map_x) / map_w) * zone_w
        local_y = ((global_y - map_y) / map_h) * zone_h
        for poly in self.forbidden_polygons:
            if cv2.pointPolygonTest(poly, (float(local_x), float(local_y)), False) >= 0:
                return True
        return False

    def _estimate_label_size(self) -> Tuple[int, int]:
        """Оценить текущий размер плашки (для безопасного размера ROI)."""
        if self.is_bbox_calibrated and self.fixed_bbox_size is not None:
            return self.fixed_bbox_size

        if self.calibration_bboxes:
            w, h = self._compute_robust_label_size(self.calibration_bboxes)
            if w / max(1.0, float(h)) < 1.3:
                w = max(w, int(round(h * 1.8)))
            return (w, h)

        return self.default_bbox_size

    def _compute_robust_label_size(self, bboxes: List[Tuple[int, int, int, int]]) -> Tuple[int, int]:
        """
        Робастная оценка размера плашки:
        берем верхнюю (крупную) часть наблюдений, чтобы мелкий шум не занижал итог.
        """
        widths = np.array([bbox[2] for bbox in bboxes], dtype=np.float32)
        heights = np.array([bbox[3] for bbox in bboxes], dtype=np.float32)
        if widths.size == 0 or heights.size == 0:
            return self.default_bbox_size

        # Порог по "крупным" наблюдениям: берем верхние ~35% размеров.
        w_cut = float(np.percentile(widths, 65))
        h_cut = float(np.percentile(heights, 65))
        top_w = widths[widths >= w_cut]
        top_h = heights[heights >= h_cut]
        if top_w.size == 0:
            top_w = widths
        if top_h.size == 0:
            top_h = heights

        w = int(max(self.min_label_w, round(float(np.median(top_w)))))
        h = int(max(self.min_label_h, round(float(np.median(top_h)))))

        # Не даем финальному размеру просесть сильно ниже лучшего наблюдения.
        w = max(w, int(round(self.calibration_max_w * 0.85)))
        h = max(h, int(round(self.calibration_max_h * 0.85)))
        return (w, h)

    def get_effective_roi_size(self) -> int:
        """Размер ROI, который реально используется в текущем кадре."""
        est_w, est_h = self._estimate_label_size()
        if not self.is_bbox_calibrated:
            # Пока идет калибровка 10 секунд, не сужаем ROI.
            return max(self.roi_size, est_w * 3, est_h * 4, self.min_tracking_roi_px) + int(self.roi_expand_px)
        if self.tracking_locked and self.consecutive_detections > 15:
            # После захвата ROI можно сужать, но не ниже размера, достаточного
            # чтобы целиком видеть плашку и область под ней.
            return max(int(self.roi_size * 0.3), est_w * 2, est_h * 3, self.min_tracking_roi_px) + int(self.roi_expand_px)
        return max(self.roi_size, est_w * 2, est_h * 3, self.min_tracking_roi_px) + int(self.roi_expand_px)

    def _update_roi_stall_logic(self, timestamp: float, point: Tuple[int, int], confident: bool) -> None:
        if confident:
            self.last_stall_point = (float(point[0]), float(point[1]))
            self.last_stall_timestamp = timestamp
            # Gradually return to normal ROI once confidence is back.
            self.roi_expand_px = max(0, self.roi_expand_px - 20)
            return

        if self.last_stall_point is None:
            self.last_stall_point = (float(point[0]), float(point[1]))
            self.last_stall_timestamp = timestamp
            return

        dist = np.hypot(point[0] - self.last_stall_point[0], point[1] - self.last_stall_point[1])
        if dist > self.stall_deadzone_px:
            self.last_stall_point = (float(point[0]), float(point[1]))
            self.last_stall_timestamp = timestamp
            return

        if self.last_stall_timestamp is not None and (timestamp - self.last_stall_timestamp) >= self.low_conf_stall_sec_threshold:
            self.roi_expand_px = min(self.max_roi_expand_px, self.roi_expand_px + self.roi_expand_step_px)
            self.state_reason = f"stall_expand_{self.roi_expand_px}"
            self.last_stall_timestamp = timestamp

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
        """
        Построить приближенный LAB-диапазон из HSV-диапазона.
        Дополнительные поля по A/B расширяем с запасом для теней/компрессии.
        """
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
        
    def _get_center(self, bbox: Tuple[int, int, int, int]) -> Tuple[int, int]:
        """Получить геометрический центр bbox (для внутренних расчетов)."""
        x, y, w, h = bbox
        return (x + w // 2, y + h // 2)
    
    def _get_tracking_point(self, bbox: Tuple[int, int, int, int]) -> Tuple[int, int]:
        """
        Получить стабильную точку отслеживания для траектории.
        Точка находится ЧУТЬ НИЖЕ центра нижней грани bbox (основание стрелки).
        Это более стабильная точка, так как верхушка стрелки может мигать/двигаться.
        """
        x, y, w, h = bbox
        # Центр по X, чуть ниже низа bbox по Y
        center_x = x + w // 2
        bottom_y = y + h + 3  # +3px ниже нижней грани
        return (center_x, bottom_y)
    
    def _smooth_position(self, new_position: Tuple[int, int]) -> Tuple[int, int]:
        """
        Сгладить позицию используя медианный фильтр по последним N точкам.
        Удаляет выбросы (резкие скачки) и сглаживает траекторию.
        
        Args:
            new_position: Новая детектированная позиция (x, y)
            
        Returns:
            Сглаженная позиция (x, y)
        """
        # Добавляем новую позицию в буфер
        self.detection_buffer.append(new_position)
        
        # Ограничиваем размер буфера
        if len(self.detection_buffer) > self.smoothing_window:
            self.detection_buffer.pop(0)
        
        # Если буфер еще не заполнен - возвращаем текущую позицию
        if len(self.detection_buffer) < 3:  # Минимум 3 точки для анализа
            return new_position
        
        # Вычисляем медиану для X и Y отдельно
        x_values = [pos[0] for pos in self.detection_buffer]
        y_values = [pos[1] for pos in self.detection_buffer]
        
        median_x = int(np.median(x_values))
        median_y = int(np.median(y_values))
        
        # Проверяем, является ли текущая точка выбросом
        distance_to_median = np.sqrt((new_position[0] - median_x)**2 + 
                                     (new_position[1] - median_y)**2)
        
        # Порог для определения выброса (настраиваемый)
        outlier_threshold = self.roi_size * self.outlier_threshold_ratio
        
        if distance_to_median > outlier_threshold:
            # Suppress noisy logs for frequent outliers in long runs.
            return (median_x, median_y)
        
        # Взвешенное среднее - больший вес последним точкам
        weights = np.linspace(0.5, 1.0, len(self.detection_buffer))  # От 0.5 до 1.0
        weights = weights / weights.sum()  # Нормализация
        
        weighted_x = int(np.average(x_values, weights=weights))
        weighted_y = int(np.average(y_values, weights=weights))
        
        return (weighted_x, weighted_y)
    
    def _save_averaged_point(self, timestamp: float) -> None:
        """
        Сохранить усредненную точку за прошедшую секунду.
        Вызывается когда текущая секунда меняется.
        """
        if not self.second_buffer:
            return
        
        # Вычисляем среднюю позицию за секунду
        x_values = [pos[0] for pos in self.second_buffer]
        y_values = [pos[1] for pos in self.second_buffer]
        
        avg_x = int(np.mean(x_values))
        avg_y = int(np.mean(y_values))
        
        # Средняя временная метка
        avg_timestamp = np.mean([pos[2] for pos in self.second_buffer])
        
        # Средняя confidence
        avg_confidence = 1.0 if len(self.second_buffer) > 6 else 0.5
        
        # Добавляем усредненную точку в траекторию
        point = TrajectoryPoint(
            x=avg_x,
            y=avg_y,
            timestamp=avg_timestamp,
            confidence=avg_confidence
        )
        self.trajectory.append(point)
        
        logger.debug(f"📍 Усредненная точка за секунду: ({avg_x}, {avg_y}), точек: {len(self.second_buffer)}")
        
        # Очищаем буфер секунды
        self.second_buffer.clear()
    
    def _create_color_mask(self, frame: np.ndarray) -> np.ndarray:
        """Создать маску для цвета команды (HSV + LAB)."""
        hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask_hsv = cv2.inRange(hsv_frame, self.lower_hsv, self.upper_hsv)

        lab_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        mask_lab = cv2.inRange(lab_frame, self.lower_lab, self.upper_lab)

        # Совместная проверка HSV и LAB. Если слишком мало пикселей,
        # мягко откатываемся к HSV-only, чтобы не терять трек полностью.
        mask = cv2.bitwise_and(mask_hsv, mask_lab)
        self.last_mask_mode = "hsv+lab"
        if cv2.countNonZero(mask) < 8:
            mask = mask_hsv
            self.last_mask_mode = "hsv_only_fallback"
            self.mask_too_sparse_count += 1
        
        # Морфологические операции
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, 
                                          (self.morph_kernel_size, self.morph_kernel_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.medianBlur(mask, 3)
        
        return mask
    
    def _extract_roi(self, frame: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int]]:
        """Извлечь ROI вокруг текущей позиции трекинга."""
        # Используем tracking_position если доступна, иначе центр bbox
        if self.tracking_position:
            center_x, center_y = self.tracking_position
        else:
            center_x, center_y = self._get_center(self.last_bbox)
        
        # Динамический размер ROI с нижней границей по оценке размера плашки.
        effective_roi_size = self.get_effective_roi_size()
        
        # Вычисление границ ROI
        roi_x = max(0, center_x - effective_roi_size // 2)
        roi_y = max(0, center_y - effective_roi_size // 2)
        roi_x_end = min(frame.shape[1], roi_x + effective_roi_size)
        roi_y_end = min(frame.shape[0], roi_y + effective_roi_size)
        
        # ОГРАНИЧЕНИЕ: ROI не должен выходить за границы MAP_ROI
        if self.map_roi:
            map_x, map_y, map_w, map_h = self.map_roi
            map_x_end = map_x + map_w
            map_y_end = map_y + map_h
            
            # Ограничиваем ROI областью карты
            roi_x = max(roi_x, map_x)
            roi_y = max(roi_y, map_y)
            roi_x_end = min(roi_x_end, map_x_end)
            roi_y_end = min(roi_y_end, map_y_end)
            
            # Проверка: если ROI полностью вне карты, возвращаем пустой массив
            if roi_x >= roi_x_end or roi_y >= roi_y_end:
                logger.warning(f"⚠️ ROI полностью вне области карты! Center: ({center_x}, {center_y})")
                return np.array([]), (roi_x, roi_y)
        
        # Извлечение ROI
        roi_frame = frame[roi_y:roi_y_end, roi_x:roi_x_end]
        
        return roi_frame, (roi_x, roi_y)
    
    def _find_arrow_in_roi(
        self,
        roi_frame: np.ndarray,
        preferred_center: Optional[Tuple[float, float]] = None,
        prev_bbox_local: Optional[Tuple[int, int, int, int]] = None,
        roi_origin: Tuple[int, int] = (0, 0),
    ) -> Optional[Tuple[int, int, int, int]]:
        """Найти цветную плашку команды в ROI."""
        if roi_frame.size == 0:
            return None
        
        mask = self._create_color_mask(roi_frame)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            self.state_reason = "mask_too_sparse"
            return None
        
        # Фильтрация под плашку команды (крупнее и более вытянутая, чем стрелка).
        search_max_area = max(self.max_area * 40, self.max_area)
        self.last_zone_rejected_candidates = 0
        valid_contours = []
        relaxed_contours = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if self.min_area <= area <= search_max_area:
                x, y, w, h = cv2.boundingRect(contour)

                # Для плашки ожидаем прямоугольник, чаще горизонтально вытянутый.
                aspect_ratio = w / h if h > 0 else 0
                fill_ratio = area / max(1.0, float(w * h))
                if 0.5 <= aspect_ratio <= 12.0 and fill_ratio >= 0.15:
                    relaxed_contours.append((x, y, w, h, area))
                if 0.7 <= aspect_ratio <= 10.0 and fill_ratio >= 0.22:
                    gx = roi_origin[0] + x
                    gy = roi_origin[1] + y
                    tp_x = gx + w // 2
                    tp_y = gy + h + 3
                    if self._is_forbidden_tracking_point(tp_x, tp_y):
                        self.last_zone_rejected_candidates += 1
                        self.zone_gate_reject_count += 1
                        continue
                    valid_contours.append((x, y, w, h, area))
        
        if not valid_contours:
            # fallback: при сложной сцене ослабляем shape thresholds, чтобы не терять
            # плашку полностью, но сохраняем zone-gating.
            if relaxed_contours:
                for x, y, w, h, area in relaxed_contours:
                    gx = roi_origin[0] + x
                    gy = roi_origin[1] + y
                    tp_x = gx + w // 2
                    tp_y = gy + h + 3
                    if self._is_forbidden_tracking_point(tp_x, tp_y):
                        self.last_zone_rejected_candidates += 1
                        self.zone_gate_reject_count += 1
                        continue
                    valid_contours.append((x, y, w, h, area))

            if not valid_contours:
                self.shape_reject_count += 1
                self.state_reason = "shape_reject"
                if self.last_zone_rejected_candidates > 0:
                    self.state_reason = f"zone_gate_{self.last_zone_rejected_candidates}"
                elif self.last_mask_mode == "hsv_only_fallback":
                    self.state_reason = "mask_too_sparse"
                return None
            self.state_reason = "shape_relaxed_fallback"
        
        # Выбор САМОЙ КРУПНОЙ области. Чтобы убрать "перепрыгивания" между близкими
        # сегментами одинакового размера, берем кандидатов рядом с max(area) и
        # выбираем ближайший к центру ROI.
        roi_center_x = roi_frame.shape[1] // 2
        roi_center_y = roi_frame.shape[0] // 2
        target_x, target_y = preferred_center if preferred_center is not None else (roi_center_x, roi_center_y)

        max_candidate_area = max(item[4] for item in valid_contours)
        area_threshold = max_candidate_area * 0.90
        finalists = [item for item in valid_contours if item[4] >= area_threshold]

        # Жесткая ассоциация с предыдущим bbox и правой гранью.
        associated: list[Tuple[int, int, int, int, float, float, float]] = []
        if prev_bbox_local is not None:
            px, py, pw, ph = prev_bbox_local
            for x, y, w, h, area in finalists:
                iou = self._bbox_iou((x, y, w, h), (px, py, pw, ph))
                rx = float(x + w)
                right_delta = abs(rx - target_x)
                if iou >= self.identity_iou_gate or right_delta <= self.identity_right_gate_px:
                    associated.append((x, y, w, h, area, iou, right_delta))

        if associated:
            # Сначала максимум IoU, затем ближе к ожидаемому центру.
            best_bbox = associated[0][:4]
            best_score = -1e9
            for x, y, w, h, area, iou, right_delta in associated:
                cx = x + w // 2
                cy = y + h // 2
                rx = x + w
                dist = np.sqrt((cx - target_x) ** 2 + (cy - target_y) ** 2)
                area_score = area / max(1e-6, max_candidate_area)
                right_align = 1.0 - min(1.0, right_delta / max(1.0, float(roi_frame.shape[1])))
                score = iou * 2.2 + area_score * 0.65 + right_align * 1.0 - (dist / max(1.0, float(roi_frame.shape[0]))) * 0.35
                if score > best_score:
                    best_score = score
                    best_bbox = (x, y, w, h)
            return best_bbox

        if self.selection_strategy == "rightmost":
            # В плотных сценах одной команды выбираем самый правый объект.
            # Чтобы уменьшить дрожание между соседними правыми контурами,
            # берем кандидатов в окне 6px от самого правого и стабилизируем по Y/дистанции.
            max_right_x = max((x + w) for x, y, w, h, _area in finalists)
            right_band = [item for item in finalists if (item[0] + item[2]) >= (max_right_x - 6)]

            best_bbox = right_band[0][:4]
            best_score = float("inf")
            for x, y, w, h, _area in right_band:
                rx = x + w
                cy = y + h // 2
                # Приоритет по правой грани + стабилизация по Y/близости.
                score = abs(cy - target_y) * 2.0 + abs(rx - target_x) * 1.2
                if score < best_score:
                    best_score = score
                    best_bbox = (x, y, w, h)
        elif self.selection_strategy == "label_arrow":
            # Предпочитаем плашку, под которой есть цветной "хвост"/стрелка той же команды.
            best_bbox = finalists[0][:4]
            max_area_local = max(item[4] for item in finalists)
            max_right_local = max((x + w) for x, y, w, h, _a in finalists)
            best_score = -1e9
            for x, y, w, h, area in finalists:
                cx = x + w // 2
                cy = y + h // 2
                rx = x + w
                dist = np.sqrt((cx - target_x) ** 2 + (cy - target_y) ** 2)

                arrow_score = self._score_arrow_below(mask, x, y, w, h)
                area_score = area / max(1e-6, max_area_local)
                right_score = rx / max(1e-6, float(max_right_local))
                dist_penalty = dist / max(1.0, float(roi_frame.shape[0]))

                score = arrow_score * 2.6 + area_score * 1.0 + right_score * 1.1 - dist_penalty * 0.55
                if score > best_score:
                    best_score = score
                    best_bbox = (x, y, w, h)
        else:
            best_bbox = finalists[0][:4]
            best_distance = float('inf')
            for x, y, w, h, _area in finalists:
                contour_center_x = x + w // 2
                contour_center_y = y + h // 2
                distance = np.sqrt((contour_center_x - target_x)**2 +
                                   (contour_center_y - target_y)**2)
                if distance < best_distance:
                    best_distance = distance
                    best_bbox = (x, y, w, h)
        
        return best_bbox

    def _bbox_iou(
        self,
        a: Tuple[int, int, int, int],
        b: Tuple[int, int, int, int],
    ) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        a_x2, a_y2 = ax + aw, ay + ah
        b_x2, b_y2 = bx + bw, by + bh

        ix1 = max(ax, bx)
        iy1 = max(ay, by)
        ix2 = min(a_x2, b_x2)
        iy2 = min(a_y2, b_y2)
        iw = max(0, ix2 - ix1)
        ih = max(0, iy2 - iy1)
        inter = float(iw * ih)
        if inter <= 0:
            return 0.0
        union = float(aw * ah + bw * bh - inter)
        if union <= 1e-6:
            return 0.0
        return inter / union

    def _score_arrow_below(self, mask: np.ndarray, x: int, y: int, w: int, h: int) -> float:
        """
        Оценка наличия стрелки/хвоста под плашкой:
        чем больше плотный цветной кластер в зоне под центром плашки, тем выше score.
        """
        mh, mw = mask.shape[:2]
        rx1 = max(0, int(x + 0.2 * w))
        rx2 = min(mw, int(x + 0.8 * w))
        ry1 = min(mh, int(y + h))
        ry2 = min(mh, int(y + h + 1.8 * h))
        if rx2 <= rx1 or ry2 <= ry1:
            return 0.0

        region = mask[ry1:ry2, rx1:rx2]
        nz = cv2.countNonZero(region)
        if nz <= 0:
            return 0.0

        ratio = nz / max(1.0, float(region.size))
        contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        largest = max((cv2.contourArea(c) for c in contours), default=0.0)
        compact = largest / max(1.0, float(w * h))
        return float(ratio * 0.7 + compact * 0.3)
    
    def update(self, frame: np.ndarray, timestamp: float, record_point: bool = True) -> bool:
        """Обновить позицию стрелочки на новом кадре."""
        # Извлекаем ROI
        roi_frame, (roi_x, roi_y) = self._extract_roi(frame)
        
        # Ищем стрелочку
        preferred_center_local = (self.stable_center[0] - roi_x, self.stable_center[1] - roi_y)
        prev_bbox_local = (
            int(self.last_bbox[0] - roi_x),
            int(self.last_bbox[1] - roi_y),
            int(self.last_bbox[2]),
            int(self.last_bbox[3]),
        )
        roi_bbox = self._find_arrow_in_roi(
            roi_frame,
            preferred_center=preferred_center_local,
            prev_bbox_local=prev_bbox_local,
            roi_origin=(roi_x, roi_y),
        )
        
        if roi_bbox:
            # Нашли! Преобразуем координаты
            x, y, w, h = roi_bbox
            detected_bbox = (roi_x + x, roi_y + y, w, h)
            detected_center = self._get_center(detected_bbox)
            detected_right_x = float(detected_bbox[0] + detected_bbox[2])

            # При резком скачке в новую точку подтверждаем кандидата несколько кадров,
            # чтобы не перепрыгивать между соседними игроками той же команды.
            raw_dist = np.hypot(detected_right_x - self.stable_right_x, detected_center[1] - self.stable_center[1])
            switch_waiting = False
            if raw_dist > self.jump_switch_threshold_px:
                if self.pending_center is not None:
                    pd = np.hypot(detected_center[0] - self.pending_center[0], detected_center[1] - self.pending_center[1])
                    if pd <= 8.0:
                        self.pending_center_hits += 1
                    else:
                        self.pending_center = (float(detected_center[0]), float(detected_center[1]))
                        self.pending_center_hits = 1
                else:
                    self.pending_center = (float(detected_center[0]), float(detected_center[1]))
                    self.pending_center_hits = 1

                if self.pending_center_hits < self.pending_required_hits:
                    detected_center = (int(round(self.stable_center[0])), int(round(self.stable_center[1])))
                    detected_right_x = float(self.stable_right_x)
                    switch_waiting = True
                    self.track_state = "hold"
                    self.state_reason = f"switch_wait_{self.pending_center_hits}/{self.pending_required_hits}"
                    self.confidence = max(0.35, self.confidence * 0.92)
                else:
                    self.pending_center = None
                    self.pending_center_hits = 0
                    self.state_reason = "switch_confirmed"
            else:
                self.pending_center = None
                self.pending_center_hits = 0

            # Калибруем размер плашки по первым 10 секундам после захвата.
            if self.calibration_start_timestamp is None:
                self.calibration_start_timestamp = timestamp
            det_w, det_h = detected_bbox[2], detected_bbox[3]
            est_w, est_h = self._estimate_label_size()
            # Отбрасываем явные "карлики" относительно текущей оценки, чтобы они не ломали калибровку.
            if det_w >= max(self.min_label_w, int(est_w * 0.55)) and det_h >= max(self.min_label_h, int(est_h * 0.55)):
                self.calibration_bboxes.append(detected_bbox)
                self.calibration_max_w = max(self.calibration_max_w, det_w)
                self.calibration_max_h = max(self.calibration_max_h, det_h)

            if (not self.is_bbox_calibrated) and self.calibration_start_timestamp is not None:
                calib_elapsed = timestamp - self.calibration_start_timestamp
                enough_time = calib_elapsed >= self.calibration_duration_sec
                enough_frames = len(self.calibration_bboxes) >= self.calibration_frames_needed
                if enough_time and enough_frames:
                    fixed_w, fixed_h = self._compute_robust_label_size(self.calibration_bboxes)
                    if fixed_w / max(1.0, float(fixed_h)) < 1.3:
                        fixed_w = max(fixed_w, int(round(fixed_h * 1.8)))

                    self.fixed_bbox_size = (fixed_w, fixed_h)
                    self.fixed_tracking_offset = (0, fixed_h // 2 + 3)
                    self.is_bbox_calibrated = True
                    logger.info(
                        "✓ Размер плашки зафиксирован после %.1fs: %dx%d (наблюдений: %d)",
                        calib_elapsed,
                        fixed_w,
                        fixed_h,
                        len(self.calibration_bboxes),
                    )
            
            # Стабилизируем центр, чтобы убрать микродребезг и прыжки между пикселями.
            dcx, dcy = float(detected_center[0]), float(detected_center[1])
            scx, scy = self.stable_center
            dist = np.sqrt((dcx - scx) ** 2 + (dcy - scy) ** 2)
            if dist > self.center_deadzone_px:
                # Ограничиваем максимальный сдвиг за кадр, особенно после потери захвата.
                if dist > self.max_center_step_px:
                    scale = self.max_center_step_px / max(1e-6, dist)
                    dcx = scx + (dcx - scx) * scale
                    dcy = scy + (dcy - scy) * scale
                scx = scx * (1.0 - self.center_smoothing_alpha) + dcx * self.center_smoothing_alpha
                scy = scy * (1.0 - self.center_smoothing_alpha) + dcy * self.center_smoothing_alpha

            # До завершения калибровки используем динамический размер по накопленным наблюдениям,
            # после завершения — строго фиксированный.
            if self.is_bbox_calibrated and self.fixed_bbox_size is not None:
                fixed_w, fixed_h = self.fixed_bbox_size
            else:
                if self.calibration_bboxes:
                    fixed_w, fixed_h = self._compute_robust_label_size(self.calibration_bboxes)
                else:
                    fixed_w, fixed_h = self.default_bbox_size
                if fixed_w / max(1.0, float(fixed_h)) < 1.3:
                    fixed_w = max(fixed_w, int(round(fixed_h * 1.8)))

            # Стабилизируем именно правую грань.
            right_delta = detected_right_x - self.stable_right_x
            if abs(right_delta) > self.right_edge_deadzone_px:
                if abs(right_delta) > self.max_right_edge_step_px:
                    right_delta = np.sign(right_delta) * self.max_right_edge_step_px
                self.stable_right_x = self.stable_right_x + right_delta * self.right_edge_smoothing_alpha

            scx = self.stable_right_x - fixed_w / 2.0
            self.stable_center = (scx, scy)
            stable_center_int = (int(round(scx)), int(round(scy)))

            self.last_bbox = (
                stable_center_int[0] - fixed_w // 2,
                stable_center_int[1] - fixed_h // 2,
                fixed_w,
                fixed_h
            )

            # Точка трекинга: строго под центром фиксированного прямоугольника.
            tracking_offset_y = fixed_h // 2 + 3
            raw_tracking_point = (
                stable_center_int[0],
                stable_center_int[1] + tracking_offset_y
            )

            # Сглаживание для подавления прыжков.
            tracking_point = self._smooth_position(raw_tracking_point)
            self.tracking_position = tracking_point
            if not switch_waiting:
                self.last_detected_timestamp = timestamp
                self.track_state = "tracked"
                if self.state_reason != "switch_confirmed":
                    self.state_reason = "detected"
                self._append_confirmed_observation(timestamp, float(stable_center_int[0]), float(stable_center_int[1]), float(self.stable_right_x))
            
            # Обновляем состояние
            self.lost_frames = 0
            if not switch_waiting:
                self.confidence = 1.0

            # Team-crowd guard: begin writing trajectory only after stable 30s.
            if self.team6_mode_enabled:
                if not switch_waiting and self.confidence >= 0.75:
                    if self.stable_tracking_start_ts is None:
                        self.stable_tracking_start_ts = timestamp
                    elif (not self.recording_enabled) and (timestamp - self.stable_tracking_start_ts) >= self.recording_warmup_sec:
                        self.recording_enabled = True
                        self.state_reason = "recording_enabled_after_warmup"
                elif not self.recording_enabled:
                    self.stable_tracking_start_ts = None
            
            # Увеличиваем счетчик успешных детекций только для подтвержденного состояния.
            if not switch_waiting:
                self.consecutive_detections += 1
            else:
                self.consecutive_detections = 0
            
            # Активируем "захват" после 15 успешных детекций подряд (~0.25 секунды при 60fps)
            # Было 30, уменьшили для более быстрого захвата
            if self.consecutive_detections > 15 and not self.tracking_locked:
                self.tracking_locked = True
                locked_roi_size = int(self.roi_size * 0.3)
                logger.info(f"🔒 Объект захвачен! ROI уменьшен до {locked_roi_size}px (30%)")
            
            self._update_roi_stall_logic(timestamp, tracking_point, confident=(self.confidence >= 0.75))

            # Добавляем точку в буфер секунды только после warmup-этапа стабильности.
            if record_point and self.recording_enabled:
                self.second_buffer.append((tracking_point[0], tracking_point[1], timestamp))
            
            # Проверяем, нужно ли сохранить усредненную точку
            current_interval = int(timestamp // self.averaging_interval)  # Интервал по 2 секунды
            if self.last_saved_second is None:
                self.last_saved_second = current_interval
            
            if current_interval > self.last_saved_second:
                # Новый интервал начался - сохраняем усредненную точку за предыдущий интервал
                self._save_averaged_point(timestamp)
                self.last_saved_second = current_interval
            
            return True
        else:
            # Не нашли - используем последнюю позицию трекинга
            self.lost_frames += 1
            self.confidence = max(0.1, self.confidence - 0.05)
            
            # Сбрасываем счетчик успешных детекций
            self.consecutive_detections = 0
            
            # Если потеряли больше 20 кадров - снимаем захват, расширяем ROI
            # Было 10, увеличили для более стабильного захвата
            if self.lost_frames > 20 and self.tracking_locked:
                self.tracking_locked = False
                logger.warning(f"🔓 Захват потерян! ROI расширен до {self.roi_size}px")
            
            # predict_short: короткий прогноз движения, затем hold.
            tracking_point: Tuple[int, int]
            if self.last_detected_timestamp is not None and (timestamp - self.last_detected_timestamp) <= self.predict_seconds:
                predicted = self._predict_from_motion_model(timestamp)
                if predicted is not None:
                    pred_cx, pred_cy, pred_rx = predicted
                    scx, scy = self.stable_center
                    step = np.hypot(pred_cx - scx, pred_cy - scy)
                    if step > self.max_center_step_px:
                        k = self.max_center_step_px / max(1e-6, step)
                        pred_cx = scx + (pred_cx - scx) * k
                        pred_cy = scy + (pred_cy - scy) * k
                    self.stable_center = (pred_cx, pred_cy)
                    # стабилизируем правую грань прогнозом
                    r_step = pred_rx - self.stable_right_x
                    if abs(r_step) > self.max_right_edge_step_px:
                        r_step = np.sign(r_step) * self.max_right_edge_step_px
                    self.stable_right_x = self.stable_right_x + r_step * self.right_edge_smoothing_alpha
                    stable_center_int = (int(round(pred_cx)), int(round(pred_cy)))
                    pred_w, pred_h = self.last_bbox[2], self.last_bbox[3]
                    pred_cx_locked = self.stable_right_x - pred_w / 2.0
                    stable_center_int = (int(round(pred_cx_locked)), int(round(pred_cy)))
                    self.last_bbox = (
                        stable_center_int[0] - pred_w // 2,
                        stable_center_int[1] - pred_h // 2,
                        pred_w,
                        pred_h,
                    )
                    tracking_offset_y = self.last_bbox[3] // 2 + 3
                    tracking_point = (stable_center_int[0], stable_center_int[1] + tracking_offset_y)
                    self.tracking_position = tracking_point
                    self.track_state = "predict"
                    self.state_reason = "occlusion_predict"
                else:
                    if self.trajectory:
                        last_point = self.trajectory[-1]
                        tracking_point = (last_point.x, last_point.y)
                    elif self.tracking_position:
                        tracking_point = self.tracking_position
                    else:
                        tracking_point = self._get_tracking_point(self.last_bbox)
                    self.track_state = "hold"
                    self.state_reason = "predict_unavailable"
            else:
                if self.trajectory:
                    last_point = self.trajectory[-1]
                    tracking_point = (last_point.x, last_point.y)
                elif self.tracking_position:
                    tracking_point = self.tracking_position
                else:
                    tracking_point = self._get_tracking_point(self.last_bbox)
                self.track_state = "hold"
                self.state_reason = "lost_hold"
            self.tracking_position = tracking_point
            self._update_roi_stall_logic(timestamp, tracking_point, confident=False)
            
            # До подтвержденного старта записи не добавляем точки в траекторию.
            if record_point and self.recording_enabled:
                self.second_buffer.append((tracking_point[0], tracking_point[1], timestamp))
            
            # Проверяем, нужно ли сохранить усредненную точку
            current_interval = int(timestamp // self.averaging_interval)  # Интервал по 2 секунды
            if self.last_saved_second is None:
                self.last_saved_second = current_interval
            
            if current_interval > self.last_saved_second:
                # Новый интервал начался - сохраняем усредненную точку
                self._save_averaged_point(timestamp)
                self.last_saved_second = current_interval
            
            if self.lost_frames % 30 == 0:
                logger.warning(f"⚠ Стрелочка не найдена уже {self.lost_frames} кадров")
            if self.last_zone_rejected_candidates > 0:
                self.state_reason = f"zone_gate_{self.last_zone_rejected_candidates}"
            
            return True
    
    def get_trajectory(self) -> List[TrajectoryPoint]:
        """Получить траекторию."""
        return self.trajectory
    
    def finalize(self, final_timestamp: float) -> None:
        """
        Финализировать трекинг - сохранить последний буфер секунды.
        Вызывается в конце обработки видео.
        """
        if self.second_buffer:
            self._save_averaged_point(final_timestamp)
            logger.info("✓ Финальная секунда сохранена")
    
    def get_current_position(self) -> Optional[Tuple[int, int]]:
        """Получить текущую позицию."""
        if self.trajectory:
            last = self.trajectory[-1]
            return (last.x, last.y)
        return None
