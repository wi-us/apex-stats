from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _safe_norm(value: float, scale: float) -> float:
    if scale <= 1e-6:
        return 0.0
    return float(value) / float(scale)


@dataclass
class SimilarityTransform:
    scale: float
    rotation_rad: float
    tx: float
    ty: float

    def to_matrix(self) -> np.ndarray:
        c = float(math.cos(self.rotation_rad))
        s = float(math.sin(self.rotation_rad))
        a = float(self.scale * c)
        b = float(self.scale * s)
        return np.array([[a, -b, self.tx], [b, a, self.ty]], dtype=np.float32)

    @classmethod
    def from_matrix(cls, matrix: np.ndarray) -> "SimilarityTransform":
        a = float(matrix[0, 0])
        b = float(matrix[1, 0])
        scale = max(1e-6, math.hypot(a, b))
        rotation = math.atan2(b, a)
        return cls(scale=scale, rotation_rad=rotation, tx=float(matrix[0, 2]), ty=float(matrix[1, 2]))

    def blend(self, target: "SimilarityTransform", alpha: float) -> "SimilarityTransform":
        a = max(0.0, min(1.0, float(alpha)))
        return SimilarityTransform(
            scale=self.scale * (1.0 - a) + target.scale * a,
            rotation_rad=self.rotation_rad * (1.0 - a) + target.rotation_rad * a,
            tx=self.tx * (1.0 - a) + target.tx * a,
            ty=self.ty * (1.0 - a) + target.ty * a,
        )


@dataclass
class RegistrationObservation:
    transform: SimilarityTransform
    inliers: int
    match_count: int
    inlier_ratio: float
    residual_px: float


@dataclass
class RegistrationConfig:
    smoothing_alpha: float = 0.25
    min_inliers: int = 10
    ratio_test: float = 0.78
    ransac_reproj_threshold: float = 4.0
    max_residual_px: float = 18.0
    orb_features: int = 1500


class MapRegistrationEngine:
    def __init__(
        self,
        reference_map: np.ndarray,
        map_roi: tuple[int, int, int, int],
        config: Optional[RegistrationConfig] = None,
    ) -> None:
        self.config = config or RegistrationConfig()
        self.map_roi = map_roi
        self.reference_gray = _to_gray(reference_map)
        self.reference_h, self.reference_w = self.reference_gray.shape[:2]
        self.orb = cv2.ORB_create(nfeatures=int(self.config.orb_features))
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        self.ref_keypoints, self.ref_descriptors = self.orb.detectAndCompute(self.reference_gray, None)
        self.current_transform = self.initial_transform_from_map_roi()

    def initial_transform_from_map_roi(self) -> SimilarityTransform:
        map_x, map_y, map_w, map_h = self.map_roi
        if map_w <= 0 or map_h <= 0:
            return SimilarityTransform(scale=1.0, rotation_rad=0.0, tx=0.0, ty=0.0)
        sx = _safe_norm(self.reference_w, map_w)
        sy = _safe_norm(self.reference_h, map_h)
        scale = float((sx + sy) / 2.0)
        tx = float(-map_x * scale)
        ty = float(-map_y * scale)
        return SimilarityTransform(scale=scale, rotation_rad=0.0, tx=tx, ty=ty)

    def project_frame_point(self, x: float, y: float, transform: Optional[SimilarityTransform] = None) -> tuple[float, float]:
        t = transform or self.current_transform
        m = t.to_matrix()
        out = m @ np.array([float(x), float(y), 1.0], dtype=np.float32)
        return float(out[0]), float(out[1])

    def transform_delta_px(
        self,
        first: SimilarityTransform,
        second: SimilarityTransform,
        sample_point: tuple[float, float],
    ) -> float:
        x1, y1 = self.project_frame_point(sample_point[0], sample_point[1], first)
        x2, y2 = self.project_frame_point(sample_point[0], sample_point[1], second)
        return float(math.hypot(x1 - x2, y1 - y2))

    def _compose_roi_to_frame(self, roi_to_map: np.ndarray, roi_origin: tuple[int, int]) -> np.ndarray:
        ox, oy = roi_origin
        frame_to_roi = np.array([[1.0, 0.0, -float(ox)], [0.0, 1.0, -float(oy)], [0.0, 0.0, 1.0]], dtype=np.float32)
        roi_to_map_3 = np.vstack([roi_to_map.astype(np.float32), np.array([0.0, 0.0, 1.0], dtype=np.float32)])
        frame_to_map = roi_to_map_3 @ frame_to_roi
        return frame_to_map[:2, :]

    def estimate_from_roi(self, roi_image: np.ndarray, roi_origin: tuple[int, int]) -> Optional[RegistrationObservation]:
        if roi_image is None or roi_image.size == 0:
            return None
        if self.ref_descriptors is None or len(self.ref_keypoints) < max(8, self.config.min_inliers):
            return None
        gray = _to_gray(roi_image)
        keypoints, descriptors = self.orb.detectAndCompute(gray, None)
        if descriptors is None or keypoints is None or len(keypoints) < max(8, self.config.min_inliers):
            return None

        knn = self.matcher.knnMatch(descriptors, self.ref_descriptors, k=2)
        good_matches: list[cv2.DMatch] = []
        for pair in knn:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < float(self.config.ratio_test) * n.distance:
                good_matches.append(m)
        if len(good_matches) < max(8, self.config.min_inliers):
            return None

        src_pts = np.float32([keypoints[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        dst_pts = np.float32([self.ref_keypoints[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        roi_to_map, inliers_mask = cv2.estimateAffinePartial2D(
            src_pts,
            dst_pts,
            method=cv2.RANSAC,
            ransacReprojThreshold=float(self.config.ransac_reproj_threshold),
        )
        if roi_to_map is None or inliers_mask is None:
            return None

        inliers = int(np.count_nonzero(inliers_mask))
        if inliers < int(self.config.min_inliers):
            return None

        inlier_src = src_pts[inliers_mask.ravel() > 0].reshape(-1, 2)
        inlier_dst = dst_pts[inliers_mask.ravel() > 0].reshape(-1, 2)
        projected = cv2.transform(inlier_src.reshape(-1, 1, 2), roi_to_map).reshape(-1, 2)
        residual = float(np.mean(np.linalg.norm(projected - inlier_dst, axis=1))) if len(projected) else float("inf")
        if residual > float(self.config.max_residual_px):
            return None

        frame_to_map = self._compose_roi_to_frame(roi_to_map, roi_origin)
        transform = SimilarityTransform.from_matrix(frame_to_map)
        return RegistrationObservation(
            transform=transform,
            inliers=inliers,
            match_count=len(good_matches),
            inlier_ratio=float(inliers / max(1, len(good_matches))),
            residual_px=residual,
        )

    def update_transform(self, observation: RegistrationObservation) -> SimilarityTransform:
        self.current_transform = self.current_transform.blend(observation.transform, self.config.smoothing_alpha)
        return self.current_transform


def resolve_reference_map_path(project_root: Path, map_name: str, explicit_path: Optional[str] = None) -> Optional[Path]:
    if explicit_path:
        path = Path(explicit_path)
        if not path.is_absolute():
            path = project_root / path
        return path if path.exists() else None

    normalized = map_name if map_name.startswith("mp_") else f"mp_{map_name}"
    short_name = normalized.removeprefix("mp_")
    maps_dir = project_root / "maps"
    output_dir = project_root / "output"
    candidates = [
        maps_dir / f"{normalized}.png",
        maps_dir / f"{normalized}.webp",
        maps_dir / f"{normalized}.jpg",
        maps_dir / f"{normalized}.jpeg",
        maps_dir / f"{short_name}.png",
        maps_dir / f"{short_name}.webp",
        output_dir / f"map_background_{normalized}.png",
        output_dir / f"map_background_{short_name}.png",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def load_image_safe(path: Path) -> Optional[np.ndarray]:
    if not path.exists():
        return None
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size > 0:
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if image is not None:
                return image
    except Exception:
        pass
    image = cv2.imread(str(path))
    return image
