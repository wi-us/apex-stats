from __future__ import annotations

import json
import sqlite3
import argparse
from pathlib import Path
from typing import Any
import time

import cv2
import numpy as np
import rings_detector as rd

DEBUG_LOG_PATH = Path("debug-dd3473.log")
DEBUG_SESSION_ID = "dd3473"
DEBUG_LOG_PATH_B91 = Path("debug-b91ec1.log")
DEBUG_SESSION_ID_B91 = "b91ec1"


def _debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    try:
        payload = {
            "sessionId": DEBUG_SESSION_ID,
            "runId": str(run_id),
            "hypothesisId": str(hypothesis_id),
            "location": str(location),
            "message": str(message),
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _debug_log_b91(run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    try:
        payload = {
            "sessionId": DEBUG_SESSION_ID_B91,
            "runId": str(run_id),
            "hypothesisId": str(hypothesis_id),
            "location": str(location),
            "message": str(message),
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with DEBUG_LOG_PATH_B91.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS CameraTrack (
            game_id INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            ring_status TEXT NOT NULL,
            ring_number INTEGER NOT NULL DEFAULT 0,
            geometry_source TEXT NOT NULL DEFAULT 'unknown',
            geometry_confidence REAL NOT NULL DEFAULT 0.0,
            center_x REAL NOT NULL DEFAULT 0.0,
            center_y REAL NOT NULL DEFAULT 0.0,
            camera_x REAL NOT NULL DEFAULT 540.0,
            camera_y REAL NOT NULL DEFAULT 540.0,
            radius REAL NOT NULL DEFAULT 0.0,
            x1 REAL NOT NULL DEFAULT 0.0,
            x2 REAL NOT NULL DEFAULT 0.0,
            y1 REAL NOT NULL DEFAULT 0.0,
            y2 REAL NOT NULL DEFAULT 0.0,
            dyn_x1 REAL NOT NULL DEFAULT 0.0,
            dyn_x2 REAL NOT NULL DEFAULT 0.0,
            dyn_y1 REAL NOT NULL DEFAULT 0.0,
            dyn_y2 REAL NOT NULL DEFAULT 0.0,
            zoom_ratio REAL NOT NULL DEFAULT 1.0,
            camera_size REAL NOT NULL DEFAULT 1080.0,
            speed_x1 REAL NOT NULL DEFAULT 0.0,
            speed_x2 REAL NOT NULL DEFAULT 0.0,
            speed_y1 REAL NOT NULL DEFAULT 0.0,
            speed_y2 REAL NOT NULL DEFAULT 0.0,
            x1x2 REAL NOT NULL DEFAULT 0.0,
            y1y2 REAL NOT NULL DEFAULT 0.0,
            delta_x1x2 REAL NOT NULL DEFAULT 0.0,
            delta_y1y2 REAL NOT NULL DEFAULT 0.0,
            speed_abs_max REAL NOT NULL DEFAULT 0.0,
            speed_abs_sum REAL NOT NULL DEFAULT 0.0,
            jump_score REAL NOT NULL DEFAULT 0.0,
            jump_flag INTEGER NOT NULL DEFAULT 0,
            move_dx REAL NOT NULL DEFAULT 0.0,
            move_dy REAL NOT NULL DEFAULT 0.0,
            move_dist REAL NOT NULL DEFAULT 0.0,
            move_side TEXT NOT NULL DEFAULT 'none',
            roi_x1 REAL NOT NULL DEFAULT 0.0,
            roi_y1 REAL NOT NULL DEFAULT 0.0,
            roi_x2 REAL NOT NULL DEFAULT 1079.0,
            roi_y2 REAL NOT NULL DEFAULT 1079.0
        );
        CREATE INDEX IF NOT EXISTS idx_cameratrack_game_id ON CameraTrack(game_id);
        CREATE INDEX IF NOT EXISTS idx_cameratrack_game_ts ON CameraTrack(game_id, timestamp);
        """
    )
    existing_columns = {str(row[1]).lower() for row in conn.execute("PRAGMA table_info(CameraTrack)")}
    required_columns: dict[str, str] = {
        "ring_number": "ALTER TABLE CameraTrack ADD COLUMN ring_number INTEGER NOT NULL DEFAULT 0",
        "geometry_source": "ALTER TABLE CameraTrack ADD COLUMN geometry_source TEXT NOT NULL DEFAULT 'unknown'",
        "geometry_confidence": "ALTER TABLE CameraTrack ADD COLUMN geometry_confidence REAL NOT NULL DEFAULT 0.0",
        "center_x": "ALTER TABLE CameraTrack ADD COLUMN center_x REAL NOT NULL DEFAULT 0.0",
        "center_y": "ALTER TABLE CameraTrack ADD COLUMN center_y REAL NOT NULL DEFAULT 0.0",
        "camera_x": "ALTER TABLE CameraTrack ADD COLUMN camera_x REAL NOT NULL DEFAULT 540.0",
        "camera_y": "ALTER TABLE CameraTrack ADD COLUMN camera_y REAL NOT NULL DEFAULT 540.0",
        "radius": "ALTER TABLE CameraTrack ADD COLUMN radius REAL NOT NULL DEFAULT 0.0",
        "dyn_x1": "ALTER TABLE CameraTrack ADD COLUMN dyn_x1 REAL NOT NULL DEFAULT 0.0",
        "dyn_x2": "ALTER TABLE CameraTrack ADD COLUMN dyn_x2 REAL NOT NULL DEFAULT 0.0",
        "dyn_y1": "ALTER TABLE CameraTrack ADD COLUMN dyn_y1 REAL NOT NULL DEFAULT 0.0",
        "dyn_y2": "ALTER TABLE CameraTrack ADD COLUMN dyn_y2 REAL NOT NULL DEFAULT 0.0",
        "delta_x1x2": "ALTER TABLE CameraTrack ADD COLUMN delta_x1x2 REAL NOT NULL DEFAULT 0.0",
        "delta_y1y2": "ALTER TABLE CameraTrack ADD COLUMN delta_y1y2 REAL NOT NULL DEFAULT 0.0",
        "speed_abs_max": "ALTER TABLE CameraTrack ADD COLUMN speed_abs_max REAL NOT NULL DEFAULT 0.0",
        "speed_abs_sum": "ALTER TABLE CameraTrack ADD COLUMN speed_abs_sum REAL NOT NULL DEFAULT 0.0",
        "jump_score": "ALTER TABLE CameraTrack ADD COLUMN jump_score REAL NOT NULL DEFAULT 0.0",
        "jump_flag": "ALTER TABLE CameraTrack ADD COLUMN jump_flag INTEGER NOT NULL DEFAULT 0",
        "move_dx": "ALTER TABLE CameraTrack ADD COLUMN move_dx REAL NOT NULL DEFAULT 0.0",
        "move_dy": "ALTER TABLE CameraTrack ADD COLUMN move_dy REAL NOT NULL DEFAULT 0.0",
        "move_dist": "ALTER TABLE CameraTrack ADD COLUMN move_dist REAL NOT NULL DEFAULT 0.0",
        "move_side": "ALTER TABLE CameraTrack ADD COLUMN move_side TEXT NOT NULL DEFAULT 'none'",
        "roi_x1": "ALTER TABLE CameraTrack ADD COLUMN roi_x1 REAL NOT NULL DEFAULT 0.0",
        "roi_y1": "ALTER TABLE CameraTrack ADD COLUMN roi_y1 REAL NOT NULL DEFAULT 0.0",
        "roi_x2": "ALTER TABLE CameraTrack ADD COLUMN roi_x2 REAL NOT NULL DEFAULT 1079.0",
        "roi_y2": "ALTER TABLE CameraTrack ADD COLUMN roi_y2 REAL NOT NULL DEFAULT 1079.0",
    }
    for col, alter_sql in required_columns.items():
        if col not in existing_columns:
            conn.execute(alter_sql)


def upsert_camera_rows(conn: sqlite3.Connection, game_id: int, camera_rows: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM CameraTrack WHERE game_id = ?", (int(game_id),))
    if not camera_rows:
        return
    payload = [
        (
            int(game_id),
            float(row.get("timestamp", 0.0) or 0.0),
            str(row.get("ring_status", "countdown") or "countdown"),
            int(row.get("ring_number", 0) or 0),
            str(row.get("geometry_source", "unknown") or "unknown"),
            float(row.get("geometry_confidence", 0.0) or 0.0),
            float(row.get("center_x", 0.0) or 0.0),
            float(row.get("center_y", 0.0) or 0.0),
            float(row.get("camera_x", 540.0) or 540.0),
            float(row.get("camera_y", 540.0) or 540.0),
            float(row.get("radius", 0.0) or 0.0),
            float(row.get("x1", 0.0) or 0.0),
            float(row.get("x2", 0.0) or 0.0),
            float(row.get("y1", 0.0) or 0.0),
            float(row.get("y2", 0.0) or 0.0),
            float(row.get("dyn_x1", 0.0) or 0.0),
            float(row.get("dyn_x2", 0.0) or 0.0),
            float(row.get("dyn_y1", 0.0) or 0.0),
            float(row.get("dyn_y2", 0.0) or 0.0),
            float(row.get("zoom_ratio", 1.0) or 1.0),
            float(row.get("camera_size", 1080.0) or 1080.0),
            float(row.get("speed_x1", 0.0) or 0.0),
            float(row.get("speed_x2", 0.0) or 0.0),
            float(row.get("speed_y1", 0.0) or 0.0),
            float(row.get("speed_y2", 0.0) or 0.0),
            float(row.get("x1x2", 0.0) or 0.0),
            float(row.get("y1y2", 0.0) or 0.0),
            float(row.get("delta_x1x2", 0.0) or 0.0),
            float(row.get("delta_y1y2", 0.0) or 0.0),
            float(row.get("speed_abs_max", 0.0) or 0.0),
            float(row.get("speed_abs_sum", 0.0) or 0.0),
            float(row.get("jump_score", 0.0) or 0.0),
            1 if bool(row.get("jump_flag", False)) else 0,
            float(row.get("move_dx", 0.0) or 0.0),
            float(row.get("move_dy", 0.0) or 0.0),
            float(row.get("move_dist", 0.0) or 0.0),
            str(row.get("move_side", "none") or "none"),
            float(row.get("roi_x1", 0.0) or 0.0),
            float(row.get("roi_y1", 0.0) or 0.0),
            float(row.get("roi_x2", 1079.0) or 1079.0),
            float(row.get("roi_y2", 1079.0) or 1079.0),
        )
        for row in camera_rows
    ]
    conn.executemany(
        """
        INSERT INTO CameraTrack (
            game_id, timestamp, ring_status, ring_number, geometry_source, geometry_confidence,
            center_x, center_y, camera_x, camera_y, radius,
            x1, x2, y1, y2, dyn_x1, dyn_x2, dyn_y1, dyn_y2, zoom_ratio, camera_size,
            speed_x1, speed_x2, speed_y1, speed_y2, x1x2, y1y2,
            delta_x1x2, delta_y1y2, speed_abs_max, speed_abs_sum, jump_score, jump_flag,
            move_dx, move_dy, move_dist, move_side,
            roi_x1, roi_y1, roi_x2, roi_y2
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )


def _parse_center(center_json: str | None) -> tuple[float, float] | None:
    if not center_json:
        return None
    try:
        payload = json.loads(center_json)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        x = float(payload.get("x", np.nan))
        y = float(payload.get("y", np.nan))
    except Exception:
        return None
    if not np.isfinite(x) or not np.isfinite(y):
        return None
    return x, y


def _signed_distance_to_circle_boundary(px: float, py: float, cx: float, cy: float, radius: float) -> float:
    return float(np.hypot(px - cx, py - cy) - max(1.0, float(radius)))


def _distance_to_circle_boundary(px: float, py: float, cx: float, cy: float, radius: float) -> float:
    return float(abs(_signed_distance_to_circle_boundary(px, py, cx, cy, radius)))


def _camera_move_deadzone_px(map_r: float) -> float:
    """Scale with ring size: smaller closing rings => smaller plausible camera pans (map units)."""
    return float(np.clip(0.048 * float(max(1.0, map_r)), 5.5, 24.0))


def _camera_zoom_deadzone(map_r: float, *, ref_r: float = 280.0) -> float:
    """Slightly tighter relative threshold when rings are small (map_r vs Storm Point–scale ring 1)."""
    ratio = float(max(1.0, map_r) / max(1.0, float(ref_r)))
    return float(np.clip(0.034 * (ratio ** 0.55), 0.022, 0.09))


def _zoom_increase_threshold(map_r: float, zoom_dz: float) -> float:
    """Require a clear step before accepting zoom-in (reduces false creep during linear closing)."""
    return float(max(0.11, 2.25 * float(zoom_dz), 0.18 * float(zoom_dz) * (280.0 / max(1.0, float(map_r))) ** 0.4))


def _zoom_decrease_threshold(map_r: float, zoom_dz: float) -> float:
    return float(max(0.038, 0.92 * float(zoom_dz)))


def _movement_side(dx: float, dy: float, eps: float = 0.35) -> str:
    move_x = ""
    move_y = ""
    if dx > eps:
        move_x = "right"
    elif dx < -eps:
        move_x = "left"
    if dy > eps:
        move_y = "down"
    elif dy < -eps:
        move_y = "up"
    if move_x and move_y:
        return f"{move_y}-{move_x}"
    if move_x:
        return move_x
    if move_y:
        return move_y
    return "none"


def _compute_fixed_edge_metrics(
    camera_cx: float,
    camera_cy: float,
    ring_cx: float,
    ring_cy: float,
    radius: float,
    camera_size: float,
    *,
    signed: bool = False,
) -> tuple[float, float, float, float]:
    half = float(camera_size) * 0.5
    left = (float(camera_cx - half), float(camera_cy))
    right = (float(camera_cx + half), float(camera_cy))
    top = (float(camera_cx), float(camera_cy - half))
    bottom = (float(camera_cx), float(camera_cy + half))
    measure = _signed_distance_to_circle_boundary if signed else _distance_to_circle_boundary
    x1 = measure(left[0], left[1], ring_cx, ring_cy, radius)
    x2 = measure(right[0], right[1], ring_cx, ring_cy, radius)
    y1 = measure(top[0], top[1], ring_cx, ring_cy, radius)
    y2 = measure(bottom[0], bottom[1], ring_cx, ring_cy, radius)
    return x1, x2, y1, y2


def _camera_square_from_center(cx: float, cy: float, camera_size: float) -> tuple[float, float, float, float]:
    half = float(camera_size) * 0.5
    return (
        float(cx - half),
        float(cy - half),
        float(cx + half),
        float(cy + half),
    )


def _compute_dynamic_edge_metrics(
    ring_cx: float,
    ring_cy: float,
    radius: float,
    camera_size: float,
    *,
    signed: bool = False,
) -> tuple[float, float, float, float]:
    # Legacy reference around map center (for comparison/debug only).
    half = float(camera_size) * 0.5
    ox1 = float(540.0 - half)
    ox2 = float(540.0 + half)
    oy1 = float(540.0 - half)
    oy2 = float(540.0 + half)
    measure = _signed_distance_to_circle_boundary if signed else _distance_to_circle_boundary
    x1 = measure(ox1, 540.0, ring_cx, ring_cy, radius)
    x2 = measure(ox2, 540.0, ring_cx, ring_cy, radius)
    y1 = measure(540.0, oy1, ring_cx, ring_cy, radius)
    y2 = measure(540.0, oy2, ring_cx, ring_cy, radius)
    return x1, x2, y1, y2


def _compute_edge_residuals(
    *,
    camera_cx: float,
    camera_cy: float,
    camera_size: float,
    observed_cx: float,
    observed_cy: float,
    observed_radius: float,
    expected_cx: float,
    expected_cy: float,
    expected_radius: float,
) -> tuple[float, float, float, float]:
    observed = _compute_fixed_edge_metrics(
        camera_cx,
        camera_cy,
        observed_cx,
        observed_cy,
        observed_radius,
        camera_size,
        signed=True,
    )
    expected = _compute_fixed_edge_metrics(
        camera_cx,
        camera_cy,
        expected_cx,
        expected_cy,
        expected_radius,
        camera_size,
        signed=True,
    )
    return tuple(float(observed[idx] - expected[idx]) for idx in range(4))


def _build_metric_row(
    *,
    ts: float,
    ring_status: str,
    ring_number: int,
    geometry_source: str,
    geometry_confidence: float,
    ring_cx: float,
    ring_cy: float,
    camera_cx: float,
    camera_cy: float,
    radius: float,
    zoom_ratio: float = 1.0,
    prev_row: dict[str, Any] | None = None,
    signed_edges: bool = False,
) -> dict[str, Any]:
    camera_size = float(1080.0 / max(1e-6, float(zoom_ratio)))
    x1, x2, y1, y2 = _compute_fixed_edge_metrics(
        camera_cx,
        camera_cy,
        ring_cx,
        ring_cy,
        radius,
        camera_size,
        signed=bool(signed_edges),
    )
    dyn_x1, dyn_x2, dyn_y1, dyn_y2 = _compute_dynamic_edge_metrics(
        ring_cx,
        ring_cy,
        radius,
        camera_size,
        signed=bool(signed_edges),
    )
    speed_x1 = float(x1 - float(prev_row.get("x1", x1))) if prev_row else 0.0
    speed_x2 = float(x2 - float(prev_row.get("x2", x2))) if prev_row else 0.0
    speed_y1 = float(y1 - float(prev_row.get("y1", y1))) if prev_row else 0.0
    speed_y2 = float(y2 - float(prev_row.get("y2", y2))) if prev_row else 0.0
    x1x2 = float(x1 + x2)
    y1y2 = float(y1 + y2)
    prev_x1x2 = float(prev_row.get("x1x2", x1x2)) if prev_row else x1x2
    prev_y1y2 = float(prev_row.get("y1y2", y1y2)) if prev_row else y1y2
    delta_x1x2 = float(x1x2 - prev_x1x2)
    delta_y1y2 = float(y1y2 - prev_y1y2)
    speed_abs_max = float(max(abs(speed_x1), abs(speed_x2), abs(speed_y1), abs(speed_y2)))
    speed_abs_sum = float(abs(speed_x1) + abs(speed_x2) + abs(speed_y1) + abs(speed_y2))
    jump_score = float(speed_abs_max + 0.5 * abs(delta_x1x2) + 0.5 * abs(delta_y1y2))
    jump_flag = bool(jump_score >= 12.0)
    prev_camera_x = float(prev_row.get("camera_x", camera_cx)) if prev_row else float(camera_cx)
    prev_camera_y = float(prev_row.get("camera_y", camera_cy)) if prev_row else float(camera_cy)
    move_dx = float(float(camera_cx) - float(prev_camera_x))
    move_dy = float(float(camera_cy) - float(prev_camera_y))
    move_dist = float(np.hypot(move_dx, move_dy))
    move_side = _movement_side(move_dx, move_dy)
    roi_x1, roi_y1, roi_x2, roi_y2 = _camera_square_from_center(camera_cx, camera_cy, camera_size)
    roi_x1 = float(np.clip(roi_x1, 0.0, 1079.0))
    roi_y1 = float(np.clip(roi_y1, 0.0, 1079.0))
    roi_x2 = float(np.clip(roi_x2, 0.0, 1079.0))
    roi_y2 = float(np.clip(roi_y2, 0.0, 1079.0))
    return {
        "timestamp": round(float(ts), 3),
        "ring_status": str(ring_status),
        "ring_number": int(ring_number),
        "geometry_source": str(geometry_source),
        "geometry_confidence": round(float(geometry_confidence), 6),
        "center_x": round(float(ring_cx), 4),
        "center_y": round(float(ring_cy), 4),
        "camera_x": round(float(camera_cx), 4),
        "camera_y": round(float(camera_cy), 4),
        "radius": round(float(radius), 4),
        "x1": round(float(x1), 4),
        "x2": round(float(x2), 4),
        "y1": round(float(y1), 4),
        "y2": round(float(y2), 4),
        "dyn_x1": round(float(dyn_x1), 4),
        "dyn_x2": round(float(dyn_x2), 4),
        "dyn_y1": round(float(dyn_y1), 4),
        "dyn_y2": round(float(dyn_y2), 4),
        "zoom_ratio": round(float(zoom_ratio), 6),
        "camera_size": round(float(camera_size), 4),
        "speed_x1": round(float(speed_x1), 6),
        "speed_x2": round(float(speed_x2), 6),
        "speed_y1": round(float(speed_y1), 6),
        "speed_y2": round(float(speed_y2), 6),
        "x1x2": round(float(x1x2), 6),
        "y1y2": round(float(y1y2), 6),
        "delta_x1x2": round(float(delta_x1x2), 6),
        "delta_y1y2": round(float(delta_y1y2), 6),
        "speed_abs_max": round(float(speed_abs_max), 6),
        "speed_abs_sum": round(float(speed_abs_sum), 6),
        "jump_score": round(float(jump_score), 6),
        "jump_flag": bool(jump_flag),
        "move_dx": round(float(move_dx), 6),
        "move_dy": round(float(move_dy), 6),
        "move_dist": round(float(move_dist), 6),
        "move_side": str(move_side),
        "roi_x1": round(float(roi_x1), 4),
        "roi_y1": round(float(roi_y1), 4),
        "roi_x2": round(float(roi_x2), 4),
        "roi_y2": round(float(roi_y2), 4),
    }


def _suppress_reverting_zoom_events(rows: list[dict[str, Any]], *, lookahead_sec: float = 120.0) -> list[dict[str, Any]]:
    if len(rows) < 3:
        return rows
    out = [dict(row) for row in rows]
    active_zoom = 1.0
    candidate_count = 0
    zoom_gt1_count = 0
    max_zoom_seen = 1.0
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for idx, row in enumerate(out):
        ts = float(row.get("timestamp", 0.0) or 0.0)
        curr_zoom = max(1.0, float(row.get("zoom_ratio", 1.0) or 1.0))
        if curr_zoom > 1.0005:
            zoom_gt1_count += 1
        if curr_zoom > max_zoom_seen:
            max_zoom_seen = float(curr_zoom)
        prev_zoom = active_zoom
        zoom_delta = float(curr_zoom - prev_zoom)
        jump_score = float(row.get("jump_score", 0.0) or 0.0)
        jump_flag = bool(row.get("jump_flag", False))
        is_candidate = bool(zoom_delta >= 0.035 or (jump_flag and zoom_delta > 0.005) or (jump_score >= 180.0 and zoom_delta > 0.005))
        if is_candidate:
            candidate_count += 1
            future = [
                max(1.0, float(probe.get("zoom_ratio", 1.0) or 1.0))
                for probe in out[idx + 1 :]
                if float(probe.get("timestamp", 0.0) or 0.0) <= ts + float(lookahead_sec)
            ]
            future_min = min(future) if future else curr_zoom
            future_end = future[-1] if future else curr_zoom
            revert_threshold = prev_zoom + max(0.012, zoom_delta * 0.35)
            reverted = bool(future and future_min <= revert_threshold)
            if reverted:
                rejected.append(
                    {
                        "timestamp": float(ts),
                        "prev_zoom": float(prev_zoom),
                        "candidate_zoom": float(curr_zoom),
                        "future_min": float(future_min),
                        "future_end": float(future_end),
                        "revert_threshold": float(revert_threshold),
                    }
                )
            else:
                active_zoom = float(curr_zoom)
                accepted.append(
                    {
                        "timestamp": float(ts),
                        "prev_zoom": float(prev_zoom),
                        "candidate_zoom": float(curr_zoom),
                        "future_min": float(future_min),
                        "future_end": float(future_end),
                    }
                )

        row["zoom_ratio"] = round(float(active_zoom), 6)
        row["camera_size"] = round(float(1080.0 / max(1e-6, active_zoom)), 4)
        roi_x1, roi_y1, roi_x2, roi_y2 = _camera_square_from_center(
            float(row.get("camera_x", 540.0) or 540.0),
            float(row.get("camera_y", 540.0) or 540.0),
            float(row.get("camera_size", 1080.0) or 1080.0),
        )
        row["roi_x1"] = round(float(np.clip(roi_x1, 0.0, 1079.0)), 4)
        row["roi_y1"] = round(float(np.clip(roi_y1, 0.0, 1079.0)), 4)
        row["roi_x2"] = round(float(np.clip(roi_x2, 0.0, 1079.0)), 4)
        row["roi_y2"] = round(float(np.clip(roi_y2, 0.0, 1079.0)), 4)

    # region agent log
    _debug_log_b91(
        "post-fix-6",
        "H7,H8,H9",
        "camera_tracker.py:_suppress_reverting_zoom_events",
        "zoom_future_filter_summary",
        {
            "rows": int(len(rows)),
            "candidate_count": int(candidate_count),
            "zoom_gt1_count": int(zoom_gt1_count),
            "max_zoom_seen": float(max_zoom_seen),
            "accepted": accepted[:20],
            "rejected": rejected[:30],
            "accepted_count": int(len(accepted)),
            "rejected_count": int(len(rejected)),
        },
    )
    # endregion
    return out


def _previous_closing_anchor(
    *,
    ring_number: int,
    center: tuple[float, float],
    radius: float,
) -> tuple[tuple[float, float] | None, float | None]:
    if int(ring_number) != 1 or float(radius) <= 0.0:
        return None, None
    # R1 closing starts from the large initial ring and shrinks to the stored
    # R1 target. Without this synthetic anchor the expected radius stays flat
    # and static outer-ring detections look like false zoom jumps.
    prev_radius = float(np.clip(max(float(radius) * 1.62, float(radius) + 135.0), float(radius), 540.0))
    prev_center = (
        float(np.clip(540.0 + (float(center[0]) - 540.0) * 0.18, 0.0, 1079.0)),
        float(np.clip(540.0 + (float(center[1]) - 540.0) * 0.18, 0.0, 1079.0)),
    )
    return prev_center, prev_radius


def build_camera_rows(
    rings_rows: list[dict[str, Any]],
    *,
    video_duration_sec: float,
    step_sec: float = 1.0,
) -> list[dict[str, Any]]:
    if not rings_rows:
        return []
    step = max(0.2, float(step_sec))
    rows_sorted = sorted(
        rings_rows,
        key=lambda row: (float(row.get("time_start", 0.0) or 0.0), int(row.get("ring_number", 0) or 0)),
    )
    timeline_rows: list[dict[str, Any]] = []
    prev_metric: dict[str, Any] | None = None

    first_center = _parse_center(str(rows_sorted[0].get("center")) if rows_sorted[0].get("center") is not None else None)
    first_radius = float(rows_sorted[0].get("radius", 0.0) or 0.0)
    first_start = max(0.0, float(rows_sorted[0].get("time_start", 0.0) or 0.0))
    if first_center is not None and first_radius > 0.0 and first_start > 0.0:
        for ts in np.arange(0.0, first_start + 1e-6, step):
            row = _build_metric_row(
                ts=float(ts),
                ring_status="countdown",
                ring_number=int(rows_sorted[0].get("ring_number", 0) or 0),
                geometry_source="timing_model",
                geometry_confidence=0.0,
                ring_cx=float(first_center[0]),
                ring_cy=float(first_center[1]),
                camera_cx=540.0,
                camera_cy=540.0,
                radius=float(first_radius),
                zoom_ratio=1.0,
                prev_row=prev_metric,
            )
            timeline_rows.append(row)
            prev_metric = row

    for idx, ring in enumerate(rows_sorted):
        center = _parse_center(str(ring.get("center")) if ring.get("center") is not None else None)
        radius = float(ring.get("radius", 0.0) or 0.0)
        if center is None or radius <= 0.0:
            continue
        t_start = max(0.0, float(ring.get("time_start", 0.0) or 0.0))
        t_end = max(t_start, float(ring.get("time_end", t_start) or t_start))
        prev_center: tuple[float, float] | None = None
        prev_radius: float | None = None
        if idx > 0:
            prev_center = _parse_center(str(rows_sorted[idx - 1].get("center")) if rows_sorted[idx - 1].get("center") is not None else None)
            try:
                prev_radius = float(rows_sorted[idx - 1].get("radius", 0.0) or 0.0)
            except Exception:
                prev_radius = None
        elif int(ring.get("ring_number", 0) or 0) == 1:
            prev_center, prev_radius = _previous_closing_anchor(
                ring_number=int(ring.get("ring_number", 0) or 0),
                center=(float(center[0]), float(center[1])),
                radius=float(radius),
            )

        for ts in np.arange(t_start, t_end + 1e-6, step):
            if prev_center is not None and prev_radius is not None and prev_radius > 0.0 and t_end > t_start:
                progress = float(np.clip((float(ts) - t_start) / max(1e-6, t_end - t_start), 0.0, 1.0))
                cx = float(prev_center[0] + (center[0] - prev_center[0]) * progress)
                cy = float(prev_center[1] + (center[1] - prev_center[1]) * progress)
                rr = float(prev_radius + (radius - prev_radius) * progress)
            else:
                cx = float(center[0])
                cy = float(center[1])
                rr = float(radius)
            row = _build_metric_row(
                ts=float(ts),
                ring_status="closing",
                ring_number=int(ring.get("ring_number", 0) or 0),
                geometry_source="timing_model",
                geometry_confidence=0.0,
                ring_cx=cx,
                ring_cy=cy,
                camera_cx=540.0,
                camera_cy=540.0,
                radius=rr,
                zoom_ratio=1.0,
                prev_row=prev_metric,
            )
            timeline_rows.append(row)
            prev_metric = row

        next_start: float | None = None
        if idx + 1 < len(rows_sorted):
            next_start = max(t_end, float(rows_sorted[idx + 1].get("time_start", t_end) or t_end))
        else:
            next_start = max(t_end, float(video_duration_sec))
        if next_start is not None and next_start > t_end:
            for ts in np.arange(t_end + step, next_start + 1e-6, step):
                row = _build_metric_row(
                    ts=float(ts),
                    ring_status="countdown",
                    ring_number=int(ring.get("ring_number", 0) or 0),
                    geometry_source="timing_model",
                    geometry_confidence=0.0,
                    ring_cx=float(center[0]),
                    ring_cy=float(center[1]),
                    camera_cx=540.0,
                    camera_cy=540.0,
                    radius=float(radius),
                    zoom_ratio=1.0,
                    prev_row=prev_metric,
                )
                timeline_rows.append(row)
                prev_metric = row
    return timeline_rows


def _iter_ring_samples(
    rings_rows: list[dict[str, Any]],
    *,
    video_duration_sec: float,
    step_sec: float,
) -> list[dict[str, Any]]:
    rows_sorted = sorted(
        rings_rows,
        key=lambda row: (float(row.get("time_start", 0.0) or 0.0), int(row.get("ring_number", 0) or 0)),
    )
    samples: list[dict[str, Any]] = []
    if not rows_sorted:
        return samples
    first_center = _parse_center(str(rows_sorted[0].get("center")) if rows_sorted[0].get("center") is not None else None)
    first_radius = float(rows_sorted[0].get("radius", 0.0) or 0.0)
    first_start = max(0.0, float(rows_sorted[0].get("time_start", 0.0) or 0.0))
    # region agent log
    _debug_log_b91(
        "pre-fix-2",
        "H4",
        "camera_tracker.py:_iter_ring_samples",
        "first_ring_sample_window",
        {
            "first_ring_number": int(rows_sorted[0].get("ring_number", 0) or 0),
            "first_start": float(first_start),
            "first_radius": float(first_radius),
        },
    )
    # endregion
    if first_center is not None and first_radius > 0.0 and first_start > 0.0:
        for ts in np.arange(0.0, first_start + 1e-6, step_sec):
            samples.append(
                {
                    "timestamp": float(ts),
                    "ring_status": "countdown",
                    "ring_number": int(rows_sorted[0].get("ring_number", 0) or 0),
                    "expected_center": (float(first_center[0]), float(first_center[1])),
                    "expected_radius": float(first_radius),
                }
            )
    for idx, ring in enumerate(rows_sorted):
        center = _parse_center(str(ring.get("center")) if ring.get("center") is not None else None)
        radius = float(ring.get("radius", 0.0) or 0.0)
        if center is None or radius <= 0.0:
            continue
        ring_no = int(ring.get("ring_number", 0) or 0)
        t_start = max(0.0, float(ring.get("time_start", 0.0) or 0.0))
        t_end = max(t_start, float(ring.get("time_end", t_start) or t_start))
        if ring_no == 1:
            # region agent log
            _debug_log_b91(
                "pre-fix-2",
                "H4",
                "camera_tracker.py:_iter_ring_samples",
                "ring1_closing_window",
                {
                    "ring_no": int(ring_no),
                    "t_start": float(t_start),
                    "t_end": float(t_end),
                },
            )
            # endregion
        prev_center: tuple[float, float] | None = None
        prev_radius: float | None = None
        if idx > 0:
            prev_center = _parse_center(str(rows_sorted[idx - 1].get("center")) if rows_sorted[idx - 1].get("center") is not None else None)
            prev_radius = float(rows_sorted[idx - 1].get("radius", 0.0) or 0.0)
        elif int(ring_no) == 1:
            prev_center, prev_radius = _previous_closing_anchor(
                ring_number=int(ring_no),
                center=(float(center[0]), float(center[1])),
                radius=float(radius),
            )
            # region agent log
            _debug_log_b91(
                "post-fix-8",
                "H13,H14,H15",
                "camera_tracker.py:_iter_ring_samples",
                "first_ring_closing_anchor",
                {
                    "ring_no": int(ring_no),
                    "target_center": {"x": float(center[0]), "y": float(center[1])},
                    "target_radius": float(radius),
                    "prev_center": {"x": float(prev_center[0]), "y": float(prev_center[1])} if prev_center is not None else None,
                    "prev_radius": float(prev_radius) if prev_radius is not None else None,
                    "t_start": float(t_start),
                    "t_end": float(t_end),
                },
            )
            # endregion
        for ts in np.arange(t_start, t_end + 1e-6, step_sec):
            if prev_center is not None and prev_radius is not None and prev_radius > 0.0 and t_end > t_start:
                progress = float(np.clip((float(ts) - t_start) / max(1e-6, t_end - t_start), 0.0, 1.0))
                cx = float(prev_center[0] + (center[0] - prev_center[0]) * progress)
                cy = float(prev_center[1] + (center[1] - prev_center[1]) * progress)
                rr = float(prev_radius + (radius - prev_radius) * progress)
            else:
                cx = float(center[0])
                cy = float(center[1])
                rr = float(radius)
            ts_i = int(round(float(ts)))
            if abs(float(ts) - float(ts_i)) <= 1e-3 and ts_i in {108, 120, 200, 368, 408, 527, 585}:
                # region agent log
                _debug_log_b91(
                    "pre-fix-9",
                    "H2,H3",
                    "camera_tracker.py:_iter_ring_samples",
                    "sample_expectation_checkpoint",
                    {
                        "timestamp": float(ts),
                        "ring_status": "closing",
                        "ring_number": int(ring_no),
                        "expected_center": {"x": float(cx), "y": float(cy)},
                        "expected_radius": float(rr),
                        "target_radius": float(radius),
                        "target_center": {"x": float(center[0]), "y": float(center[1])},
                    },
                )
                # endregion
            samples.append(
                {
                    "timestamp": float(ts),
                    "ring_status": "closing",
                    "ring_number": int(ring_no),
                    "expected_center": (float(cx), float(cy)),
                    "expected_radius": float(rr),
                }
            )
        if idx + 1 < len(rows_sorted):
            next_start = max(t_end, float(rows_sorted[idx + 1].get("time_start", t_end) or t_end))
        else:
            next_start = max(t_end, float(video_duration_sec))
        if next_start > t_end:
            for ts in np.arange(t_end + step_sec, next_start + 1e-6, step_sec):
                ts_i = int(round(float(ts)))
                if abs(float(ts) - float(ts_i)) <= 1e-3 and ts_i in {108, 120, 200, 368, 408, 527, 585}:
                    # region agent log
                    _debug_log_b91(
                        "pre-fix-9",
                        "H2,H3",
                        "camera_tracker.py:_iter_ring_samples",
                        "sample_expectation_checkpoint",
                        {
                            "timestamp": float(ts),
                            "ring_status": "countdown",
                            "ring_number": int(ring_no),
                            "expected_center": {"x": float(center[0]), "y": float(center[1])},
                            "expected_radius": float(radius),
                            "target_radius": float(radius),
                            "target_center": {"x": float(center[0]), "y": float(center[1])},
                        },
                    )
                    # endregion
                samples.append(
                    {
                        "timestamp": float(ts),
                        "ring_status": "countdown",
                        "ring_number": int(ring_no),
                        "expected_center": (float(center[0]), float(center[1])),
                        "expected_radius": float(radius),
                    }
                )
    samples.sort(key=lambda item: float(item.get("timestamp", 0.0) or 0.0))
    uniq: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in samples:
        ts = float(item.get("timestamp", 0.0) or 0.0)
        status = str(item.get("ring_status", "countdown") or "countdown")
        key = (int(round(ts * 1000.0)), status)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def build_camera_rows_from_video(
    *,
    video_path: str | Path,
    rings_rows: list[dict[str, Any]],
    video_duration_sec: float,
    step_sec: float = 1.0,
    map_mp_id: str | None = None,
    countdown_zone_mode: bool = False,
    strict_line_profile: bool = False,
    arc_only_mode: bool = False,
    camera_tracking_mode: str = "geometry",
) -> list[dict[str, Any]]:
    tracking_mode = str(camera_tracking_mode or "geometry")
    if tracking_mode not in {"geometry", "edge_residual"}:
        tracking_mode = "geometry"
    # region agent log
    _debug_log(
        "pre-fix-1",
        "H0",
        "camera_tracker.py:build_camera_rows_from_video",
        "build_start",
        {
            "video_path": str(video_path),
            "video_duration_sec": float(video_duration_sec),
            "step_sec": float(step_sec),
            "map_mp_id": str(map_mp_id or ""),
            "countdown_zone_mode": bool(countdown_zone_mode),
            "strict_line_profile": bool(strict_line_profile),
            "arc_only_mode": bool(arc_only_mode),
            "camera_tracking_mode": str(tracking_mode),
        },
    )
    # endregion
    if not rings_rows:
        return []
    step = max(0.25, float(step_sec))
    samples = _iter_ring_samples(
        rings_rows,
        video_duration_sec=float(video_duration_sec),
        step_sec=step,
    )
    if not samples:
        return []
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return build_camera_rows(rings_rows, video_duration_sec=video_duration_sec, step_sec=step_sec)
    rows: list[dict[str, Any]] = []
    prev_metric: dict[str, Any] | None = None
    closing_center_only_accept_count = 0
    closing_full_reject_count = 0
    closing_direct_accept_count = 0
    stable_camera_x = 540.0
    stable_camera_y = 540.0
    stable_zoom = 1.0
    raw_cameras_x: list[float] = []
    raw_cameras_y: list[float] = []
    raw_zooms: list[float] = []
    max_raw_len = max(1, int(round(2.8 / step)))
    timeline_t0 = float(samples[0].get("timestamp", 0.0) or 0.0) if samples else 0.0
    warmup_sec = 4.5
    prev_phase_key: tuple[int, str] | None = None
    move_commit_streak = 0
    zoom_up_streak = 0
    zoom_down_streak = 0
    segment_closing_t0: float | None = None
    prev_edge_residual: tuple[float, float, float, float] | None = None
    first_ring_end = 0.0
    try:
        first_ring = min(
            (row for row in rings_rows if int(row.get("ring_number", 0) or 0) == 1),
            key=lambda row: float(row.get("time_end", 0.0) or 0.0),
        )
        first_ring_end = float(first_ring.get("time_end", 0.0) or 0.0)
    except Exception:
        first_ring_end = 0.0

    rd.set_map_context(map_mp_id)
    for sample in samples:
        ts = float(sample.get("timestamp", 0.0) or 0.0)
        ring_status = str(sample.get("ring_status", "countdown") or "countdown")
        ring_number = int(sample.get("ring_number", 0) or 0)
        expected_center = sample.get("expected_center")
        expected_radius = float(sample.get("expected_radius", 0.0) or 0.0)
        if not isinstance(expected_center, tuple) or len(expected_center) != 2:
            continue
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, ts) * 1000.0)
        ok, frame = cap.read()
        geom = None
        conf = 0.0
        source = "timing_fallback"
        raw_geom_cx: float | None = None
        raw_geom_cy: float | None = None
        raw_geom_rr: float | None = None
        raw_geom_center_delta: float | None = None
        raw_geom_radius_ratio: float | None = None
        applied_countdown_zone_mode = bool(countdown_zone_mode and str(ring_status) == "countdown")
        if ok and frame is not None:
            geom, conf = rd.detect_ring_geometry_in_frame(
                frame,
                zones_payload=None,
                expected_center=(float(expected_center[0]), float(expected_center[1])),
                expected_radius=float(expected_radius) if expected_radius > 0.0 else None,
                min_radius_map_units=max(1.0, float(expected_radius) * 0.25) if expected_radius > 0.0 else None,
                countdown_zone_mode=bool(applied_countdown_zone_mode),
                strict_line_profile=bool(strict_line_profile),
                arc_only_mode=bool(arc_only_mode),
            )
        map_x = float(expected_center[0])
        map_y = float(expected_center[1])
        map_r = max(1.0, float(expected_radius))
        if geom is not None:
            cand_cx = float(geom.get("x", expected_center[0]))
            cand_cy = float(geom.get("y", expected_center[1]))
            cand_rr = float(geom.get("radius", expected_radius))
            cand_source = str(geom.get("geometry_source", "detected") or "detected")
            cand_center_delta = float(np.hypot(float(cand_cx) - float(map_x), float(cand_cy) - float(map_y)))
            cand_radius_delta_ratio = float(abs(float(cand_rr) - float(map_r)) / max(1e-6, float(map_r)))
            raw_geom_cx = float(cand_cx)
            raw_geom_cy = float(cand_cy)
            raw_geom_rr = float(cand_rr)
            raw_geom_center_delta = float(cand_center_delta)
            raw_geom_radius_ratio = float(cand_rr) / max(1e-6, float(map_r))
            prev_public_radius = float(prev_metric.get("radius", map_r)) if prev_metric else float(map_r)
            radius_step_ratio = float(abs(float(cand_rr) - float(prev_public_radius)) / max(1e-6, float(prev_public_radius)))
            if (95.0 <= float(ts) <= 130.0) or (500.0 <= float(ts) <= 535.0) or (575.0 <= float(ts) <= 595.0):
                # region agent log
                _debug_log_b91(
                    "pre-fix-9",
                    "H1,H2",
                    "camera_tracker.py:build_camera_rows_from_video",
                    "geom_mode_candidate",
                    {
                        "timestamp": float(ts),
                        "ring_status": str(ring_status),
                        "ring_number": int(ring_number),
                        "applied_countdown_zone_mode": bool(applied_countdown_zone_mode),
                        "global_countdown_zone_mode": bool(countdown_zone_mode),
                        "raw_radius": float(cand_rr),
                        "expected_radius": float(map_r),
                        "raw_radius_ratio": float(raw_geom_radius_ratio),
                        "raw_center_delta": float(raw_geom_center_delta),
                        "prev_public_radius": float(prev_public_radius),
                        "radius_step_ratio": float(radius_step_ratio),
                        "detected_source": str(cand_source),
                    },
                )
                # endregion
            center_reject_px = max(16.0, float(map_r) * 0.20)
            radius_reject_ratio = 0.18
            center_only_radius_ratio = 0.12
            center_only_max_delta = max(140.0, float(map_r) * 0.50)
            center_only_step_ratio = 0.085
            reject_as_unstable = bool(
                str(ring_status) == "closing"
                and (
                    cand_center_delta > float(center_reject_px)
                    or cand_radius_delta_ratio > float(radius_reject_ratio)
                )
            )
            use_center_anchor_with_raw_radius = bool(
                str(ring_status) == "closing"
                and str(cand_source) in {"arc_boundary", "diagonal_bbox", "largest_bbox", "safe_component"}
                and cand_center_delta > float(center_reject_px)
                and cand_center_delta <= float(center_only_max_delta)
                and cand_radius_delta_ratio <= float(center_only_radius_ratio)
                and radius_step_ratio <= float(center_only_step_ratio)
            )
            if reject_as_unstable and use_center_anchor_with_raw_radius:
                cx = float(map_x)
                cy = float(map_y)
                rr = float(cand_rr)
                source = "timing_anchor_center_only"
                closing_center_only_accept_count += 1
                if (500.0 <= float(ts) <= 535.0) or (575.0 <= float(ts) <= 595.0) or (190.0 <= float(ts) <= 215.0):
                    # region agent log
                    _debug_log_b91(
                        "pre-fix-10",
                        "H1,H2,H3",
                        "camera_tracker.py:build_camera_rows_from_video",
                        "closing_center_only_accept",
                        {
                            "timestamp": float(ts),
                            "ring_number": int(ring_number),
                            "detected_source": str(cand_source),
                            "detected_center": {"x": float(cand_cx), "y": float(cand_cy)},
                            "expected_center": {"x": float(map_x), "y": float(map_y)},
                            "detected_radius": float(cand_rr),
                            "expected_radius": float(map_r),
                            "center_delta_px": float(cand_center_delta),
                            "center_reject_px": float(center_reject_px),
                            "center_only_max_delta": float(center_only_max_delta),
                            "radius_delta_ratio": float(cand_radius_delta_ratio),
                            "center_only_radius_ratio": float(center_only_radius_ratio),
                            "prev_public_radius": float(prev_public_radius),
                            "radius_step_ratio": float(radius_step_ratio),
                            "center_only_step_ratio": float(center_only_step_ratio),
                        },
                    )
                    # endregion
            elif reject_as_unstable:
                cx = float(map_x)
                cy = float(map_y)
                rr = float(map_r)
                source = "timing_anchor_reject_unstable_geom"
                closing_full_reject_count += 1
                # region agent log
                _debug_log_b91(
                    "post-fix",
                    "H8",
                    "camera_tracker.py:build_camera_rows_from_video",
                    "geometry_rejected_use_expected",
                    {
                        "timestamp": float(ts),
                        "ring_status": str(ring_status),
                        "ring_number": int(ring_number),
                        "detected_source": str(cand_source),
                        "detected_center": {"x": float(cand_cx), "y": float(cand_cy)},
                        "expected_center": {"x": float(map_x), "y": float(map_y)},
                        "detected_radius": float(cand_rr),
                        "expected_radius": float(map_r),
                        "center_delta_px": float(cand_center_delta),
                        "radius_delta_ratio": float(cand_radius_delta_ratio),
                        "center_reject_px": float(center_reject_px),
                        "radius_reject_ratio": float(radius_reject_ratio),
                    },
                )
                # endregion
            else:
                cx = float(cand_cx)
                cy = float(cand_cy)
                rr = float(cand_rr)
                source = str(cand_source)
                if str(ring_status) == "closing":
                    closing_direct_accept_count += 1
            if str(ring_status) == "countdown":
                cx = float(map_x)
                cy = float(map_y)
                rr = float(map_r)
                source = "timing_anchor_countdown"
        else:
            cx = float(expected_center[0])
            cy = float(expected_center[1])
            rr = float(expected_radius)
        center_delta = float(np.hypot(float(cx) - float(map_x), float(cy) - float(map_y)))
        radius_delta_ratio = float(abs(float(rr) - float(map_r)) / max(1e-6, float(map_r)))
        if str(ring_status) == "closing" and (
            center_delta >= 12.0 or radius_delta_ratio >= 0.055 or float(conf) < 0.20
        ):
            # region agent log
            _debug_log_b91(
                "pre-fix",
                "H8,H9",
                "camera_tracker.py:build_camera_rows_from_video",
                "geometry_vs_expected_delta",
                {
                    "timestamp": float(ts),
                    "ring_status": str(ring_status),
                    "ring_number": int(ring_number),
                    "source": str(source),
                    "geometry_confidence": float(conf),
                    "detected_center": {"x": float(cx), "y": float(cy)},
                    "expected_center": {"x": float(map_x), "y": float(map_y)},
                    "detected_radius": float(rr),
                    "expected_radius": float(map_r),
                    "center_delta_px": float(center_delta),
                    "radius_delta_ratio": float(radius_delta_ratio),
                },
            )
            # endregion

        is_countdown = str(ring_status) == "countdown"
        phase_key = (int(ring_number), str(ring_status))
        phase_changed = prev_phase_key is not None and phase_key != prev_phase_key
        if phase_changed:
            raw_cameras_x.clear()
            raw_cameras_y.clear()
            raw_zooms.clear()
            prev_edge_residual = None
            move_commit_streak = 0
            zoom_up_streak = 0
            zoom_down_streak = 0
            segment_closing_t0 = float(ts) if (not is_countdown) else None
        elif prev_phase_key is None:
            segment_closing_t0 = float(ts) if (not is_countdown) else None
        prev_phase_key = phase_key

        if is_countdown:
            allow_countdown_zoom = bool(float(ts) >= float(first_ring_end) - 1e-3)
            countdown_geom_ok = bool(
                allow_countdown_zoom
                and raw_geom_cx is not None
                and raw_geom_cy is not None
                and raw_geom_rr is not None
                and raw_geom_center_delta is not None
                and raw_geom_radius_ratio is not None
                and float(conf) >= 0.05
                and raw_geom_center_delta <= max(32.0, float(map_r) * 0.11)
                and 0.85 <= raw_geom_radius_ratio <= 1.75
            )
            if countdown_geom_ok:
                raw_zoom = max(1.0, float(raw_geom_rr) / max(1e-6, float(map_r)))
                raw_cam_x = float(map_x) - (float(raw_geom_cx) - 540.0) / max(1e-6, raw_zoom)
                raw_cam_y = float(map_y) - (float(raw_geom_cy) - 540.0) / max(1e-6, raw_zoom)
                raw_cameras_x.append(raw_cam_x)
                raw_cameras_y.append(raw_cam_y)
                raw_zooms.append(raw_zoom)
                if len(raw_cameras_x) > max_raw_len:
                    raw_cameras_x.pop(0)
                    raw_cameras_y.pop(0)
                    raw_zooms.pop(0)
                med_win = max(1, int(round(2.2 / step)))
                median_cam_x = float(np.median(raw_cameras_x[-min(med_win, len(raw_cameras_x)) :]))
                median_cam_y = float(np.median(raw_cameras_y[-min(med_win, len(raw_cameras_y)) :]))
                median_zoom = float(np.median(raw_zooms[-min(med_win, len(raw_zooms)) :]))
                zoom_up_th = max(0.035, float(_zoom_increase_threshold(map_r, _camera_zoom_deadzone(map_r))) * 0.72)
                if median_zoom > stable_zoom + zoom_up_th:
                    zoom_up_streak += 1
                    if zoom_up_streak >= 2:
                        stable_zoom = float(median_zoom)
                        stable_camera_x = float(median_cam_x)
                        stable_camera_y = float(median_cam_y)
                        zoom_up_streak = 0
                        # region agent log
                        _debug_log_b91(
                            "post-fix-4",
                            "H2",
                            "camera_tracker.py:build_camera_rows_from_video",
                            "countdown_zoom_committed",
                            {
                                "timestamp": float(ts),
                                "ring_number": int(ring_number),
                                "raw_radius": float(raw_geom_rr),
                                "expected_radius": float(map_r),
                                "raw_zoom": float(raw_zoom),
                                "median_zoom": float(median_zoom),
                                "zoom_up_th": float(zoom_up_th),
                                "center_delta": float(raw_geom_center_delta),
                            },
                        )
                        # endregion
                else:
                    zoom_up_streak = 0
            else:
                if not allow_countdown_zoom:
                    stable_zoom = 1.0
                    stable_camera_x = 540.0
                    stable_camera_y = 540.0
                zoom_up_streak = 0
            if (float(ts) <= 115.0) or (390.0 <= float(ts) <= 430.0) or (490.0 <= float(ts) <= 510.0):
                # region agent log
                _debug_log_b91(
                    "post-fix-4",
                    "H1,H2,H3",
                    "camera_tracker.py:build_camera_rows_from_video",
                    "countdown_geom_decision",
                    {
                        "timestamp": float(ts),
                        "ring_number": int(ring_number),
                        "allow_countdown_zoom": bool(allow_countdown_zoom),
                        "accepted": bool(countdown_geom_ok),
                        "raw_radius": float(raw_geom_rr) if raw_geom_rr is not None else None,
                        "expected_radius": float(map_r),
                        "raw_radius_ratio": float(raw_geom_radius_ratio) if raw_geom_radius_ratio is not None else None,
                        "raw_center_delta": float(raw_geom_center_delta) if raw_geom_center_delta is not None else None,
                        "stable_zoom": float(stable_zoom),
                    },
                )
                # endregion
            prev_edge_residual = None
        else:
            raw_evidence_ok = bool(
                raw_geom_cx is not None
                and raw_geom_cy is not None
                and raw_geom_rr is not None
                and raw_geom_radius_ratio is not None
                and float(conf) >= 0.05
                and 0.75 <= float(raw_geom_radius_ratio) <= 2.5
                and source != "timing_anchor_reject_unstable_geom"
            )
            evidence_cx = float(raw_geom_cx) if raw_evidence_ok else float(cx)
            evidence_cy = float(raw_geom_cy) if raw_evidence_ok else float(cy)
            evidence_rr = float(raw_geom_rr) if raw_evidence_ok else float(rr)
            geom_raw_zoom = max(1.0, float(evidence_rr) / map_r)
            geom_raw_cam_x = map_x - (evidence_cx - 540.0) / geom_raw_zoom
            geom_raw_cam_y = map_y - (evidence_cy - 540.0) / geom_raw_zoom
            raw_zoom = float(geom_raw_zoom)
            raw_cam_x = float(geom_raw_cam_x)
            raw_cam_y = float(geom_raw_cam_y)
            if raw_evidence_ok and abs(float(evidence_rr) - float(rr)) >= max(8.0, float(map_r) * 0.08):
                # region agent log
                _debug_log_b91(
                    "post-fix-7",
                    "H10,H11,H12",
                    "camera_tracker.py:build_camera_rows_from_video",
                    "raw_zoom_evidence_used_despite_anchor",
                    {
                        "timestamp": float(ts),
                        "ring_status": str(ring_status),
                        "ring_number": int(ring_number),
                        "public_radius": float(rr),
                        "raw_radius": float(evidence_rr),
                        "expected_radius": float(map_r),
                        "raw_zoom": float(raw_zoom),
                        "raw_center_delta": float(raw_geom_center_delta) if raw_geom_center_delta is not None else None,
                        "geometry_source": str(source),
                        "confidence": float(conf),
                    },
                )
                # endregion
            in_closing_boot = bool(
                str(ring_status) == "closing"
                and segment_closing_t0 is not None
                and float(ts) - float(segment_closing_t0) < 6.0
            )
            if tracking_mode == "edge_residual":
                stable_camera_size = float(1080.0 / max(1e-6, float(stable_zoom)))
                residual = _compute_edge_residuals(
                    camera_cx=float(stable_camera_x),
                    camera_cy=float(stable_camera_y),
                    camera_size=float(stable_camera_size),
                    observed_cx=float(cx),
                    observed_cy=float(cy),
                    observed_radius=float(rr),
                    expected_cx=float(map_x),
                    expected_cy=float(map_y),
                    expected_radius=float(map_r),
                )
                side_dz = float(np.clip(0.022 * float(map_r), 2.0, 16.0))
                if prev_edge_residual is not None:
                    edge_speed = tuple(float(residual[idx] - prev_edge_residual[idx]) for idx in range(4))
                    dx_signal = float(edge_speed[1] - edge_speed[0])
                    dy_signal = float(edge_speed[3] - edge_speed[2])
                    has_pan_signal = bool(abs(dx_signal) > side_dz or abs(dy_signal) > side_dz)
                    zoom_in_signal = bool(max(edge_speed) < -side_dz)
                    zoom_out_signal = bool(min(edge_speed) > side_dz)
                    if not has_pan_signal:
                        raw_cam_x = float(stable_camera_x)
                        raw_cam_y = float(stable_camera_y)
                    if not (zoom_in_signal or zoom_out_signal):
                        raw_zoom = float(stable_zoom)
                prev_edge_residual = residual

            if in_closing_boot:
                # Ring phase boundaries often redraw/re-anchor geometry. Keep the observer zoom
                # from the countdown phase until closing geometry has settled.
                raw_zoom = float(stable_zoom)
                raw_cam_x = float(stable_camera_x)
                raw_cam_y = float(stable_camera_y)

            raw_cameras_x.append(raw_cam_x)
            raw_cameras_y.append(raw_cam_y)
            raw_zooms.append(raw_zoom)
            if len(raw_cameras_x) > max_raw_len:
                raw_cameras_x.pop(0)
                raw_cameras_y.pop(0)
                raw_zooms.pop(0)

            in_warmup = bool(float(ts) <= float(timeline_t0) + float(warmup_sec))
            med_win = max(1, int(round((2.6 if in_warmup else 1.5) / step)))
            tail_x = raw_cameras_x[-min(med_win, len(raw_cameras_x)) :]
            tail_y = raw_cameras_y[-min(med_win, len(raw_cameras_y)) :]
            tail_z = raw_zooms[-min(med_win, len(raw_zooms)) :]
            median_cam_x = float(np.median(tail_x))
            median_cam_y = float(np.median(tail_y))
            median_zoom = float(np.median(tail_z))

            move_dz = float(_camera_move_deadzone_px(map_r))
            zoom_dz = float(_camera_zoom_deadzone(map_r))
            zoom_up_th = float(_zoom_increase_threshold(map_r, zoom_dz))
            zoom_dn_th = float(_zoom_decrease_threshold(map_r, zoom_dz))
            if tracking_mode == "edge_residual":
                move_dz = float(move_dz * 0.82)
                zoom_up_th = float(zoom_up_th * 0.82)
                zoom_dn_th = float(zoom_dn_th * 0.82)
            if in_warmup:
                move_dz = float(move_dz * 1.42)
                zoom_dz = float(zoom_dz * 1.28)
                zoom_up_th = float(zoom_up_th * 1.32)
                zoom_dn_th = float(zoom_dn_th * 1.12)
            if in_closing_boot:
                zoom_up_th = float(zoom_up_th * 1.5)
                zoom_dn_th = float(zoom_dn_th * 1.12)
            move_need = 3 if in_warmup else 2
            need_zoom_up = (4 if in_warmup else 3) + (1 if in_closing_boot else 0)
            need_zoom_dn = 3 if in_warmup else 2

            d_move = float(np.hypot(median_cam_x - stable_camera_x, median_cam_y - stable_camera_y))
            if d_move > move_dz:
                move_commit_streak += 1
                if move_commit_streak >= move_need:
                    stable_camera_x = float(median_cam_x)
                    stable_camera_y = float(median_cam_y)
                    move_commit_streak = 0
            else:
                move_commit_streak = 0

            if float(median_zoom) > float(stable_zoom) + float(zoom_up_th):
                zoom_up_streak += 1
                zoom_down_streak = 0
                if zoom_up_streak >= need_zoom_up:
                    stable_zoom = float(median_zoom)
                    zoom_up_streak = 0
            elif float(median_zoom) < float(stable_zoom) - float(zoom_dn_th):
                zoom_down_streak += 1
                zoom_up_streak = 0
                if zoom_down_streak >= need_zoom_dn:
                    stable_zoom = float(median_zoom)
                    zoom_down_streak = 0
            else:
                zoom_up_streak = 0
                zoom_down_streak = 0

        row = _build_metric_row(
            ts=ts,
            ring_status=ring_status,
            ring_number=int(ring_number),
            geometry_source=source,
            geometry_confidence=float(conf),
            ring_cx=float(np.clip(cx, 0.0, 1079.0)),
            ring_cy=float(np.clip(cy, 0.0, 1079.0)),
            camera_cx=float(np.clip(stable_camera_x, 0.0, 1079.0)),
            camera_cy=float(np.clip(stable_camera_y, 0.0, 1079.0)),
            radius=float(max(1.0, rr)),
            zoom_ratio=float(stable_zoom),
            prev_row=prev_metric,
            signed_edges=bool(tracking_mode == "edge_residual"),
        )
        
        if float(row.get("move_dist", 0.0) or 0.0) >= 0.35:
            # #region agent log
            _debug_log(
                "post-fix",
                "H1",
                "camera_tracker.py:movement_write",
                "camera_movement_recorded",
                {
                    "timestamp": float(ts),
                    "move_dist": float(row.get("move_dist", 0.0) or 0.0),
                    "camera_x": float(row.get("camera_x", 0.0) or 0.0),
                    "camera_y": float(row.get("camera_y", 0.0) or 0.0),
                    "zoom_ratio": float(stable_zoom),
                },
            )
            # #endregion
        rows.append(row)
        prev_metric = row
    cap.release()
    # region agent log
    _debug_log_b91(
        "pre-fix-10",
        "H1,H2,H3",
        "camera_tracker.py:build_camera_rows_from_video",
        "closing_resolution_summary",
        {
            "rows": int(len(rows)),
            "closing_direct_accept_count": int(closing_direct_accept_count),
            "closing_center_only_accept_count": int(closing_center_only_accept_count),
            "closing_full_reject_count": int(closing_full_reject_count),
        },
    )
    # endregion
    return _suppress_reverting_zoom_events(rows)


def render_camera_track_preview(
    *,
    db_path: str | Path,
    output_video: str | Path,
    game_id: int,
    start_ts: float = 0.0,
    fps: float = 15.0,
) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.commit()
    rows = conn.execute(
        """
        SELECT timestamp, ring_status, ring_number, center_x, center_y, radius,
               camera_x, camera_y,
               zoom_ratio, camera_size, jump_score, jump_flag,
               x1, x2, y1, y2, x1x2, y1y2,
               roi_x1, roi_y1, roi_x2, roi_y2
        FROM CameraTrack
        WHERE game_id = ? AND timestamp >= ?
        ORDER BY timestamp
        """,
        (int(game_id), float(start_ts)),
    ).fetchall()
    conn.close()
    if not rows:
        return 1
    out_path = Path(output_video)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(out_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        max(1.0, float(fps)),
        (1080, 1080),
    )
    if not writer.isOpened():
        return 2
    for row in rows:
        frame = np.zeros((1080, 1080, 3), dtype=np.uint8)
        cx = int(round(float(row["center_x"])))
        cy = int(round(float(row["center_y"])))
        cam_x = int(round(float(row["camera_x"])))
        cam_y = int(round(float(row["camera_y"])))
        rr = max(1, int(round(float(row["radius"]))))
        status = str(row["ring_status"])

        effective_zoom = float(max(1.0, row["zoom_ratio"]))
        effective_camera_size = float(np.clip(1080.0 / max(1e-6, effective_zoom), 120.0, 1080.0))
        rx1f, ry1f, rx2f, ry2f = _camera_square_from_center(float(cam_x), float(cam_y), effective_camera_size)
        rx1 = int(round(float(np.clip(rx1f, 0.0, 1079.0))))
        ry1 = int(round(float(np.clip(ry1f, 0.0, 1079.0))))
        rx2 = int(round(float(np.clip(rx2f, 0.0, 1079.0))))
        ry2 = int(round(float(np.clip(ry2f, 0.0, 1079.0))))

        zoomed_radius = max(1, int(round(float(rr) / max(1e-6, float(effective_zoom)))))
        
        color = (40, 180, 255) if status == "closing" else (120, 120, 120)
        cv2.rectangle(frame, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
        cv2.circle(frame, (cx, cy), rr, color, 2)
        cv2.circle(frame, (cx, cy), zoomed_radius, (220, 120, 255), 1)
        cv2.circle(frame, (cx, cy), 3, (255, 255, 255), -1)
        cv2.circle(frame, (cam_x, cam_y), 3, (0, 255, 0), -1)
        
        cv2.putText(
            frame,
            f"ts={float(row['timestamp']):.2f}s status={status} ring={int(row['ring_number'])}",
            (24, 38),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"cam_zoom={effective_zoom:.3f} size={effective_camera_size:.1f} jump={int(row['jump_flag'])} score={float(row['jump_score']):.2f}",
            (24, 64),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        writer.write(frame)
    writer.release()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Camera tracking helpers.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    preview = sub.add_parser("preview", help="Render camera square preview on black 1080 canvas.")
    preview.add_argument("--db-path", default="output/camera.sqlite")
    preview.add_argument("--game-id", type=int, required=True)
    preview.add_argument("--start-ts", type=float, default=0.0)
    preview.add_argument("--fps", type=float, default=15.0)
    preview.add_argument("--output-video", default="output/map_start_roi/camera_preview.mp4")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if str(args.cmd) == "preview":
        return render_camera_track_preview(
            db_path=Path(str(args.db_path)),
            output_video=Path(str(args.output_video)),
            game_id=int(args.game_id),
            start_ts=float(args.start_ts),
            fps=float(args.fps),
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

