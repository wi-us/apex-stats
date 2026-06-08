import argparse
import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from tqdm import tqdm


Box = Tuple[int, int, int, int, float, Dict]
RejectedBox = Tuple[int, int, int, int, str, Dict]
TeamHSV = Dict[str, Dict]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def central_roi(frame: np.ndarray, left_ignore: int = 420, roi_size: int = 1080) -> Tuple[np.ndarray, int, int]:
    """
    Для стандартного кадра 1920x1080:
    - игнорируем 420 px слева;
    - игнорируем 420 px справа;
    - работаем только с центральной областью 1080x1080.

    Координаты ROI:
    x = 420..1500
    y = 0..1080
    """
    h, w = frame.shape[:2]

    if w < roi_size or h < roi_size:
        raise ValueError(f"Frame is too small: {w}x{h}, need at least {roi_size}x{roi_size}")

    if w >= left_ignore * 2 + roi_size:
        x1 = left_ignore
    else:
        # fallback для нестандартных разрешений
        x1 = max(0, (w - roi_size) // 2)

    y1 = max(0, (h - roi_size) // 2)

    roi = frame[y1:y1 + roi_size, x1:x1 + roi_size]
    return roi, x1, y1


def largest_true_segment(mask_1d: np.ndarray, min_len: int = 1) -> Optional[Tuple[int, int]]:
    """
    Возвращает самый длинный непрерывный True-сегмент [start, end).
    """
    best = None
    best_len = 0
    start = None

    for i, value in enumerate(mask_1d):
        if value and start is None:
            start = i
        elif not value and start is not None:
            length = i - start
            if length >= min_len and length > best_len:
                best = (start, i)
                best_len = length
            start = None

    if start is not None:
        length = len(mask_1d) - start
        if length >= min_len and length > best_len:
            best = (start, len(mask_1d))

    return best


def clamp_box(x: int, y: int, w: int, h: int, img_w: int, img_h: int) -> Tuple[int, int, int, int]:
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(img_w, x + w)
    y2 = min(img_h, y + h)

    return x1, y1, max(0, x2 - x1), max(0, y2 - y1)


def normalize_range(value, max_value: int, h_range: int = 179, is_hue: bool = False) -> Tuple[int, int]:
    """
    Принимает число или диапазон [min, max] и возвращает нормализованный диапазон.
    Для Hue поддерживает вход 0..179 / 0..180 / 0..360.
    """
    if isinstance(value, list):
        lo, hi = int(value[0]), int(value[1])
    else:
        lo = hi = int(value)

    if is_hue and h_range == 360:
        lo = int(round(lo / 2))
        hi = int(round(hi / 2))

    if is_hue:
        # В OpenCV Hue фактически 0..179. Значение 180 считаем верхней границей 179.
        lo = max(0, min(179, lo))
        hi = max(0, min(179, hi))
    else:
        lo = max(0, min(max_value, lo))
        hi = max(0, min(max_value, hi))

    if lo > hi:
        lo, hi = hi, lo

    return lo, hi


def load_team_hsv(path: Optional[str]) -> Optional[TeamHSV]:
    """
    Загружает HSV-цвета команд из JSON.

    Поддерживает оба формата:

    1) Твой текущий формат:
    {
      "frame": "storm-point",
      "teams": [
        {
          "slot": 1,
          "id": "t-tsm",
          "name": "Team 1",
          "hex": "#11758e",
          "h": [91, 101],
          "s": [195, 255],
          "v": [92, 192]
        }
      ]
    }

    2) Простой формат:
    {
      "_meta": {"h_range": 179},
      "Team Liquid": {"h": 8, "s": 170, "v": 180}
    }
    """
    if not path:
        return None

    json_path = Path(path)
    if not json_path.exists():
        raise FileNotFoundError(f"HSV JSON not found: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("_meta", {}) if isinstance(data, dict) else {}
    h_range = int(meta.get("h_range", 179))

    result: TeamHSV = {}

    # Формат с массивом teams — как в твоём файле.
    if isinstance(data, dict) and isinstance(data.get("teams"), list):
        for item in data["teams"]:
            name = str(item.get("name") or item.get("id") or f"slot_{item.get('slot', len(result) + 1)}")
            key = f"slot_{int(item.get('slot', len(result) + 1)):02d}_{name}"

            h_min, h_max = normalize_range(item["h"], 179, h_range=h_range, is_hue=True)
            s_min, s_max = normalize_range(item["s"], 255)
            v_min, v_max = normalize_range(item["v"], 255)

            result[key] = {
                "slot": int(item.get("slot", len(result) + 1)),
                "id": item.get("id"),
                "name": name,
                "hex": item.get("hex"),
                "h_min": h_min,
                "h_max": h_max,
                "s_min": s_min,
                "s_max": s_max,
                "v_min": v_min,
                "v_max": v_max,
            }

        return result

    # Простой dict-формат.
    for name, value in data.items():
        if name == "_meta":
            continue
        if not isinstance(value, dict):
            continue

        h_min, h_max = normalize_range(value["h"], 179, h_range=h_range, is_hue=True)
        s_min, s_max = normalize_range(value["s"], 255)
        v_min, v_max = normalize_range(value["v"], 255)

        result[name] = {
            "slot": value.get("slot"),
            "id": value.get("id"),
            "name": name,
            "hex": value.get("hex"),
            "h_min": h_min,
            "h_max": h_max,
            "s_min": s_min,
            "s_max": s_max,
            "v_min": v_min,
            "v_max": v_max,
        }

    return result


def hsv_range_mask(
    hsv: np.ndarray,
    h_min: int,
    h_max: int,
    s_min: int,
    s_max: int,
    v_min: int,
    v_max: int,
    h_pad: int = 0,
    s_pad: int = 0,
    v_pad: int = 0,
    min_s: int = 0,
    min_v: int = 0,
) -> np.ndarray:
    """
    Маска по диапазонам HSV.
    Hue в OpenCV: 0..179. Диапазон может быть расширен pad-ами.
    """
    h = hsv[:, :, 0].astype(np.int16)
    s = hsv[:, :, 1].astype(np.int16)
    v = hsv[:, :, 2].astype(np.int16)

    h1 = max(0, int(h_min) - int(h_pad))
    h2 = min(179, int(h_max) + int(h_pad))
    s1 = max(int(min_s), int(s_min) - int(s_pad))
    s2 = min(255, int(s_max) + int(s_pad))
    v1 = max(int(min_v), int(v_min) - int(v_pad))
    v2 = min(255, int(v_max) + int(v_pad))

    mask = (h >= h1) & (h <= h2) & (s >= s1) & (s <= s2) & (v >= v1) & (v <= v2)
    return (mask.astype(np.uint8) * 255)


def build_color_mask(
    hsv: np.ndarray,
    team_hsv: Optional[TeamHSV],
    h_tol: int = 8,
    s_tol: int = 85,
    v_tol: int = 95,
    min_s: int = 35,
    min_v: int = 35,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Union-маска по всем цветам. Используется в основном для debug_masks.
    Для самой детекции лучше работать не с union-маской, а с отдельной маской каждой команды,
    иначе близкие/наложенные плашки разных цветов начинают склеиваться.
    """
    if not team_hsv:
        lower = np.array([0, 45, 45], dtype=np.uint8)
        upper = np.array([179, 255, 255], dtype=np.uint8)
        raw_mask = cv2.inRange(hsv, lower, upper)
        team_id_map = np.zeros(hsv.shape[:2], dtype=np.uint8)
        return raw_mask, team_id_map

    raw_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    team_id_map = np.zeros(hsv.shape[:2], dtype=np.uint8)

    for idx, (team_name, color) in enumerate(team_hsv.items(), start=1):
        one_mask = hsv_range_mask(
            hsv,
            h_min=color["h_min"],
            h_max=color["h_max"],
            s_min=color["s_min"],
            s_max=color["s_max"],
            v_min=color["v_min"],
            v_max=color["v_max"],
            h_pad=h_tol,
            s_pad=s_tol,
            v_pad=v_tol,
            min_s=min_s,
            min_v=min_v,
        )

        raw_mask = cv2.bitwise_or(raw_mask, one_mask)
        team_id_map[one_mask > 0] = min(idx, 255)

    return raw_mask, team_id_map


def build_single_team_mask(
    hsv: np.ndarray,
    color: Dict,
    h_tol: int,
    s_tol: int,
    v_tol: int,
    min_s: int,
    min_v: int,
) -> np.ndarray:
    return hsv_range_mask(
        hsv,
        h_min=color["h_min"],
        h_max=color["h_max"],
        s_min=color["s_min"],
        s_max=color["s_max"],
        v_min=color["v_min"],
        v_max=color["v_max"],
        h_pad=h_tol,
        s_pad=s_tol,
        v_pad=v_tol,
        min_s=min_s,
        min_v=min_v,
    )


def process_team_mask(raw_mask: np.ndarray) -> np.ndarray:
    """
    Обработка маски одной конкретной команды.
    Здесь специально почти нет вертикального расширения, чтобы не клеить плашку со стрелкой.
    """
    open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    proc_mask = cv2.morphologyEx(raw_mask, cv2.MORPH_OPEN, open_kernel, iterations=1)

    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 1))
    proc_mask = cv2.morphologyEx(proc_mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)

    dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    proc_mask = cv2.dilate(proc_mask, dilate_kernel, iterations=1)

    return proc_mask


def refine_box_to_label_band(
    color_mask: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    min_height: int,
    max_height: int,
    row_density_threshold: float = 0.30,
    col_density_threshold: float = 0.18,
    pad_x: int = 3,
    pad_y: int = 2,
) -> Optional[Tuple[int, int, int, int]]:
    """
    Главная правка против проблемы "плашка + стрелка".

    Если контур захватил цветную плашку вместе со стрелкой игрока,
    мы смотрим на плотность цветных пикселей по строкам.

    У настоящей плашки строки заполнены цветом заметно плотнее.
    У стрелки строки заполнены только частично.
    """
    img_h, img_w = color_mask.shape[:2]
    x, y, w, h = clamp_box(x, y, w, h, img_w, img_h)

    if w <= 0 or h <= 0:
        return None

    sub = color_mask[y:y + h, x:x + w]
    binary = sub > 0

    if binary.size == 0:
        return None

    row_density = binary.mean(axis=1)

    dynamic_row_thr = min(row_density_threshold, max(0.16, float(row_density.max()) * 0.50))
    good_rows = row_density >= dynamic_row_thr

    good_rows_u8 = good_rows.astype(np.uint8).reshape(-1, 1) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3))
    good_rows_u8 = cv2.morphologyEx(good_rows_u8, cv2.MORPH_CLOSE, kernel, iterations=1)
    good_rows = (good_rows_u8.reshape(-1) > 0)

    segments = []
    start = None

    for i, value in enumerate(good_rows):
        if value and start is None:
            start = i
        elif not value and start is not None:
            segments.append((start, i))
            start = None

    if start is not None:
        segments.append((start, len(good_rows)))

    if not segments:
        return None

    best_segment = None
    best_score = -1.0

    for a, b in segments:
        seg_h = b - a
        if seg_h < max(4, min_height // 2):
            continue

        avg_density = float(row_density[a:b].mean())
        height_penalty = 1.0
        if seg_h > max_height:
            height_penalty = max_height / max(seg_h, 1)

        score = avg_density * min(seg_h, max_height) * height_penalty

        if score > best_score:
            best_score = score
            best_segment = (a, b)

    if best_segment is None:
        return None

    yy1, yy2 = best_segment
    yy1 = max(0, yy1 - pad_y)
    yy2 = min(h, yy2 + pad_y)

    refined_h = yy2 - yy1
    if refined_h < min_height or refined_h > max_height:
        return None

    band = binary[yy1:yy2, :]
    col_density = band.mean(axis=0)

    dynamic_col_thr = min(col_density_threshold, max(0.08, float(col_density.max()) * 0.30))
    good_cols = col_density >= dynamic_col_thr

    # Сильно закрываем горизонтальные разрывы от белого текста.
    good_cols_u8 = good_cols.astype(np.uint8).reshape(1, -1) * 255
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 1))
    good_cols_u8 = cv2.morphologyEx(good_cols_u8, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    good_cols = (good_cols_u8.reshape(-1) > 0)

    col_segment = largest_true_segment(good_cols, min_len=max(6, min(w // 5, 20)))

    if col_segment is None:
        return None

    xx1, xx2 = col_segment
    xx1 = max(0, xx1 - pad_x)
    xx2 = min(w, xx2 + pad_x)

    refined_w = xx2 - xx1
    if refined_w <= 0:
        return None

    return clamp_box(x + xx1, y + yy1, refined_w, refined_h, img_w, img_h)


def expand_box_to_mask_segment(
    color_mask: np.ndarray,
    box: Tuple[int, int, int, int],
    max_width: int,
    min_height: int,
    max_height: int,
    target_plate_height: int = 0,
    plate_height_tolerance: int = 8,
    pad_x: int = 4,
    pad_y: int = 1,
    max_expand_x: int = 48,
) -> Optional[Tuple[int, int, int, int]]:
    """
    Уточняет границы плашки после первичного refine.

    ВАЖНОЕ отличие от прошлой версии:
    расширение теперь ищется НЕ по всей ширине кадра, а только в локальном окне вокруг seed-бокса.
    Иначе похожие цвета карты/зоны могут давать огромные пустые горизонтальные bbox.
    """
    img_h, img_w = color_mask.shape[:2]
    x, y, w, h = box
    x, y, w, h = clamp_box(x, y, w, h, img_w, img_h)

    if w <= 0 or h <= 0:
        return None

    aspect = w / max(h, 1)

    # Для обычных горизонтальных плашек высота почти фиксированная.
    # Квадратные/короткие плашки типа S2/DKK не трогаем слишком агрессивно.
    if target_plate_height > 0 and aspect >= 1.35:
        if abs(h - target_plate_height) <= plate_height_tolerance or h < target_plate_height:
            center_y = y + h // 2
            new_h = max(min_height, min(max_height, int(target_plate_height)))
            y = center_y - new_h // 2
            h = new_h
            x, y, w, h = clamp_box(x, y, w, h, img_w, img_h)

    # Локальное окно расширения. Это главный фикс против ложных длинных боксов по пустой карте.
    local_x1 = max(0, x - max_expand_x)
    local_x2 = min(img_w, x + w + max_expand_x)

    band_y1 = max(0, y - 2)
    band_y2 = min(img_h, y + h + 2)
    band = color_mask[band_y1:band_y2, local_x1:local_x2] > 0

    if band.size == 0:
        return None

    col_density = band.mean(axis=0)
    good_cols = col_density >= max(0.025, min(0.12, float(col_density.max()) * 0.22))

    # Закрываем небольшие разрывы от белого текста/компрессии, но не склеиваем далёкие объекты.
    close_w = min(23, max(9, w // 3))
    good_cols_u8 = good_cols.astype(np.uint8).reshape(1, -1) * 255
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_w, 1))
    good_cols_u8 = cv2.morphologyEx(good_cols_u8, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    good_cols = good_cols_u8.reshape(-1) > 0

    segments = []
    start = None
    for i, value in enumerate(good_cols):
        if value and start is None:
            start = i
        elif not value and start is not None:
            segments.append((start, i))
            start = None
    if start is not None:
        segments.append((start, len(good_cols)))

    if not segments:
        return None

    # Координаты текущего бокса в локальной системе.
    current_x1 = x - local_x1
    current_x2 = x + w - local_x1

    best = None
    best_score = -1

    for a, b in segments:
        seg_w = b - a
        if seg_w < 8:
            continue

        inter = max(0, min(current_x2, b) - max(current_x1, a))

        # Сегмент должен пересекаться с seed-боксом, иначе это соседняя плашка/карта.
        if inter <= 0:
            continue

        # Штрафуем чрезмерное расширение относительно seed.
        width_penalty = 1.0
        if seg_w > max(w * 2.8, 90):
            width_penalty = max(w * 2.8, 90) / max(seg_w, 1)

        score = (inter * 3 + min(seg_w, max_width)) * width_penalty
        if score > best_score:
            best_score = score
            best = (a, b)

    if best is None:
        return None

    nx1, nx2 = best
    nx1 = max(0, nx1 - pad_x)
    nx2 = min(local_x2 - local_x1, nx2 + pad_x)

    global_x1 = local_x1 + nx1
    global_x2 = local_x1 + nx2

    nw = global_x2 - global_x1
    if nw <= 0 or nw > max_width:
        return None

    y = max(0, y - pad_y)
    h = min(max_height, h + pad_y * 2)
    x, y, w, h = clamp_box(global_x1, y, nw, h, img_w, img_h)

    if h < min_height or h > max_height:
        return None

    return x, y, w, h


def nms_boxes(boxes: List[Box], iou_threshold: float = 0.25) -> List[Box]:
    """
    Non-Maximum Suppression для удаления дублей.
    По умолчанию лучше применять внутри одного team_slot, а не глобально по всем командам,
    потому что плашки разных команд могут визуально перекрываться.
    """
    if not boxes:
        return []

    boxes_np = np.array(
        [[x, y, x + w, y + h, score] for x, y, w, h, score, _ in boxes],
        dtype=np.float32,
    )

    x1 = boxes_np[:, 0]
    y1 = boxes_np[:, 1]
    x2 = boxes_np[:, 2]
    y2 = boxes_np[:, 3]
    scores = boxes_np[:, 4]

    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]

    keep_indices = []

    while order.size > 0:
        i = order[0]
        keep_indices.append(i)

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        inter_w = np.maximum(0, xx2 - xx1)
        inter_h = np.maximum(0, yy2 - yy1)
        inter = inter_w * inter_h

        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]

    return [boxes[int(i)] for i in keep_indices]


def nms_boxes_by_slot(boxes: List[Box], iou_threshold: float = 0.25) -> List[Box]:
    """
    Удаляет дубли только внутри одного slot/team.
    """
    grouped: Dict[str, List[Box]] = {}

    for box in boxes:
        features = box[5]
        key = str(features.get("dominant_team_id") or features.get("slot") or features.get("team_key") or "unknown")
        grouped.setdefault(key, []).append(box)

    result: List[Box] = []
    for group_boxes in grouped.values():
        result.extend(nms_boxes(group_boxes, iou_threshold=iou_threshold))

    return result


def box_iou_xywh(a: Box, b: Box) -> float:
    ax, ay, aw, ah, _, _ = a
    bx, by, bw, bh, _, _ = b

    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    ix1 = max(ax, bx)
    iy1 = max(ay, by)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih

    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def box_ioa_xywh(a: Box, b: Box) -> float:
    """
    Intersection over area of the smaller box.
    Полезно для дублей, когда один bbox чуть шире второго.
    """
    ax, ay, aw, ah, _, _ = a
    bx, by, bw, bh, _, _ = b

    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    ix1 = max(ax, bx)
    iy1 = max(ay, by)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih

    smaller = min(aw * ah, bw * bh)
    if smaller <= 0:
        return 0.0
    return inter / smaller


def remove_cross_team_duplicates(
    boxes: List[Box],
    iou_threshold: float = 0.72,
    ioa_threshold: float = 0.86,
    center_distance_threshold: int = 8,
) -> List[Box]:
    """
    Мягкая глобальная дедупликация.

    Мы НЕ хотим обычную глобальную NMS, потому что разные команды реально могут накладываться.
    Но если два bbox почти совпадают геометрически, это почти всегда дубль от соседних HSV-диапазонов.
    """
    if not boxes:
        return []

    sorted_boxes = sorted(boxes, key=lambda b: b[4], reverse=True)
    kept: List[Box] = []

    for box in sorted_boxes:
        x, y, w, h, score, features = box
        cx = x + w / 2
        cy = y + h / 2

        duplicate = False
        for kept_box in kept:
            kx, ky, kw, kh, kscore, kfeatures = kept_box
            kcx = kx + kw / 2
            kcy = ky + kh / 2

            center_close = abs(cx - kcx) <= center_distance_threshold and abs(cy - kcy) <= center_distance_threshold
            same_size = abs(w - kw) <= max(12, min(w, kw) * 0.25) and abs(h - kh) <= max(6, min(h, kh) * 0.35)

            if box_iou_xywh(box, kept_box) >= iou_threshold:
                duplicate = True
                break

            if box_ioa_xywh(box, kept_box) >= ioa_threshold and (center_close or same_size):
                duplicate = True
                break

        if not duplicate:
            kept.append(box)

    return kept


def plate_quality_reject_reason(features: Dict, w: int, h: int) -> Optional[str]:
    """
    Дополнительный фильтр качества против захвата карты.

    Ложные bbox по карте часто длинные, но внутри мало белого текста и/или слабая цветная заливка.
    Настоящая плашка — это плотный цветной прямоугольник с заметным белым текстом.
    """
    aspect = float(w / max(h, 1))
    white_ratio = float(features.get("white_ratio", 0.0))
    color_fill = float(features.get("color_fill_ratio", 0.0))

    # Для длинных плашек требования жёстче: там должно быть много текста и цветной подложки.
    if aspect >= 4.5 and white_ratio < 0.018:
        return "long_low_text"
    if aspect >= 3.0 and white_ratio < 0.014:
        return "wide_low_text"
    if aspect >= 2.0 and white_ratio < 0.010:
        return "medium_low_text"

    # Длинный bbox с плохой цветной заливкой — почти всегда кусок карты.
    if aspect >= 2.2 and color_fill < 0.22:
        return "wide_low_fill"
    if aspect >= 3.5 and color_fill < 0.28:
        return "long_low_fill"

    # Слишком большая площадь при маленькой доле текста — подозрительно.
    if w * h > 5200 and white_ratio < 0.016:
        return "large_low_text"

    return None


def compute_box_features(
    roi: np.ndarray,
    color_mask: np.ndarray,
    box: Tuple[int, int, int, int],
    team_id_map: Optional[np.ndarray] = None,
    team_names: Optional[List[str]] = None,
) -> Dict:
    x, y, w, h = box
    crop = roi[y:y + h, x:x + w]
    mask_crop = color_mask[y:y + h, x:x + w]

    hsv_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    color_fill_ratio = float(np.mean(mask_crop > 0))

    # Ищем белый/светлый текст. Белые буквы обычно имеют высокую яркость и низкую насыщенность.
    white_text_mask = (gray > 135) & (hsv_crop[:, :, 1] < 165)
    white_ratio = float(np.mean(white_text_mask))

    # Цвет плашки считаем только по насыщенным пикселям, чтобы белый текст не портил средний цвет.
    colored_pixels = crop[mask_crop > 0]
    if len(colored_pixels) > 0:
        mean_bgr = colored_pixels.mean(axis=0).astype(int).tolist()
        mean_hsv = hsv_crop[mask_crop > 0].mean(axis=0).astype(int).tolist()
    else:
        mean_bgr = crop.reshape(-1, 3).mean(axis=0).astype(int).tolist()
        mean_hsv = hsv_crop.reshape(-1, 3).mean(axis=0).astype(int).tolist()

    dominant_team = None
    dominant_team_id = None
    dominant_team_ratio = 0.0

    if team_id_map is not None:
        team_crop = team_id_map[y:y + h, x:x + w]
        ids, counts = np.unique(team_crop[team_crop > 0], return_counts=True)
        if len(ids) > 0:
            best_idx = int(np.argmax(counts))
            dominant_team_id = int(ids[best_idx])
            dominant_team_ratio = float(counts[best_idx] / max(np.sum(team_crop > 0), 1))
            if team_names and 1 <= dominant_team_id <= len(team_names):
                dominant_team = team_names[dominant_team_id - 1]

    aspect = float(w / max(h, 1))

    return {
        "aspect": aspect,
        "color_fill_ratio": color_fill_ratio,
        "white_ratio": white_ratio,
        "mean_bgr": mean_bgr,
        "mean_hsv": mean_hsv,
        "dominant_team": dominant_team,
        "dominant_team_id": dominant_team_id,
        "dominant_team_ratio": dominant_team_ratio,
    }


def detect_colored_plates_opencv(
    roi: np.ndarray,
    min_width: int = 24,
    max_width: int = 300,
    min_height: int = 12,
    max_height: int = 42,
    min_aspect: float = 0.75,
    max_aspect: float = 9.0,
    min_color_fill: float = 0.18,
    min_white_ratio: float = 0.006,
    nms_iou: float = 0.22,
    team_hsv: Optional[TeamHSV] = None,
    h_tol: int = 10,
    s_tol: int = 95,
    v_tol: int = 110,
    hsv_min_s: int = 30,
    hsv_min_v: int = 30,
    loose_h_extra: int = 2,
    loose_s_extra: int = 25,
    loose_v_extra: int = 35,
    search_pad_x: int = 36,
    search_pad_y: int = 14,
    max_expand_x: int = 48,
    target_plate_height: int = 30,
    plate_height_tolerance: int = 9,
    ignore_bottom_px: int = 105,
) -> Tuple[List[Box], List[RejectedBox], np.ndarray]:
    """
    Черновой OpenCV-детектор плашек для создания YOLO-разметки.

    Если есть HSV-диапазоны команд, детекция идёт по каждой команде отдельно.
    Это лучше union-маски: соседние и наложенные плашки разных цветов меньше склеиваются.

    Новая логика:
    1. strict mask ищет seed-кандидаты;
    2. loose mask расширяет bbox до реальных границ плашки;
    3. нижняя UI-зона отсекается через ignore_bottom_px.
    """
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    debug_union_mask, debug_team_id_map = build_color_mask(
        hsv,
        team_hsv=team_hsv,
        h_tol=h_tol,
        s_tol=s_tol,
        v_tol=v_tol,
        min_s=hsv_min_s,
        min_v=hsv_min_v,
    )

    accepted: List[Box] = []
    rejected: List[RejectedBox] = []

    if team_hsv:
        for team_idx, (team_key, color) in enumerate(team_hsv.items(), start=1):
            # Strict mask: качественный seed, меньше мусора.
            raw_team_mask = build_single_team_mask(
                hsv,
                color=color,
                h_tol=h_tol,
                s_tol=s_tol,
                v_tol=v_tol,
                min_s=hsv_min_s,
                min_v=hsv_min_v,
            )
            proc_mask = process_team_mask(raw_team_mask)

            # Loose mask: чуть шире, чтобы восстановить реальные края плашки.
            loose_team_mask = build_single_team_mask(
                hsv,
                color=color,
                h_tol=h_tol + loose_h_extra,
                s_tol=s_tol + loose_s_extra,
                v_tol=v_tol + loose_v_extra,
                min_s=hsv_min_s,
                min_v=hsv_min_v,
            )
            loose_proc_mask = process_team_mask(loose_team_mask)

            contours, _ = cv2.findContours(proc_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            team_boxes: List[Box] = []

            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)

                if w < min_width or h < 7:
                    continue
                if w > 380 or h > 95:
                    continue

                sx, sy, sw, sh = clamp_box(
                    x - search_pad_x,
                    y - search_pad_y,
                    w + search_pad_x * 2,
                    h + search_pad_y * 2,
                    roi.shape[1],
                    roi.shape[0],
                )

                refined = refine_box_to_label_band(
                    loose_proc_mask,
                    sx,
                    sy,
                    sw,
                    sh,
                    min_height=min_height,
                    max_height=max_height,
                    row_density_threshold=0.24,
                    col_density_threshold=0.12,
                    pad_x=3,
                    pad_y=2,
                )

                if refined is None:
                    rejected.append((x, y, w, h, f"refine_failed:{team_idx}", {"team_key": team_key}))
                    continue

                expanded = expand_box_to_mask_segment(
                    loose_proc_mask,
                    refined,
                    max_width=max_width,
                    min_height=min_height,
                    max_height=max_height,
                    target_plate_height=target_plate_height,
                    plate_height_tolerance=plate_height_tolerance,
                    pad_x=4,
                    pad_y=1,
                    max_expand_x=max_expand_x,
                )

                if expanded is None:
                    rejected.append((refined[0], refined[1], refined[2], refined[3], f"expand_failed:{team_idx}", {"team_key": team_key}))
                    continue

                rx, ry, rw, rh = expanded

                # Отсекаем нижний UI: Ring closing / баннеры трансляции.
                if ignore_bottom_px > 0 and (ry + rh) > (roi.shape[0] - ignore_bottom_px):
                    rejected.append((rx, ry, rw, rh, f"bottom_ui:{team_idx}", {"team_key": team_key}))
                    continue

                one_team_id_map = np.zeros(loose_proc_mask.shape[:2], dtype=np.uint8)
                one_team_id_map[loose_proc_mask > 0] = min(team_idx, 255)

                features = compute_box_features(
                    roi,
                    loose_proc_mask,
                    expanded,
                    team_id_map=one_team_id_map,
                    team_names=[team_key],
                )

                features["team_key"] = team_key
                features["slot"] = color.get("slot")
                features["team_id"] = color.get("id")
                features["team_name"] = color.get("name")
                features["hex"] = color.get("hex")
                features["dominant_team_id"] = color.get("slot") or team_idx

                aspect = features["aspect"]
                reason = None

                if rw < min_width:
                    reason = "too_narrow"
                elif rw > max_width:
                    reason = "too_wide"
                elif rh < min_height:
                    reason = "too_low"
                elif rh > max_height:
                    reason = "too_tall"
                elif aspect < min_aspect:
                    reason = "bad_aspect_low"
                elif aspect > max_aspect:
                    reason = "bad_aspect_high"
                elif features["color_fill_ratio"] < min_color_fill:
                    reason = "low_color_fill"
                elif features["white_ratio"] < min_white_ratio:
                    reason = "no_white_text"
                else:
                    reason = plate_quality_reject_reason(features, rw, rh)

                if reason:
                    rejected.append((rx, ry, rw, rh, f"{reason}:{team_idx}", features))
                    continue

                score = (
                    features["color_fill_ratio"] * 2.0
                    + min(features["white_ratio"] * 6.0, 1.0)
                    + min(aspect / 4.0, 1.0)
                )

                team_boxes.append((rx, ry, rw, rh, float(score), features))

            # NMS только внутри одной команды, чтобы не убивать наложенные плашки разных цветов.
            accepted.extend(nms_boxes(team_boxes, iou_threshold=nms_iou))

        accepted = remove_cross_team_duplicates(accepted)
        return accepted, rejected, debug_union_mask

    # Fallback без HSV JSON: общий режим по насыщенности.
    color_mask = debug_union_mask

    open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, open_kernel, iterations=1)

    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 1))
    proc_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)

    dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    proc_mask = cv2.dilate(proc_mask, dilate_kernel, iterations=1)

    contours, _ = cv2.findContours(proc_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)

        if w < min_width or h < 8:
            continue
        if w > 380 or h > 95:
            continue

        sx, sy, sw, sh = clamp_box(
            x - search_pad_x,
            y - search_pad_y,
            w + search_pad_x * 2,
            h + search_pad_y * 2,
            roi.shape[1],
            roi.shape[0],
        )

        refined = refine_box_to_label_band(
            proc_mask,
            sx,
            sy,
            sw,
            sh,
            min_height=min_height,
            max_height=max_height,
            row_density_threshold=0.30,
            col_density_threshold=0.18,
            pad_x=3,
            pad_y=2,
        )

        if refined is None:
            rejected.append((x, y, w, h, "refine_failed", {}))
            continue

        expanded = expand_box_to_mask_segment(
            proc_mask,
            refined,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            target_plate_height=target_plate_height,
            plate_height_tolerance=plate_height_tolerance,
            pad_x=4,
            pad_y=1,
        )

        if expanded is None:
            rejected.append((refined[0], refined[1], refined[2], refined[3], "expand_failed", {}))
            continue

        rx, ry, rw, rh = expanded

        if ignore_bottom_px > 0 and (ry + rh) > (roi.shape[0] - ignore_bottom_px):
            rejected.append((rx, ry, rw, rh, "bottom_ui", {}))
            continue

        features = compute_box_features(roi, proc_mask, expanded, team_id_map=debug_team_id_map, team_names=None)
        aspect = features["aspect"]

        reason = None
        if rw < min_width:
            reason = "too_narrow"
        elif rw > max_width:
            reason = "too_wide"
        elif rh < min_height:
            reason = "too_low"
        elif rh > max_height:
            reason = "too_tall"
        elif aspect < min_aspect:
            reason = "bad_aspect_low"
        elif aspect > max_aspect:
            reason = "bad_aspect_high"
        elif features["color_fill_ratio"] < min_color_fill:
            reason = "low_color_fill"
        elif features["white_ratio"] < min_white_ratio:
            reason = "no_white_text"
        else:
            reason = plate_quality_reject_reason(features, rw, rh)

        if reason:
            rejected.append((rx, ry, rw, rh, reason, features))
            continue

        score = (
            features["color_fill_ratio"] * 2.0
            + min(features["white_ratio"] * 6.0, 1.0)
            + min(aspect / 4.0, 1.0)
        )

        accepted.append((rx, ry, rw, rh, float(score), features))

    accepted = nms_boxes(accepted, iou_threshold=nms_iou)
    accepted = remove_cross_team_duplicates(accepted)
    return accepted, rejected, proc_mask


def to_yolo_line(box: Box, img_w: int, img_h: int, class_id: int = 0) -> str:
    x, y, w, h, _, _ = box

    x_center = (x + w / 2) / img_w
    y_center = (y + h / 2) / img_h
    bw = w / img_w
    bh = h / img_h

    return f"{class_id} {x_center:.6f} {y_center:.6f} {bw:.6f} {bh:.6f}"


def draw_boxes(
    image: np.ndarray,
    accepted: List[Box],
    rejected: Optional[List[RejectedBox]] = None,
    draw_rejected: bool = False,
) -> np.ndarray:
    out = image.copy()

    for x, y, w, h, score, features in accepted:
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(
            out,
            f"{score:.2f}",
            (x, max(0, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

    if draw_rejected and rejected:
        for x, y, w, h, reason, _ in rejected:
            cv2.rectangle(out, (x, y), (x + w, y + h), (0, 0, 255), 1)
            cv2.putText(
                out,
                reason[:16],
                (x, min(out.shape[0] - 2, y + h + 12)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (0, 0, 255),
                1,
                cv2.LINE_AA,
            )

    # Счётчик в debug нужен, чтобы сразу понимать: этот кадр реально без боксов или ты открыл не ту папку.
    text = f"accepted: {len(accepted)}"
    if rejected is not None:
        text += f" | rejected: {len(rejected)}"

    cv2.rectangle(out, (10, 10), (360, 50), (0, 0, 0), -1)
    cv2.putText(
        out,
        text,
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

    return out


def save_crop(path: Path, image: np.ndarray) -> None:
    if image is None or image.size == 0:
        return
    cv2.imwrite(str(path), image)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, help="Path to source video")
    parser.add_argument("--out", default="dataset", help="Output dataset directory")
    parser.add_argument("--sample-fps", type=float, default=1.0, help="How many frames per second to sample")
    parser.add_argument("--left-ignore", type=int, default=420)
    parser.add_argument("--roi-size", type=int, default=1080)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)

    # ВАЖНО: не включай --save-empty на обычных кадрах с картой,
    # если на них видны плашки, но OpenCV их не нашёл.
    # Иначе YOLO получит ложный сигнал: "плашки видны, но это фон".
    parser.add_argument("--save-empty", action="store_true", help="Save frames without detections as negative samples")

    # Полезно для диагностики.
    parser.add_argument("--save-debug-all", action="store_true", default=True, help="Save debug image for every sampled frame")
    parser.add_argument("--no-save-debug-all", dest="save_debug_all", action="store_false")
    parser.add_argument("--save-rejected", action="store_true", help="Save rejected candidate crops for analysis")
    parser.add_argument("--draw-rejected", action="store_true", help="Draw rejected candidates in red on debug images")

    # Настройки детектора.
    parser.add_argument("--min-width", type=int, default=24)
    parser.add_argument("--max-width", type=int, default=300)
    parser.add_argument("--min-height", type=int, default=12)
    parser.add_argument("--max-height", type=int, default=42)
    parser.add_argument("--min-aspect", type=float, default=0.75)
    parser.add_argument("--max-aspect", type=float, default=9.0)
    parser.add_argument("--min-color-fill", type=float, default=0.28)
    parser.add_argument("--min-white-ratio", type=float, default=0.012)
    parser.add_argument("--nms-iou", type=float, default=0.22)

    # Улучшение границ: strict seed + loose expand.
    parser.add_argument("--ignore-bottom-px", type=int, default=105, help="Ignore detections in bottom UI area")
    parser.add_argument("--loose-h-extra", type=int, default=2, help="Extra H padding for boundary expansion")
    parser.add_argument("--loose-s-extra", type=int, default=25, help="Extra S padding for boundary expansion")
    parser.add_argument("--loose-v-extra", type=int, default=35, help="Extra V padding for boundary expansion")
    parser.add_argument("--search-pad-x", type=int, default=36, help="Horizontal search padding around strict seed")
    parser.add_argument("--search-pad-y", type=int, default=14, help="Vertical search padding around strict seed")
    parser.add_argument("--max-expand-x", type=int, default=48, help="Max local horizontal expansion after strict seed")
    parser.add_argument("--target-plate-height", type=int, default=30, help="Target height for normal horizontal plates")
    parser.add_argument("--plate-height-tolerance", type=int, default=9)

    # HSV-цвета команд. Очень желательно использовать, если они у тебя есть.
    parser.add_argument("--team-hsv-json", default=None, help="JSON with team HSV colors")
    parser.add_argument("--h-tol", type=int, default=3, help="Extra padding around JSON H ranges in OpenCV H range 0..179")
    parser.add_argument("--s-tol", type=int, default=25, help="Extra padding around JSON S ranges")
    parser.add_argument("--v-tol", type=int, default=35, help="Extra padding around JSON V ranges")
    parser.add_argument("--hsv-min-s", type=int, default=30)
    parser.add_argument("--hsv-min-v", type=int, default=30)

    args = parser.parse_args()

    random.seed(args.seed)

    video_path = Path(args.video)
    out_dir = Path(args.out)
    team_hsv = load_team_hsv(args.team_hsv_json)

    images_train = out_dir / "images" / "train"
    images_val = out_dir / "images" / "val"
    labels_train = out_dir / "labels" / "train"
    labels_val = out_dir / "labels" / "val"
    crops_dir = out_dir / "crops"
    rejected_crops_dir = out_dir / "rejected_crops"
    debug_dir = out_dir / "debug"
    masks_dir = out_dir / "debug_masks"

    required_dirs = [images_train, images_val, labels_train, labels_val, crops_dir, debug_dir, masks_dir]
    if args.save_rejected:
        required_dirs.append(rejected_crops_dir)

    for p in required_dirs:
        ensure_dir(p)

    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    source_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if source_fps <= 0:
        source_fps = 30

    frame_step = max(1, int(round(source_fps / args.sample_fps)))

    metadata = {
        "video": str(video_path),
        "source_fps": source_fps,
        "sample_fps": args.sample_fps,
        "frame_step": frame_step,
        "left_ignore": args.left_ignore,
        "roi_size": args.roi_size,
        "detector_params": {
            "min_width": args.min_width,
            "max_width": args.max_width,
            "min_height": args.min_height,
            "max_height": args.max_height,
            "min_aspect": args.min_aspect,
            "max_aspect": args.max_aspect,
            "min_color_fill": args.min_color_fill,
            "min_white_ratio": args.min_white_ratio,
            "nms_iou": args.nms_iou,
            "ignore_bottom_px": args.ignore_bottom_px,
            "loose_h_extra": args.loose_h_extra,
            "loose_s_extra": args.loose_s_extra,
            "loose_v_extra": args.loose_v_extra,
            "search_pad_x": args.search_pad_x,
            "search_pad_y": args.search_pad_y,
            "max_expand_x": args.max_expand_x,
            "target_plate_height": args.target_plate_height,
            "plate_height_tolerance": args.plate_height_tolerance,
            "team_hsv_json": args.team_hsv_json,
            "h_tol": args.h_tol,
            "s_tol": args.s_tol,
            "v_tol": args.v_tol,
            "hsv_min_s": args.hsv_min_s,
            "hsv_min_v": args.hsv_min_v,
            "team_hsv_count": len(team_hsv) if team_hsv else 0,
        },
        "frames": [],
    }

    frame_idx = 0
    saved_dataset_frames = 0
    sampled_frames = 0
    total_accepted = 0

    pbar = tqdm(total=total_frames, desc="Processing video")

    while True:
        ret, frame = cap.read()

        if not ret:
            break

        if frame_idx % frame_step != 0:
            frame_idx += 1
            pbar.update(1)
            continue

        sampled_frames += 1

        roi, roi_x, roi_y = central_roi(
            frame,
            left_ignore=args.left_ignore,
            roi_size=args.roi_size,
        )

        boxes, rejected, mask = detect_colored_plates_opencv(
            roi,
            min_width=args.min_width,
            max_width=args.max_width,
            min_height=args.min_height,
            max_height=args.max_height,
            min_aspect=args.min_aspect,
            max_aspect=args.max_aspect,
            min_color_fill=args.min_color_fill,
            min_white_ratio=args.min_white_ratio,
            nms_iou=args.nms_iou,
            team_hsv=team_hsv,
            h_tol=args.h_tol,
            s_tol=args.s_tol,
            v_tol=args.v_tol,
            hsv_min_s=args.hsv_min_s,
            hsv_min_v=args.hsv_min_v,
            loose_h_extra=args.loose_h_extra,
            loose_s_extra=args.loose_s_extra,
            loose_v_extra=args.loose_v_extra,
            search_pad_x=args.search_pad_x,
            search_pad_y=args.search_pad_y,
            max_expand_x=args.max_expand_x,
            target_plate_height=args.target_plate_height,
            plate_height_tolerance=args.plate_height_tolerance,
            ignore_bottom_px=args.ignore_bottom_px,
        )

        total_accepted += len(boxes)

        image_name = f"{video_path.stem}_frame_{frame_idx:07d}.jpg"
        label_name = f"{video_path.stem}_frame_{frame_idx:07d}.txt"

        # Debug сохраняем даже когда нет боксов, чтобы было видно accepted: 0.
        if args.save_debug_all or boxes:
            debug_img = draw_boxes(
                roi,
                boxes,
                rejected=rejected,
                draw_rejected=args.draw_rejected,
            )
            cv2.imwrite(str(debug_dir / image_name), debug_img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            cv2.imwrite(str(masks_dir / image_name), mask)

        # Rejected crops нужны только для диагностики фильтров.
        if args.save_rejected:
            for rej_idx, (rx, ry, rw, rh, reason, features) in enumerate(rejected):
                rejected_crop = roi[ry:ry + rh, rx:rx + rw]
                crop_name = f"{video_path.stem}_frame_{frame_idx:07d}_rejected_{rej_idx:02d}_{reason}.png"
                save_crop(rejected_crops_dir / crop_name, rejected_crop)

        # Не сохраняем в YOLO-датасет пустые кадры, если явно не попросили.
        # Для твоей карты обычно плашки видны почти всегда, поэтому --save-empty лучше не использовать на старте.
        if not boxes and not args.save_empty:
            metadata["frames"].append({
                "frame_idx": frame_idx,
                "time_sec": frame_idx / source_fps,
                "saved_to_dataset": False,
                "reason": "no_boxes",
                "roi_x": roi_x,
                "roi_y": roi_y,
                "accepted_count": 0,
                "rejected_count": len(rejected),
            })

            frame_idx += 1
            pbar.update(1)
            continue

        split = "val" if random.random() < args.val_ratio else "train"

        if split == "train":
            image_path = images_train / image_name
            label_path = labels_train / label_name
        else:
            image_path = images_val / image_name
            label_path = labels_val / label_name

        cv2.imwrite(str(image_path), roi, [cv2.IMWRITE_JPEG_QUALITY, 95])

        with open(label_path, "w", encoding="utf-8") as f:
            for box in boxes:
                f.write(to_yolo_line(box, args.roi_size, args.roi_size, class_id=0) + "\n")

        frame_meta = {
            "frame_idx": frame_idx,
            "time_sec": frame_idx / source_fps,
            "saved_to_dataset": True,
            "split": split,
            "image": str(image_path),
            "label": str(label_path),
            "roi_x": roi_x,
            "roi_y": roi_y,
            "accepted_count": len(boxes),
            "rejected_count": len(rejected),
            "boxes": [],
        }

        for i, box in enumerate(boxes):
            x, y, w, h, score, features = box
            plate_crop = roi[y:y + h, x:x + w]

            crop_name = f"{video_path.stem}_frame_{frame_idx:07d}_plate_{i:02d}.png"
            crop_path = crops_dir / crop_name
            save_crop(crop_path, plate_crop)

            frame_meta["boxes"].append({
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "score": score,
                "features": features,
                "crop": str(crop_path),
                "original_frame_x": roi_x + x,
                "original_frame_y": roi_y + y,
            })

        metadata["frames"].append(frame_meta)
        saved_dataset_frames += 1

        frame_idx += 1
        pbar.update(1)

    pbar.close()
    cap.release()

    data_yaml = out_dir / "data.yaml"
    data_yaml.write_text(
        f"""path: {out_dir.resolve()}
train: images/train
val: images/val

names:
  0: team_plate
""",
        encoding="utf-8",
    )

    with open(out_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    avg_boxes = total_accepted / max(sampled_frames, 1)

    print("\nDone.")
    print(f"Dataset saved to: {out_dir.resolve()}")
    print(f"YOLO config: {data_yaml.resolve()}")
    print(f"Sampled frames: {sampled_frames}")
    print(f"Saved dataset frames: {saved_dataset_frames}")
    print(f"Total accepted boxes: {total_accepted}")
    print(f"Average accepted boxes per sampled frame: {avg_boxes:.2f}")
    print(f"Check debug images: {debug_dir.resolve()}")
    print(f"Check crops: {crops_dir.resolve()}")
    if team_hsv:
        print(f"Team HSV colors loaded: {len(team_hsv)}")

    if args.save_empty:
        print("\nWARNING: --save-empty was enabled.")
        print("Use it only for real negative frames where no team plates are visible.")
        print("If plates are visible but OpenCV missed them, empty labels will hurt YOLO training.")


if __name__ == "__main__":
    main()
