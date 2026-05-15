from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .types import MinimapCropConfig


@dataclass
class MatchingFeatures:
    gray: np.ndarray
    edges: np.ndarray


def crop_minimap(frame_bgr: np.ndarray, crop_config: MinimapCropConfig) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    x, y, size = int(crop_config.x), int(crop_config.y), int(crop_config.size)
    if size <= 0:
        raise ValueError(f"minimap crop size must be positive, got {size}")
    if x < 0 or y < 0 or x + size > w or y + size > h:
        raise ValueError(
            f"minimap crop out of frame bounds: crop=({x},{y},{size}) frame=({w}x{h})"
        )
    return frame_bgr[y : y + size, x : x + size].copy()


def crop_inner_minimap(minimap_bgr: np.ndarray, border_px: int = 10) -> np.ndarray:
    border = max(0, int(border_px))
    h, w = minimap_bgr.shape[:2]
    if h <= border * 2 or w <= border * 2:
        raise ValueError(
            f"minimap too small for border={border_px}: shape=({w}x{h})"
        )
    return minimap_bgr[border : h - border, border : w - border].copy()


def build_minimap_ignore_mask(minimap_bgr: np.ndarray) -> np.ndarray:
    """255 = ignore (HUD markers, bright overlays)."""
    if minimap_bgr.ndim != 3:
        return np.zeros(minimap_bgr.shape[:2], dtype=np.uint8)
    hsv = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    ignore = ((s > 80) & (v > 120)) | (v > 230)
    ignore = ignore.astype(np.uint8) * 255
    ignore = cv2.dilate(ignore, np.ones((5, 5), np.uint8), iterations=1)
    return ignore


def build_valid_matching_mask(minimap_bgr: np.ndarray, border_px: int = 12) -> np.ndarray:
    """255 = use for matching, 0 = ignore."""
    h, w = minimap_bgr.shape[:2]
    mask = np.full((h, w), 255, dtype=np.uint8)
    border = max(0, int(border_px))
    if border > 0:
        mask[:border, :] = 0
        mask[-border:, :] = 0
        mask[:, :border] = 0
        mask[:, -border:] = 0

    ignore = build_minimap_ignore_mask(minimap_bgr)
    mask[ignore > 0] = 0

    if minimap_bgr.ndim == 3:
        gray = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = minimap_bgr
    mask[gray < 18] = 0
    return mask


def remove_hud_noise(minimap_bgr: np.ndarray) -> np.ndarray:
    """Mask bright saturated HUD markers; returns BGR with markers darkened."""
    if minimap_bgr.ndim != 3:
        return minimap_bgr
    ignore = build_minimap_ignore_mask(minimap_bgr)
    if not np.any(ignore):
        return minimap_bgr
    out = minimap_bgr.copy()
    out[ignore > 0] = (0, 0, 0)
    return out


def preprocess_for_matching(img_bgr: np.ndarray, mode: str = "edges") -> np.ndarray:
    """Legacy single-channel output (edges by default)."""
    feat = preprocess_matching_features(img_bgr)
    if mode == "gray":
        return feat.gray
    return feat.edges


def preprocess_matching_features(img_bgr: np.ndarray) -> MatchingFeatures:
    if img_bgr.ndim == 3:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = img_bgr.copy()
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 50, 150)
    kernel = np.ones((2, 2), np.uint8)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    return MatchingFeatures(gray=gray, edges=edges)


def resize_keep_aspect(img: np.ndarray, scale: float) -> np.ndarray:
    if scale <= 0:
        raise ValueError(f"scale must be positive, got {scale}")
    h, w = img.shape[:2]
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    return cv2.resize(img, (new_w, new_h), interpolation=interp)


def features_to_bgr(feat: MatchingFeatures) -> np.ndarray:
    """Stack gray + edges for debug thumbnails."""
    g = feat.gray
    e = feat.edges
    if g.shape != e.shape:
        e = cv2.resize(e, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(cv2.addWeighted(g, 0.55, e, 0.45, 0), cv2.COLOR_GRAY2BGR)
