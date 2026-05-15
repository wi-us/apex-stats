from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .debug_viz import draw_trajectory_on_map, save_debug_visualization, write_debug_video
from .io import imread_bgr
from .locator import (
    _load_map_bgr,
    _window_sizes_around,
    locate_by_window_search,
    locate_by_window_search_local,
)
from .preprocessing import (
    build_valid_matching_mask,
    crop_inner_minimap,
    crop_minimap,
    preprocess_matching_features,
)
from .types import (
    FAST_WINDOW_SIZES,
    MapLocatorConfig,
    MinimapCropConfig,
    MinimapMatchResult,
    VideoTrackingPoint,
    VideoTrackingResult,
    VideoTrackingSettings,
)


def _resolve_frame_step(settings: VideoTrackingSettings, fps: float, total_frames: int) -> int:
    if settings.frame_step and settings.frame_step > 0:
        return max(1, int(settings.frame_step))
    interval = max(0.25, float(settings.sample_interval_sec))
    if fps > 0:
        return max(1, int(round(fps * interval)))
    return max(1, int(60 * interval))


def _tracking_locator_config(
    base: MapLocatorConfig,
    settings: VideoTrackingSettings,
) -> MapLocatorConfig:
    if not settings.fast_mode:
        return base
    return replace(
        base,
        window_sizes=FAST_WINDOW_SIZES,
        coarse_step=36,
        fine_step=10,
        top_k=5,
        top_n_candidates=3,
    )


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(math.hypot(a[0] - b[0], a[1] - b[1]))


def _classify_point(
    match: MinimapMatchResult,
    jump_distance: float | None,
    settings: VideoTrackingSettings,
) -> tuple[str, str | None]:
    if not match.bbox[2] or not match.bbox[3]:
        return "failed", match.reason or "no_match"
    score = match.score
    if score < settings.min_score:
        return "low_score", match.reason or "score_below_threshold"
    if jump_distance is not None and jump_distance > settings.max_jump_distance:
        return "rejected_jump", f"jump_distance={jump_distance:.1f}"
    if score >= settings.good_score:
        return "accepted", None
    return "accepted_low_score", None


def smooth_path(
    points: list[VideoTrackingPoint],
    window: int = 3,
) -> None:
    trackable = [
        p
        for p in points
        if p.status in ("accepted", "accepted_low_score", "relocked") and p.center is not None
    ]
    if len(trackable) < 2:
        for p in trackable:
            if p.center:
                p.smoothed_center = p.center
        return

    half = max(1, window // 2)
    for i, p in enumerate(points):
        if p.status not in ("accepted", "accepted_low_score", "relocked") or not p.center:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for j in range(max(0, i - half), min(len(points), i + half + 1)):
            q = points[j]
            if q.status in ("accepted", "accepted_low_score", "relocked") and q.center:
                xs.append(q.center[0])
                ys.append(q.center[1])
        if xs:
            p.smoothed_center = (sum(xs) / len(xs), sum(ys) / len(ys))
        else:
            p.smoothed_center = p.center


def _write_progress(
    output_dir: Path,
    status: str,
    processed_frames: int,
    total_to_process: int,
    current_frame_index: int,
    current_timestamp_sec: float,
    extra: dict | None = None,
) -> None:
    payload = {
        "status": status,
        "processed_frames": processed_frames,
        "total_frames_to_process": total_to_process,
        "current_frame_index": current_frame_index,
        "current_timestamp_sec": round(current_timestamp_sec, 3),
    }
    if extra:
        payload.update(extra)
    (output_dir / "progress.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _prepare_frame_match(
    frame_bgr: np.ndarray,
    config: MapLocatorConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, object]:
    minimap_raw = crop_minimap(frame_bgr, config.minimap_crop)
    minimap_inner = crop_inner_minimap(minimap_raw, config.minimap_border)
    valid_mask = build_valid_matching_mask(minimap_inner, border_px=max(8, config.minimap_border))
    minimap_feat = preprocess_matching_features(minimap_inner)
    return minimap_raw, minimap_inner, valid_mask, minimap_feat


def track_minimap_in_video(
    video_path: str,
    map_id: str,
    map_path: str,
    output_dir: str,
    crop_config: MinimapCropConfig,
    settings: VideoTrackingSettings | None = None,
    config: MapLocatorConfig | None = None,
    minimap_border: int = 12,
    max_frames: int | None = None,
    debug_video: bool = False,
    trajectory_image: bool = True,
) -> VideoTrackingResult:
    settings = settings or VideoTrackingSettings()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "frames").mkdir(exist_ok=True)
    (out / "minimaps").mkdir(exist_ok=True)
    (out / "matches").mkdir(exist_ok=True)

    if config is None:
        config = MapLocatorConfig(
            map_id=map_id,
            map_path=map_path,
            minimap_crop=crop_config,
            search_mode="window",
            minimap_border=minimap_border,
            min_score=settings.min_score,
            good_score=settings.good_score,
            debug_dir=str(out),
        )
    else:
        config = MapLocatorConfig(
            **{
                **config.__dict__,
                "map_id": map_id,
                "map_path": map_path,
                "minimap_crop": crop_config,
                "minimap_border": minimap_border,
                "min_score": settings.min_score,
                "good_score": settings.good_score,
                "debug_dir": str(out),
                "search_mode": "window",
            }
        )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"failed to open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_sec = total_frames / fps if fps > 0 else 0.0
    frame_step = _resolve_frame_step(settings, fps, total_frames)

    frame_indices: list[int] = []
    fi = 0
    while fi < total_frames:
        frame_indices.append(fi)
        fi += frame_step
        if max_frames is not None and len(frame_indices) >= max_frames:
            break

    total_to_process = len(frame_indices)
    full_map_bgr = _load_map_bgr(map_path)
    full_map_feat = preprocess_matching_features(full_map_bgr)
    config = _tracking_locator_config(config, settings)
    local_step = max(12, int(settings.local_coarse_step))
    if settings.fast_mode:
        local_step = max(local_step, 20)

    points: list[VideoTrackingPoint] = []
    prev_accepted_center: tuple[float, float] | None = None
    prev_accepted_ws: int | None = None
    debug_frames: list[tuple[np.ndarray, MinimapMatchResult, int, float]] = []

    (out / "job.json").write_text(
        json.dumps({"status": "processing", "map_id": map_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_progress(out, "processing", 0, total_to_process, 0, 0.0)

    for proc_i, frame_index in enumerate(frame_indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        ts = frame_index / fps if fps > 0 else 0.0

        if not ok or frame is None:
            points.append(
                VideoTrackingPoint(
                    frame_index=frame_index,
                    timestamp_sec=ts,
                    center=None,
                    smoothed_center=None,
                    bbox=None,
                    score=0.0,
                    window_size=None,
                    status="failed",
                    jump_distance=None,
                    reason="read_failed",
                )
            )
            continue

        minimap_raw, minimap_inner, valid_mask, minimap_feat = _prepare_frame_match(frame, config)

        if prev_accepted_center is None:
            match, _ = locate_by_window_search(minimap_feat, full_map_feat, config, valid_mask)
            jump_dist: float | None = 0.0
        else:
            ws_hint = _window_sizes_around(
                prev_accepted_ws or 300,
                full_map_feat.gray.shape[0],
                full_map_feat.gray.shape[1],
            )
            match, _ = locate_by_window_search_local(
                minimap_feat,
                full_map_feat,
                config,
                valid_mask,
                prev_accepted_center[0],
                prev_accepted_center[1],
                settings.search_radius,
                window_sizes_hint=ws_hint,
                coarse_step=local_step,
            )
            jump_dist = (
                _distance(match.center, prev_accepted_center)
                if match.bbox[2] > 0
                else None
            )

        status, reason = _classify_point(match, jump_dist, settings)

        if status in ("low_score", "rejected_jump", "failed") and settings.allow_global_relock:
            global_match, _ = locate_by_window_search(
                minimap_feat, full_map_feat, config, valid_mask
            )
            if global_match.score >= settings.relock_score and global_match.bbox[2] > 0:
                g_jump = (
                    _distance(global_match.center, prev_accepted_center)
                    if prev_accepted_center
                    else 0.0
                )
                if prev_accepted_center is None or g_jump <= settings.max_jump_distance:
                    match = global_match
                    jump_dist = g_jump
                    status = "relocked"
                    reason = None
                elif global_match.score >= settings.relock_score:
                    status = "rejected_jump"
                    reason = f"relock_jump={g_jump:.1f}"

        if status in ("low_score", "rejected_jump", "failed"):
            if match.score < settings.min_score and status != "rejected_jump":
                status = "low_score"
            elif status == "failed" and match.bbox[2] > 0:
                status = "skipped"
                reason = reason or "skipped"

        center = match.center if match.bbox[2] > 0 else None
        bbox = match.bbox if match.bbox[2] > 0 else None

        if status in ("accepted", "accepted_low_score", "relocked") and center:
            prev_accepted_center = center
            prev_accepted_ws = match.window_size or match.bbox[2]

        pt = VideoTrackingPoint(
            frame_index=frame_index,
            timestamp_sec=ts,
            center=center,
            smoothed_center=center,
            bbox=bbox,
            score=match.score,
            window_size=match.window_size or (bbox[2] if bbox else None),
            status=status,
            jump_distance=jump_dist,
            reason=reason,
        )
        points.append(pt)

        if settings.save_frame_debug:
            prefix = f"frame_{frame_index:06d}"
            save_debug_visualization(
                frame_bgr=frame,
                config=config,
                result=match,
                full_map_bgr=full_map_bgr,
                output_dir=out / "frames",
                prefix=prefix,
                frame_index=frame_index,
                timestamp_sec=ts,
            )
            from .io import imwrite_bgr

            imwrite_bgr(out / "minimaps" / f"{prefix}_minimap_raw.jpg", minimap_raw)

        if debug_video:
            debug_frames.append((frame.copy(), match, frame_index, ts))

        accepted = sum(
            1
            for p in points
            if p.status in ("accepted", "accepted_low_score", "relocked")
        )
        rejected = sum(1 for p in points if p.status == "rejected_jump")
        low = sum(1 for p in points if p.status == "low_score")
        avg_score = sum(p.score for p in points) / max(1, len(points))

        _write_progress(
            out,
            "processing",
            proc_i + 1,
            total_to_process,
            frame_index,
            ts,
            extra={
                "accepted_points": accepted,
                "rejected_jumps": rejected,
                "low_score": low,
                "average_score": round(avg_score, 4),
            },
        )

    cap.release()

    if settings.smoothing:
        smooth_path(points, settings.smooth_window)

    summary = {
        "processed_frames": len(points),
        "accepted_points": sum(
            1 for p in points if p.status in ("accepted", "accepted_low_score", "relocked")
        ),
        "rejected_jumps": sum(1 for p in points if p.status == "rejected_jump"),
        "low_score": sum(1 for p in points if p.status == "low_score"),
        "relocked": sum(1 for p in points if p.status == "relocked"),
        "skipped": sum(1 for p in points if p.status in ("skipped", "failed")),
        "average_score": round(sum(p.score for p in points) / max(1, len(points)), 4),
    }

    if trajectory_image:
        draw_trajectory_on_map(
            full_map_bgr,
            points,
            out / "trajectory_map.jpg",
            draw_rejected=True,
        )
        draw_trajectory_on_map(
            full_map_bgr,
            points,
            out / "trajectory_map_clean.jpg",
            draw_rejected=False,
        )

    if debug_video and debug_frames:
        write_debug_video(debug_frames, full_map_bgr, config, out / "debug_video.mp4", fps=min(fps / frame_step, 8.0))

    result_payload = {
        "map_id": map_id,
        "video": {
            "path": str(video_path),
            "fps": fps,
            "total_frames": total_frames,
            "duration_sec": duration_sec,
            "frame_step": frame_step,
        },
        "settings": {
            "minimap_crop": {
                "x": crop_config.x,
                "y": crop_config.y,
                "size": crop_config.size,
                "border": config.minimap_border,
            },
            "search_mode": config.search_mode,
            "min_score": settings.min_score,
            "good_score": settings.good_score,
            "max_jump_distance": settings.max_jump_distance,
            "search_radius": settings.search_radius,
            "allow_global_relock": settings.allow_global_relock,
            "relock_score": settings.relock_score,
            "smoothing": settings.smoothing,
            "fast_mode": settings.fast_mode,
            "save_frame_debug": settings.save_frame_debug,
            "sample_interval_sec": settings.sample_interval_sec,
        },
        "summary": summary,
        "points": [
            {
                "frame_index": p.frame_index,
                "timestamp_sec": p.timestamp_sec,
                "status": p.status,
                "score": p.score,
                "window_size": p.window_size,
                "jump_distance": p.jump_distance,
                "reason": p.reason,
                "bbox": (
                    {"x": p.bbox[0], "y": p.bbox[1], "w": p.bbox[2], "h": p.bbox[3]}
                    if p.bbox
                    else None
                ),
                "center": (
                    {"x": p.center[0], "y": p.center[1]} if p.center else None
                ),
                "smoothed_center": (
                    {"x": p.smoothed_center[0], "y": p.smoothed_center[1]}
                    if p.smoothed_center
                    else None
                ),
            }
            for p in points
        ],
    }
    (out / "result.json").write_text(
        json.dumps(result_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_progress(
        out,
        "completed",
        len(points),
        total_to_process,
        frame_indices[-1] if frame_indices else 0,
        duration_sec,
        extra=summary,
    )
    (out / "job.json").write_text(
        json.dumps({"status": "completed", "map_id": map_id, "summary": summary}, ensure_ascii=False),
        encoding="utf-8",
    )

    return VideoTrackingResult(
        map_id=map_id,
        video_path=str(video_path),
        frame_step=frame_step,
        fps=fps,
        total_frames=total_frames,
        duration_sec=duration_sec,
        points=points,
        summary=summary,
        output_dir=str(out),
    )
