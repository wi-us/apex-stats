from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


@dataclass
class BackgroundConfidence:
    feature_inlier_score: float
    photometric_score: float
    temporal_score: float
    fused_score: float


class BackgroundConfidenceEstimator:
    def __init__(
        self,
        low_threshold: float = 0.45,
        high_threshold: float = 0.65,
        check_every_frames: int = 12,
    ) -> None:
        self.low_threshold = float(low_threshold)
        self.high_threshold = float(high_threshold)
        self.check_every_frames = max(1, int(check_every_frames))
        self._prev_roi_gray: Optional[np.ndarray] = None
        self._last_fused = 1.0

    def _photometric_score(self, curr_roi_gray: np.ndarray) -> float:
        curr_resized = cv2.resize(curr_roi_gray, (128, 128), interpolation=cv2.INTER_AREA)
        if self._prev_roi_gray is None:
            self._prev_roi_gray = curr_resized
            return 1.0
        prev_resized = self._prev_roi_gray
        if prev_resized.shape != curr_resized.shape:
            prev_resized = cv2.resize(prev_resized, (128, 128), interpolation=cv2.INTER_AREA)
        mad = float(np.mean(cv2.absdiff(curr_resized, prev_resized)))
        self._prev_roi_gray = curr_resized
        return float(max(0.0, min(1.0, 1.0 - (mad / 255.0))))

    def _temporal_score(self, transform_delta_px: float, residual_px: float) -> float:
        delta_term = math.exp(-max(0.0, float(transform_delta_px)) / 35.0)
        residual_term = math.exp(-max(0.0, float(residual_px)) / 18.0)
        return float(max(0.0, min(1.0, delta_term * residual_term)))

    def evaluate(
        self,
        roi_image: np.ndarray,
        feature_inlier_score: float,
        transform_delta_px: float,
        residual_px: float,
    ) -> BackgroundConfidence:
        curr_gray = _to_gray(roi_image)
        feature = float(max(0.0, min(1.0, feature_inlier_score)))
        photo = self._photometric_score(curr_gray)
        temporal = self._temporal_score(transform_delta_px, residual_px)
        fused = 0.45 * feature + 0.35 * photo + 0.20 * temporal
        fused = float(max(0.0, min(1.0, fused)))
        self._last_fused = fused
        return BackgroundConfidence(
            feature_inlier_score=feature,
            photometric_score=photo,
            temporal_score=temporal,
            fused_score=fused,
        )

    def needs_relocalization(self, score: float, currently_degraded: bool) -> bool:
        if currently_degraded:
            return float(score) < self.high_threshold
        return float(score) < self.low_threshold

