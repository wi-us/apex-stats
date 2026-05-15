from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .preprocessing import (
    crop_inner_minimap,
    crop_minimap,
    features_to_bgr,
    preprocess_matching_features,
)
from .io import imwrite_bgr
from .types import (
    CandidateMatch,
    MapLocatorConfig,
    MinimapCropConfig,
    MinimapMatchResult,
    VideoTrackingPoint,
)


def draw_minimap_crop_on_frame(
    frame_bgr: np.ndarray,
    crop_config: MinimapCropConfig,
) -> np.ndarray:
    out = frame_bgr.copy()
    x, y, size = int(crop_config.x), int(crop_config.y), int(crop_config.size)
    cv2.rectangle(out, (x, y), (x + size, y + size), (0, 255, 255), 2)
    cv2.putText(
        out,
        "minimap crop",
        (x, max(20, y - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return out


def _score_color(score: float, is_best: bool) -> tuple[int, int, int]:
    if is_best:
        return (0, 255, 0)
    if score >= 0.55:
        return (0, 220, 255)
    if score >= 0.45:
        return (0, 180, 255)
    if score >= 0.35:
        return (0, 140, 255)
    return (0, 100, 255)


def draw_match_on_map(
    full_map_bgr: np.ndarray,
    result: MinimapMatchResult,
    candidates: Optional[list[CandidateMatch]] = None,
) -> np.ndarray:
    out = full_map_bgr.copy()
    if candidates:
        for i, cand in enumerate(candidates[:5]):
            x, y, w, h = cand.bbox
            color = _score_color(cand.score, i == 0)
            thickness = 2 if i == 0 else 1
            cv2.rectangle(out, (x, y), (x + w, y + h), color, thickness)
            label = f"#{i + 1} {cand.score:.3f}"
            if cand.window_size:
                label += f" ws={cand.window_size}"
            cv2.putText(
                out,
                label,
                (x, max(16, y - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                color,
                1,
                cv2.LINE_AA,
            )

    if result.bbox[2] > 0 and result.bbox[3] > 0:
        x, y, w, h = result.bbox
        color = (0, 255, 0) if result.ok else (0, 0, 255)
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 3)
        cx, cy = int(result.center[0]), int(result.center[1])
        cv2.circle(out, (cx, cy), 6, (255, 0, 255), -1)
        ws = result.window_size or w
        label = f"score={result.score:.3f} ws={ws}"
        if result.ambiguous:
            label += " AMB"
        if result.suspicious:
            label += " SUS"
        cv2.putText(
            out,
            label,
            (x, max(18, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return out


def draw_candidates_map(
    full_map_bgr: np.ndarray,
    candidates: list[CandidateMatch],
) -> np.ndarray:
    out = full_map_bgr.copy()
    for i, cand in enumerate(candidates[:5]):
        x, y, w, h = cand.bbox
        color = _score_color(cand.score, False)
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        cv2.putText(
            out,
            f"{cand.score:.3f}",
            (x + 4, y + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return out


def _to_bgr_panel(gray_or_bgr: np.ndarray) -> np.ndarray:
    if gray_or_bgr.ndim == 2:
        return cv2.cvtColor(gray_or_bgr, cv2.COLOR_GRAY2BGR)
    return gray_or_bgr


def compose_debug_panel(
    frame_with_crop: np.ndarray,
    minimap_raw: np.ndarray,
    minimap_processed: np.ndarray,
    map_with_match: np.ndarray,
    patch_bgr: np.ndarray,
    candidate_processed: np.ndarray,
    result: MinimapMatchResult,
    search_mode: str,
) -> np.ndarray:
    def _resize_to_height(img: np.ndarray, target_h: int) -> np.ndarray:
        h, w = img.shape[:2]
        if h == target_h:
            return img
        scale = target_h / max(1, h)
        return cv2.resize(img, (max(1, int(w * scale)), target_h), interpolation=cv2.INTER_AREA)

    panels = [
        _to_bgr_panel(frame_with_crop),
        _to_bgr_panel(minimap_raw),
        _to_bgr_panel(minimap_processed),
        _to_bgr_panel(map_with_match),
        _to_bgr_panel(patch_bgr),
        _to_bgr_panel(candidate_processed),
    ]
    target_h = 200
    panels = [_resize_to_height(p, target_h) for p in panels]
    row = np.hstack(panels)
    banner_h = 44
    banner = np.zeros((banner_h, row.shape[1], 3), dtype=np.uint8)
    ws = result.window_size or result.bbox[2]
    text = (
        f"map={result.map_id} mode={search_mode} ok={result.ok} score={result.score:.3f} "
        f"ws={ws} center=({result.center[0]:.0f},{result.center[1]:.0f}) "
        f"ambiguous={result.ambiguous} suspicious={result.suspicious}"
    )
    if result.reason:
        text += f" | {result.reason}"
    cv2.putText(
        banner,
        text[:140],
        (8, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    bbox_note = f"bbox={result.bbox[2]}x{result.bbox[3]}"
    cv2.putText(
        banner,
        bbox_note,
        (8, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        (200, 220, 255),
        1,
        cv2.LINE_AA,
    )
    return np.vstack([banner, row])


def save_debug_visualization(
    frame_bgr: np.ndarray,
    config: MapLocatorConfig,
    result: MinimapMatchResult,
    full_map_bgr: np.ndarray,
    output_dir: Path | str,
    prefix: str = "frame",
    frame_index: Optional[int] = None,
    timestamp_sec: Optional[float] = None,
) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    minimap_raw = crop_minimap(frame_bgr, config.minimap_crop)
    minimap_inner = crop_inner_minimap(minimap_raw, config.minimap_border)
    minimap_feat = preprocess_matching_features(minimap_inner)
    minimap_processed_vis = features_to_bgr(minimap_feat)

    frame_marked = draw_minimap_crop_on_frame(frame_bgr, config.minimap_crop)
    map_marked = draw_match_on_map(full_map_bgr, result, result.candidates)

    patch_bgr = np.zeros((64, 64, 3), dtype=np.uint8)
    candidate_processed = np.zeros_like(minimap_processed_vis)
    if result.bbox[2] > 0 and result.bbox[3] > 0:
        x, y, w, h = result.bbox
        fh, fw = full_map_bgr.shape[:2]
        x2, y2 = min(fw, x + w), min(fh, y + h)
        if x2 > x and y2 > y:
            patch_bgr = full_map_bgr[y:y2, x:x2].copy()
            mh, mw = minimap_feat.gray.shape[:2]
            resized = cv2.resize(patch_bgr, (mw, mh), interpolation=cv2.INTER_AREA)
            cand_feat = preprocess_matching_features(resized)
            candidate_processed = features_to_bgr(cand_feat)

    panel = compose_debug_panel(
        frame_marked,
        minimap_raw,
        minimap_processed_vis,
        map_marked,
        patch_bgr,
        candidate_processed,
        result,
        config.search_mode,
    )

    imwrite_bgr(out / f"{prefix}_debug.jpg", panel)
    imwrite_bgr(out / f"{prefix}_frame_crop.jpg", frame_marked)
    imwrite_bgr(out / f"{prefix}_minimap_raw.jpg", minimap_raw)
    imwrite_bgr(out / f"{prefix}_minimap_processed.jpg", minimap_processed_vis)
    imwrite_bgr(out / f"{prefix}_map_match.jpg", map_marked)
    imwrite_bgr(out / f"{prefix}_matched_patch.jpg", patch_bgr)
    imwrite_bgr(out / f"{prefix}_candidate_processed.jpg", candidate_processed)

    if result.candidates:
        candidates_img = draw_candidates_map(full_map_bgr, result.candidates)
        imwrite_bgr(out / f"{prefix}_candidates.jpg", candidates_img)
        top_img = draw_match_on_map(full_map_bgr, result, result.candidates)
        imwrite_bgr(out / "top_candidates.jpg", top_img)

    top_candidates_json = [
        {
            "x": c.bbox[0],
            "y": c.bbox[1],
            "w": c.bbox[2],
            "h": c.bbox[3],
            "score": c.score,
            "window_size": c.window_size or c.bbox[2],
            "scale": c.scale,
        }
        for c in (result.candidates or [])
    ]

    payload = {
        "map_id": config.map_id,
        "frame_index": frame_index,
        "timestamp_sec": timestamp_sec,
        "search_mode": config.search_mode,
        "minimap_crop": {
            "x": config.minimap_crop.x,
            "y": config.minimap_crop.y,
            "size": config.minimap_crop.size,
        },
        "match": {
            "ok": result.ok,
            "score": result.score,
            "scale": result.scale,
            "window_size": result.window_size,
            "ambiguous": result.ambiguous,
            "suspicious": result.suspicious,
            "bbox": {
                "x": result.bbox[0],
                "y": result.bbox[1],
                "w": result.bbox[2],
                "h": result.bbox[3],
            },
            "center": {"x": result.center[0], "y": result.center[1]},
            "reason": result.reason,
        },
        "top_candidates": top_candidates_json,
        "candidates": [
            {
                "bbox": {"x": c.bbox[0], "y": c.bbox[1], "w": c.bbox[2], "h": c.bbox[3]},
                "score": c.score,
                "scale": c.scale,
                "window_size": c.window_size or c.bbox[2],
                "tile_id": c.tile_id,
            }
            for c in (result.candidates or [])
        ],
    }
    json_path = out / f"{prefix}_result.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    if result.candidates:
        (out / "top_candidates.json").write_text(
            json.dumps(top_candidates_json, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return json_path


_STATUS_COLORS_BGR = {
    "accepted": (34, 214, 24),
    "accepted_low_score": (32, 176, 255),
    "relocked": (247, 85, 168),
    "rejected_jump": (68, 68, 239),
    "low_score": (148, 116, 100),
    "skipped": (128, 128, 128),
    "failed": (80, 80, 80),
}


def draw_trajectory_on_map(
    full_map_bgr: np.ndarray,
    points: list[VideoTrackingPoint],
    output_path: Path | str,
    draw_rejected: bool = True,
) -> None:
    out = full_map_bgr.copy()
    path_pts: list[tuple[int, int]] = []

    for i, p in enumerate(points):
        use = p.smoothed_center or p.center
        if not use:
            continue
        if p.status not in ("accepted", "accepted_low_score", "relocked"):
            if not draw_rejected:
                continue
        cx, cy = int(use[0]), int(use[1])
        color = _STATUS_COLORS_BGR.get(p.status, (200, 200, 200))

        if p.status in ("accepted", "accepted_low_score", "relocked"):
            path_pts.append((cx, cy))
            cv2.circle(out, (cx, cy), 5, color, -1)
            if i % 5 == 0:
                cv2.putText(
                    out,
                    f"{p.timestamp_sec:.0f}s",
                    (cx + 6, cy - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    color,
                    1,
                    cv2.LINE_AA,
                )
        elif draw_rejected and p.status == "rejected_jump" and p.center:
            rcx, rcy = int(p.center[0]), int(p.center[1])
            cv2.drawMarker(out, (rcx, rcy), color, markerType=cv2.MARKER_CROSS, markerSize=12, thickness=2)
        elif draw_rejected and p.status in ("low_score", "skipped", "failed") and p.center:
            lx, ly = int(p.center[0]), int(p.center[1])
            cv2.circle(out, (lx, ly), 3, color, 1)

    if len(path_pts) >= 2:
        for j in range(1, len(path_pts)):
            cv2.line(out, path_pts[j - 1], path_pts[j], (24, 214, 232), 2, cv2.LINE_AA)
        cv2.arrowedLine(
            out,
            path_pts[-2],
            path_pts[-1],
            (255, 91, 18),
            2,
            tipLength=0.2,
        )

    legend_y = 24
    for status, color in _STATUS_COLORS_BGR.items():
        cv2.circle(out, (16, legend_y), 5, color, -1)
        cv2.putText(
            out,
            status,
            (28, legend_y + 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )
        legend_y += 18

    imwrite_bgr(output_path, out)


def write_debug_video(
    frames: list[tuple[np.ndarray, MinimapMatchResult, int, float]],
    full_map_bgr: np.ndarray,
    config: MapLocatorConfig,
    output_path: Path | str,
    fps: float = 5.0,
) -> None:
    if not frames:
        return
    writer = None
    out_path = Path(output_path)
    try:
        for frame, result, frame_index, ts in frames:
            left = draw_minimap_crop_on_frame(frame, config.minimap_crop)
            right = draw_match_on_map(full_map_bgr, result, result.candidates)
            lh, lw = left.shape[:2]
            rh, rw = right.shape[:2]
            target_h = 540
            left_s = cv2.resize(left, (int(lw * target_h / lh), target_h))
            right_s = cv2.resize(right, (int(rw * target_h / rh), target_h))
            combined = np.hstack([left_s, right_s])
            ws = result.window_size or result.bbox[2]
            overlay = (
                f"frame={frame_index} t={ts:.1f}s score={result.score:.3f} "
                f"ws={ws} bbox={result.bbox[2]}x{result.bbox[3]}"
            )
            cv2.putText(
                combined,
                overlay,
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            if writer is None:
                h, w = combined.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
            writer.write(combined)
    finally:
        if writer is not None:
            writer.release()
