from __future__ import annotations

import bisect
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from background_confidence import BackgroundConfidenceEstimator
from map_registration import (
    MapRegistrationEngine,
    RegistrationConfig,
    SimilarityTransform,
    load_image_safe,
    resolve_reference_map_path,
)


@dataclass
class ObserverCameraConfig:
    enabled: bool = True
    check_every_frames: int = 10
    coarse_check_every_frames: int = 120
    refine_check_every_frames: int = 10
    refine_window_sec: float = 2.5
    low_threshold: float = 0.45
    high_threshold: float = 0.65
    fail_limit: int = 3
    smoothing_alpha: float = 1.0
    map_image_path: Optional[str] = None
    hold_last_good_sec: float = 1.25
    event_delta_px: float = 18.0
    event_zoom_ratio: float = 0.025
    min_event_inlier_ratio: float = 0.25
    max_event_residual_px: float = 8.0


@dataclass
class ObserverCameraSample:
    timestamp_sec: float
    frame_num: int
    state: str
    confidence: float
    residual_px: float
    delta_px: float
    feature_inlier_score: float
    scale: float
    rotation_rad: float
    tx: float
    ty: float
    held: bool = False
    scale_delta: float = 0.0
    cumulative_zoom_ratio: float = 1.0


@dataclass
class ObserverCameraEvent:
    timestamp_sec: float
    frame_num: int
    event_type: str
    magnitude: float
    delta_px: float
    scale_delta: float
    state: str
    diagnostics: Optional[dict[str, Any]] = None


@dataclass
class ObserverProjection:
    x: Optional[float]
    y: Optional[float]
    state: str
    confidence: float
    residual_px: float
    delta_px: float
    feature_inlier_score: float
    scale: float
    cumulative_zoom_ratio: float
    event_type: Optional[str]
    map_space_valid: bool


def _transform_from_sample(sample: ObserverCameraSample) -> SimilarityTransform:
    return SimilarityTransform(
        scale=float(sample.scale),
        rotation_rad=float(sample.rotation_rad),
        tx=float(sample.tx),
        ty=float(sample.ty),
    )


class ObserverCameraCompensator:
    def __init__(
        self,
        *,
        samples: list[ObserverCameraSample],
        events: list[ObserverCameraEvent],
        map_width: int,
        map_height: int,
        config: ObserverCameraConfig | None = None,
    ) -> None:
        self.samples = sorted(samples, key=lambda item: float(item.timestamp_sec))
        self.events = sorted(events, key=lambda item: float(item.timestamp_sec))
        self.map_width = int(map_width)
        self.map_height = int(map_height)
        self.config = config or ObserverCameraConfig()
        self._sample_times = [float(item.timestamp_sec) for item in self.samples]
        self._event_times = [float(item.timestamp_sec) for item in self.events]

    @classmethod
    def disabled(cls, *, map_roi: tuple[int, int, int, int]) -> "ObserverCameraCompensator":
        map_x, map_y, map_w, map_h = map_roi
        transform = SimilarityTransform(scale=1.0, rotation_rad=0.0, tx=float(-map_x), ty=float(-map_y))
        sample = ObserverCameraSample(
            timestamp_sec=0.0,
            frame_num=0,
            state="disabled",
            confidence=0.0,
            residual_px=999.0,
            delta_px=0.0,
            feature_inlier_score=0.0,
            scale=float(transform.scale),
            rotation_rad=float(transform.rotation_rad),
            tx=float(transform.tx),
            ty=float(transform.ty),
        )
        return cls(samples=[sample], events=[], map_width=int(map_w), map_height=int(map_h), config=ObserverCameraConfig(enabled=False))

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> Optional["ObserverCameraCompensator"]:
        if not isinstance(payload, dict):
            return None
        config_payload = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        samples_payload = payload.get("samples") if isinstance(payload.get("samples"), list) else []
        events_payload = payload.get("events") if isinstance(payload.get("events"), list) else []
        samples: list[ObserverCameraSample] = []
        for item in samples_payload:
            if not isinstance(item, dict):
                continue
            try:
                samples.append(ObserverCameraSample(**item))
            except TypeError:
                continue
        events: list[ObserverCameraEvent] = []
        for item in events_payload:
            if not isinstance(item, dict):
                continue
            try:
                events.append(ObserverCameraEvent(**item))
            except TypeError:
                continue
        if not samples:
            return None
        try:
            cfg = ObserverCameraConfig(**config_payload)
        except TypeError:
            cfg = ObserverCameraConfig()
        return cls(
            samples=samples,
            events=events,
            map_width=int(payload.get("map_width", 1080) or 1080),
            map_height=int(payload.get("map_height", 1080) or 1080),
            config=cfg,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "map_width": int(self.map_width),
            "map_height": int(self.map_height),
            "samples": [asdict(item) for item in self.samples],
            "events": [asdict(item) for item in self.events],
            "summary": self.summary(),
        }

    def summary(self) -> dict[str, Any]:
        confidences = [float(item.confidence) for item in self.samples]
        degraded = [item for item in self.samples if item.state == "degraded"]
        scale_deltas = [abs(float(item.scale_delta)) for item in self.samples]
        pan_jumps = [abs(float(item.delta_px)) for item in self.samples]
        degraded_intervals = _state_intervals(self.samples, "degraded")
        event_counts: dict[str, int] = {}
        for event in self.events:
            event_counts[str(event.event_type)] = event_counts.get(str(event.event_type), 0) + 1
        return {
            "enabled": bool(self.config.enabled),
            "samples": int(len(self.samples)),
            "events": int(len(self.events)),
            "eventCounts": event_counts,
            "degradedSamples": int(len(degraded)),
            "degradedIntervals": degraded_intervals,
            "meanConfidence": round(float(np.mean(confidences)) if confidences else 0.0, 6),
            "maxDeltaPx": round(max((float(item.delta_px) for item in self.samples), default=0.0), 6),
            "maxPanJumpPx": round(max(pan_jumps, default=0.0), 6),
            "maxZoomJumpRatio": round(max(scale_deltas, default=0.0), 6),
            "maxZoomJumpPct": round(max(scale_deltas, default=0.0) * 100.0, 6),
            "finalCumulativeZoomRatio": round(float(self.samples[-1].cumulative_zoom_ratio) if self.samples else 1.0, 6),
            "maxEventMagnitude": round(max((float(item.magnitude) for item in self.events), default=0.0), 6),
            "mapWidth": int(self.map_width),
            "mapHeight": int(self.map_height),
        }

    def nearest_sample(self, timestamp_sec: float) -> ObserverCameraSample:
        if not self.samples:
            raise ValueError("ObserverCameraCompensator has no samples")
        idx = bisect.bisect_right(self._sample_times, float(timestamp_sec)) - 1
        idx = max(0, min(idx, len(self.samples) - 1))
        return self.samples[idx]

    def nearest_event_type(self, timestamp_sec: float, window_sec: float = 0.75) -> Optional[str]:
        if not self.events:
            return None
        idx = bisect.bisect_right(self._event_times, float(timestamp_sec))
        candidates = []
        if 0 <= idx - 1 < len(self.events):
            candidates.append(self.events[idx - 1])
        if 0 <= idx < len(self.events):
            candidates.append(self.events[idx])
        best = min(candidates, key=lambda item: abs(float(item.timestamp_sec) - float(timestamp_sec)), default=None)
        if best is None or abs(float(best.timestamp_sec) - float(timestamp_sec)) > float(window_sec):
            return None
        return str(best.event_type)

    def project_point(self, timestamp_sec: float, x: float, y: float) -> ObserverProjection:
        sample = self.nearest_sample(timestamp_sec)
        valid = bool(sample.state in {"tracked", "tracked_relative", "relocalized", "held"})
        if not valid:
            fallback_x: Optional[float] = None
            fallback_y: Optional[float] = None
            if sample.state == "disabled":
                mx, my = _project_with_transform(float(x), float(y), _transform_from_sample(sample))
                fallback_x = min(max(0.0, float(mx)), float(max(0, self.map_width - 1)))
                fallback_y = min(max(0.0, float(my)), float(max(0, self.map_height - 1)))
            return ObserverProjection(
                x=fallback_x,
                y=fallback_y,
                state=str(sample.state),
                confidence=float(sample.confidence),
                residual_px=float(sample.residual_px),
                delta_px=float(sample.delta_px),
                feature_inlier_score=float(sample.feature_inlier_score),
                scale=float(sample.scale),
                cumulative_zoom_ratio=float(sample.cumulative_zoom_ratio),
                event_type=self.nearest_event_type(timestamp_sec),
                map_space_valid=False,
            )
        mx, my = _project_with_transform(float(x), float(y), _transform_from_sample(sample))
        clipped_x = min(max(0.0, float(mx)), float(max(0, self.map_width - 1)))
        clipped_y = min(max(0.0, float(my)), float(max(0, self.map_height - 1)))
        return ObserverProjection(
            x=clipped_x,
            y=clipped_y,
            state=str(sample.state),
            confidence=float(sample.confidence),
            residual_px=float(sample.residual_px),
            delta_px=float(sample.delta_px),
            feature_inlier_score=float(sample.feature_inlier_score),
            scale=float(sample.scale),
            cumulative_zoom_ratio=float(sample.cumulative_zoom_ratio),
            event_type=self.nearest_event_type(timestamp_sec),
            map_space_valid=True,
        )


def _project_with_transform(x: float, y: float, transform: SimilarityTransform) -> tuple[float, float]:
    m = transform.to_matrix()
    out = m @ np.array([float(x), float(y), 1.0], dtype=np.float32)
    return float(out[0]), float(out[1])


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _estimate_relative_roi_transform(
    previous_roi: np.ndarray,
    current_roi: np.ndarray,
    *,
    roi_origin: tuple[int, int],
    orb: cv2.ORB,
    matcher: cv2.BFMatcher,
) -> Optional[tuple[SimilarityTransform, float, float, float]]:
    feature_scale = 0.5
    prev_gray = cv2.resize(_to_gray(previous_roi), (0, 0), fx=feature_scale, fy=feature_scale, interpolation=cv2.INTER_AREA)
    curr_gray = cv2.resize(_to_gray(current_roi), (0, 0), fx=feature_scale, fy=feature_scale, interpolation=cv2.INTER_AREA)
    prev_keypoints, prev_descriptors = orb.detectAndCompute(prev_gray, None)
    curr_keypoints, curr_descriptors = orb.detectAndCompute(curr_gray, None)
    if (
        prev_descriptors is None
        or curr_descriptors is None
        or prev_keypoints is None
        or curr_keypoints is None
        or len(prev_keypoints) < 12
        or len(curr_keypoints) < 12
    ):
        return None

    good_matches: list[cv2.DMatch] = []
    for pair in matcher.knnMatch(prev_descriptors, curr_descriptors, k=2):
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.78 * n.distance:
            good_matches.append(m)
    if len(good_matches) < 12:
        return None

    prev_pts = np.float32([prev_keypoints[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    curr_pts = np.float32([curr_keypoints[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    prev_to_curr, inliers_mask = cv2.estimateAffinePartial2D(
        prev_pts,
        curr_pts,
        method=cv2.RANSAC,
        ransacReprojThreshold=4.0,
    )
    if prev_to_curr is None or inliers_mask is None or int(np.count_nonzero(inliers_mask)) < 8:
        return None
    inliers = int(np.count_nonzero(inliers_mask))
    inlier_ratio = float(inliers / max(1, len(good_matches)))
    inlier_prev = prev_pts[inliers_mask.ravel() > 0].reshape(-1, 2)
    inlier_curr = curr_pts[inliers_mask.ravel() > 0].reshape(-1, 2)
    projected = cv2.transform(inlier_prev.reshape(-1, 1, 2), prev_to_curr).reshape(-1, 2)
    residual_px = float(np.mean(np.linalg.norm(projected - inlier_curr, axis=1)) / feature_scale) if len(projected) else 999.0

    prev_to_curr = prev_to_curr.astype(np.float32)
    prev_to_curr[0, 2] = float(prev_to_curr[0, 2]) / feature_scale
    prev_to_curr[1, 2] = float(prev_to_curr[1, 2]) / feature_scale
    visual_pan_px = float(math.hypot(float(prev_to_curr[0, 2]), float(prev_to_curr[1, 2])))
    prev_to_curr_3 = np.vstack([prev_to_curr, np.array([0.0, 0.0, 1.0], dtype=np.float32)])
    try:
        curr_to_prev_3 = np.linalg.inv(prev_to_curr_3)
    except np.linalg.LinAlgError:
        return None

    ox, oy = roi_origin
    frame_to_roi = np.array([[1.0, 0.0, -float(ox)], [0.0, 1.0, -float(oy)], [0.0, 0.0, 1.0]], dtype=np.float32)
    roi_to_frame = np.array([[1.0, 0.0, float(ox)], [0.0, 1.0, float(oy)], [0.0, 0.0, 1.0]], dtype=np.float32)
    curr_frame_to_prev_frame = roi_to_frame @ curr_to_prev_3 @ frame_to_roi
    return SimilarityTransform.from_matrix(curr_frame_to_prev_frame[:2, :]), inlier_ratio, residual_px, visual_pan_px


def _compose_transforms(first: SimilarityTransform, second: SimilarityTransform) -> SimilarityTransform:
    first_3 = np.vstack([first.to_matrix(), np.array([0.0, 0.0, 1.0], dtype=np.float32)])
    second_3 = np.vstack([second.to_matrix(), np.array([0.0, 0.0, 1.0], dtype=np.float32)])
    return SimilarityTransform.from_matrix((first_3 @ second_3)[:2, :])


def _relative_scale_delta(previous_scale: float, current_scale: float) -> float:
    current = max(1e-6, float(current_scale))
    return float((float(previous_scale) / current) - 1.0)


def _state_intervals(samples: list[ObserverCameraSample], state: str) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    start: Optional[ObserverCameraSample] = None
    prev: Optional[ObserverCameraSample] = None
    for sample in samples:
        if sample.state == state:
            if start is None:
                start = sample
            prev = sample
            continue
        if start is not None and prev is not None:
            intervals.append(
                {
                    "startSec": round(float(start.timestamp_sec), 6),
                    "endSec": round(float(prev.timestamp_sec), 6),
                    "frames": [int(start.frame_num), int(prev.frame_num)],
                }
            )
        start = None
        prev = None
    if start is not None and prev is not None:
        intervals.append(
            {
                "startSec": round(float(start.timestamp_sec), 6),
                "endSec": round(float(prev.timestamp_sec), 6),
                "frames": [int(start.frame_num), int(prev.frame_num)],
            }
        )
    return intervals


def _event_type_for_delta(delta_px: float, scale_delta: float, cfg: ObserverCameraConfig) -> Optional[str]:
    has_pan = abs(float(delta_px)) >= float(cfg.event_delta_px)
    has_zoom = abs(float(scale_delta)) >= float(cfg.event_zoom_ratio)
    if has_pan and has_zoom:
        return "pan_zoom"
    if has_pan:
        return "pan"
    if has_zoom:
        return "zoom"
    return None


def _read_roi_at_frame(
    cap: cv2.VideoCapture,
    frame_num: int,
    map_roi: tuple[int, int, int, int],
) -> Optional[np.ndarray]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_num))
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    map_x, map_y, map_w, map_h = map_roi
    roi = frame[map_y : map_y + map_h, map_x : map_x + map_w]
    if roi.size <= 0:
        return None
    return roi


def _build_adaptive_frame_plan(
    *,
    cap: cv2.VideoCapture,
    map_roi: tuple[int, int, int, int],
    start_frame: int,
    end_frame: int,
    fps: float,
    config: ObserverCameraConfig,
    orb: cv2.ORB,
    matcher: cv2.BFMatcher,
) -> list[int]:
    coarse_step = max(1, int(config.coarse_check_every_frames))
    fine_step = max(1, int(config.refine_check_every_frames or config.check_every_frames))
    if fine_step >= coarse_step:
        return list(range(int(start_frame), int(end_frame) + 1, fine_step))

    coarse_frames = list(range(int(start_frame), int(end_frame) + 1, coarse_step))
    if not coarse_frames or coarse_frames[-1] != int(end_frame):
        coarse_frames.append(int(end_frame))

    event_windows: list[tuple[int, int]] = []
    previous_frame: Optional[int] = None
    previous_roi: Optional[np.ndarray] = None
    anchor_frame: Optional[int] = None
    anchor_roi: Optional[np.ndarray] = None
    for frame_num in coarse_frames:
        roi = _read_roi_at_frame(cap, frame_num, map_roi)
        if roi is None:
            continue
        if anchor_roi is None:
            anchor_frame = int(frame_num)
            anchor_roi = roi
        if previous_roi is not None and previous_frame is not None:
            relative = _estimate_relative_roi_transform(
                previous_roi,
                roi,
                roi_origin=(map_roi[0], map_roi[1]),
                orb=orb,
                matcher=matcher,
            )
            if relative is not None:
                curr_to_prev, _inlier_ratio, _residual_px, visual_pan_px = relative
                scale_delta = _relative_scale_delta(1.0, curr_to_prev.scale)
                if _event_type_for_delta(visual_pan_px, scale_delta, config) is not None:
                    pad = int(max(0.0, float(config.refine_window_sec)) * max(1.0, float(fps)))
                    event_windows.append(
                        (
                            max(int(start_frame), int(previous_frame) - pad),
                            min(int(end_frame), int(frame_num) + pad),
                        )
                    )
                    anchor_frame = int(frame_num)
                    anchor_roi = roi
                    previous_frame = int(frame_num)
                    previous_roi = roi
                    continue
        if anchor_roi is not None and anchor_frame is not None and anchor_frame != int(frame_num):
            anchored = _estimate_relative_roi_transform(
                anchor_roi,
                roi,
                roi_origin=(map_roi[0], map_roi[1]),
                orb=orb,
                matcher=matcher,
            )
            if anchored is not None:
                curr_to_anchor, _inlier_ratio, _residual_px, visual_pan_px = anchored
                scale_delta = _relative_scale_delta(1.0, curr_to_anchor.scale)
                if _event_type_for_delta(visual_pan_px, scale_delta, config) is not None:
                    pad = int(max(0.0, float(config.refine_window_sec)) * max(1.0, float(fps)))
                    event_windows.append(
                        (
                            max(int(start_frame), int(anchor_frame) - pad),
                            min(int(end_frame), int(frame_num) + pad),
                        )
                    )
                    anchor_frame = int(frame_num)
                    anchor_roi = roi
                elif abs(scale_delta) < max(0.006, float(config.event_zoom_ratio) * 0.35) and visual_pan_px < max(6.0, float(config.event_delta_px) * 0.5):
                    anchor_frame = int(frame_num)
                    anchor_roi = roi
        previous_frame = int(frame_num)
        previous_roi = roi

    frames = set(coarse_frames)
    for start, end in event_windows:
        frames.update(range(int(start), int(end) + 1, fine_step))
        frames.add(int(end))
    return sorted(frame for frame in frames if int(start_frame) <= int(frame) <= int(end_frame))


def _sample_from_transform(
    *,
    timestamp_sec: float,
    frame_num: int,
    state: str,
    confidence: float,
    residual_px: float,
    delta_px: float,
    feature_inlier_score: float,
    transform: SimilarityTransform,
    held: bool = False,
    scale_delta: float = 0.0,
    cumulative_zoom_ratio: float = 1.0,
) -> ObserverCameraSample:
    return ObserverCameraSample(
        timestamp_sec=round(float(timestamp_sec), 6),
        frame_num=int(frame_num),
        state=str(state),
        confidence=float(confidence),
        residual_px=float(residual_px),
        delta_px=float(delta_px),
        feature_inlier_score=float(feature_inlier_score),
        scale=float(transform.scale),
        rotation_rad=float(transform.rotation_rad),
        tx=float(transform.tx),
        ty=float(transform.ty),
        held=bool(held),
        scale_delta=float(scale_delta),
        cumulative_zoom_ratio=float(cumulative_zoom_ratio),
    )


def build_observer_camera_compensator(
    *,
    project_root: Path,
    video_path: str | Path,
    map_name: str,
    map_roi: tuple[int, int, int, int],
    fps: float,
    start_seconds: float,
    end_seconds: float,
    config: ObserverCameraConfig,
) -> ObserverCameraCompensator:
    if not config.enabled:
        return ObserverCameraCompensator.disabled(map_roi=map_roi)

    ref_path = resolve_reference_map_path(project_root, map_name, config.map_image_path)
    reference_map = load_image_safe(ref_path) if ref_path is not None else None
    if reference_map is None:
        return ObserverCameraCompensator.disabled(map_roi=map_roi)

    engine = MapRegistrationEngine(
        reference_map=reference_map,
        map_roi=map_roi,
        config=RegistrationConfig(smoothing_alpha=float(config.smoothing_alpha)),
    )
    bg_estimator = BackgroundConfidenceEstimator(
        low_threshold=float(config.low_threshold),
        high_threshold=float(config.high_threshold),
        check_every_frames=int(config.check_every_frames),
    )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return ObserverCameraCompensator.disabled(map_roi=map_roi)

    samples: list[ObserverCameraSample] = []
    events: list[ObserverCameraEvent] = []
    map_x, map_y, map_w, map_h = map_roi
    relative_orb = cv2.ORB_create(nfeatures=2500)
    relative_matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    start_frame = max(0, int(float(start_seconds) * max(1.0, float(fps))))
    end_frame = max(start_frame, int(float(end_seconds) * max(1.0, float(fps))))
    frame_plan = _build_adaptive_frame_plan(
        cap=cap,
        map_roi=map_roi,
        start_frame=start_frame,
        end_frame=end_frame,
        fps=float(fps),
        config=config,
        orb=relative_orb,
        matcher=relative_matcher,
    )
    current_transform = engine.current_transform
    last_good_transform = current_transform
    last_good_ts = float(start_seconds)
    prev_sample_transform = current_transform
    baseline_scale = max(1e-6, float(current_transform.scale))
    transform_failures = 0
    state = "tracked"
    prev_state = state
    previous_roi: Optional[np.ndarray] = None

    for frame_num in frame_plan:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_num))
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        timestamp = float(frame_num) / max(1e-6, float(fps))
        roi = frame[map_y : map_y + map_h, map_x : map_x + map_w]
        if roi.size <= 0:
            continue

        relative = (
            _estimate_relative_roi_transform(
                previous_roi,
                roi,
                roi_origin=(map_x, map_y),
                orb=relative_orb,
                matcher=relative_matcher,
            )
            if previous_roi is not None
            else None
        )
        observation = engine.estimate_from_roi(roi, (map_x, map_y)) if relative is None else None
        if relative is not None:
            curr_to_prev, relative_inlier_ratio, relative_residual, visual_pan_px = relative
            current_transform = _compose_transforms(current_transform, curr_to_prev)
            state = "tracked_relative"
            delta_px = float(visual_pan_px)
            scale_delta = _relative_scale_delta(prev_sample_transform.scale, current_transform.scale)
            cumulative_zoom_ratio = baseline_scale / max(1e-6, float(current_transform.scale))
            transform_failures = 0
            last_good_transform = current_transform
            last_good_ts = timestamp
            sample = _sample_from_transform(
                timestamp_sec=timestamp,
                frame_num=frame_num,
                state=state,
                confidence=max(0.1, min(0.85, float(relative_inlier_ratio))),
                residual_px=relative_residual,
                delta_px=delta_px,
                feature_inlier_score=relative_inlier_ratio,
                transform=current_transform,
                scale_delta=scale_delta,
                cumulative_zoom_ratio=cumulative_zoom_ratio,
            )
        elif observation is not None:
            delta_px = engine.transform_delta_px(
                current_transform,
                observation.transform,
                sample_point=(float(map_x + map_w / 2.0), float(map_y + map_h / 2.0)),
            )
            bg = bg_estimator.evaluate(
                roi,
                feature_inlier_score=observation.inlier_ratio,
                transform_delta_px=delta_px,
                residual_px=observation.residual_px,
            )
            good_event_geometry = (
                float(observation.inlier_ratio) >= float(config.min_event_inlier_ratio)
                and float(observation.residual_px) <= float(config.max_event_residual_px)
            )
            if bg_estimator.needs_relocalization(bg.fused_score, currently_degraded=(state == "degraded")) and good_event_geometry:
                current_transform = observation.transform
                state = "relocalized"
            else:
                current_transform = engine.update_transform(observation)
                state = "tracked"
            scale_delta = _relative_scale_delta(prev_sample_transform.scale, current_transform.scale)
            cumulative_zoom_ratio = baseline_scale / max(1e-6, float(current_transform.scale))
            transform_failures = 0
            last_good_transform = current_transform
            last_good_ts = timestamp
            sample = _sample_from_transform(
                timestamp_sec=timestamp,
                frame_num=frame_num,
                state=state,
                confidence=bg.fused_score,
                residual_px=observation.residual_px,
                delta_px=delta_px,
                feature_inlier_score=observation.inlier_ratio,
                transform=current_transform,
                scale_delta=scale_delta,
                cumulative_zoom_ratio=cumulative_zoom_ratio,
            )
        else:
            transform_failures += 1
            can_hold = (timestamp - last_good_ts) <= float(config.hold_last_good_sec)
            if transform_failures < int(config.fail_limit) and can_hold:
                current_transform = last_good_transform
                scale_delta = _relative_scale_delta(prev_sample_transform.scale, current_transform.scale)
                cumulative_zoom_ratio = baseline_scale / max(1e-6, float(current_transform.scale))
                sample = _sample_from_transform(
                    timestamp_sec=timestamp,
                    frame_num=frame_num,
                    state="held",
                    confidence=0.35,
                    residual_px=999.0,
                    delta_px=0.0,
                    feature_inlier_score=0.0,
                    transform=current_transform,
                    held=True,
                    scale_delta=scale_delta,
                    cumulative_zoom_ratio=cumulative_zoom_ratio,
                )
            else:
                state = "degraded"
                scale_delta = _relative_scale_delta(prev_sample_transform.scale, current_transform.scale)
                cumulative_zoom_ratio = baseline_scale / max(1e-6, float(current_transform.scale))
                sample = _sample_from_transform(
                    timestamp_sec=timestamp,
                    frame_num=frame_num,
                    state=state,
                    confidence=0.0,
                    residual_px=999.0,
                    delta_px=0.0,
                    feature_inlier_score=0.0,
                    transform=current_transform,
                    scale_delta=scale_delta,
                    cumulative_zoom_ratio=cumulative_zoom_ratio,
                )

        scale_delta = float(sample.scale_delta)
        event_type = _event_type_for_delta(float(sample.delta_px), scale_delta, config)
        if sample.state == "degraded" and prev_state != "degraded":
            event_type = "degraded"
        elif sample.state == "relocalized":
            event_type = "relocalized"
        elif prev_state == "degraded" and sample.state in {"tracked", "tracked_relative"}:
            event_type = "stable"
        if event_type is not None:
            events.append(
                ObserverCameraEvent(
                    timestamp_sec=round(float(timestamp), 6),
                    frame_num=int(frame_num),
                    event_type=str(event_type),
                    magnitude=float(max(abs(float(sample.delta_px)), abs(scale_delta) * 1080.0)),
                    delta_px=float(sample.delta_px),
                    scale_delta=float(scale_delta),
                    state=str(sample.state),
                    diagnostics={
                        "confidence": float(sample.confidence),
                        "residualPx": float(sample.residual_px),
                        "featureInlierScore": float(sample.feature_inlier_score),
                        "scale": float(sample.scale),
                        "cumulativeZoomRatio": float(sample.cumulative_zoom_ratio),
                        "tx": float(sample.tx),
                        "ty": float(sample.ty),
                    },
                )
            )
        samples.append(sample)
        prev_sample_transform = _transform_from_sample(sample)
        prev_state = str(sample.state)
        previous_roi = roi.copy()

    cap.release()
    if not samples:
        return ObserverCameraCompensator.disabled(map_roi=map_roi)
    return ObserverCameraCompensator(
        samples=samples,
        events=events,
        map_width=int(engine.reference_w),
        map_height=int(engine.reference_h),
        config=config,
    )

