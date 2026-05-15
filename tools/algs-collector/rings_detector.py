from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MAP_ROI_X = 420
MAP_ROI_Y = 0
MAP_ROI_WIDTH = 1080
MAP_ROI_HEIGHT = 1080

RING_COUNTDOWN_MASK_HSV = {
    "h": (0, 179),
    "s": (0, 255),
    "v": (0, 74),
}
RING_DIAMETERS_METERS: dict[int, float] = {
    1: 1100.0,
    2: 650.0,
    3: 400.0,
    4: 200.0,
    5: 100.0,
    6: 0.05,
}
RING_PHASE_SEQUENCE: list[tuple[str, int, float]] = [
    ("countdown", 1, 2 * 60 + 0),
    ("closing", 1, 4 * 60 + 20),
    ("countdown", 2, 2 * 60 + 20),
    ("closing", 2, 1 * 60 + 30),
    ("countdown", 3, 1 * 60 + 40),
    ("closing", 3, 2 * 60 + 0),
    ("countdown", 4, 1 * 60 + 40),
    ("closing", 4, 0 * 60 + 45),
    ("countdown", 5, 1 * 60 + 20),
    ("closing", 5, 0 * 60 + 55),
    ("countdown", 6, 1 * 60 + 5),
    ("closing", 6, 2 * 60 + 0),
]
RING_PHASE_INDEX: dict[tuple[str, int], int] = {
    (str(event_type), int(ring_number)): int(idx)
    for idx, (event_type, ring_number, _duration_sec) in enumerate(RING_PHASE_SEQUENCE)
}
RING_MIN_DIAMETER_BASE_METERS = 900.0
MAP_RING_RADIUS_MAP_OVERRIDE: dict[str, dict[int, float]] = {
    "mp_storm_point": {
        1: 329.0,
        2: 162.0,
    }
}
MAP_RING_RADIUS_METERS_OVERRIDE: dict[str, dict[int, float]] = {
    "mp_storm_point": {
        1: 550.0,
        2: 275.0,
    }
}
DEFAULT_METERS_TO_MAP_UNITS = 0.94
RING_RADIUS_TOLERANCE_RATIO = 0.35
RING_RADIUS_TOLERANCE_ABS = 35.0
NESTED_TOLERANCE_MAP_UNITS = 35.0


def set_map_context(map_mp_id: str | None) -> None:
    map_id = str(map_mp_id or "")
    setattr(expected_ring_radius_map_units, "_map_mp_id", map_id)
    setattr(ring_radius_meters, "_map_mp_id", map_id)


def parse_center_json(center_json: str | None) -> tuple[float, float] | None:
    if not center_json:
        return None
    try:
        payload = json.loads(center_json)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    x = float(payload.get("x", np.nan))
    y = float(payload.get("y", np.nan))
    if not np.isfinite(x) or not np.isfinite(y):
        return None
    return x, y


def ring_phase_duration_seconds(event_type: str, ring_number: int) -> float | None:
    idx = RING_PHASE_INDEX.get((str(event_type), int(ring_number)))
    if idx is None:
        return None
    return float(RING_PHASE_SEQUENCE[idx][2])


def extrapolate_ring_pattern(
    rings_rows: list[dict[str, Any]],
    target_ts_start: float,
    expected_radius: float | None,
) -> tuple[tuple[float, float], float]:
    if not rings_rows:
        base_radius = float(expected_radius if expected_radius is not None else 120.0)
        return (540.0, 540.0), max(1.0, base_radius)
    if len(rings_rows) == 1:
        center1 = parse_center_json(str(rings_rows[-1].get("center")) if rings_rows[-1].get("center") is not None else None)
        r1 = float(rings_rows[-1].get("radius", expected_radius or 120.0) or (expected_radius or 120.0))
        if center1 is None:
            center1 = (540.0, 540.0)
        return (
            (
                float(np.clip(center1[0], 0.0, 1079.0)),
                float(np.clip(center1[1], 0.0, 1079.0)),
            ),
            max(1.0, float(expected_radius if expected_radius is not None else r1)),
        )
    prev2 = rings_rows[-2]
    prev1 = rings_rows[-1]
    c0 = parse_center_json(str(prev2.get("center")) if prev2.get("center") is not None else None)
    c1 = parse_center_json(str(prev1.get("center")) if prev1.get("center") is not None else None)
    t0 = float(prev2.get("time_start", 0.0) or 0.0)
    t1 = float(prev1.get("time_start", 0.0) or 0.0)
    r0 = float(prev2.get("radius", 0.0) or 0.0)
    r1 = float(prev1.get("radius", 0.0) or 0.0)
    if c0 is None:
        c0 = c1 if c1 is not None else (540.0, 540.0)
    if c1 is None:
        c1 = c0
    dt = max(1e-3, float(t1 - t0))
    ahead = max(0.0, float(target_ts_start - t1))
    lookback_sec = min(2.0, dt)
    p0x = float(c1[0] - ((c1[0] - c0[0]) * (lookback_sec / dt)))
    p0y = float(c1[1] - ((c1[1] - c0[1]) * (lookback_sec / dt)))
    p1x = float(c1[0])
    p1y = float(c1[1])
    sample_count = 10
    ts_samples = np.linspace(0.0, lookback_sec, num=sample_count, dtype=np.float64)
    xs = np.linspace(p0x, p1x, num=sample_count, dtype=np.float64)
    ys = np.linspace(p0y, p1y, num=sample_count, dtype=np.float64)
    vx = float(np.polyfit(ts_samples, xs, 1)[0]) if sample_count >= 2 else float((c1[0] - c0[0]) / dt)
    vy = float(np.polyfit(ts_samples, ys, 1)[0]) if sample_count >= 2 else float((c1[1] - c0[1]) / dt)
    cx = float(c1[0] + (vx * ahead))
    cy = float(c1[1] + (vy * ahead))
    dr = float(r1 - r0)
    k = ahead / dt
    rr = float(r1 + (dr * k))
    if expected_radius is not None and np.isfinite(float(expected_radius)):
        rr = float(expected_radius)
    return (
        (
            float(np.clip(cx, 0.0, 1079.0)),
            float(np.clip(cy, 0.0, 1079.0)),
        ),
        max(1.0, float(rr)),
    )


def backfill_previous_ring_pattern(
    rings_rows: list[dict[str, Any]],
    *,
    target_ring_number: int,
    target_ts_start: float,
    expected_radius: float | None,
) -> tuple[tuple[float, float], float]:
    future_rows = sorted(
        (
            row
            for row in rings_rows
            if int(row.get("ring_number", 0) or 0) > int(target_ring_number)
        ),
        key=lambda row: (
            int(row.get("ring_number", 0) or 0),
            float(row.get("time_start", 0.0) or 0.0),
        ),
    )
    if not future_rows:
        base_radius = float(expected_radius if expected_radius is not None else 120.0)
        return (540.0, 540.0), max(1.0, base_radius)

    next_row = future_rows[0]
    next_center = parse_center_json(str(next_row.get("center")) if next_row.get("center") is not None else None)
    if next_center is None:
        next_center = (540.0, 540.0)
    next_radius = float(next_row.get("radius", expected_radius or 120.0) or (expected_radius or 120.0))

    if len(future_rows) == 1:
        return (
            (
                float(np.clip(next_center[0], 0.0, 1079.0)),
                float(np.clip(next_center[1], 0.0, 1079.0)),
            ),
            max(1.0, float(expected_radius if expected_radius is not None else next_radius)),
        )

    after_row = future_rows[1]
    after_center = parse_center_json(str(after_row.get("center")) if after_row.get("center") is not None else None)
    if after_center is None:
        after_center = next_center

    next_start = float(next_row.get("time_start", 0.0) or 0.0)
    after_start = float(after_row.get("time_start", next_start) or next_start)
    future_dt = max(1e-3, float(after_start - next_start))
    back_dt = max(0.0, float(next_start - float(target_ts_start)))
    # Use the observed center trend in reverse, capped to avoid huge jumps when ring1 is far before visible data.
    back_scale = float(np.clip(back_dt / future_dt, 0.0, 1.75))
    vx = float(after_center[0] - next_center[0])
    vy = float(after_center[1] - next_center[1])
    cx = float(next_center[0] - vx * back_scale)
    cy = float(next_center[1] - vy * back_scale)

    return (
        (
            float(np.clip(cx, 0.0, 1079.0)),
            float(np.clip(cy, 0.0, 1079.0)),
        ),
        max(1.0, float(expected_radius if expected_radius is not None else next_radius)),
    )


def ring_radius_meters(ring_number: int) -> float | None:
    map_mp_id = str(getattr(ring_radius_meters, "_map_mp_id", "") or "")
    if map_mp_id:
        per_map = MAP_RING_RADIUS_METERS_OVERRIDE.get(map_mp_id)
        if isinstance(per_map, dict) and int(ring_number) in per_map:
            return float(per_map[int(ring_number)])
    if ring_number in RING_DIAMETERS_METERS:
        return float(RING_DIAMETERS_METERS[ring_number]) * 0.5
    if ring_number > 0:
        return float(RING_DIAMETERS_METERS[max(RING_DIAMETERS_METERS.keys())]) * 0.5
    return None


def expected_ring_radius_map_units(ring_number: int, meters_to_map_units: float) -> float | None:
    map_mp_id = str(getattr(expected_ring_radius_map_units, "_map_mp_id", "") or "")
    if map_mp_id:
        overrides = MAP_RING_RADIUS_MAP_OVERRIDE.get(map_mp_id)
        if isinstance(overrides, dict) and int(ring_number) in overrides:
            return float(overrides[int(ring_number)])
    meters_radius = ring_radius_meters(int(ring_number))
    if meters_radius is None:
        return None
    return float(meters_radius) * max(1e-6, float(meters_to_map_units))


def min_ring_radius_map_units(ring_number: int, meters_to_map_units: float) -> float | None:
    base_d = float(RING_DIAMETERS_METERS.get(1, 1100.0))
    ring_d = float(RING_DIAMETERS_METERS.get(int(ring_number), RING_DIAMETERS_METERS[max(RING_DIAMETERS_METERS.keys())]))
    if base_d <= 0.0 or ring_d <= 0.0:
        return None
    ratio = float(RING_MIN_DIAMETER_BASE_METERS) / base_d
    min_d_meters = ring_d * ratio
    min_r_meters = 0.5 * min_d_meters
    return float(min_r_meters) * max(1e-6, float(meters_to_map_units))


def is_ring_nested(prev_center_json: str | None, prev_radius: float | None, center_json: str | None, radius: float | None) -> bool:
    if prev_radius is None or radius is None:
        return True
    prev_center = parse_center_json(prev_center_json)
    center = parse_center_json(center_json)
    if prev_center is None or center is None:
        return True
    if float(radius) >= (float(prev_radius) - 1.0):
        return False
    center_dist = float(np.hypot(center[0] - prev_center[0], center[1] - prev_center[1]))
    return (center_dist + float(radius)) <= (float(prev_radius) + NESTED_TOLERANCE_MAP_UNITS)


def clamp_ring_inside_parent(
    *,
    prev_center_json: str | None,
    prev_radius: float | None,
    center_json: str | None,
    radius: float | None,
    min_radius: float | None = None,
) -> tuple[str | None, float | None, bool, float]:
    if center_json is None or radius is None or prev_center_json is None or prev_radius is None:
        return center_json, radius, False, 0.0
    prev_center = parse_center_json(prev_center_json)
    center = parse_center_json(center_json)
    if prev_center is None or center is None:
        return center_json, radius, False, 0.0
    current_radius = float(max(1.0, radius))
    center_dist = float(np.hypot(center[0] - prev_center[0], center[1] - prev_center[1]))
    max_allowed = float(prev_radius) + float(NESTED_TOLERANCE_MAP_UNITS) - center_dist
    if not np.isfinite(max_allowed):
        return center_json, radius, False, 0.0
    target_radius = float(min(current_radius, max_allowed))
    if min_radius is not None and np.isfinite(float(min_radius)):
        target_radius = float(max(float(min_radius), target_radius))
    target_radius = float(max(1.0, target_radius))
    if target_radius >= current_radius:
        return center_json, float(round(current_radius, 2)), False, 0.0
    return center_json, float(round(target_radius, 2)), True, float(current_radius - target_radius)


def ring_minimap_bounds(frame: np.ndarray) -> tuple[int, int, int, int]:
    h, w = frame.shape[:2]
    x1 = int(np.clip(MAP_ROI_X, 0, max(0, w - 1)))
    y1 = int(np.clip(MAP_ROI_Y, 0, max(0, h - 1)))
    x2 = int(np.clip(MAP_ROI_X + MAP_ROI_WIDTH, x1 + 1, w))
    y2 = int(np.clip(MAP_ROI_Y + MAP_ROI_HEIGHT, y1 + 1, h))
    return x1, y1, x2, y2


def _fit_circle_from_contour(cnt: np.ndarray) -> tuple[float, float, float] | None:
    if cnt is None or len(cnt) < 5:
        return None
    pts = cnt.reshape(-1, 2).astype(np.float32)
    cx = float(np.mean(pts[:, 0]))
    cy = float(np.mean(pts[:, 1]))
    dists = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
    if dists.size == 0:
        return None
    radius = float(np.median(dists))
    if not np.isfinite(radius) or radius <= 0:
        return None
    return cx, cy, radius


def _fit_circle_from_points(points: np.ndarray) -> tuple[float, float, float] | None:
    if points is None or points.shape[0] < 3:
        return None
    pts = points.astype(np.float64)
    x = pts[:, 0]
    y = pts[:, 1]
    a = np.column_stack((x, y, np.ones_like(x)))
    b = -(x * x + y * y)
    try:
        sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    except Exception:
        return None
    aa, cc, dd = float(sol[0]), float(sol[1]), float(sol[2])
    cx = -0.5 * aa
    cy = -0.5 * cc
    r_sq = (cx * cx) + (cy * cy) - dd
    if not np.isfinite(r_sq) or r_sq <= 0:
        return None
    radius = float(np.sqrt(r_sq))
    if not np.isfinite(radius) or radius <= 0:
        return None
    return cx, cy, radius


def _circle_fit_error(points: np.ndarray, cx: float, cy: float, radius: float) -> float:
    if points is None or points.shape[0] == 0:
        return 9999.0
    pts = points.astype(np.float64)
    dists = np.sqrt((pts[:, 0] - float(cx)) ** 2 + (pts[:, 1] - float(cy)) ** 2)
    if dists.size == 0:
        return 9999.0
    err = float(np.mean(np.abs(dists - float(radius))))
    return err if np.isfinite(err) else 9999.0


def _dedupe_lines(line_items: list[tuple[float, np.ndarray]]) -> list[tuple[float, np.ndarray]]:
    kept: list[tuple[float, np.ndarray]] = []
    for length, line in line_items:
        x1l, y1l, x2l, y2l = [float(v) for v in line]
        ang = float(np.degrees(np.arctan2(y2l - y1l, x2l - x1l)))
        midx = 0.5 * (x1l + x2l)
        midy = 0.5 * (y1l + y2l)
        duplicate = False
        for _, kline in kept:
            kx1, ky1, kx2, ky2 = [float(v) for v in kline]
            kang = float(np.degrees(np.arctan2(ky2 - ky1, kx2 - kx1)))
            kmidx = 0.5 * (kx1 + kx2)
            kmidy = 0.5 * (ky1 + ky2)
            dang = abs(ang - kang)
            dang = min(dang, abs(180.0 - dang))
            dmid = float(np.hypot(midx - kmidx, midy - kmidy))
            if dang < 8.0 and dmid < 20.0:
                duplicate = True
                break
        if not duplicate:
            kept.append((length, line))
    return kept


def _radial_boundary_circle(
    safe_mask: np.ndarray,
    *,
    cx_seed: float,
    cy_seed: float,
) -> tuple[float, float, float, float] | None:
    h, w = safe_mask.shape[:2]
    cx0 = float(np.clip(cx_seed, 0.0, max(0, w - 1)))
    cy0 = float(np.clip(cy_seed, 0.0, max(0, h - 1)))
    pts: list[list[float]] = []
    max_r = float(max(h, w))
    for deg in range(0, 360, 5):
        ang = np.deg2rad(float(deg))
        last_in: tuple[float, float] | None = None
        for rr in np.linspace(1.0, max_r, num=220):
            px = cx0 + (float(np.cos(ang)) * float(rr))
            py = cy0 + (float(np.sin(ang)) * float(rr))
            ix = int(np.clip(round(px), 0, w - 1))
            iy = int(np.clip(round(py), 0, h - 1))
            if safe_mask[iy, ix] > 0:
                last_in = (float(ix), float(iy))
            elif last_in is not None:
                break
        if last_in is not None:
            pts.append([last_in[0], last_in[1]])
    if len(pts) < 10:
        return None
    arr = np.asarray(pts, dtype=np.float64)
    fit = _fit_circle_from_points(arr)
    if fit is None:
        return None
    cx, cy, radius = fit
    err = _circle_fit_error(arr, cx, cy, radius)
    return float(cx), float(cy), float(radius), float(err)


def _circle_from_3_points(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> tuple[float, float, float] | None:
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    x3, y3 = float(p3[0]), float(p3[1])
    d = 2.0 * ((x1 * (y2 - y3)) + (x2 * (y3 - y1)) + (x3 * (y1 - y2)))
    if abs(d) < 1e-6:
        return None
    ux = (((x1 * x1 + y1 * y1) * (y2 - y3)) + ((x2 * x2 + y2 * y2) * (y3 - y1)) + ((x3 * x3 + y3 * y3) * (y1 - y2))) / d
    uy = (((x1 * x1 + y1 * y1) * (x3 - x2)) + ((x2 * x2 + y2 * y2) * (x1 - x3)) + ((x3 * x3 + y3 * y3) * (x2 - x1))) / d
    rr = float(np.hypot(x1 - ux, y1 - uy))
    if not np.isfinite(rr) or rr <= 0.0:
        return None
    return float(ux), float(uy), float(rr)


def _fit_circle_ransac(points: np.ndarray, *, iterations: int = 220) -> tuple[float, float, float, np.ndarray] | None:
    if points is None or points.shape[0] < 12:
        return None
    pts = points.astype(np.float64)
    n = int(pts.shape[0])
    rng = np.random.default_rng(int(n * 13 + 17))
    best_circle: tuple[float, float, float] | None = None
    best_inliers = np.zeros((n,), dtype=bool)
    best_score = -1e9
    tries = max(80, min(int(iterations), n * 4))
    for _ in range(tries):
        ids = rng.choice(n, size=3, replace=False)
        circ = _circle_from_3_points(pts[ids[0]], pts[ids[1]], pts[ids[2]])
        if circ is None:
            continue
        cx, cy, radius = circ
        if radius <= 1.0:
            continue
        d = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
        residual = np.abs(d - radius)
        thr = max(3.5, min(14.0, radius * 0.06))
        inliers = residual <= thr
        in_count = int(np.count_nonzero(inliers))
        if in_count < max(10, int(n * 0.18)):
            continue
        score = float(in_count) - (float(np.mean(residual[inliers])) * 1.8)
        if score > best_score:
            best_score = score
            best_circle = (float(cx), float(cy), float(radius))
            best_inliers = inliers
    if best_circle is None:
        return None
    in_pts = pts[best_inliers]
    fit = _fit_circle_from_points(in_pts if in_pts.shape[0] >= 3 else pts)
    if fit is None:
        return None
    cx, cy, radius = fit
    d = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
    residual = np.abs(d - radius)
    thr = max(3.5, min(14.0, radius * 0.06))
    inliers = residual <= thr
    if int(np.count_nonzero(inliers)) < max(10, int(n * 0.2)):
        return None
    return float(cx), float(cy), float(radius), inliers


def _arc_annulus_score(edge_mask: np.ndarray, cx: float, cy: float, radius: float) -> tuple[float, float]:
    h, w = edge_mask.shape[:2]
    samples = 220
    band = max(2.0, min(12.0, radius * 0.07))
    on_hits = 0
    in_hits = 0
    out_hits = 0
    valid = 0
    for k in range(samples):
        ang = (2.0 * np.pi * float(k)) / float(samples)
        ca = float(np.cos(ang))
        sa = float(np.sin(ang))
        px = int(round(cx + (radius * ca)))
        py = int(round(cy + (radius * sa)))
        p_in_x = int(round(cx + ((radius - band) * ca)))
        p_in_y = int(round(cy + ((radius - band) * sa)))
        p_out_x = int(round(cx + ((radius + band) * ca)))
        p_out_y = int(round(cy + ((radius + band) * sa)))
        if px < 0 or py < 0 or px >= w or py >= h:
            continue
        if p_in_x < 0 or p_in_y < 0 or p_in_x >= w or p_in_y >= h:
            continue
        if p_out_x < 0 or p_out_y < 0 or p_out_x >= w or p_out_y >= h:
            continue
        valid += 1
        if edge_mask[py, px] > 0:
            on_hits += 1
        if edge_mask[p_in_y, p_in_x] > 0:
            in_hits += 1
        if edge_mask[p_out_y, p_out_x] > 0:
            out_hits += 1
    if valid <= 0:
        return 0.0, 0.0
    on_ratio = float(on_hits) / float(valid)
    bg_ratio = 0.5 * ((float(in_hits) / float(valid)) + (float(out_hits) / float(valid)))
    score = float(on_ratio - bg_ratio)
    return float(score), float(on_ratio)


def _fit_circle_from_visible_arc(
    contours: list[np.ndarray],
    *,
    edge_mask: np.ndarray,
    roi_w: int,
    roi_h: int,
    expected_center: tuple[float, float] | None = None,
    expected_radius: float | None = None,
    min_radius_map_units: float | None = None,
) -> dict[str, float] | None:
    best: dict[str, float] | None = None
    best_score = -1e9
    roi_area = max(1.0, float(roi_w * roi_h))
    exp_px: tuple[float, float] | None = None
    if expected_center is not None:
        exp_px = (
            (float(expected_center[0]) / 1080.0) * float(roi_w),
            (float(expected_center[1]) / 1080.0) * float(roi_h),
        )
    exp_r_px: float | None = None
    if expected_radius is not None and np.isfinite(float(expected_radius)):
        exp_r_px = (float(expected_radius) / 1080.0) * float(roi_w)
    min_r_px: float | None = None
    if min_radius_map_units is not None and np.isfinite(float(min_radius_map_units)):
        min_r_px = (float(min_radius_map_units) / 1080.0) * float(roi_w)

    contour_candidates = sorted((cnt for cnt in contours if cnt is not None and len(cnt) >= 10), key=cv2.contourArea, reverse=True)[:14]
    for cnt in contour_candidates:
        area = float(cv2.contourArea(cnt))
        if area < max(120.0, roi_area * 0.0018):
            continue
        pts = cnt.reshape(-1, 2).astype(np.float64)
        eps = 0.008 * float(cv2.arcLength(cnt, True))
        approx = cv2.approxPolyDP(cnt, eps, True).reshape(-1, 2).astype(np.float64)
        if approx.shape[0] >= 10:
            pts = np.vstack([pts, approx])
        fit = _fit_circle_ransac(pts)
        if fit is None:
            continue
        cx, cy, radius, inliers = fit
        if not np.isfinite(radius) or radius <= 1.0:
            continue
        if min_r_px is not None and radius < float(min_r_px):
            continue
        inlier_pts = pts[inliers] if int(np.count_nonzero(inliers)) >= 8 else pts
        dists = np.sqrt((inlier_pts[:, 0] - cx) ** 2 + (inlier_pts[:, 1] - cy) ** 2)
        if dists.size < 8:
            continue
        residual = np.abs(dists - radius)
        fit_err = float(np.mean(residual))
        residual_p95 = float(np.percentile(residual, 95))
        if residual_p95 > max(12.0, radius * 0.12):
            continue
        angles = np.mod(np.arctan2(inlier_pts[:, 1] - cy, inlier_pts[:, 0] - cx), 2.0 * np.pi)
        angles = np.sort(angles)
        wrapped = np.concatenate([angles, [angles[0] + (2.0 * np.pi)]])
        max_gap = float(np.max(np.diff(wrapped)))
        coverage_deg = float(np.degrees((2.0 * np.pi) - max_gap))
        if coverage_deg < 52.0:
            continue
        bins = np.floor((angles / (2.0 * np.pi)) * 8.0).astype(np.int32)
        sector_count = int(np.unique(np.clip(bins, 0, 7)).size)
        if sector_count < 3:
            continue
        annulus_score, edge_on_ratio = _arc_annulus_score(edge_mask, cx, cy, radius)
        if annulus_score < 0.02:
            continue
        score = (
            (coverage_deg * 1.25)
            + (float(np.count_nonzero(inliers)) * 0.22)
            + (annulus_score * 180.0)
            - (fit_err * 3.0)
            - (residual_p95 * 1.8)
        )
        if exp_px is not None:
            score -= float(np.hypot(cx - exp_px[0], cy - exp_px[1])) * 0.2
        if exp_r_px is not None:
            score -= abs(float(radius) - float(exp_r_px)) * 1.0
        if score > best_score:
            best_score = score
            best = {
                "cx": float(cx),
                "cy": float(cy),
                "radius": float(radius),
                "fit_error": float(fit_err),
                "coverage_deg": float(coverage_deg),
                "inliers": float(np.count_nonzero(inliers)),
                "sector_count": float(sector_count),
                "annulus_score": float(annulus_score),
                "edge_on_ratio": float(edge_on_ratio),
                "residual_p95": float(residual_p95),
                "score": float(score),
            }
    return best


def build_ring_detection_views(
    frame: np.ndarray,
    *,
    countdown_zone_mode: bool = False,
) -> dict[str, Any] | None:
    x1, y1, x2, y2 = ring_minimap_bounds(frame)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mh, mw = roi.shape[:2]
    map_circle_mask = np.zeros((mh, mw), dtype=np.uint8)
    circle_r = int(max(1.0, min(float(mh), float(mw)) * 0.49))
    cv2.circle(map_circle_mask, (mw // 2, mh // 2), circle_r, 255, thickness=-1)
    if countdown_zone_mode:
        hmin, hmax = RING_COUNTDOWN_MASK_HSV["h"]
        smin, smax = RING_COUNTDOWN_MASK_HSV["s"]
        vmin, vmax = RING_COUNTDOWN_MASK_HSV["v"]
        ring_hsv_lower = np.array([hmin, smin, vmin], dtype=np.uint8)
        ring_hsv_upper = np.array([hmax, smax, vmax], dtype=np.uint8)
        mask = cv2.inRange(hsv, ring_hsv_lower, ring_hsv_upper)
    else:
        ring_hsv_lower = np.array([0, 0, 48], dtype=np.uint8)
        ring_hsv_upper = np.array([180, 125, 175], dtype=np.uint8)
        mask_hsv = cv2.inRange(hsv, ring_hsv_lower, ring_hsv_upper)
        mask_gray = cv2.inRange(gray, 45, 180)
        mask = cv2.bitwise_and(mask_hsv, mask_gray)
    mask = cv2.bitwise_and(mask, map_circle_mask)
    kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_small)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_small)
    mask_connected = mask.copy()
    if countdown_zone_mode:
        kernel_big = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
        mask_connected = cv2.morphologyEx(mask_connected, cv2.MORPH_CLOSE, kernel_big)
        mask_connected = cv2.dilate(mask_connected, kernel_big, iterations=1)
    ring_like = cv2.GaussianBlur(mask_connected if countdown_zone_mode else mask, (9, 9), 1.4)
    return {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "roi": roi,
        "hsv": hsv,
        "mask": mask,
        "mask_connected": mask_connected,
        "map_circle_mask": map_circle_mask,
        "ring_like": ring_like,
        "countdown_zone_mode": bool(countdown_zone_mode),
    }


def detect_ring_geometry_in_frame(
    frame: np.ndarray,
    zones_payload: dict[str, Any] | None,
    expected_center: tuple[float, float] | None = None,
    expected_radius: float | None = None,
    min_radius_map_units: float | None = None,
    countdown_zone_mode: bool = False,
    strict_line_profile: bool = False,
    arc_only_mode: bool = False,
) -> tuple[dict[str, Any] | None, float]:
    views = build_ring_detection_views(frame, countdown_zone_mode=bool(countdown_zone_mode))
    if views is None:
        return None, 0.0
    x1, y1, x2, y2 = int(views["x1"]), int(views["y1"]), int(views["x2"]), int(views["y2"])
    roi = views["roi"]
    mask = views["mask"]
    mask_connected = views.get("mask_connected", mask)
    map_circle_mask = views.get("map_circle_mask")
    if not isinstance(map_circle_mask, np.ndarray):
        map_circle_mask = np.full(mask.shape, 255, dtype=np.uint8)
    ring_like = views["ring_like"]
    contour_mask = mask_connected if bool(countdown_zone_mode) else mask
    contours, _ = cv2.findContours(contour_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    use_arc_primary = bool(arc_only_mode or countdown_zone_mode)
    if use_arc_primary:
        edge_mask = cv2.Canny(contour_mask, 40, 140)
        arc_fit = _fit_circle_from_visible_arc(
            contours,
            edge_mask=edge_mask,
            roi_w=roi.shape[1],
            roi_h=roi.shape[0],
            expected_center=expected_center,
            expected_radius=expected_radius,
            min_radius_map_units=min_radius_map_units,
        )
        if arc_fit is None and bool(arc_only_mode):
            return None, 0.0
        if arc_fit is not None:
            cx = float(arc_fit["cx"])
            cy = float(arc_fit["cy"])
            radius = float(arc_fit["radius"])
            fit_error = float(arc_fit["fit_error"])
            coverage_deg = float(arc_fit["coverage_deg"])
            arc_points = int(round(float(arc_fit["inliers"])))
            annulus_score = float(arc_fit.get("annulus_score", 0.0))
            residual_p95 = float(arc_fit.get("residual_p95", 9999.0))
            sector_count = int(round(float(arc_fit.get("sector_count", 0.0))))
            if coverage_deg >= 52.0 and sector_count >= 3 and residual_p95 <= max(12.0, radius * 0.12):
                frame_x = float(x1 + cx)
                frame_y = float(y1 + cy)
                frame_r = float(radius)
                map_x = ((frame_x - x1) / max(1.0, float(x2 - x1))) * 1080.0
                map_y = ((frame_y - y1) / max(1.0, float(y2 - y1))) * 1080.0
                map_r = (frame_r / max(1.0, float(x2 - x1))) * 1080.0
                best = {
                    "x": round(float(np.clip(map_x, 0.0, 1079.0)), 2),
                    "y": round(float(np.clip(map_y, 0.0, 1079.0)), 2),
                    "radius": round(float(max(1.0, map_r)), 2),
                    "radius_px": round(float(max(1.0, frame_r)), 3),
                    "diameter_map_units": round(float(max(2.0, map_r * 2.0)), 3),
                    "diameter_px": round(float(max(2.0, frame_r * 2.0)), 3),
                    "geometry_source": "arc_boundary",
                    "fit_error": round(float(fit_error), 3),
                    "angle_deg": round(float(coverage_deg), 2),
                    "line_len": round(float(arc_points), 2),
                    "score": round(float(arc_fit.get("score", 0.0)), 3),
                    "arc_coverage": round(float(coverage_deg), 3),
                    "arc_inliers": int(max(0, arc_points)),
                    "arc_annulus_score": round(float(annulus_score), 4),
                    "arc_residual_p95": round(float(residual_p95), 3),
                    "arc_sector_count": int(max(0, sector_count)),
                }
                setattr(detect_ring_geometry_in_frame, "_last_map_center", (float(best["x"]), float(best["y"])))
                conf = float(np.clip((best["radius"] / 1080.0) * 2.8, 0.25, 1.0))
                conf *= float(np.clip(coverage_deg / 180.0, 0.55, 1.0))
                conf *= float(np.clip((annulus_score + 0.1) / 0.3, 0.55, 1.0))
                return best, float(np.clip(conf, 0.2, 1.0))
            if bool(arc_only_mode):
                return None, 0.0
    circles = cv2.HoughCircles(
        ring_like,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(20, roi.shape[1] // 8),
        param1=90,
        param2=36,
        minRadius=max(10, int(min(roi.shape[0], roi.shape[1]) * 0.12)),
        maxRadius=max(20, int(min(roi.shape[0], roi.shape[1]) * 0.50)),
    )
    best: dict[str, Any] | None = None
    best_score = -1e9
    candidates: list[dict[str, float | str]] = []
    prev_map_center = getattr(detect_ring_geometry_in_frame, "_last_map_center", None)
    if circles is not None:
        for circle in circles[0]:
            candidates.append(
                {
                    "cx": float(circle[0]),
                    "cy": float(circle[1]),
                    "radius": float(circle[2]),
                    "source": "hough",
                    "area_ratio": 0.0,
                    "fit_error": 0.0,
                    "angle_deg": 0.0,
                    "line_len": 0.0,
                }
            )
    largest_cnt = None
    largest_area = 0.0
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area > largest_area:
            largest_area = area
            largest_cnt = cnt
        if area < max(300.0, float(roi.shape[0] * roi.shape[1]) * 0.01):
            continue
        fit = _fit_circle_from_contour(cnt)
        if fit is None:
            continue
        cx, cy, radius = fit
        candidates.append(
            {
                "cx": float(cx),
                "cy": float(cy),
                "radius": float(radius),
                "source": "contour",
                "area_ratio": float(area / max(1.0, float(roi.shape[0] * roi.shape[1]))),
                "fit_error": float(_circle_fit_error(cnt.reshape(-1, 2), cx, cy, radius)),
                "angle_deg": 0.0,
                "line_len": 0.0,
            }
        )
    if largest_cnt is not None and largest_area > 0.0:
        bx, by, bw, bh = cv2.boundingRect(largest_cnt)
        if bw > 0 and bh > 0:
            largest_radius = 0.5 * max(float(bw), float(bh))
            diagonal_radius = 0.5 * float(np.hypot(float(bw), float(bh)))
            cx_diag = float(bx) + 0.5 * float(bw)
            cy_diag = float(by) + 0.5 * float(bh)
            largest_pts = largest_cnt.reshape(-1, 2)
            largest_bbox_err = float(_circle_fit_error(largest_pts, cx_diag, cy_diag, largest_radius))
            diagonal_bbox_err = float(_circle_fit_error(largest_pts, cx_diag, cy_diag, diagonal_radius))
            candidates.append(
                {
                    "cx": cx_diag,
                    "cy": cy_diag,
                    "radius": float(max(1.0, largest_radius)),
                    "source": "largest_bbox",
                    "area_ratio": float(largest_area / max(1.0, float(roi.shape[0] * roi.shape[1]))),
                    "fit_error": float(max(0.5, largest_bbox_err)),
                    "angle_deg": 90.0,
                    "line_len": float(max(bw, bh)),
                }
            )
            candidates.append(
                {
                    "cx": cx_diag,
                    "cy": cy_diag,
                    "radius": float(max(1.0, diagonal_radius)),
                    "source": "diagonal_bbox",
                    "area_ratio": float(largest_area / max(1.0, float(roi.shape[0] * roi.shape[1]))),
                    "fit_error": float(max(0.5, diagonal_bbox_err)),
                    "angle_deg": 90.0,
                    "line_len": float(np.hypot(float(bw), float(bh))),
                }
            )
    if bool(countdown_zone_mode):
        safe_mask = cv2.bitwise_and(cv2.bitwise_not(mask_connected), map_circle_mask)
        safe_mask = cv2.morphologyEx(safe_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        safe_mask = cv2.morphologyEx(safe_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
        n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(safe_mask, connectivity=8)
        roi_area = max(1.0, float(roi.shape[0] * roi.shape[1]))
        for label_idx in range(1, int(n_labels)):
            sx = int(stats[label_idx, cv2.CC_STAT_LEFT])
            sy = int(stats[label_idx, cv2.CC_STAT_TOP])
            sw = int(stats[label_idx, cv2.CC_STAT_WIDTH])
            sh = int(stats[label_idx, cv2.CC_STAT_HEIGHT])
            area = float(stats[label_idx, cv2.CC_STAT_AREA])
            if sw <= 0 or sh <= 0 or area < 500.0:
                continue
            component = np.where(labels == label_idx, 255, 0).astype(np.uint8)
            touches_border = sx <= 1 or sy <= 1 or (sx + sw) >= (roi.shape[1] - 1) or (sy + sh) >= (roi.shape[0] - 1)
            if touches_border:
                continue
            contours_safe, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours_safe:
                continue
            cnt = max(contours_safe, key=cv2.contourArea)
            fit = _fit_circle_from_contour(cnt)
            if fit is None:
                continue
            cx, cy, radius = fit
            candidates.append(
                {
                    "cx": float(cx),
                    "cy": float(cy),
                    "radius": float(radius),
                    "source": "safe_component",
                    "area_ratio": float(area / roi_area),
                    "fit_error": float(_circle_fit_error(cnt.reshape(-1, 2), cx, cy, radius)),
                    "angle_deg": 0.0,
                    "line_len": 0.0,
                }
            )
        edges = cv2.Canny(contour_mask, 60, 160)
        min_line_len = max(30, int(min(roi.shape[0], roi.shape[1]) * (0.25 if bool(strict_line_profile) else 0.18)))
        if expected_radius is not None and np.isfinite(float(expected_radius)):
            expected_radius_px = (float(expected_radius) / 1080.0) * float(roi.shape[1])
            dyn_factor = 0.62 if bool(strict_line_profile) else 0.48
            min_line_len = max(min_line_len, int(max(24.0, expected_radius_px * dyn_factor)))
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=80, minLineLength=min_line_len, maxLineGap=20)
        line_items: list[tuple[float, np.ndarray]] = []
        if lines is not None:
            for li in lines:
                x1l, y1l, x2l, y2l = [float(v) for v in li[0]]
                length = float(np.hypot(x2l - x1l, y2l - y1l))
                if length < 20.0:
                    continue
                line_items.append((length, np.asarray([x1l, y1l, x2l, y2l], dtype=np.float64)))
        line_items.sort(key=lambda p: p[0], reverse=True)
        top_lines = _dedupe_lines(line_items)[:20]
        for i in range(len(top_lines)):
            len1, l1 = top_lines[i]
            v1 = np.asarray([l1[2] - l1[0], l1[3] - l1[1]], dtype=np.float64)
            n1 = float(np.linalg.norm(v1))
            if n1 <= 1e-6:
                continue
            v1 /= n1
            for j in range(i + 1, len(top_lines)):
                len2, l2 = top_lines[j]
                v2 = np.asarray([l2[2] - l2[0], l2[3] - l2[1]], dtype=np.float64)
                n2 = float(np.linalg.norm(v2))
                if n2 <= 1e-6:
                    continue
                v2 /= n2
                cosang = float(np.clip(abs(float(np.dot(v1, v2))), -1.0, 1.0))
                angle_deg = float(np.degrees(np.arccos(cosang)))
                min_angle = 50.0 if bool(strict_line_profile) else 30.0
                if angle_deg < min_angle:
                    continue
                points = np.asarray([[l1[0], l1[1]], [l1[2], l1[3]], [l2[0], l2[1]], [l2[2], l2[3]]], dtype=np.float64)
                fit = _fit_circle_from_points(points)
                if fit is None:
                    continue
                cx, cy, radius = fit
                if radius <= 1.0:
                    continue
                fit_err = float(_circle_fit_error(points, cx, cy, radius))
                if fit_err > (20.0 if bool(strict_line_profile) else 40.0):
                    continue
                endpoint_dists = np.sqrt((points[:, 0] - cx) ** 2 + (points[:, 1] - cy) ** 2)
                dist_mean = float(np.mean(endpoint_dists)) if endpoint_dists.size > 0 else 0.0
                dist_std = float(np.std(endpoint_dists)) if endpoint_dists.size > 0 else 0.0
                dist_cv = float(dist_std / max(1e-6, dist_mean))
                if dist_cv > (0.28 if bool(strict_line_profile) else 0.42):
                    continue
                quadrants: set[tuple[int, int]] = set()
                for px, py in points:
                    qx = 1 if float(px) >= float(cx) else -1
                    qy = 1 if float(py) >= float(cy) else -1
                    quadrants.add((qx, qy))
                if len(quadrants) < (3 if bool(strict_line_profile) else 2):
                    continue
                candidates.append(
                    {
                        "cx": float(cx),
                        "cy": float(cy),
                        "radius": float(radius),
                        "source": "line_pair",
                        "area_ratio": float(largest_area / max(1.0, float(roi.shape[0] * roi.shape[1]))),
                        "fit_error": fit_err,
                        "angle_deg": float(angle_deg),
                        "line_len": float(0.5 * (len1 + len2)),
                        "distance_cv": float(dist_cv),
                    }
                )
                break
            else:
                continue
            break
        line_pair_candidates = [c for c in candidates if str(c.get("source", "")) == "line_pair"]
        line_pair_best_err = min([float(c.get("fit_error", 9999.0) or 9999.0) for c in line_pair_candidates], default=9999.0)
        should_try_radial = (not line_pair_candidates) or (line_pair_best_err > (12.0 if bool(strict_line_profile) else 24.0))
        if should_try_radial:
            seed_points: list[tuple[float, float]] = []
            if candidates:
                best_seed = max(candidates, key=lambda c: float(c.get("area_ratio", 0.0)))
                seed_points.append((float(best_seed.get("cx", roi.shape[1] * 0.5)), float(best_seed.get("cy", roi.shape[0] * 0.5))))
            if expected_center is not None:
                seed_points.append(((float(expected_center[0]) / 1080.0) * float(roi.shape[1]), (float(expected_center[1]) / 1080.0) * float(roi.shape[0])))
            if isinstance(prev_map_center, tuple) and len(prev_map_center) == 2:
                px, py = float(prev_map_center[0]), float(prev_map_center[1])
                seed_points.append(((px / 1080.0) * float(roi.shape[1]), (py / 1080.0) * float(roi.shape[0])))
            seed_points.append((float(roi.shape[1] * 0.5), float(roi.shape[0] * 0.5)))
            deduped_seeds: list[tuple[float, float]] = []
            for sx0, sy0 in seed_points:
                if not deduped_seeds:
                    deduped_seeds.append((sx0, sy0))
                    continue
                if all(float(np.hypot(sx0 - sx1, sy0 - sy1)) > 8.0 for sx1, sy1 in deduped_seeds):
                    deduped_seeds.append((sx0, sy0))
            best_radial: tuple[float, float, float, float] | None = None
            for seed_x, seed_y in deduped_seeds:
                radial_fit = _radial_boundary_circle(safe_mask, cx_seed=seed_x, cy_seed=seed_y)
                if radial_fit is None:
                    continue
                if best_radial is None or float(radial_fit[3]) < float(best_radial[3]):
                    best_radial = radial_fit
            if best_radial is not None:
                rcx, rcy, rr, rerr = best_radial
                candidates.append(
                    {
                        "cx": float(rcx),
                        "cy": float(rcy),
                        "radius": float(rr),
                        "source": "radial_boundary",
                        "area_ratio": float(largest_area / max(1.0, float(roi.shape[0] * roi.shape[1]))),
                        "fit_error": float(rerr),
                        "angle_deg": 0.0,
                        "line_len": 0.0,
                    }
                )
    if bool(countdown_zone_mode) and bool(strict_line_profile):
        preferred_sources = {"line_pair", "radial_boundary", "safe_component"}
        if any(str(c.get("source", "")) in preferred_sources for c in candidates):
            candidates = [c for c in candidates if str(c.get("source", "")) in preferred_sources]
    for cand in candidates:
        cx = float(cand.get("cx", 0.0))
        cy = float(cand.get("cy", 0.0))
        radius = float(cand.get("radius", 0.0))
        source = str(cand.get("source", ""))
        source_area_ratio = float(cand.get("area_ratio", 0.0))
        fit_error = float(cand.get("fit_error", 0.0) or 0.0)
        angle_deg = float(cand.get("angle_deg", 0.0) or 0.0)
        line_len = float(cand.get("line_len", 0.0) or 0.0)
        distance_cv = float(cand.get("distance_cv", 0.0) or 0.0)
        area = float(np.pi * (radius ** 2))
        area_ratio = area / max(1.0, float(roi.shape[0] * roi.shape[1]))
        max_area_ratio = 0.98 if (bool(countdown_zone_mode) and source in {"safe_component", "largest_bbox", "diagonal_bbox", "line_pair", "radial_boundary"}) else 0.75
        if area_ratio < 0.01 or area_ratio > max_area_ratio:
            continue
        center_dist = np.hypot(cx - (roi.shape[1] / 2.0), cy - (roi.shape[0] / 2.0))
        score = radius - (center_dist * 0.08)
        score += float(source_area_ratio) * 120.0
        score -= min(120.0, fit_error * 2.0)
        if bool(countdown_zone_mode) and source == "largest_bbox":
            score += -220.0 if bool(strict_line_profile) else 80.0
        if bool(countdown_zone_mode) and source == "diagonal_bbox":
            score += -180.0 if bool(strict_line_profile) else 140.0
        if bool(countdown_zone_mode) and source == "safe_component":
            score += 70.0 if bool(strict_line_profile) else 120.0
        if bool(countdown_zone_mode) and source == "line_pair":
            score += 260.0 if bool(strict_line_profile) else 180.0
            score += min(60.0, max(0.0, angle_deg - 30.0) * 1.5)
            score += min(50.0, line_len * 0.05)
            score -= min(80.0, distance_cv * 120.0)
        if bool(countdown_zone_mode) and source == "radial_boundary":
            score += 180.0 if bool(strict_line_profile) else 135.0
        if expected_radius is not None and np.isfinite(float(expected_radius)):
            score -= abs(float(radius) - float(expected_radius)) * 2.4
        if expected_center is not None:
            exp_map_x, exp_map_y = expected_center
            exp_px = (float(exp_map_x) / 1080.0) * float(x2 - x1)
            exp_py = (float(exp_map_y) / 1080.0) * float(y2 - y1)
            score -= float(np.hypot(cx - exp_px, cy - exp_py)) * 0.25
        if score <= best_score:
            continue
        best_score = score
        frame_x = float(x1 + cx)
        frame_y = float(y1 + cy)
        frame_r = float(radius)
        map_x = ((frame_x - x1) / max(1.0, float(x2 - x1))) * 1080.0
        map_y = ((frame_y - y1) / max(1.0, float(y2 - y1))) * 1080.0
        map_r = (frame_r / max(1.0, float(x2 - x1))) * 1080.0
        best = {
            "x": round(float(np.clip(map_x, 0.0, 1079.0)), 2),
            "y": round(float(np.clip(map_y, 0.0, 1079.0)), 2),
            "radius": round(float(max(1.0, map_r)), 2),
            "radius_px": round(float(max(1.0, frame_r)), 3),
            "diameter_map_units": round(float(max(2.0, map_r * 2.0)), 3),
            "diameter_px": round(float(max(2.0, frame_r * 2.0)), 3),
            "geometry_source": source,
            "fit_error": round(float(fit_error), 3),
            "angle_deg": round(float(angle_deg), 2),
            "line_len": round(float(line_len), 2),
            "score": round(float(score), 3),
        }
    if best is None:
        return None, 0.0
    setattr(detect_ring_geometry_in_frame, "_last_map_center", (float(best["x"]), float(best["y"])))
    conf = float(np.clip((best["radius"] / 1080.0) * 3.0, 0.25, 1.0))
    return best, conf
