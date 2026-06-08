#!/usr/bin/env python3
"""Shared helpers for checking motion_detect anchors against GT points."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[3].parent
MAPS_DIR = ROOT / "scripts" / "tracking" / "shared" / "canonical_maps"


def slot_sort_key(label: str) -> tuple[int, float, str]:
    base, _, t_part = str(label).partition("@")
    try:
        n = int(base.split("_")[-1])
    except Exception:
        n = 10**9
    try:
        t = float(t_part.rstrip("s")) if t_part else 0.0
    except Exception:
        t = 0.0
    return n, t, str(label)


def group_gt_points(gt_points: list[dict]) -> list[dict]:
    """Group duplicate GT clicks for the same slot at the same time as alternatives."""
    buckets: dict[tuple[str, float], list[tuple[float, float]]] = defaultdict(list)
    for p in gt_points:
        sid = str(p["slot_id"])
        t = round(float(p.get("t", 0.0)), 2)
        x, y = p["world_xy"]
        buckets[(sid, t)].append((float(x), float(y)))

    times_by_slot: dict[str, set[float]] = defaultdict(set)
    for sid, t in buckets:
        times_by_slot[sid].add(t)

    out = []
    for (sid, t), pts in buckets.items():
        label = sid if len(times_by_slot[sid]) == 1 else f"{sid}@{t:.2f}s"
        out.append({"slot_id": sid, "t": t, "points": pts, "label": label})
    out.sort(key=lambda g: slot_sort_key(g["label"]))
    return out


def _fit_affine(points: list[dict]) -> np.ndarray:
    src = np.array([p["minimap_px"] for p in points], dtype=np.float64)
    dst = np.array([p["canonical_px"] for p in points], dtype=np.float64)
    n = len(src)
    m = np.zeros((2 * n, 6))
    b = np.zeros(2 * n)
    for i, ((x, y), (cx, cy)) in enumerate(zip(src, dst)):
        m[2 * i] = [x, y, 1, 0, 0, 0]
        m[2 * i + 1] = [0, 0, 0, x, y, 1]
        b[2 * i] = cx
        b[2 * i + 1] = cy
    a, *_ = np.linalg.lstsq(m, b, rcond=None)
    return np.array([[a[0], a[1], a[2]], [a[3], a[4], a[5]]], dtype=np.float64)


def load_minimap_affine(map_name: str = "storm_point", maps_dir: Path = MAPS_DIR) -> np.ndarray | None:
    path = maps_dir / f"{map_name}.minimap_affine.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    matrix = raw.get("affine", {}).get("matrix")
    if matrix:
        return np.array(matrix, dtype=np.float64)
    return _fit_affine(raw["points"])


def map_minimap_to_canonical(affine: np.ndarray | None, x: float, y: float) -> tuple[float, float]:
    if affine is None:
        return float(x), float(y)
    return (
        float(affine[0, 0] * x + affine[0, 1] * y + affine[0, 2]),
        float(affine[1, 0] * x + affine[1, 1] * y + affine[1, 2]),
    )


def extract_motion_points(doc: dict, map_name: str = "storm_point") -> tuple[list[dict], float]:
    """Extract nested motion_detect trajectory points as canonical-map pixels."""
    fps = float(doc.get("fps") or 60.0)
    affine = load_minimap_affine(map_name)
    points: list[dict] = []

    for result in doc.get("results") or []:
        slot = result.get("slot")
        slot_id = f"slot_{int(slot)}" if slot is not None else str(result.get("slot_id") or "")
        for tr in result.get("moving") or []:
            source = tr.get("source")
            for p in tr.get("points") or []:
                if isinstance(p, (list, tuple)) and len(p) >= 3:
                    frame, mx, my = int(p[0]), float(p[1]), float(p[2])
                    t = frame / fps
                elif isinstance(p, dict):
                    xy = p.get("xy") or p.get("minimap_px") or p.get("canonical_px") or p.get("world") or p.get("pos")
                    if not xy or len(xy) < 2:
                        continue
                    mx, my = float(xy[0]), float(xy[1])
                    frame = p.get("frame")
                    t = float(p.get("t", (float(frame) / fps) if isinstance(frame, (int, float)) else 0.0))
                else:
                    continue
                cx, cy = map_minimap_to_canonical(affine, mx, my)
                points.append({"t": t, "frame": frame, "slot_id": slot_id, "canonical_xy": (cx, cy), "source": source})

    return points, fps


def summarize_anchor_coverage(
    anchors_path: Path,
    gt_points: list[dict],
    end_sec: float,
    map_name: str = "storm_point",
    radius: float = 200.0,
    suggested_step: int = 5,
) -> dict:
    out = {"t_min": 0.0, "t_max": 0.0, "total_pts": 0, "total_raw_pts": 0, "per_slot": {}}
    try:
        doc = json.loads(Path(anchors_path).read_text(encoding="utf-8"))
    except Exception as e:
        out["error"] = f"parse:{e}"
        return out

    pts, fps = extract_motion_points(doc, map_name=map_name)
    frame_times = [float(f) / fps for f in (doc.get("frames_used") or []) if isinstance(f, (int, float))]
    point_times = [p["t"] for p in pts]
    all_times = frame_times + point_times
    if all_times:
        out["t_min"] = min(all_times)
        out["t_max"] = max(all_times)
    else:
        start_sec = float(doc.get("start_sec") or 0.0)
        out["t_min"] = start_sec
        out["t_max"] = start_sec

    win_pts = [p for p in pts if 0.0 <= p["t"] <= end_sec]
    out["total_pts"] = len(win_pts)
    out["total_raw_pts"] = len(pts)
    out["fps"] = fps
    out["suggested_window_step5"] = int(math.ceil(end_sec * fps / max(1, suggested_step))) + 30

    by_slot_gt: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for gp in gt_points:
        gx, gy = gp["world_xy"]
        by_slot_gt[str(gp["slot_id"])].append((float(gx), float(gy)))

    for sid, gt_xy in by_slot_gt.items():
        near_count = 0
        nearest = None
        for p in win_pts:
            px, py = p["canonical_xy"]
            d = min(math.hypot(px - gx, py - gy) for gx, gy in gt_xy)
            if nearest is None or d < nearest:
                nearest = d
            if d <= radius:
                near_count += 1
        out["per_slot"][sid] = {"n_near": near_count, "nearest_px": nearest}
    return out