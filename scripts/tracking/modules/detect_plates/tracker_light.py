"""
tracker_light.py — лёгкий sub-frame трекер слотов между keyframe.

Между двумя keyframe детектора (например, 1 fps) для каждого активного слота
мы хотим иметь плотную траекторию. Делаем минимально дорого:

1. На keyframe — re-init трекеров от свежих bbox детектора;
2. На промежуточном кадре — `tracker.update(frame)` (KCF, нативный C++);
3. Если KCF недоступен или вернул False — fallback на центроид через
   Farneback dense optical flow в локальном окне 80x80 вокруг last_box.

Никакой межслотовой логики, никаких ID-switch'ей — это просто протяжка
(x,y,w,h) одного слота во времени. Все обновления локальны.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import cv2
import numpy as np


BBox = Tuple[int, int, int, int]  # x, y, w, h


def _make_kcf():
    # OpenCV 4.5+: cv2.TrackerKCF_create; в legacy — cv2.legacy.TrackerKCF_create.
    for ns in (cv2, getattr(cv2, "legacy", None)):
        if ns is None:
            continue
        f = getattr(ns, "TrackerKCF_create", None)
        if f is not None:
            try:
                return f()
            except cv2.error:
                continue
    return None


class SlotTracker:
    """Один трекер для одного слота."""

    __slots__ = ("kcf", "last_bbox", "prev_gray")

    def __init__(self) -> None:
        self.kcf = None
        self.last_bbox: Optional[BBox] = None
        self.prev_gray: Optional[np.ndarray] = None

    def init(self, frame: np.ndarray, bbox: BBox) -> None:
        x, y, w, h = bbox
        self.last_bbox = (int(x), int(y), int(w), int(h))
        self.kcf = _make_kcf()
        if self.kcf is not None:
            try:
                self.kcf.init(frame, (int(x), int(y), int(w), int(h)))
            except cv2.error:
                self.kcf = None
        if self.kcf is None:
            self.prev_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def update(self, frame: np.ndarray) -> Optional[BBox]:
        if self.last_bbox is None:
            return None
        if self.kcf is not None:
            ok, box = self.kcf.update(frame)
            if ok:
                x, y, w, h = box
                self.last_bbox = (int(x), int(y), int(w), int(h))
                return self.last_bbox
            # KCF потерял — переключаемся на OF
            self.kcf = None
            self.prev_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            return self.last_bbox
        # Farneback fallback в локальном окне
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.prev_gray is None:
            self.prev_gray = gray
            return self.last_bbox
        x, y, w, h = self.last_bbox
        pad = 20
        H, W = gray.shape[:2]
        x1 = max(0, x - pad); y1 = max(0, y - pad)
        x2 = min(W, x + w + pad); y2 = min(H, y + h + pad)
        if x2 - x1 < 4 or y2 - y1 < 4:
            self.prev_gray = gray
            return self.last_bbox
        a = self.prev_gray[y1:y2, x1:x2]
        b = gray[y1:y2, x1:x2]
        try:
            flow = cv2.calcOpticalFlowFarneback(
                a, b, None, 0.5, 2, 15, 2, 5, 1.2, 0
            )
            dx = float(np.median(flow[..., 0]))
            dy = float(np.median(flow[..., 1]))
        except cv2.error:
            dx = dy = 0.0
        nx = max(0, min(W - w, int(round(x + dx))))
        ny = max(0, min(H - h, int(round(y + dy))))
        self.last_bbox = (nx, ny, w, h)
        self.prev_gray = gray
        return self.last_bbox


class MultiSlotTracker:
    """Один объект-обёртка над per-slot трекерами."""

    def __init__(self) -> None:
        self.slots: Dict[str, SlotTracker] = {}

    def reset(self, slot_key: str) -> None:
        self.slots.pop(slot_key, None)

    def init_slot(self, slot_key: str, frame: np.ndarray, bbox: BBox) -> None:
        st = SlotTracker()
        st.init(frame, bbox)
        self.slots[slot_key] = st

    def update_all(self, frame: np.ndarray) -> Dict[str, BBox]:
        out: Dict[str, BBox] = {}
        for k, st in list(self.slots.items()):
            bb = st.update(frame)
            if bb is not None:
                out[k] = bb
        return out