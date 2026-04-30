"""
Batch analysis pipeline for all teams on a selected map.

Uses proven tracking flow from stabilized team tracking modules:
- map ROI constrained initial detection
- SimpleArrowTracker with team-specific morphology/outlier settings
- frame skip from centralized runtime settings
"""

import argparse
import copy
import concurrent.futures
import json
import logging
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import uuid
from multiprocessing import Queue as MPQueue
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from concurrent.futures.process import BrokenProcessPool

import cv2
import numpy as np

try:
    import psutil
except ImportError:  # Optional dependency for performance reporting only.
    psutil = None

# Ensure project root is importable when script is run directly.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from team_tracking import config
from team_tracking.motion_detector import find_initial_position
from team_tracking.simple_arrow_tracker import SimpleArrowTracker
from team_tracking.tracking_settings import get_all_teams_for_map, get_frame_skip, get_round_windows
from runtime_paths import load_runtime_paths

logger = logging.getLogger("analysis.batch")
RUNTIME_PATHS = load_runtime_paths(PROJECT_ROOT)
JOBS_STORE_PATH = RUNTIME_PATHS["artifacts"]["jobs_store"]
MAP_ADMIN_SETTINGS_PATH = RUNTIME_PATHS["artifacts"]["map_admin_settings"]
TRACKS_OUTPUT_DIR = RUNTIME_PATHS["artifacts"]["tracks_dir"]
DEFAULT_MAP_START_DB_PATH = RUNTIME_PATHS["databases"]["map_start_detection"]


def suppress_opencv_warnings() -> None:
    try:
        if hasattr(cv2, "setLogLevel") and hasattr(cv2, "LOG_LEVEL_ERROR"):
            cv2.setLogLevel(cv2.LOG_LEVEL_ERROR)
        elif hasattr(cv2, "utils") and hasattr(cv2.utils, "logging"):
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass


@dataclass
class TrackResult:
    team_id: str
    team_name: str
    color_bgr: list[int]
    points: list[dict[str, Any]]
    eliminated: bool = False
    eliminationTimestampSec: Optional[float] = None
    eliminationFrame: Optional[int] = None
    eliminationConfidence: Optional[float] = None
    eliminationMethod: Optional[str] = None


@dataclass
class TeamRunStatus:
    team_id: str
    team_name: str
    status: str
    progress_percent: float
    last_frame: Optional[int] = None
    last_timestamp_sec: Optional[float] = None
    error: Optional[str] = None
    diagnostics: Optional[dict[str, Any]] = None


@dataclass
class TeamRunError:
    team_id: str
    team_name: str
    stage: str
    message: str


@dataclass
class TeamRunOutcome:
    result: Optional[TrackResult]
    status: TeamRunStatus
    error: Optional[TeamRunError] = None


def _load_jobs_store() -> dict[str, Any]:
    if not JOBS_STORE_PATH.exists():
        return {"jobs": []}
    try:
        return json.loads(JOBS_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"jobs": []}


def _save_jobs_store(payload: dict[str, Any]) -> None:
    JOBS_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(JOBS_STORE_PATH)


def upsert_job_record(job_id: str, patch: dict[str, Any], create_if_missing: Optional[dict[str, Any]] = None) -> None:
    payload = _load_jobs_store()
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        jobs = []
    idx = next((i for i, job in enumerate(jobs) if job.get("id") == job_id), -1)
    if idx >= 0:
        jobs[idx] = {**jobs[idx], **patch}
    elif create_if_missing is not None:
        jobs.insert(0, {**create_if_missing, **patch})
    payload["jobs"] = jobs
    _save_jobs_store(payload)


def normalize_map_name(map_name: str) -> str:
    return map_name if map_name.startswith("mp_") else f"mp_{map_name}"


def slugify_for_filename(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9_]+", "_", lowered)
    return slug.strip("_") or "unknown"


def resolve_map_number(match_id: str, provided_map_number: Optional[int]) -> int:
    if provided_map_number is not None and provided_map_number > 0:
        return int(provided_map_number)
    if not TRACKS_OUTPUT_DIR.exists():
        return 1
    pattern = re.compile(rf"^{re.escape(match_id)}_(\d+)_.*\.json$", re.IGNORECASE)
    max_num = 0
    for item in TRACKS_OUTPUT_DIR.iterdir():
        if not item.is_file():
            continue
        matched = pattern.match(item.name)
        if matched:
            max_num = max(max_num, int(matched.group(1)))
    return max_num + 1 if max_num > 0 else 1


def build_output_path(
    match_id: str,
    map_number: int,
    map_name: str,
    explicit_output: Optional[str],
) -> Path:
    if explicit_output:
        candidate = Path(explicit_output)
        return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
    TRACKS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_map_name = slugify_for_filename(normalize_map_name(map_name))
    return TRACKS_OUTPUT_DIR / f"{match_id}_{map_number}_{safe_map_name}.json"


def resolve_video_path(video_arg: Optional[str], video_name: Optional[str], records_dir: Optional[str]) -> Path:
    if video_arg:
        candidate = Path(video_arg)
    elif video_name:
        base_dir = Path(records_dir) if records_dir else (PROJECT_ROOT / "ffmpeg_downloader" / "records")
        candidate = base_dir / video_name
    else:
        raise ValueError("Provide --video or --video-name.")
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def _resolve_map_start_db_path(path_arg: Optional[str]) -> Path:
    if not path_arg:
        return DEFAULT_MAP_START_DB_PATH
    path = Path(path_arg)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def load_map_start_record(db_path: Path, video_name: str) -> Optional[dict[str, Any]]:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT video_name, video_path, map_name, map_mp_id, start_timestamp_sec, confidence, status, notes, updated_at
            FROM map_start_detection
            WHERE video_name = ?
            LIMIT 1
            """,
            (video_name,),
        ).fetchone()
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}
    finally:
        conn.close()


def load_eliminations_from_teams_db(
    db_path: Path,
    video_name: str,
) -> Optional[dict[str, dict[str, Any]]]:
    """
    Load elimination states from map_start_detection.sqlite/Teams.
    Teams table has no slot column, so we map rows by insertion order (1..20).
    """
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        detection_row = conn.execute(
            "SELECT rowid FROM map_start_detection WHERE video_name = ? LIMIT 1",
            (video_name,),
        ).fetchone()
        if detection_row is None or detection_row["rowid"] is None:
            return None
        game_id = int(detection_row["rowid"])
        rows = conn.execute(
            """
            SELECT team_name, is_eliminated, time_eliminated
            FROM Teams
            WHERE game_id = ?
            ORDER BY rowid ASC
            """,
            (game_id,),
        ).fetchall()
        if not rows:
            return None

        payload: dict[str, dict[str, Any]] = {}
        for idx, row in enumerate(rows[:20], start=1):
            team_id = f"TEAM_{idx}"
            eliminated = bool(row["is_eliminated"])
            time_eliminated = row["time_eliminated"]
            payload[team_id] = {
                "eliminated": eliminated,
                "eliminationFrame": None,
                "eliminationTimestampSec": (float(time_eliminated) if eliminated and time_eliminated is not None else None),
                "eliminationConfidence": 1.0 if eliminated else None,
                "method": "map_start_detection_teams_db",
            }

        for i in range(1, 21):
            team_id = f"TEAM_{i}"
            if team_id not in payload:
                payload[team_id] = {"eliminated": False}
        return payload
    finally:
        conn.close()


def load_round_windows_from_rings(
    db_path: Path,
    video_name: str,
    map_start_seconds: Optional[float],
    video_duration_sec: float,
) -> Optional[dict[str, dict[str, float]]]:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        detection_row = conn.execute(
            "SELECT rowid FROM map_start_detection WHERE video_name = ? LIMIT 1",
            (video_name,),
        ).fetchone()
        if detection_row is None:
            return None
        game_id = int(detection_row["rowid"])
        ring_rows = conn.execute(
            """
            SELECT ring_number, time_start, time_end
            FROM Rings
            WHERE game_id = ?
            ORDER BY ring_number ASC
            """,
            (game_id,),
        ).fetchall()
        if not ring_rows:
            return None

        ring_start_by_number: dict[int, float] = {}
        all_timestamps: list[float] = []
        for row in ring_rows:
            try:
                ring_number = int(row["ring_number"])
            except Exception:
                continue
            time_start_raw = row["time_start"]
            time_end_raw = row["time_end"]
            if time_start_raw is not None:
                ts_start = float(time_start_raw)
                ring_start_by_number[ring_number] = ts_start
                all_timestamps.append(ts_start)
            if time_end_raw is not None:
                all_timestamps.append(float(time_end_raw))

        if not all_timestamps:
            return None

        base_start = (
            max(0.0, float(map_start_seconds))
            if map_start_seconds is not None
            else max(0.0, min(all_timestamps))
        )
        round1_start = base_start
        round2_start = ring_start_by_number.get(2, ring_start_by_number.get(1, round1_start + 375.0))
        round3_start = ring_start_by_number.get(3)
        last_known_ts = max(all_timestamps)
        all_end = max(round1_start + 1.0, float(last_known_ts))
        if video_duration_sec > 0:
            all_end = min(all_end, float(video_duration_sec))

        round1_end = round2_start if round2_start > round1_start else min(all_end, round1_start + 375.0)
        if round3_start is not None and round3_start > round2_start:
            round2_end = round3_start
        else:
            round2_end = all_end
        if round2_end <= round2_start:
            round2_end = min(all_end, round2_start + 225.0)
        if round1_end <= round1_start:
            round1_end = min(all_end, round1_start + 375.0)

        return {
            "round1": {"start_sec": float(round1_start), "end_sec": float(round1_end)},
            "round2": {"start_sec": float(round2_start), "end_sec": float(round2_end)},
            "all": {"start_sec": float(round1_start), "end_sec": float(all_end)},
        }
    finally:
        conn.close()


def ensure_map_pov_ring_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS map_pov_ring (
            video_name TEXT PRIMARY KEY,
            video_path TEXT,
            map_mp_id TEXT,
            ring_x REAL,
            ring_y REAL,
            ring_radius REAL,
            ring_confidence REAL,
            ring_frame INTEGER,
            ring_timestamp_sec REAL,
            status TEXT NOT NULL,
            notes TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )


def upsert_map_pov_ring(
    conn: sqlite3.Connection,
    *,
    video_name: str,
    video_path: str,
    map_mp_id: Optional[str],
    ring_x: Optional[float],
    ring_y: Optional[float],
    ring_radius: Optional[float],
    ring_confidence: Optional[float],
    ring_frame: Optional[int],
    ring_timestamp_sec: Optional[float],
    status: str,
    notes: str,
) -> None:
    conn.execute(
        """
        INSERT INTO map_pov_ring (
            video_name, video_path, map_mp_id, ring_x, ring_y, ring_radius, ring_confidence,
            ring_frame, ring_timestamp_sec, status, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_name) DO UPDATE SET
            video_path = excluded.video_path,
            map_mp_id = excluded.map_mp_id,
            ring_x = excluded.ring_x,
            ring_y = excluded.ring_y,
            ring_radius = excluded.ring_radius,
            ring_confidence = excluded.ring_confidence,
            ring_frame = excluded.ring_frame,
            ring_timestamp_sec = excluded.ring_timestamp_sec,
            status = excluded.status,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (
            video_name,
            video_path,
            map_mp_id,
            ring_x,
            ring_y,
            ring_radius,
            ring_confidence,
            ring_frame,
            ring_timestamp_sec,
            status,
            notes,
            datetime.now().isoformat(),
        ),
    )


def _load_map_admin_settings_store() -> dict[str, Any]:
    if not MAP_ADMIN_SETTINGS_PATH.exists():
        return {"maps": {}}
    try:
        return json.loads(MAP_ADMIN_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"maps": {}}


def get_map_admin_settings(map_name: str) -> Optional[dict[str, Any]]:
    payload = _load_map_admin_settings_store()
    maps_payload = payload.get("maps")
    if not isinstance(maps_payload, dict):
        return None
    normalized_map = normalize_map_name(map_name)
    direct = maps_payload.get(normalized_map)
    if isinstance(direct, dict):
        return direct
    for item in maps_payload.values():
        if isinstance(item, dict) and normalize_map_name(str(item.get("mapName", ""))) == normalized_map:
            return item
    return None


def apply_team_hsv_overrides(
    teams: dict[str, dict[str, Any]],
    map_settings: Optional[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not teams:
        return teams
    updated = {team_id: dict(team_config) for team_id, team_config in teams.items()}
    if not map_settings:
        return updated
    team_hsv = map_settings.get("teamHsv")
    if not isinstance(team_hsv, dict):
        return updated
    for team_id, hsv_cfg in team_hsv.items():
        if team_id not in updated or not isinstance(hsv_cfg, dict):
            continue
        lower = hsv_cfg.get("lower")
        upper = hsv_cfg.get("upper")
        if (
            isinstance(lower, list)
            and isinstance(upper, list)
            and len(lower) == 3
            and len(upper) == 3
        ):
            updated[team_id]["hsv_range"] = (
                (int(lower[0]), int(lower[1]), int(lower[2])),
                (int(upper[0]), int(upper[1]), int(upper[2])),
            )
    return updated


def resolve_round_windows(
    map_name: str,
    map_settings: Optional[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    base = get_round_windows(map_name)
    normalized = {
        "round1": {
            "start_sec": float(base["round1"]["start_sec"]),
            "end_sec": float(base["round1"]["end_sec"]),
        },
        "round2": {
            "start_sec": float(base["round2"]["start_sec"]),
            "end_sec": float(base["round2"]["end_sec"]),
        },
    }
    if not isinstance(map_settings, dict):
        return normalized
    runtime = map_settings.get("runtime")
    if not isinstance(runtime, dict):
        return normalized
    windows = runtime.get("roundWindows")
    if not isinstance(windows, dict):
        return normalized
    for key in ("round1", "round2"):
        value = windows.get(key)
        if not isinstance(value, dict):
            continue
        start_sec = value.get("startSec")
        end_sec = value.get("endSec")
        if start_sec is not None:
            normalized[key]["start_sec"] = float(start_sec)
        if end_sec is not None:
            normalized[key]["end_sec"] = float(end_sec)
    return normalized


_ANSI_ESC_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(s: str) -> str:
    return _ANSI_ESC_RE.sub("", s)


def _want_status_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR", "") == ""


def _fmt_team_pct(team_id: str, pct_raw: float) -> str:
    pct = float(pct_raw)
    if pct >= 99.5:
        pct_s = "100%"
    elif pct <= 0.05:
        pct_s = "0%"
    else:
        pct_s = f"{pct:.1f}%"
    if not _want_status_color():
        return f"{team_id}:{pct_s}"
    if pct >= 99.5:
        return f"\033[32m{team_id}:{pct_s}\033[0m"
    if pct >= 1.0:
        return f"\033[33m{team_id}:{pct_s}\033[0m"
    return f"\033[2m{team_id}:{pct_s}\033[0m"


def _sorted_team_ids(team_configs: dict[str, dict[str, Any]]) -> list[str]:
    def _team_num(team_id: str) -> int:
        try:
            return int(team_id.replace("TEAM_", ""))
        except Exception:
            return 10**9

    return sorted(team_configs.keys(), key=_team_num)


def _build_panel_slots() -> dict[str, tuple[int, int, int, int]]:
    """
    Build 20 side-panel slots:
    left 1-10, right 11-20, top->bottom.
    """
    slots: dict[str, tuple[int, int, int, int]] = {}
    list_top_ratio = 0.24
    list_bottom_ratio = 0.94
    for side_name, roi, start_idx in (
        ("left", config.LEFT_PANEL_ROI, 1),
        ("right", config.RIGHT_PANEL_ROI, 11),
    ):
        x, y, w, h = roi
        list_y0 = y + int(round(h * list_top_ratio))
        list_y1 = y + int(round(h * list_bottom_ratio))
        list_h = max(10, list_y1 - list_y0)
        row_h = list_h / 10.0
        for i in range(10):
            top = int(round(list_y0 + i * row_h))
            bottom = int(round(list_y0 + (i + 1) * row_h))
            margin_y = max(1, int((bottom - top) * 0.16))
            margin_x = max(1, int(w * 0.08))
            slot_x = x + margin_x
            slot_y = top + margin_y
            slot_w = max(6, w - margin_x * 2)
            slot_h = max(6, (bottom - top) - margin_y * 2)
            team_id = f"TEAM_{start_idx + i}"
            slots[team_id] = (slot_x, slot_y, slot_w, slot_h)
            _ = side_name
    return slots


def _build_panel_slots_with_geometry(
    left_top: int,
    left_row_h: int,
    right_top: int,
    right_row_h: int,
) -> dict[str, tuple[int, int, int, int]]:
    slots: dict[str, tuple[int, int, int, int]] = {}
    for roi, start_idx, top, row_h in (
        (config.LEFT_PANEL_ROI, 1, left_top, left_row_h),
        (config.RIGHT_PANEL_ROI, 11, right_top, right_row_h),
    ):
        x, y, w, h = roi
        row_h = max(8, int(row_h))
        top = int(max(y, min(y + h - row_h * 10, top)))
        for i in range(10):
            row_y0 = top + i * row_h
            row_y1 = row_y0 + row_h
            # Use compact pixel ROI around colored team plate only.
            margin_y = max(1, int((row_y1 - row_y0) * 0.24))
            slot_x = x + max(1, int(w * 0.03))
            slot_y = row_y0 + margin_y
            slot_w = max(18, int(w * 0.34))
            slot_h = max(6, (row_y1 - row_y0) - margin_y * 2)
            team_id = f"TEAM_{start_idx + i}"
            slots[team_id] = (slot_x, slot_y, slot_w, slot_h)
    return slots


def _bgr_to_hsv_triplet(color_bgr: tuple[int, int, int]) -> tuple[int, int, int]:
    pixel = np.array([[[int(color_bgr[0]), int(color_bgr[1]), int(color_bgr[2])]]], dtype=np.uint8)
    hsv = cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)
    return int(hsv[0, 0, 0]), int(hsv[0, 0, 1]), int(hsv[0, 0, 2])


def _circular_hue_distance(h: np.ndarray, target_h: int) -> np.ndarray:
    d = np.abs(h.astype(np.int16) - int(target_h))
    return np.minimum(d, 180 - d).astype(np.float32)


def _detect_team_color_square_in_roi(
    roi: np.ndarray,
    target_bgr: tuple[int, int, int],
) -> tuple[int, int, int, int]:
    if roi.size == 0:
        return (0, 0, 0, 0)
    h_px, w_px = roi.shape[:2]
    if h_px < 4 or w_px < 4:
        return (0, 0, w_px, h_px)

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    th, _ts, _tv = _bgr_to_hsv_triplet(target_bgr)
    hue_dist = _circular_hue_distance(hsv[:, :, 0], th)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]

    loose_mask = ((hue_dist <= 22.0) & (sat >= 22) & (val >= 18)).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    loose_mask = cv2.morphologyEx(loose_mask, cv2.MORPH_OPEN, kernel)
    loose_mask = cv2.morphologyEx(loose_mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(loose_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_bbox = (0, 0, w_px, h_px)
    best_score = -1.0
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = float(w * h)
        if area < 12:
            continue
        area_norm = area / max(1.0, float(w_px * h_px))
        if area_norm > 0.75:
            continue
        aspect = float(w) / max(1.0, float(h))
        aspect_score = max(0.0, 1.0 - abs(aspect - 1.0) / 1.25)
        left_bias = max(0.0, 1.0 - (float(x) / max(1.0, float(w_px))))
        size_score = 1.0 - min(1.0, abs(area_norm - 0.14) / 0.14)
        score = size_score * 0.40 + aspect_score * 0.30 + left_bias * 0.30
        if score > best_score:
            best_score = score
            best_bbox = (x, y, w, h)

    return best_bbox


def _presence_score_in_slot(
    frame: np.ndarray,
    slot: tuple[int, int, int, int],
    target_bgr: tuple[int, int, int],
    color_square_rel: Optional[tuple[float, float, float, float]] = None,
) -> float:
    x, y, w, h = slot
    x2 = min(frame.shape[1], x + w)
    y2 = min(frame.shape[0], y + h)
    x = max(0, x)
    y = max(0, y)
    if x >= x2 or y >= y2:
        return 0.0
    roi = frame[y:y2, x:x2]
    if roi.size == 0:
        return 0.0

    # Track only team color square (pixel-extracted at calibration).
    if color_square_rel is not None:
        rx, ry, rw, rh = color_square_rel
        sx = int(max(0.0, min(roi.shape[1] - 1, rx * roi.shape[1])))
        sy = int(max(0.0, min(roi.shape[0] - 1, ry * roi.shape[0])))
        sw = int(max(4, min(roi.shape[1] - sx, rw * roi.shape[1])))
        sh = int(max(4, min(roi.shape[0] - sy, rh * roi.shape[0])))
        roi = roi[sy : sy + sh, sx : sx + sw]
        if roi.size == 0:
            return 0.0
    else:
        sq_x, sq_y, sq_w, sq_h = _detect_team_color_square_in_roi(roi, target_bgr)
        sq_w = max(4, min(roi.shape[1] - sq_x, sq_w))
        sq_h = max(4, min(roi.shape[0] - sq_y, sq_h))
        roi = roi[sq_y : sq_y + sq_h, sq_x : sq_x + sq_w]
        if roi.size == 0:
            return 0.0

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    target_pixel = np.array([[[int(target_bgr[0]), int(target_bgr[1]), int(target_bgr[2])]]], dtype=np.uint8)
    target_lab = cv2.cvtColor(target_pixel, cv2.COLOR_BGR2LAB)[0, 0, :].astype(np.float32)
    th, _ts, _tv = _bgr_to_hsv_triplet(target_bgr)

    h = hsv[:, :, 0].astype(np.int16)
    s = hsv[:, :, 1].astype(np.int16)
    v = hsv[:, :, 2].astype(np.int16)
    hue_dist = _circular_hue_distance(h, th)
    lab_dist = np.linalg.norm(lab.astype(np.float32) - target_lab.reshape(1, 1, 3), axis=2)

    # Pixel-level team separation: strict and soft masks against team color.
    strict_mask = (hue_dist <= 12.0) & (s >= 45) & (v >= 24) & (lab_dist <= 55.0)
    soft_mask = (hue_dist <= 18.0) & (s >= 28) & (v >= 20) & (lab_dist <= 72.0)
    strict_ratio = float(np.count_nonzero(strict_mask)) / max(1.0, float(strict_mask.size))
    soft_ratio = float(np.count_nonzero(soft_mask)) / max(1.0, float(soft_mask.size))

    use_mask = soft_mask if np.count_nonzero(soft_mask) >= 12 else ((s >= 22) & (v >= 18))
    if np.count_nonzero(use_mask) > 0:
        dom_h = float(np.median(h[use_mask]))
        sat_norm = float(np.mean(s[use_mask])) / 255.0
        val_norm = float(np.mean(v[use_mask])) / 255.0
    else:
        dom_h = float(np.median(h))
        sat_norm = float(np.mean(s)) / 255.0
        val_norm = float(np.mean(v)) / 255.0

    hue_shift_norm = min(1.0, float(min(abs(dom_h - th), 180 - abs(dom_h - th))) / 22.0)
    rgb_std = np.std(roi.astype(np.float32), axis=2)
    chroma_norm = max(0.0, min(1.0, float(np.mean(rgb_std)) / 64.0))
    color_stability = 1.0 - hue_shift_norm
    score = strict_ratio * 0.50 + soft_ratio * 0.25 + color_stability * 0.18 + sat_norm * 0.05 + chroma_norm * 0.02
    return float(max(0.0, min(1.0, score)))


def _calibrate_team_slots_from_frame(
    frame: np.ndarray,
    team_ids: list[str],
    team_colors: dict[str, tuple[int, int, int]],
) -> dict[str, tuple[int, int, int, int]]:
    default_slots = _build_panel_slots()

    def optimize_panel(roi: tuple[int, int, int, int], panel_team_ids: list[str]) -> tuple[int, int]:
        x, y, w, h = roi
        if len(panel_team_ids) != 10:
            return y + int(h * 0.24), int((h * 0.70) / 10.0)
        top_candidates = range(int(y + h * 0.14), int(y + h * 0.34), max(2, int(h * 0.008)))
        row_candidates = range(max(20, int(h * 0.050)), max(24, int(h * 0.082)), max(1, int(h * 0.0025)))

        best_score = -1.0
        best_top = y + int(h * 0.24)
        best_row_h = int((h * 0.70) / 10.0)
        for top in top_candidates:
            for row_h in row_candidates:
                if top + row_h * 10 > (y + int(h * 0.97)):
                    continue
                trial_slots = _build_panel_slots_with_geometry(top, row_h, top, row_h)
                score_sum = 0.0
                for team_id in panel_team_ids:
                    slot = trial_slots.get(team_id)
                    if slot is None:
                        continue
                    score_sum += _presence_score_in_slot(frame, slot, team_colors[team_id])
                if score_sum > best_score:
                    best_score = score_sum
                    best_top = top
                    best_row_h = row_h
        return best_top, best_row_h

    left_ids = [f"TEAM_{i}" for i in range(1, 11) if f"TEAM_{i}" in team_ids]
    right_ids = [f"TEAM_{i}" for i in range(11, 21) if f"TEAM_{i}" in team_ids]
    left_top, left_row_h = optimize_panel(config.LEFT_PANEL_ROI, left_ids if len(left_ids) == 10 else [f"TEAM_{i}" for i in range(1, 11)])
    right_top, right_row_h = optimize_panel(config.RIGHT_PANEL_ROI, right_ids if len(right_ids) == 10 else [f"TEAM_{i}" for i in range(11, 21)])
    calibrated = _build_panel_slots_with_geometry(left_top, left_row_h, right_top, right_row_h)

    # Keep default fallback if generated slots are invalid for any team.
    for team_id in team_ids:
        if team_id not in calibrated:
            return default_slots
    return calibrated


def detect_team_eliminations_timeline(
    video_path: str,
    fps: float,
    team_configs: dict[str, dict[str, Any]],
    start_seconds: float,
    end_seconds: float,
    coarse_step_frames: int = 10000,
    refine_step_frames: int = 1000,
    tolerance_frames: int = 300,
) -> dict[str, dict[str, Any]]:
    """
    Detect team elimination moments by side-panel color disappearance.
    Coarse pass (10000) -> refine pass (1000) -> binary tighten to tolerance.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {team_id: {"eliminated": False} for team_id in team_configs.keys()}

    team_ids = _sorted_team_ids(team_configs)
    team_colors: dict[str, tuple[int, int, int]] = {}
    for team_id in team_ids:
        cfg = team_configs.get(team_id, {})
        color_bgr = cfg.get("display_color_bgr", cfg.get("color_bgr", (180, 180, 180)))
        team_colors[team_id] = (int(color_bgr[0]), int(color_bgr[1]), int(color_bgr[2]))

    start_frame = max(0, int(start_seconds * fps))
    end_frame = max(start_frame, int(end_seconds * fps))
    coarse_step = max(1, int(coarse_step_frames))
    refine_step = max(1, int(refine_step_frames))
    tolerance = max(1, int(tolerance_frames))
    frame_score_cache: dict[int, dict[str, float]] = {}
    team_color_square_rel: dict[str, tuple[float, float, float, float]] = {}

    slots = _build_panel_slots()
    calibration_frame = start_frame + min(int(max(0, end_frame - start_frame) * 0.15), int(max(1.0, fps) * 60))
    calibration_frame = int(max(start_frame, min(end_frame, calibration_frame)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, calibration_frame)
    calib_ok, calib_frame = cap.read()
    if calib_ok and calib_frame is not None:
        slots = _calibrate_team_slots_from_frame(calib_frame, team_ids, team_colors)
        for team_id in team_ids:
            slot = slots.get(team_id)
            if slot is None:
                continue
            x, y, w, h = slot
            x2 = min(calib_frame.shape[1], x + w)
            y2 = min(calib_frame.shape[0], y + h)
            x = max(0, x)
            y = max(0, y)
            if x >= x2 or y >= y2:
                continue
            slot_roi = calib_frame[y:y2, x:x2]
            if slot_roi.size == 0:
                continue
            sq_x, sq_y, sq_w, sq_h = _detect_team_color_square_in_roi(slot_roi, team_colors[team_id])
            sq_w = max(4, min(slot_roi.shape[1] - sq_x, sq_w))
            sq_h = max(4, min(slot_roi.shape[0] - sq_y, sq_h))
            if sq_w <= 0 or sq_h <= 0:
                continue
            team_color_square_rel[team_id] = (
                float(sq_x) / max(1.0, float(slot_roi.shape[1])),
                float(sq_y) / max(1.0, float(slot_roi.shape[0])),
                float(sq_w) / max(1.0, float(slot_roi.shape[1])),
                float(sq_h) / max(1.0, float(slot_roi.shape[0])),
            )
        logger.info("[elim] slot calibration frame=%d applied.", calibration_frame)
    else:
        logger.warning("[elim] slot calibration skipped: cannot read frame=%d, using default slots.", calibration_frame)

    def sample_scores_at(frame_idx: int) -> dict[str, float]:
        frame_idx = int(max(start_frame, min(end_frame, frame_idx)))
        if frame_idx in frame_score_cache:
            return frame_score_cache[frame_idx]
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret or frame is None:
            scores = {team_id: 0.0 for team_id in team_ids}
            frame_score_cache[frame_idx] = scores
            return scores
        scores: dict[str, float] = {}
        for team_id in team_ids:
            slot = slots.get(team_id)
            if slot is None:
                scores[team_id] = 0.0
                continue
            scores[team_id] = _presence_score_in_slot(
                frame,
                slot,
                team_colors[team_id],
                team_color_square_rel.get(team_id),
            )
        frame_score_cache[frame_idx] = scores
        return scores

    coarse_frames_asc = list(range(start_frame, end_frame + 1, coarse_step))
    if not coarse_frames_asc or coarse_frames_asc[-1] != end_frame:
        coarse_frames_asc.append(end_frame)
    coarse_frames_desc = sorted(set(coarse_frames_asc), reverse=True)

    coarse_samples_desc: dict[str, list[tuple[int, float]]] = {team_id: [] for team_id in team_ids}
    logger.info(
        "[elim] reverse coarse scan start: frames=%d..%d step=%d refine_step=%d tol=%d teams=%d",
        start_frame,
        end_frame,
        coarse_step,
        refine_step,
        tolerance,
        len(team_ids),
    )
    for probe_team in ("TEAM_1", "TEAM_10", "TEAM_11", "TEAM_20"):
        slot = slots.get(probe_team)
        if slot is not None:
            logger.info("[elim] calibrated slot %s: x=%d y=%d w=%d h=%d", probe_team, slot[0], slot[1], slot[2], slot[3])
        color_sq = team_color_square_rel.get(probe_team)
        if color_sq is not None:
            logger.info(
                "[elim] calibrated color square %s: rx=%.3f ry=%.3f rw=%.3f rh=%.3f",
                probe_team,
                color_sq[0],
                color_sq[1],
                color_sq[2],
                color_sq[3],
            )
    for frame_idx in coarse_frames_desc:
        scores = sample_scores_at(frame_idx)
        for team_id in team_ids:
            coarse_samples_desc[team_id].append((frame_idx, float(scores.get(team_id, 0.0))))

    results: dict[str, dict[str, Any]] = {}
    baseline_calibration_points = min(6, len(coarse_frames_asc))

    for team_id in team_ids:
        samples_desc = coarse_samples_desc[team_id]
        samples_asc = sorted(samples_desc, key=lambda item: item[0])
        baseline_scores = [score for _frame, score in samples_asc[:baseline_calibration_points]]
        global_peak = max([0.0] + [score for _frame, score in samples_desc])
        baseline = max([0.0] + baseline_scores + [global_peak * 0.85])
        # Pixel-level color separation: keep threshold stricter to catch subtle color loss.
        threshold = max(0.06, baseline * 0.52)

        end_score = samples_desc[0][1] if samples_desc else 0.0
        logger.debug(
            "[elim/%s] baseline=%.4f threshold=%.4f end_score=%.4f",
            team_id,
            baseline,
            threshold,
            end_score,
        )
        # If team is still alive at end of window, no elimination in this window.
        if end_score >= threshold:
            logger.debug("[elim/%s] no elimination in window: team alive at end.", team_id)
            results[team_id] = {
                "eliminated": False,
                "baselineScore": round(float(baseline), 4),
                "threshold": round(float(threshold), 4),
                "endScore": round(float(end_score), 4),
            }
            continue

        coarse_transition_idx: Optional[int] = None
        for idx in range(1, len(samples_desc)):
            newer_dead_score = samples_desc[idx - 1][1]
            older_score = samples_desc[idx][1]
            if newer_dead_score < threshold and older_score >= threshold:
                coarse_transition_idx = idx
                break
        if coarse_transition_idx is None:
            # Team appears dead for entire selected window.
            logger.debug(
                "[elim/%s] dead on all sampled reverse frames; marking elimination at window start frame=%d",
                team_id,
                start_frame,
            )
            results[team_id] = {
                "eliminated": True,
                "eliminationFrame": int(start_frame),
                "eliminationTimestampSec": round(float(start_frame / fps), 3),
                "eliminationConfidence": 0.35,
                "method": "side_panel_pixel_color_shift_dead_at_window_start",
                "baselineScore": round(float(baseline), 4),
                "threshold": round(float(threshold), 4),
                "endScore": round(float(end_score), 4),
            }
            continue

        newer_dead_frame = samples_desc[coarse_transition_idx - 1][0]
        older_alive_frame = samples_desc[coarse_transition_idx][0]
        logger.debug(
            "[elim/%s] reverse coarse transition found: alive_frame=%d -> dead_frame=%d",
            team_id,
            older_alive_frame,
            newer_dead_frame,
        )
        low_frame = min(older_alive_frame, newer_dead_frame)
        high_frame = max(older_alive_frame, newer_dead_frame)
        found_refined = False
        candidate_frame = high_frame
        logger.debug(
            "[elim/%s] refine search start in [%d, %d] step=%d",
            team_id,
            low_frame,
            high_frame,
            refine_step,
        )
        for fidx in range(low_frame, high_frame + 1, refine_step):
            score = sample_scores_at(fidx).get(team_id, 0.0)
            if score < threshold:
                candidate_frame = fidx
                high_frame = fidx
                found_refined = True
                logger.debug("[elim/%s] refine hit at frame=%d score=%.4f", team_id, fidx, score)
                break
            low_frame = fidx
        if not found_refined:
            candidate_frame = high_frame
            logger.debug("[elim/%s] refine did not hit directly; fallback candidate frame=%d", team_id, candidate_frame)

        while (high_frame - low_frame) > tolerance:
            mid = (low_frame + high_frame) // 2
            score = sample_scores_at(mid).get(team_id, 0.0)
            if score < threshold:
                high_frame = mid
                candidate_frame = mid
            else:
                low_frame = mid + 1

        final_frame = int(max(start_frame, min(end_frame, candidate_frame)))
        final_score = float(sample_scores_at(final_frame).get(team_id, 0.0))
        confidence = 0.0
        if threshold > 1e-6:
            confidence = max(0.0, min(1.0, (threshold - final_score) / threshold))
        results[team_id] = {
            "eliminated": True,
            "eliminationFrame": final_frame,
            "eliminationTimestampSec": round(float(final_frame / fps), 3),
            "eliminationConfidence": round(float(confidence), 3),
            "method": "side_panel_pixel_color_shift_coarse10000_refine1000_tol300",
            "baselineScore": round(float(baseline), 4),
            "threshold": round(float(threshold), 4),
            "endScore": round(float(end_score), 4),
            "coarseDeadFrame": int(newer_dead_frame),
            "coarseAliveFrame": int(older_alive_frame),
        }
        logger.info(
            "[elim/%s] final elimination frame=%d ts=%.3fs confidence=%.3f",
            team_id,
            final_frame,
            final_frame / fps,
            confidence,
        )

    cap.release()
    logger.info("[elim] reverse coarse scan completed.")
    return results


def load_zone_rules(zones_file: Optional[str], map_name: str) -> Optional[dict[str, Any]]:
    """Load zone polygons for map-level filtering (forbidden areas)."""
    normalized_map = normalize_map_name(map_name)
    candidate_paths: list[Path] = []

    if zones_file:
        candidate_paths.append(Path(zones_file))
    else:
        candidate_paths.append(PROJECT_ROOT / "output" / "zones" / f"{normalized_map}.zones.json")

    resolved_path: Optional[Path] = None
    for candidate in candidate_paths:
        path = candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
        if path.exists():
            resolved_path = path
            break

    if resolved_path is None:
        logger.info("Zone filter: file not found, skipping zone filtering.")
        return None

    with resolved_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    image_size = payload.get("image_size", {})
    zone_width = float(image_size.get("width", config.MAP_ROI[2]))
    zone_height = float(image_size.get("height", config.MAP_ROI[3]))
    zones = payload.get("zones", [])
    forbidden_polygons = [
        zone["polygon"]
        for zone in zones
        if zone.get("type") == "forbidden" and isinstance(zone.get("polygon"), list) and len(zone["polygon"]) >= 3
    ]

    logger.info(
        "Zone filter loaded: %s (forbidden polygons: %d)",
        resolved_path,
        len(forbidden_polygons),
    )
    if not forbidden_polygons:
        return None

    return {
        "map": payload.get("map", normalized_map),
        "path": str(resolved_path),
        "width": zone_width,
        "height": zone_height,
        "forbidden_polygons": forbidden_polygons,
    }


def load_zone_preview(zones_file: Optional[str], map_name: str) -> Optional[dict[str, Any]]:
    """Load full polygon zones for debug visualization on map ROI."""
    normalized_map = normalize_map_name(map_name)
    candidate_paths: list[Path] = []
    if zones_file:
        candidate_paths.append(Path(zones_file))
    else:
        candidate_paths.append(PROJECT_ROOT / "output" / "zones" / f"{normalized_map}.zones.json")

    resolved_path: Optional[Path] = None
    for candidate in candidate_paths:
        path = candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
        if path.exists():
            resolved_path = path
            break
    if resolved_path is None:
        return None

    try:
        with resolved_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception:
        return None

    image_size = payload.get("image_size", {})
    zone_width = float(image_size.get("width", config.MAP_ROI[2]))
    zone_height = float(image_size.get("height", config.MAP_ROI[3]))
    zones_raw = payload.get("zones", [])
    zones: list[dict[str, Any]] = []
    for zone in zones_raw:
        polygon = zone.get("polygon")
        if not isinstance(polygon, list) or len(polygon) < 3:
            continue
        zone_type = str(zone.get("type", "unknown"))
        zones.append(
            {
                "id": str(zone.get("id", "")),
                "type": zone_type,
                "polygon": polygon,
            }
        )
    if not zones:
        return None
    return {
        "map": payload.get("map", normalized_map),
        "path": str(resolved_path),
        "width": zone_width,
        "height": zone_height,
        "zones": zones,
    }


def filter_points_by_forbidden_zones(
    points: list[dict[str, Any]],
    zone_rules: Optional[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Remove points that fall inside forbidden polygons."""
    if not points or not zone_rules:
        return points, 0

    map_x, map_y, map_w, map_h = config.MAP_ROI
    zone_w = max(1e-6, float(zone_rules["width"]))
    zone_h = max(1e-6, float(zone_rules["height"]))
    polygons = [np.array(poly, dtype=np.float32) for poly in zone_rules["forbidden_polygons"]]

    filtered: list[dict[str, Any]] = []
    removed = 0
    for point in points:
        px = float(point.get("x", 0.0))
        py = float(point.get("y", 0.0))

        local_x = ((px - map_x) / map_w) * zone_w
        local_y = ((py - map_y) / map_h) * zone_h

        inside_forbidden = any(cv2.pointPolygonTest(poly, (local_x, local_y), False) >= 0 for poly in polygons)
        if inside_forbidden:
            removed += 1
            continue
        filtered.append(point)

    return filtered, removed


def build_tracker_zone_gate(
    zone_rules: Optional[dict[str, Any]],
) -> tuple[list[np.ndarray], Optional[tuple[float, float]]]:
    """Prepare forbidden polygons for in-tracker gating."""
    if not zone_rules:
        return [], None
    polygons = [
        np.array(poly, dtype=np.float32)
        for poly in zone_rules.get("forbidden_polygons", [])
        if isinstance(poly, list) and len(poly) >= 3
    ]
    if not polygons:
        return [], None
    zone_w = float(zone_rules.get("width", config.MAP_ROI[2]))
    zone_h = float(zone_rules.get("height", config.MAP_ROI[3]))
    return polygons, (zone_w, zone_h)


class PerformanceSampler:
    """Collect min/max system load metrics while workload runs."""

    def __init__(self, sample_interval_sec: float = 1.0):
        if psutil is None:
            raise RuntimeError(
                "Performance sampling requires 'psutil'. Install dependencies: "
                "pip install -r services/analysis/requirements.txt"
            )
        self.sample_interval_sec = sample_interval_sec
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.samples: list[dict[str, Optional[float]]] = []
        self._last_disk = None
        self._last_time = 0.0
        self._gpu_enabled = False
        self._nvml = None
        self._nvml_handle = None

    def _setup_gpu(self) -> None:
        try:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            self._nvml = pynvml
            if pynvml.nvmlDeviceGetCount() > 0:
                self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._gpu_enabled = True
        except Exception:
            self._gpu_enabled = False

    def _read_gpu_percent(self) -> Optional[float]:
        if not self._gpu_enabled or self._nvml is None or self._nvml_handle is None:
            return None
        try:
            util = self._nvml.nvmlDeviceGetUtilizationRates(self._nvml_handle)
            return float(util.gpu)
        except Exception:
            return None

    def _sample_once(self) -> None:
        now = time.perf_counter()
        cpu_percent = float(psutil.cpu_percent(interval=None))
        ram_percent = float(psutil.virtual_memory().percent)
        gpu_percent = self._read_gpu_percent()

        disk = psutil.disk_io_counters()
        read_mbps: Optional[float] = None
        write_mbps: Optional[float] = None
        total_mbps: Optional[float] = None

        if self._last_disk is not None and disk is not None:
            dt = max(1e-6, now - self._last_time)
            read_mbps = float((disk.read_bytes - self._last_disk.read_bytes) / dt / 1024 / 1024)
            write_mbps = float((disk.write_bytes - self._last_disk.write_bytes) / dt / 1024 / 1024)
            total_mbps = float(read_mbps + write_mbps)

        self._last_disk = disk
        self._last_time = now
        self.samples.append(
            {
                "cpu_percent": cpu_percent,
                "ram_percent": ram_percent,
                "gpu_percent": gpu_percent,
                "ssd_read_mbps": read_mbps,
                "ssd_write_mbps": write_mbps,
                "ssd_total_mbps": total_mbps,
            }
        )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._sample_once()
            self._stop_event.wait(self.sample_interval_sec)

    def start(self) -> None:
        self._setup_gpu()
        psutil.cpu_percent(interval=None)
        self._last_disk = psutil.disk_io_counters()
        self._last_time = time.perf_counter()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass

    def summarize(self) -> dict[str, Optional[dict[str, float]]]:
        def min_max(metric: str) -> Optional[dict[str, float]]:
            vals = [
                float(sample[metric])
                for sample in self.samples
                if sample.get(metric) is not None
            ]
            if not vals:
                return None
            return {"min": round(min(vals), 3), "max": round(max(vals), 3)}

        return {
            "cpu_percent": min_max("cpu_percent"),
            "ram_percent": min_max("ram_percent"),
            "gpu_percent": min_max("gpu_percent"),
            "ssd_read_mbps": min_max("ssd_read_mbps"),
            "ssd_write_mbps": min_max("ssd_write_mbps"),
            "ssd_total_mbps": min_max("ssd_total_mbps"),
        }


class RunContextFilter(logging.Filter):
    def __init__(self, command_line: str, started_perf: Optional[float] = None, started_wall: Optional[float] = None):
        super().__init__()
        self.command_line = command_line
        self.started_perf = started_perf
        self.started_wall = started_wall

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_command = self.command_line
        if self.started_perf is not None:
            record.run_elapsed = time.perf_counter() - self.started_perf
        elif self.started_wall is not None:
            record.run_elapsed = time.time() - self.started_wall
        else:
            record.run_elapsed = 0.0
        return True


class ProgressLogger:
    """Emit per-team progress on time interval."""

    def __init__(
        self,
        team_id: str,
        team_name: str,
        total_steps: int,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
        progress_interval_sec: float = 1.0,
    ):
        self.team_id = team_id
        self.team_name = team_name
        self.total_steps = max(1, int(total_steps))
        self.progress_interval_sec = max(0.1, float(progress_interval_sec))
        self.last_emit_monotonic = 0.0
        self.last_percent = -1
        self.on_progress = on_progress

    def maybe_log(self, processed_steps: int, frame_num: int, video_timestamp: float) -> None:
        percent = int(min(100, max(0, (processed_steps * 100) // self.total_steps)))
        now = time.monotonic()
        should_emit = (
            self.last_emit_monotonic == 0.0
            or (now - self.last_emit_monotonic) >= self.progress_interval_sec
            or percent >= 100
        )
        if not should_emit:
            return
        if percent == self.last_percent and (now - self.last_emit_monotonic) < (self.progress_interval_sec * 1.5):
            return
        if self.on_progress is not None:
            self.on_progress(percent, frame_num, video_timestamp)
        self.last_emit_monotonic = now
        self.last_percent = percent


def save_map_background(video_path: str, map_name: str, start_seconds: float = 0.0) -> Optional[str]:
    """Save map ROI from selected video frame for web player background."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    start_frame = max(0, int(max(0.0, float(start_seconds)) * float(fps)))
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None

    map_x, map_y, map_w, map_h = config.MAP_ROI
    map_frame = frame[map_y:map_y + map_h, map_x:map_x + map_w]
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = f"map_background_{map_name}.png".replace("/", "_")
    output_path = output_dir / output_name
    cv2.imwrite(str(output_path), map_frame)
    return str(output_path)


def detect_white_ring_timeline(
    video_path: str,
    fps: float,
    map_id: str,
    map_name: str,
    start_seconds: float,
    end_seconds: Optional[float],
    max_seconds: Optional[float],
    round_windows: Optional[dict[str, Any]] = None,
    sample_step_frames: int = 1000,
    ring_config: Optional[dict[str, Any]] = None,
    visualize: bool = False,
) -> list[dict[str, Any]]:
    """
    Detect mostly-static white ring positions per round segment.
    Samples one frame every N frames (default 1000), aggregates candidates,
    and returns one stable circle per segment (round1/round2).
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    effective_windows = round_windows or get_round_windows(map_name)
    round2_start = float(effective_windows["round2"]["start_sec"])
    start_frame = max(0, int(start_seconds * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    end_limit = end_seconds
    if end_limit is None:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        end_limit = (total_frames / fps) if total_frames > 0 else start_seconds
    if max_seconds is not None:
        end_limit = min(end_limit, max_seconds)

    map_x, map_y, map_w, map_h = config.MAP_ROI
    frame_skip = max(1, int(sample_step_frames))
    timeline: list[dict[str, Any]] = []
    last_segment: Optional[int] = None
    segment_candidates: dict[int, list[tuple[float, float, float, float, float]]] = {1: [], 2: []}
    ring_cfg = ring_config or {}
    ring_hsv_lower = np.array(ring_cfg.get("hsvLower", [0, 0, 67]), dtype=np.uint8)
    ring_hsv_upper = np.array(ring_cfg.get("hsvUpper", [180, 68, 89]), dtype=np.uint8)
    ring_gray_min = int(ring_cfg.get("grayMin", 68))
    ring_gray_max = int(ring_cfg.get("grayMax", 88))
    ring_morph_k = max(1, int(ring_cfg.get("morphK", 1)))
    ring_blur_k = max(1, int(ring_cfg.get("blurK", 13)))
    if ring_blur_k % 2 == 0:
        ring_blur_k += 1
    ring_hough_p2 = int(ring_cfg.get("houghP2", 100))
    ring_min_r_pct = float(ring_cfg.get("minRPct", 4))
    ring_max_r_pct = float(ring_cfg.get("maxRPct", 52))

    frame_num = start_frame
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_num += 1
        if frame_num % frame_skip != 0:
            continue
        timestamp = frame_num / fps
        if timestamp > end_limit:
            break

        map_frame = frame[map_y : map_y + map_h, map_x : map_x + map_w]
        if map_frame.size == 0:
            continue

        segment = 1 if timestamp < round2_start else 2
        if segment != last_segment:
            logger.info("Ring detector reset for segment %d at %.1fs", segment, timestamp)
            last_segment = segment

        hsv = cv2.cvtColor(map_frame, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(map_frame, cv2.COLOR_BGR2GRAY)
        # White ring on minimap is near-neutral (low S) and bright (high V).
        mask_hsv = cv2.inRange(hsv, ring_hsv_lower, ring_hsv_upper)
        mask_gray = cv2.inRange(gray, ring_gray_min, ring_gray_max)
        mask = cv2.bitwise_and(mask_hsv, mask_gray)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ring_morph_k, ring_morph_k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        ring_like = cv2.GaussianBlur(mask, (ring_blur_k, ring_blur_k), 1.6)
        circles = cv2.HoughCircles(
            ring_like,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(20, map_w // 6),
            param1=90,
            param2=ring_hough_p2,
            minRadius=max(5, int(map_w * (ring_min_r_pct / 100.0))),
            maxRadius=max(10, int(map_w * (ring_max_r_pct / 100.0))),
        )
        if circles is None:
            continue

        best: Optional[tuple[float, float, float, float]] = None
        best_score = -1e9
        for circle in circles[0]:
            cx = float(circle[0])
            cy = float(circle[1])
            radius = float(circle[2])
            ring_area = np.pi * (radius ** 2)
            area_ratio = ring_area / max(1.0, float(map_w * map_h))
            if area_ratio < 0.01 or area_ratio > 0.70:
                continue
            score = radius
            # Static circle is usually in the inner map area.
            score -= np.hypot(cx - (map_w / 2.0), cy - (map_h / 2.0)) * 0.10
            if score > best_score:
                best_score = score
                best = (cx, cy, radius, max(0.2, min(1.0, area_ratio * 2.5)))

        if best is None:
            continue
        cx, cy, radius, confidence = best
        segment_candidates[segment].append((timestamp, cx, cy, radius, confidence))

        if visualize:
            ring_viz = map_frame.copy()
            cv2.circle(ring_viz, (int(round(cx)), int(round(cy))), int(round(radius)), (255, 255, 255), 2)
            cv2.putText(
                ring_viz,
                f"seg={segment} t={timestamp:.1f}s r={radius:.1f} conf={confidence:.2f}",
                (8, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
            )
            cv2.imshow("3. Ring Detection", ring_viz)
            cv2.imshow("4. Ring Mask HSV", mask)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == 27:
                break

    segment_start_time = {
        1: max(start_seconds, float(effective_windows["round1"]["start_sec"])),
        2: max(start_seconds, round2_start),
    }
    for segment in (1, 2):
        candidates = segment_candidates.get(segment, [])
        if not candidates:
            continue
        cxs = np.array([item[1] for item in candidates], dtype=np.float32)
        cys = np.array([item[2] for item in candidates], dtype=np.float32)
        rs = np.array([item[3] for item in candidates], dtype=np.float32)
        confs = np.array([item[4] for item in candidates], dtype=np.float32)
        cx = float(np.median(cxs))
        cy = float(np.median(cys))
        radius = float(np.median(rs))
        confidence = float(np.mean(confs))
        timeline.append(
            {
                "mapId": map_id,
                "timestampSec": round(float(segment_start_time[segment]), 3),
                "x": round(float(map_x + cx), 2),
                "y": round(float(map_y + cy), 2),
                "radius": round(float(radius), 2),
                "segment": segment,
                "confidence": round(float(min(1.0, max(0.0, confidence))), 3),
            }
        )

    cap.release()
    if visualize:
        cv2.destroyWindow("3. Ring Detection")
        cv2.destroyWindow("4. Ring Mask HSV")
    return timeline


def detect_start_ring_snapshot(
    video_path: str,
    fps: float,
    start_seconds: float,
    sample_step_frames: int = 32,
    ring_config: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """
    Fast ring detection around the given start timestamp.
    Returns the best circle candidate near start_seconds in full-frame coordinates.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    map_x, map_y, map_w, map_h = config.MAP_ROI
    ring_cfg = ring_config or {}
    ring_hsv_lower = np.array(ring_cfg.get("hsvLower", [0, 0, 67]), dtype=np.uint8)
    ring_hsv_upper = np.array(ring_cfg.get("hsvUpper", [180, 68, 89]), dtype=np.uint8)
    ring_gray_min = int(ring_cfg.get("grayMin", 68))
    ring_gray_max = int(ring_cfg.get("grayMax", 88))
    ring_morph_k = max(1, int(ring_cfg.get("morphK", 1)))
    ring_blur_k = max(1, int(ring_cfg.get("blurK", 13)))
    if ring_blur_k % 2 == 0:
        ring_blur_k += 1
    ring_hough_p2 = int(ring_cfg.get("houghP2", 100))
    ring_min_r_pct = float(ring_cfg.get("minRPct", 4))
    ring_max_r_pct = float(ring_cfg.get("maxRPct", 52))

    start_frame = max(0, int(start_seconds * fps))
    probe_span = max(1, int(6 * fps))
    frame_skip = max(1, int(sample_step_frames))
    best: Optional[dict[str, Any]] = None
    best_score = -1e9

    for frame_num in range(start_frame, start_frame + probe_span + 1, frame_skip):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        if not ret:
            break
        map_frame = frame[map_y : map_y + map_h, map_x : map_x + map_w]
        if map_frame.size == 0:
            continue

        hsv = cv2.cvtColor(map_frame, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(map_frame, cv2.COLOR_BGR2GRAY)
        mask_hsv = cv2.inRange(hsv, ring_hsv_lower, ring_hsv_upper)
        mask_gray = cv2.inRange(gray, ring_gray_min, ring_gray_max)
        mask = cv2.bitwise_and(mask_hsv, mask_gray)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ring_morph_k, ring_morph_k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        ring_like = cv2.GaussianBlur(mask, (ring_blur_k, ring_blur_k), 1.6)

        circles = cv2.HoughCircles(
            ring_like,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(20, map_w // 6),
            param1=90,
            param2=ring_hough_p2,
            minRadius=max(5, int(map_w * (ring_min_r_pct / 100.0))),
            maxRadius=max(10, int(map_w * (ring_max_r_pct / 100.0))),
        )
        if circles is None:
            continue

        for circle in circles[0]:
            cx = float(circle[0])
            cy = float(circle[1])
            radius = float(circle[2])
            ring_area = np.pi * (radius ** 2)
            area_ratio = ring_area / max(1.0, float(map_w * map_h))
            if area_ratio < 0.01 or area_ratio > 0.70:
                continue
            score = radius - (np.hypot(cx - (map_w / 2.0), cy - (map_h / 2.0)) * 0.10)
            confidence = max(0.2, min(1.0, area_ratio * 2.5))
            score += confidence * 4.0
            if score <= best_score:
                continue
            best_score = score
            best = {
                "x": float(map_x + cx),
                "y": float(map_y + cy),
                "radius": float(radius),
                "confidence": float(min(1.0, max(0.0, confidence))),
                "frame": int(frame_num),
                "timestampSec": float(frame_num / fps if fps > 0 else start_seconds),
            }

    cap.release()
    return best


def analyze_team_task(
    task: tuple[Any, ...],
):
    """Pickle-safe wrapper for multiprocessing."""
    suppress_opencv_warnings()
    (
        video_path,
        fps,
        team_id,
        team_config,
        map_name,
        visualize,
        start_seconds,
        end_seconds,
        max_seconds,
        zone_rules,
        selection_strategy,
        calibration_seconds,
        predict_seconds,
        switch_confirm_frames,
        max_step_px,
        frame_skip,
        elimination_info,
        run_command,
        run_started_wall,
        status_interval_sec,
        progress_queue,
    ) = task

    progress_callback: Optional[Callable[[int, int, float], None]] = None
    if progress_queue is not None:

        def on_worker_progress(percent: int, frame_num: int, timestamp: float) -> None:
            try:
                progress_queue.put_nowait(("progress", team_id, float(percent), int(frame_num), float(timestamp)))
            except Exception:
                pass

        progress_callback = on_worker_progress

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - [cmd=%(run_command)s t=+%(run_elapsed).1fs] %(message)s",
        force=True,
    )
    logging.getLogger("team_tracking.simple_arrow_tracker").setLevel(logging.CRITICAL)
    logging.getLogger("team_tracking.motion_detector").setLevel(logging.CRITICAL)
    task_filter = RunContextFilter(command_line=run_command, started_wall=run_started_wall)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        has_same = any(isinstance(existing, RunContextFilter) and getattr(existing, "command_line", None) == run_command for existing in handler.filters)
        if not has_same:
            handler.addFilter(task_filter)
    return analyze_team(
        video_path=video_path,
        fps=fps,
        team_id=team_id,
        team_config=team_config,
        map_name=map_name,
        visualize=visualize,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        max_seconds=max_seconds,
        zone_rules=zone_rules,
        selection_strategy=selection_strategy,
        calibration_seconds=calibration_seconds,
        predict_seconds=predict_seconds,
        switch_confirm_frames=switch_confirm_frames,
        max_step_px=max_step_px,
        frame_skip=frame_skip,
        elimination_info=elimination_info,
        progress_callback=progress_callback,
        progress_interval_sec=status_interval_sec,
    )


def render_debug_windows(
    frame: np.ndarray,
    tracker: SimpleArrowTracker,
    team_name: str,
    timestamp: float,
    frame_num: int,
    zone_preview: Optional[dict[str, Any]] = None,
    show_polygons_map: bool = False,
) -> None:
    """Render tracking and HSV debug windows."""
    viz = frame.copy()

    # Draw map bounds for context.
    map_x, map_y, map_w, map_h = config.MAP_ROI
    cv2.rectangle(viz, (map_x, map_y), (map_x + map_w, map_y + map_h), (255, 255, 255), 1)

    # Draw current ROI search area.
    if tracker.tracking_position:
        tx, ty = tracker.tracking_position
    else:
        tx, ty = tracker._get_center(tracker.last_bbox)

    if hasattr(tracker, "get_effective_roi_size"):
        roi_size = int(tracker.get_effective_roi_size())
    elif tracker.tracking_locked and tracker.consecutive_detections > 15:
        roi_size = int(tracker.roi_size * 0.3)
    else:
        roi_size = tracker.roi_size

    half_roi = roi_size // 2
    if tracker.confidence >= 0.75:
        roi_color = (0, 200, 0)       # Green: confident
    elif tracker.confidence >= 0.40:
        roi_color = (0, 220, 255)     # Yellow: uncertain
    else:
        roi_color = (0, 0, 255)       # Red: very uncertain
    cv2.rectangle(viz, (tx - half_roi, ty - half_roi), (tx + half_roi, ty + half_roi), roi_color, 2)

    # Draw tracked bbox and point.
    x, y, w, h = tracker.last_bbox
    cv2.rectangle(viz, (x, y), (x + w, y + h), (0, 0, 255), 2)
    if tracker.tracking_position:
        cv2.circle(viz, tracker.tracking_position, 6, (0, 255, 0), -1)

    # Draw trajectory line.
    trajectory = tracker.get_trajectory()
    for idx in range(1, len(trajectory)):
        p1 = (trajectory[idx - 1].x, trajectory[idx - 1].y)
        p2 = (trajectory[idx].x, trajectory[idx].y)
        cv2.line(viz, p1, p2, (255, 0, 0), 2)

    minutes = int(timestamp // 60)
    seconds = int(timestamp % 60)
    time_label = f"{minutes:02d}:{seconds:02d}"
    cv2.putText(
        viz,
        f"{team_name}  Time={time_label} ({timestamp:.1f}s)  Frame={frame_num}",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2
    )
    track_state = getattr(tracker, "track_state", "tracked")
    state_reason = getattr(tracker, "state_reason", "n/a")
    cv2.putText(
        viz,
        f"Lost={tracker.lost_frames} Conf={tracker.confidence:.2f} State={track_state} Reason={state_reason}",
        (10, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
    )
    mask_mode = getattr(tracker, "last_mask_mode", "n/a")
    shape_rejects = int(getattr(tracker, "shape_reject_count", 0))
    sparse_count = int(getattr(tracker, "mask_too_sparse_count", 0))
    cv2.putText(
        viz,
        f"Mask={mask_mode} shapeReject={shape_rejects} sparse={sparse_count}",
        (10, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )
    cv2.imshow("1. Tracking", viz)

    # HSV mask on map ROI.
    map_frame = frame[map_y:map_y + map_h, map_x:map_x + map_w]
    mask = tracker._create_color_mask(map_frame)
    mask_viz = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for contour in contours:
        area = cv2.contourArea(contour)
        bx, by, bw, bh = cv2.boundingRect(contour)
        color = (0, 0, 255)
        if tracker.min_area <= area <= tracker.max_area:
            aspect_ratio = bw / bh if bh > 0 else 0
            if 0.3 <= aspect_ratio <= 3.0:
                color = (0, 255, 0)
            else:
                color = (0, 255, 255)
        cv2.rectangle(mask_viz, (bx, by), (bx + bw, by + bh), color, 1)

    cv2.imshow("2. HSV Mask", mask_viz)

    if show_polygons_map and zone_preview:
        preview = map_frame.copy()
        zone_w = max(1e-6, float(zone_preview.get("width", map_w)))
        zone_h = max(1e-6, float(zone_preview.get("height", map_h)))
        colors = {
            "forbidden": (40, 40, 220),
            "transient": (0, 200, 255),
            "trusted": (60, 180, 60),
            "unknown": (200, 200, 200),
        }
        for zone in zone_preview.get("zones", []):
            zone_type = str(zone.get("type", "unknown")).lower()
            color = colors.get(zone_type, colors["unknown"])
            poly_src = zone.get("polygon")
            if not isinstance(poly_src, list) or len(poly_src) < 3:
                continue
            points_local: list[list[int]] = []
            for point in poly_src:
                if not isinstance(point, list) or len(point) < 2:
                    continue
                px = int(round((float(point[0]) / zone_w) * map_w))
                py = int(round((float(point[1]) / zone_h) * map_h))
                points_local.append([max(0, min(map_w - 1, px)), max(0, min(map_h - 1, py))])
            if len(points_local) < 3:
                continue
            poly_np = np.array(points_local, dtype=np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(preview, [poly_np], tuple(int(c * 0.15) for c in color))
            cv2.polylines(preview, [poly_np], isClosed=True, color=color, thickness=2)
            zone_id = str(zone.get("id", "")).strip()
            if zone_id:
                tx, ty = points_local[0]
                cv2.putText(preview, zone_id, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

        cv2.putText(preview, "forbidden", (10, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colors["forbidden"], 1)
        cv2.putText(preview, "transient", (110, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colors["transient"], 1)
        cv2.putText(preview, "trusted", (210, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colors["trusted"], 1)
        cv2.imshow("3. Polygons Map", preview)


def run_size_calibration_pass(
    video_path: str,
    fps: float,
    initial_bbox: tuple[int, int, int, int],
    team_config: dict[str, Any],
    map_name: str,
    start_seconds: float,
    end_seconds: Optional[float],
    max_seconds: Optional[float],
    selection_strategy: str,
    calibration_seconds: float,
    predict_seconds: float,
    switch_confirm_frames: int,
    max_step_px: float,
    frame_skip: int,
    zone_rules: Optional[dict[str, Any]] = None,
    advanced_recovery_mode: bool = False,
) -> Optional[tuple[int, int]]:
    """
    First pass: observe target for N seconds and lock robust bbox size.
    Returns (w, h) if successful, otherwise None.
    """
    if calibration_seconds <= 0:
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    start_frame = max(0, int(start_seconds * fps))
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    calibration_until = start_seconds + calibration_seconds
    if end_seconds is not None:
        calibration_until = min(calibration_until, end_seconds)
    if max_seconds is not None:
        calibration_until = min(calibration_until, max_seconds)

    forbidden_polygons, forbidden_zone_size = build_tracker_zone_gate(zone_rules)

    tracker = SimpleArrowTracker(
        initial_bbox=initial_bbox,
        color_hsv_range=team_config["hsv_range"],
        roi_size=config.ROI_SEARCH_SIZE,
        min_area=team_config.get("min_area", config.MIN_ARROW_AREA),
        max_area=team_config.get("max_area", config.MAX_ARROW_AREA),
        morph_kernel_size=team_config.get("morph_kernel_size", 5),
        outlier_threshold_ratio=team_config.get("outlier_threshold_ratio", 0.08),
        map_roi=config.MAP_ROI,
        selection_strategy=selection_strategy,
        calibration_duration_sec=calibration_seconds,
        predict_seconds=predict_seconds,
        switch_confirm_frames=switch_confirm_frames,
        max_step_px=max_step_px,
        forbidden_polygons=forbidden_polygons,
        forbidden_zone_size=forbidden_zone_size,
        advanced_recovery_mode=advanced_recovery_mode,
    )

    frame_skip = max(1, int(frame_skip))
    frame_num = start_frame
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_num += 1
        if frame_num % frame_skip != 0:
            continue
        timestamp = frame_num / fps
        if timestamp > calibration_until:
            break
        tracker.update(frame, timestamp)

    cap.release()

    if tracker.fixed_bbox_size is not None:
        return tracker.fixed_bbox_size
    if tracker.calibration_bboxes:
        return tracker._compute_robust_label_size(tracker.calibration_bboxes)
    return None


def analyze_team(
    video_path: str,
    fps: float,
    team_id: str,
    team_config: dict[str, Any],
    map_name: str,
    visualize: bool = False,
    start_seconds: float = 0.0,
    end_seconds: Optional[float] = None,
    max_seconds: Optional[float] = None,
    zone_rules: Optional[dict[str, Any]] = None,
    selection_strategy: str = "rightmost",
    calibration_seconds: float = 30.0,
    predict_seconds: float = 1.5,
    switch_confirm_frames: int = 3,
    max_step_px: float = 16.0,
    frame_skip: Optional[int] = None,
    elimination_info: Optional[dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    zone_preview: Optional[dict[str, Any]] = None,
    show_polygons_map: bool = False,
    progress_interval_sec: float = 1.0,
) -> TeamRunOutcome:
    team_name = team_config["name"]
    eliminated = bool((elimination_info or {}).get("eliminated", False))
    elimination_ts = (elimination_info or {}).get("eliminationTimestampSec")
    elimination_frame = (elimination_info or {}).get("eliminationFrame")
    elimination_confidence = (elimination_info or {}).get("eliminationConfidence")
    elimination_method = (elimination_info or {}).get("method")

    effective_end_seconds = end_seconds
    if eliminated and elimination_ts is not None:
        try:
            elimination_cutoff = max(float(start_seconds), float(elimination_ts))
            if effective_end_seconds is None:
                effective_end_seconds = elimination_cutoff
            else:
                effective_end_seconds = min(float(effective_end_seconds), elimination_cutoff)
        except (TypeError, ValueError):
            pass

    # If team is already eliminated at segment start, skip expensive tracking entirely.
    if eliminated and effective_end_seconds is not None and float(effective_end_seconds) <= (float(start_seconds) + (1.0 / max(1.0, fps))):
        logger.info(
            "[%s/%s] skip tracking: eliminated before/at segment start (start=%.3fs, elimination=%.3fs)",
            team_id,
            team_name,
            float(start_seconds),
            float(effective_end_seconds),
        )
        status = TeamRunStatus(
            team_id=team_id,
            team_name=team_name,
            status="completed",
            progress_percent=100.0,
            last_frame=max(0, int(float(start_seconds) * fps)),
            last_timestamp_sec=float(start_seconds),
            diagnostics={"skippedDueToElimination": True},
        )
        display_color = team_config.get("display_color_bgr", team_config.get("color_bgr", (180, 180, 180)))
        result = TrackResult(
            team_id=team_id,
            team_name=team_name,
            color_bgr=[int(display_color[0]), int(display_color[1]), int(display_color[2])],
            points=[],
            eliminated=eliminated,
            eliminationTimestampSec=(float(elimination_ts) if elimination_ts is not None else None),
            eliminationFrame=(int(elimination_frame) if elimination_frame is not None else None),
            eliminationConfidence=(float(elimination_confidence) if elimination_confidence is not None else None),
            eliminationMethod=(str(elimination_method) if elimination_method is not None else None),
        )
        return TeamRunOutcome(result=result, status=status, error=None)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        message = f"Cannot open video: {video_path}"
        return TeamRunOutcome(
            result=None,
            status=TeamRunStatus(
                team_id=team_id,
                team_name=team_name,
                status="failed",
                progress_percent=0.0,
                error=message,
            ),
            error=TeamRunError(team_id=team_id, team_name=team_name, stage="open_video", message=message),
        )

    start_frame = max(0, int(start_seconds * fps))
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    observation_count = int(config.OBSERVATION_TIME * fps)
    observation_frames = []
    frame_num = start_frame
    for _ in range(observation_count):
        ret, frame = cap.read()
        if not ret:
            break
        frame_num += 1
        timestamp = frame_num / fps
        if effective_end_seconds is not None and timestamp > effective_end_seconds:
            break
        observation_frames.append(frame)

    if not observation_frames:
        cap.release()
        message = "No observation frames"
        return TeamRunOutcome(
            result=None,
            status=TeamRunStatus(team_id=team_id, team_name=team_name, status="failed", progress_percent=0.0, error=message),
            error=TeamRunError(team_id=team_id, team_name=team_name, stage="observation", message=message),
        )

    initial_bbox = find_initial_position(
        observation_frames,
        team_config["hsv_range"],
        team_name=team_config["name"],
        min_area=team_config.get("min_area", config.MIN_ARROW_AREA),
        max_area=team_config.get("max_area", config.MAX_ARROW_AREA),
        map_roi=config.MAP_ROI
    )

    if initial_bbox is None:
        cap.release()
        message = "Initial bbox not found"
        return TeamRunOutcome(
            result=None,
            status=TeamRunStatus(team_id=team_id, team_name=team_name, status="failed", progress_percent=0.0, error=message),
            error=TeamRunError(team_id=team_id, team_name=team_name, stage="initial_detection", message=message),
        )

    forbidden_polygons, forbidden_zone_size = build_tracker_zone_gate(zone_rules)
    advanced_recovery_mode = (team_id == "TEAM_6") or ("gonext" in team_name.lower())
    if forbidden_polygons:
        logger.info("[%s] tracker zone-gating enabled (forbidden polygons: %d)", team_config["name"], len(forbidden_polygons))
    if advanced_recovery_mode:
        logger.info("[%s/%s] advanced recovery mode enabled", team_id, team_name)

    # Pass 1: size calibration (N seconds), then full re-run from start with locked size.
    calibrated_bbox_size = run_size_calibration_pass(
        video_path=video_path,
        fps=fps,
        initial_bbox=initial_bbox,
        team_config=team_config,
        map_name=map_name,
        start_seconds=start_seconds,
        end_seconds=effective_end_seconds,
        max_seconds=max_seconds,
        selection_strategy=selection_strategy,
        calibration_seconds=calibration_seconds,
        predict_seconds=predict_seconds,
        switch_confirm_frames=switch_confirm_frames,
        max_step_px=max_step_px,
        frame_skip=int(frame_skip or get_frame_skip(map_name, default=config.FRAME_SKIP)),
        zone_rules=zone_rules,
        advanced_recovery_mode=advanced_recovery_mode,
    )
    if calibrated_bbox_size is not None:
        logger.info(
            "[%s] calibration pass locked bbox size: %dx%d",
            team_config["name"],
            calibrated_bbox_size[0],
            calibrated_bbox_size[1],
        )

    # Restart video from beginning of selected segment for full analysis pass.
    cap.release()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        message = f"Cannot reopen video: {video_path}"
        return TeamRunOutcome(
            result=None,
            status=TeamRunStatus(team_id=team_id, team_name=team_name, status="failed", progress_percent=0.0, error=message),
            error=TeamRunError(team_id=team_id, team_name=team_name, stage="reopen_video", message=message),
        )
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frame_num = start_frame

    tracker = SimpleArrowTracker(
        initial_bbox=initial_bbox,
        color_hsv_range=team_config["hsv_range"],
        roi_size=config.ROI_SEARCH_SIZE,
        min_area=team_config.get("min_area", config.MIN_ARROW_AREA),
        max_area=team_config.get("max_area", config.MAX_ARROW_AREA),
        morph_kernel_size=team_config.get("morph_kernel_size", 5),
        outlier_threshold_ratio=team_config.get("outlier_threshold_ratio", 0.08),
        map_roi=config.MAP_ROI,
        selection_strategy=selection_strategy,
        forced_bbox_size=calibrated_bbox_size,
        calibration_duration_sec=max(0.0, calibration_seconds),
        predict_seconds=predict_seconds,
        switch_confirm_frames=switch_confirm_frames,
        max_step_px=max_step_px,
        forbidden_polygons=forbidden_polygons,
        forbidden_zone_size=forbidden_zone_size,
        advanced_recovery_mode=advanced_recovery_mode,
    )

    base_frame_skip = int(frame_skip or get_frame_skip(map_name, default=config.FRAME_SKIP))
    video_total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    video_end_sec = (video_total_frames / fps) if video_total_frames > 0 else 0.0
    effective_end_sec = effective_end_seconds if effective_end_seconds is not None else video_end_sec
    if max_seconds is not None:
        effective_end_sec = min(effective_end_sec, max_seconds)
    total_window_frames = max(1, int(max(0.0, effective_end_sec - start_seconds) * fps))
    total_steps = max(1, total_window_frames // max(1, base_frame_skip))
    progress = ProgressLogger(
        team_id=team_id,
        team_name=team_name,
        total_steps=total_steps,
        on_progress=progress_callback,
        progress_interval_sec=progress_interval_sec,
    )
    processed_steps = 0

    active_frame_skip = max(1, base_frame_skip)
    next_process_frame = start_frame
    last_confident_frame: Optional[int] = start_frame
    last_confident_tracker_snapshot: Optional[dict[str, Any]] = None
    last_confident_snapshot_frame: Optional[int] = start_frame
    rewind_budget = 4
    last_rewind_trigger_frame = -10**9
    hard_redetect_threshold_lost_frames = max(8, base_frame_skip * 2)
    hard_redetect_cooldown_until_frame = -10**9
    map_x, map_y, map_w, map_h = config.MAP_ROI
    hard_redetect_target_roi = int(map_w * 0.82)  # large but still not full map
    state_counts: dict[str, int] = {"tracked": 0, "predict": 0, "hold": 0}
    confident_frames_count = 0
    record_attempt_count = 0
    recorded_points_added = 0
    record_anchor_frame: Optional[int] = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_num += 1
        if frame_num < next_process_frame:
            continue
        timestamp = frame_num / fps
        if effective_end_seconds is not None and timestamp > effective_end_seconds:
            break
        if max_seconds is not None and timestamp > max_seconds:
            break

        # Keep trajectory writing cadence on standard FRAME_SKIP even during recovery.
        # Anchor cadence to the first actually processed frame, not absolute frame parity.
        if record_anchor_frame is None:
            record_anchor_frame = frame_num
        should_record_point = ((frame_num - record_anchor_frame) % max(1, base_frame_skip) == 0)
        if should_record_point:
            record_attempt_count += 1
        before_traj_len = len(tracker.get_trajectory())
        tracker.update(frame, timestamp, record_point=should_record_point)
        after_traj_len = len(tracker.get_trajectory())
        if after_traj_len > before_traj_len:
            recorded_points_added += (after_traj_len - before_traj_len)
        confidence = float(getattr(tracker, "confidence", 0.0))
        track_state = str(getattr(tracker, "track_state", "tracked"))
        state_counts[track_state] = state_counts.get(track_state, 0) + 1
        confident_now = (confidence >= 0.75) and (track_state == "tracked")
        if confident_now:
            confident_frames_count += 1

        if confident_now:
            last_confident_frame = frame_num
            last_confident_snapshot_frame = frame_num
            # Rollback point: full tracker state at latest confident frame.
            last_confident_tracker_snapshot = copy.deepcopy(tracker.__dict__)
            active_frame_skip = max(1, base_frame_skip)
        else:
            active_frame_skip = 2
            lost_frames = int(getattr(tracker, "lost_frames", 0))
            can_hard_redetect = (
                lost_frames >= hard_redetect_threshold_lost_frames
                and frame_num >= hard_redetect_cooldown_until_frame
            )
            if can_hard_redetect:
                base_roi_floor = max(int(getattr(tracker, "roi_size", base_frame_skip)), int(getattr(tracker, "min_tracking_roi_px", 120)))
                hard_expand = max(0, hard_redetect_target_roi - base_roi_floor)
                tracker.roi_expand_px = max(int(getattr(tracker, "roi_expand_px", 0)), hard_expand)
                tracker.tracking_locked = False
                tracker.pending_center = None
                tracker.pending_center_hits = 0
                tracker.track_state = "hold"
                tracker.state_reason = "hard_redetect"
                if last_confident_tracker_snapshot and "tracking_position" in last_confident_tracker_snapshot:
                    snapshot_pos = last_confident_tracker_snapshot.get("tracking_position")
                    if isinstance(snapshot_pos, tuple) and len(snapshot_pos) == 2:
                        tracker.tracking_position = (int(snapshot_pos[0]), int(snapshot_pos[1]))
                    else:
                        tracker.tracking_position = (map_x + map_w // 2, map_y + map_h // 2)
                else:
                    tracker.tracking_position = (map_x + map_w // 2, map_y + map_h // 2)
                hard_redetect_cooldown_until_frame = frame_num + max(24, base_frame_skip * 8)
                logger.info(
                    "[%s/%s] hard re-detect enabled: lost=%d, target_roi~%dpx, center=(%d,%d)",
                    team_id,
                    team_name,
                    lost_frames,
                    hard_redetect_target_roi,
                    tracker.tracking_position[0],
                    tracker.tracking_position[1],
                )
            can_rewind = (
                rewind_budget > 0
                and last_confident_frame is not None
                and last_confident_tracker_snapshot is not None
                and last_confident_snapshot_frame is not None
                and (frame_num - last_confident_frame) >= max(4, base_frame_skip)
                and (frame_num - last_rewind_trigger_frame) >= max(6, base_frame_skip)
                and (track_state in ("hold", "predict") or lost_frames >= 2)
            )
            if can_rewind:
                rewind_target = max(start_frame, int(last_confident_snapshot_frame))
                tracker.__dict__.clear()
                tracker.__dict__.update(copy.deepcopy(last_confident_tracker_snapshot))
                cap.set(cv2.CAP_PROP_POS_FRAMES, rewind_target)
                actual_seek_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or rewind_target)
                if actual_seek_frame < start_frame:
                    actual_seek_frame = start_frame
                logger.info(
                    "[%s/%s] rewind+rollback recovery: frame %d -> %d (actual=%d), reduce FRAME_SKIP to 2",
                    team_id,
                    team_name,
                    frame_num,
                    rewind_target,
                    actual_seek_frame,
                )
                frame_num = actual_seek_frame - 1
                next_process_frame = actual_seek_frame
                last_rewind_trigger_frame = actual_seek_frame
                rewind_budget -= 1
                continue

        next_process_frame = frame_num + max(1, active_frame_skip)
        processed_steps += 1
        progress.maybe_log(processed_steps, frame_num, timestamp)

        if visualize:
            render_debug_windows(
                frame,
                tracker,
                team_config["name"],
                timestamp,
                frame_num,
                zone_preview=zone_preview,
                show_polygons_map=show_polygons_map,
            )
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == 27:
                break

    final_timestamp = frame_num / fps
    tracker.finalize(final_timestamp)
    cap.release()
    if visualize:
        cv2.destroyAllWindows()

    points = [asdict(point) for point in tracker.get_trajectory()]
    if eliminated and elimination_ts is not None and float(elimination_ts) > (float(start_seconds) + 1.0):
        cutoff = float(elimination_ts)
        points = [point for point in points if float(point.get("timestamp", 0.0)) <= cutoff]

    points, removed_count = filter_points_by_forbidden_zones(points, zone_rules)
    if removed_count > 0:
        logger.info("[%s] zone filter removed points: %d", team_config["name"], removed_count)
    diagnostics = {
        "maskTooSparseCount": int(getattr(tracker, "mask_too_sparse_count", 0)),
        "shapeRejectCount": int(getattr(tracker, "shape_reject_count", 0)),
        "zoneGateRejectCount": int(getattr(tracker, "zone_gate_reject_count", 0)),
        "finalStateReason": str(getattr(tracker, "state_reason", "")),
    }
    logger.info("[%s/%s] diagnostics=%s", team_id, team_name, diagnostics)
    display_color = team_config.get("display_color_bgr", team_config.get("color_bgr", (180, 180, 180)))
    status = TeamRunStatus(
        team_id=team_id,
        team_name=team_name,
        status="completed",
        progress_percent=100.0,
        last_frame=frame_num,
        last_timestamp_sec=final_timestamp,
        diagnostics=diagnostics,
    )
    result = TrackResult(
        team_id=team_id,
        team_name=team_name,
        color_bgr=[int(display_color[0]), int(display_color[1]), int(display_color[2])],
        points=points,
        eliminated=eliminated,
        eliminationTimestampSec=(float(elimination_ts) if elimination_ts is not None else None),
        eliminationFrame=(int(elimination_frame) if elimination_frame is not None else None),
        eliminationConfidence=(float(elimination_confidence) if elimination_confidence is not None else None),
        eliminationMethod=(str(elimination_method) if elimination_method is not None else None),
    )
    return TeamRunOutcome(result=result, status=status, error=None)


def main():
    suppress_opencv_warnings()
    started_perf = time.perf_counter()
    started_wall = time.time()
    command_line = " ".join(sys.argv)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - [cmd=%(run_command)s t=+%(run_elapsed).1fs] %(message)s",
        force=True,
    )
    context_filter = RunContextFilter(command_line=command_line, started_perf=started_perf)
    for handler in logging.getLogger().handlers:
        handler.addFilter(context_filter)
    # Suppress noisy logs from lower-level tracking modules.
    logging.getLogger("team_tracking.simple_arrow_tracker").setLevel(logging.CRITICAL)
    logging.getLogger("team_tracking.motion_detector").setLevel(logging.CRITICAL)

    parser = argparse.ArgumentParser(description="Batch analyze all map teams")
    parser.add_argument("--video", help="Video path")
    parser.add_argument("--video-name", help="Video file name inside --records-dir (single-video mode)")
    parser.add_argument("--records-dir", default="ffmpeg_downloader/records", help="Records directory for --video-name")
    parser.add_argument("--map", default="mp_storm_point", help="Map key or short map name")
    parser.add_argument("--map-id", default="", help="Map id in match context (auto-generated if omitted)")
    parser.add_argument("--match-id", default="test", help="Match id for output naming and catalog grouping")
    parser.add_argument("--tournament-id", default="test", help="Tournament id for catalog grouping")
    parser.add_argument("--map-number", type=int, help="Map number in match (auto-incremented if omitted)")
    parser.add_argument("--output", default="", help="Optional explicit output JSON path")
    parser.add_argument("--team", type=int, help="Analyze a single team number")
    parser.add_argument("--visualize", action="store_true", help="Show tracking and HSV windows")
    parser.add_argument(
        "--visualize-polygons-map",
        action="store_true",
        help="With --visualize, show a debug window with polygon zones over map ROI",
    )
    parser.add_argument("--max-seconds", type=float, help="Limit analysis to first N seconds of video")
    parser.add_argument(
        "--status-interval-sec",
        type=float,
        default=1.0,
        help="Live status refresh interval in seconds (default 1.0)",
    )
    parser.add_argument("--round", choices=["1", "2", "all"], default="all", help="Analyze a specific round window")
    parser.add_argument("--workers", type=int, default=1, help="Parallel worker processes for team analysis")
    parser.add_argument(
        "--benchmark-streams",
        default="1,5,10,20",
        help="Comma-separated concurrent stream counts for performance report",
    )
    parser.add_argument(
        "--performance-report",
        action="store_true",
        help="Run performance benchmark and write report file",
    )
    parser.add_argument("--zones-file", help="Path to zones JSON file. Default: output/zones/<map>.zones.json")
    parser.add_argument(
        "--disable-zone-filter",
        action="store_true",
        help="Disable forbidden-zone filtering even if zones file exists",
    )
    parser.add_argument(
        "--selection-strategy",
        choices=["nearest", "rightmost", "label_arrow"],
        default="rightmost",
        help="Candidate selection strategy in ROI (default: rightmost)",
    )
    parser.add_argument(
        "--calibration-seconds",
        type=float,
        default=30.0,
        help="First-pass calibration duration in seconds before full re-analysis",
    )
    parser.add_argument(
        "--predict-seconds",
        type=float,
        default=1.5,
        help="Predict-short window in seconds before hold fallback",
    )
    parser.add_argument(
        "--switch-confirm-frames",
        type=int,
        default=3,
        help="How many consecutive frames are required to confirm target switch",
    )
    parser.add_argument(
        "--max-step-px",
        type=float,
        default=16.0,
        help="Maximum per-frame movement for center/right-edge stabilization",
    )
    parser.add_argument(
        "--job-id",
        help="Optional external job id for status tracking in output/jobs.json",
    )
    parser.add_argument(
        "--frame-skip",
        type=int,
        default=None,
        help="Override frame skip for team tracking (higher is faster, lower is more accurate)",
    )
    parser.add_argument(
        "--use-map-start-db",
        action="store_true",
        help="Read map/timestamp from map_start_detection.sqlite by video basename.",
    )
    parser.add_argument(
        "--map-start-db-path",
        default=str(DEFAULT_MAP_START_DB_PATH.relative_to(PROJECT_ROOT)),
        help="Path to map_start_detection.sqlite (relative to project root or absolute).",
    )
    args = parser.parse_args()
    video_path = resolve_video_path(args.video, args.video_name, args.records_dir)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    video_path_str = str(video_path)
    map_start_db_path = _resolve_map_start_db_path(args.map_start_db_path)
    map_start_record = (
        load_map_start_record(map_start_db_path, video_path.name) if args.use_map_start_db else None
    )

    normalized_map_name = normalize_map_name(args.map)
    map_start_seconds: Optional[float] = None
    if map_start_record:
        db_map_mp_id = map_start_record.get("map_mp_id")
        db_start_sec = map_start_record.get("start_timestamp_sec")
        if isinstance(db_map_mp_id, str) and db_map_mp_id.strip():
            normalized_map_name = normalize_map_name(db_map_mp_id.strip())
            logger.info("[map-start-db] map override: %s", normalized_map_name)
        if db_start_sec is not None:
            map_start_seconds = max(0.0, float(db_start_sec))
            logger.info("[map-start-db] start timestamp: %.3fs", map_start_seconds)

    match_id = slugify_for_filename(args.match_id or "test")
    tournament_id = slugify_for_filename(args.tournament_id or "test")
    map_number = resolve_map_number(match_id, args.map_number)
    effective_map_id = args.map_id.strip() if args.map_id else f"{match_id}_map{map_number}"
    output_path = build_output_path(match_id, map_number, normalized_map_name, args.output)

    cap = cv2.VideoCapture(video_path_str)
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    video_duration_sec = (total_frames / fps) if total_frames > 0 and fps > 0 else 0.0
    cap.release()

    save_map_background(video_path_str, normalized_map_name, start_seconds=(map_start_seconds or 0.0))

    map_admin_settings = get_map_admin_settings(normalized_map_name)
    if map_admin_settings:
        logger.info("Loaded map admin settings for %s", normalized_map_name)
    round_windows = resolve_round_windows(normalized_map_name, map_admin_settings)
    all_teams = get_all_teams_for_map(normalized_map_name)
    all_teams = apply_team_hsv_overrides(all_teams, map_admin_settings)

    if not all_teams:
        raise ValueError(f"No team configs found for map '{normalized_map_name}' and team filter '{args.team}'")
    results: list[TrackResult] = []
    errors: list[dict[str, Any]] = []

    ring_round_windows = None
    if args.use_map_start_db:
        ring_round_windows = load_round_windows_from_rings(
            map_start_db_path,
            video_path.name,
            map_start_seconds,
            video_duration_sec,
        )
        if ring_round_windows:
            round_windows["round1"] = dict(ring_round_windows["round1"])
            round_windows["round2"] = dict(ring_round_windows["round2"])
            logger.info(
                "[map-start-db] round windows from Rings: r1=%.3f..%.3f r2=%.3f..%.3f all=%.3f..%.3f",
                ring_round_windows["round1"]["start_sec"],
                ring_round_windows["round1"]["end_sec"],
                ring_round_windows["round2"]["start_sec"],
                ring_round_windows["round2"]["end_sec"],
                ring_round_windows["all"]["start_sec"],
                ring_round_windows["all"]["end_sec"],
            )
        else:
            logger.info("[map-start-db] Rings windows not found for %s, using configured windows.", video_path.name)

    if args.round == "1":
        window = round_windows["round1"]
    elif args.round == "2":
        window = round_windows["round2"]
    else:
        window = ring_round_windows["all"] if ring_round_windows else {
            "start_sec": float(map_start_seconds) if map_start_seconds is not None else float(round_windows["round1"]["start_sec"]),
            "end_sec": float(video_duration_sec) if video_duration_sec > 0 else float(round_windows["round2"]["end_sec"]),
        }

    start_seconds = float(window["start_sec"])
    end_seconds = float(window["end_sec"])
    if args.max_seconds is not None:
        end_seconds = min(end_seconds, start_seconds + max(0.0, float(args.max_seconds)))
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 1.0
    output_time_offset_sec = float(start_seconds) if map_start_seconds is not None else 0.0

    start_ring: Optional[dict[str, Any]] = None
    video_label = video_path.name
    video_total_duration_sec = max(0.0, float(video_duration_sec))
    status_interval_sec = max(0.2, float(args.status_interval_sec))
    last_live_render = 0.0
    last_live_line_len = 0
    status_render_lock = threading.Lock()

    def emit_progress_line(
        team_label: str,
        progress_percent: float,
        action_label: str,
        current_video_sec: float,
    ) -> None:
        nonlocal last_live_line_len
        pct = float(progress_percent)
        ts_sec = max(0.0, float(current_video_sec))
        with status_render_lock:
            if last_live_line_len > 0:
                print("", flush=True)
                last_live_line_len = 0
        if _want_status_color():
            if action_label == "fail" or action_label.endswith("fail"):
                act = f"\033[91m{action_label}\033[0m"
            elif action_label == "done":
                act = f"\033[92m{action_label}\033[0m"
            elif action_label in ("elim", "retry"):
                act = f"\033[94m{action_label}\033[0m"
            else:
                act = action_label
            label = f"\033[1m{team_label}\033[0m" if team_label.startswith("TEAM_") else team_label
            print(
                f"\033[90m{video_label}\033[0m {label} \033[96m{pct:.0f}%\033[0m {act} \033[2m{ts_sec:.1f}s\033[0m",
                flush=True,
            )
        else:
            print(
                f"{video_label} {team_label} {pct:.0f}% {action_label} {ts_sec:.1f}s",
                flush=True,
            )

    current_action = "detecting_team_eliminations"
    emit_progress_line("ALL", 0.0, "elim", float(start_seconds))
    elimination_by_team = (
        load_eliminations_from_teams_db(map_start_db_path, video_path.name)
        if args.use_map_start_db
        else None
    )
    if elimination_by_team is None:
        elimination_by_team = detect_team_eliminations_timeline(
            video_path=video_path_str,
            fps=fps,
            team_configs=all_teams,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            coarse_step_frames=10000,
            refine_step_frames=1000,
            tolerance_frames=300,
        )
    eliminated_count = sum(1 for item in elimination_by_team.values() if item.get("eliminated"))
    logger.info(
        "Team elimination pass done: eliminated=%d/%d",
        eliminated_count,
        len(elimination_by_team),
    )
    for team_id in _sorted_team_ids(all_teams):
        info = elimination_by_team.get(team_id, {})
        if info.get("eliminated"):
            logger.info(
                "[elim/%s] frame=%s ts=%ss conf=%s",
                team_id,
                info.get("eliminationFrame"),
                info.get("eliminationTimestampSec"),
                info.get("eliminationConfidence"),
            )

    teams = dict(all_teams)
    if args.team is not None:
        team_key = f"TEAM_{args.team}"
        teams = {team_key: teams[team_key]} if team_key in teams else {}

    if not teams:
        raise ValueError(f"No team configs found for map '{normalized_map_name}' and team filter '{args.team}'")

    polygons_cfg = map_admin_settings.get("polygons", {}) if isinstance(map_admin_settings, dict) else {}
    polygons_enabled = bool(polygons_cfg.get("enabled", True))
    zones_file = args.zones_file or (str(polygons_cfg.get("zonesFile", "")).strip() or None)
    if args.disable_zone_filter or not polygons_enabled:
        zone_rules = None
    else:
        zone_rules = load_zone_rules(zones_file, normalized_map_name)
    zone_preview = None
    if args.visualize_polygons_map:
        if not args.visualize:
            logger.warning("--visualize-polygons-map is set without --visualize; option will have no effect.")
        zone_preview = load_zone_preview(zones_file, normalized_map_name)
        if zone_preview:
            logger.info(
                "Polygon preview loaded: %s (zones=%d)",
                zone_preview.get("path"),
                len(zone_preview.get("zones", [])),
            )
        else:
            logger.warning("Polygon preview is enabled, but zone file/polygons were not found.")

    runtime_cfg = map_admin_settings.get("runtime", {}) if isinstance(map_admin_settings, dict) else {}
    configured_frame_skip = int(runtime_cfg.get("frameSkip", get_frame_skip(normalized_map_name, default=config.FRAME_SKIP)))
    effective_frame_skip = max(1, int(args.frame_skip or configured_frame_skip))
    effective_max_step_px = float(args.max_step_px)
    if normalized_map_name == "mp_olympus":
        # Olympus has frequent faster mini-map shifts; keep a softer motion clamp.
        effective_max_step_px = max(effective_max_step_px, 24.0)

    team_items = sorted(teams.items(), key=lambda item: item[0])
    benchmark_results: list[dict[str, Any]] = []

    job_id = args.job_id or f"analysis-{datetime.now().strftime('%Y%m%d_%H%M%S')}-{uuid.uuid4().hex[:6]}"
    team_statuses: dict[str, dict[str, Any]] = {
        team_id: {
            "teamId": team_id,
            "teamName": team_config["name"],
            "status": "queued",
            "progressPercent": 0.0,
            "lastFrame": None,
            "lastTimestampSec": None,
            "error": None,
            "diagnostics": None,
        }
        for team_id, team_config in team_items
    }
    current_action = "initializing"
    last_job_heartbeat = 0.0

    def compute_overall_progress() -> float:
        if not team_statuses:
            return 0.0
        return float(sum(float(item.get("progressPercent", 0.0)) for item in team_statuses.values()) / len(team_statuses))

    def render_live_status(force: bool = False) -> None:
        nonlocal last_live_render, last_live_line_len
        now = time.monotonic()
        with status_render_lock:
            if not force and (now - last_live_render) < status_interval_sec:
                return
            last_live_render = now
            overall_pct = compute_overall_progress()
            current_ts = max(
                float(start_seconds),
                max((float(item.get("lastTimestampSec") or 0.0) for item in team_statuses.values()), default=float(start_seconds)),
            )
            ordered = _sorted_team_ids({tid: {} for tid in team_statuses.keys()})

            plain_left = f"{video_label} ALL {overall_pct:.0f}% track {current_ts:.1f}s | "
            MAX_V = 420
            team_parts_plain: list[str] = []
            seen_ids: list[str] = []

            def _pct_token(pct_val: float) -> str:
                if pct_val >= 99.5:
                    return "100%"
                if pct_val <= 0.05:
                    return "0%"
                return f"{pct_val:.1f}%"

            for tid in ordered:
                p = float(team_statuses[tid].get("progressPercent", 0.0))
                piece_plain = f"{tid}:{_pct_token(p)}"
                candidate_plain = plain_left + " ".join(team_parts_plain + [piece_plain])
                if len(candidate_plain) > MAX_V and team_parts_plain:
                    break
                team_parts_plain.append(piece_plain)
                seen_ids.append(tid)

            tail = ""
            if len(seen_ids) < len(ordered):
                tail = " ..."
            plain_line = plain_left + " ".join(team_parts_plain) + tail

            if _want_status_color():
                left = (
                    f"\033[1m{video_label}\033[0m "
                    f"\033[93mALL {overall_pct:.0f}%\033[0m "
                    f"\033[2mtrack {current_ts:.1f}s\033[0m | "
                )
                team_bits = [
                    _fmt_team_pct(tid, float(team_statuses[tid].get("progressPercent", 0.0))) for tid in seen_ids
                ]
                tail_c = ""
                if len(seen_ids) < len(ordered):
                    tail_c = " \033[2m...\033[0m"
                compact = left + " ".join(team_bits) + tail_c
            else:
                compact = plain_line

            stdout_is_tty_local = sys.stdout.isatty()
            vis_len = len(_strip_ansi(compact))
            if stdout_is_tty_local:
                pad = max(0, last_live_line_len - vis_len)
                print("\r" + compact + (" " * pad), end="", flush=True)
                last_live_line_len = vis_len
            else:
                print(compact, flush=True)
                last_live_line_len = 0

    def persist_job(status: str, force: bool = False) -> None:
        nonlocal last_job_heartbeat
        now = time.time()
        if not force and (now - last_job_heartbeat) < 5.0:
            return
        last_job_heartbeat = now
        with status_render_lock:
            overall_pct_calc = round(compute_overall_progress(), 2)
            team_snap = copy.deepcopy(list(team_statuses.values()))
        upsert_job_record(
            job_id=job_id,
            patch={
                "status": status,
                "currentAction": current_action,
                "lastHeartbeatAt": datetime.now().isoformat(),
                "progressPercent": overall_pct_calc,
                "teamStatuses": team_snap,
                "errors": [item.get("message", "") for item in errors],
                "mapId": effective_map_id,
                "video": video_path_str,
            },
            create_if_missing={
                "id": job_id,
                "jobType": "analysis",
                "status": status,
                "command": command_line,
                "currentAction": current_action,
                "lastHeartbeatAt": datetime.now().isoformat(),
                "progressPercent": 0.0,
                "queuedAt": datetime.now().isoformat(),
                "startedAt": datetime.now().isoformat(),
                "finishedAt": None,
                "durationMs": None,
                "mapId": effective_map_id,
                "video": video_path_str,
                "teamStatuses": copy.deepcopy(team_snap),
                "errors": [],
                "payload": {
                    "round": args.round,
                    "workers": args.workers,
                    "team": args.team,
                    "selectionStrategy": args.selection_strategy,
                    "map": normalized_map_name,
                    "matchId": match_id,
                    "tournamentId": tournament_id,
                    "mapNumber": map_number,
                },
            },
        )

    persist_job("running")

    if args.performance_report:
        stream_values = []
        for token in args.benchmark_streams.split(","):
            token = token.strip()
            if token:
                stream_values.append(max(1, int(token)))

        team_items_for_bench = team_items
        if not team_items_for_bench:
            raise ValueError("No teams available for benchmark run.")

        for stream_count in stream_values:
            logger.info("Benchmark start: %d parallel streams", stream_count)
            sampler = PerformanceSampler(sample_interval_sec=1.0)
            sampler.start()
            started_at = time.perf_counter()

            tasks = []
            for idx in range(stream_count):
                team_id, team_config = team_items_for_bench[idx % len(team_items_for_bench)]
                tasks.append(
                    (
                        video_path_str,
                        fps,
                        team_id,
                        team_config,
                        normalized_map_name,
                        False,
                        start_seconds,
                        end_seconds,
                        args.max_seconds,
                        zone_rules,
                        args.selection_strategy,
                        args.calibration_seconds,
                        args.predict_seconds,
                        args.switch_confirm_frames,
                        effective_max_step_px,
                        effective_frame_skip,
                        elimination_by_team.get(team_id),
                        command_line,
                        started_wall,
                        status_interval_sec,
                        None,
                    )
                )

            with concurrent.futures.ProcessPoolExecutor(max_workers=stream_count) as executor:
                list(executor.map(analyze_team_task, tasks))

            sampler.stop()
            elapsed = time.perf_counter() - started_at
            benchmark_results.append(
                {
                    "streams": stream_count,
                    "elapsed_sec": round(elapsed, 3),
                    "load": sampler.summarize(),
                }
            )
            logger.info("Benchmark done: %d streams in %.1fs", stream_count, elapsed)

    if args.visualize or args.workers <= 1 or len(team_items) == 1:
        for team_id, team_config in team_items:
            current_action = f"processing {team_id} ({team_config['name']})"
            team_statuses[team_id]["status"] = "running"
            render_live_status(force=True)
            persist_job("running", force=True)

            def on_team_progress(
                percent: int,
                frame_num: int,
                timestamp: float,
                target_team_id: str = team_id,
            ) -> None:
                nonlocal current_action
                with status_render_lock:
                    team_statuses[target_team_id]["progressPercent"] = float(percent)
                    team_statuses[target_team_id]["lastFrame"] = int(frame_num)
                    team_statuses[target_team_id]["lastTimestampSec"] = float(timestamp)
                    current_action = f"{target_team_id}: tracking frame={frame_num} t={timestamp:.1f}s ({percent}%)"
                render_live_status()
                persist_job("running")

            outcome = analyze_team(
                video_path_str,
                fps,
                team_id,
                team_config,
                normalized_map_name,
                visualize=args.visualize,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                max_seconds=args.max_seconds,
                zone_rules=zone_rules,
                selection_strategy=args.selection_strategy,
                calibration_seconds=args.calibration_seconds,
                predict_seconds=args.predict_seconds,
                switch_confirm_frames=args.switch_confirm_frames,
                max_step_px=effective_max_step_px,
                frame_skip=effective_frame_skip,
                elimination_info=elimination_by_team.get(team_id),
                progress_callback=on_team_progress,
                zone_preview=zone_preview,
                show_polygons_map=args.visualize_polygons_map,
                progress_interval_sec=status_interval_sec,
            )
            with status_render_lock:
                team_statuses[team_id] = {
                    "teamId": outcome.status.team_id,
                    "teamName": outcome.status.team_name,
                    "status": outcome.status.status,
                    "progressPercent": float(outcome.status.progress_percent),
                    "lastFrame": outcome.status.last_frame,
                    "lastTimestampSec": outcome.status.last_timestamp_sec,
                    "error": outcome.status.error,
                    "diagnostics": outcome.status.diagnostics,
                }
            if outcome.result is not None:
                results.append(outcome.result)
            if outcome.error is not None:
                errors.append(asdict(outcome.error))
            emit_progress_line(
                outcome.status.team_id,
                float(outcome.status.progress_percent),
                "done" if outcome.error is None else "fail",
                float(outcome.status.last_timestamp_sec or start_seconds),
            )
            render_live_status(force=True)
            persist_job("running", force=True)
    else:
        worker_count = max(1, min(args.workers, len(team_items)))
        with status_render_lock:
            for _tid_running in team_statuses:
                team_statuses[_tid_running]["status"] = "running"
        current_action = "processing teams in parallel"
        render_live_status(force=True)
        persist_job("running", force=True)

        progress_queue_mp = MPQueue(maxsize=8192)
        tasks = [
            (
                video_path_str,
                fps,
                team_id,
                team_config,
                normalized_map_name,
                False,  # visualize in parallel mode is disabled
                start_seconds,
                end_seconds,
                args.max_seconds,
                zone_rules,
                args.selection_strategy,
                args.calibration_seconds,
                args.predict_seconds,
                args.switch_confirm_frames,
                effective_max_step_px,
                effective_frame_skip,
                elimination_by_team.get(team_id),
                command_line,
                started_wall,
                status_interval_sec,
                progress_queue_mp,
            )
            for team_id, team_config in team_items
        ]
        completed_team_ids: set[str] = set()
        progress_stop_flag = threading.Event()

        def _drain_progress_queue() -> None:
            while not progress_stop_flag.is_set():
                try:
                    msg = progress_queue_mp.get(timeout=0.35)
                except queue.Empty:
                    continue
                if isinstance(msg, tuple) and len(msg) >= 5 and msg[0] == "progress":
                    prog_tid = str(msg[1])
                    prog_pct, prog_fr, prog_ts = float(msg[2]), int(msg[3]), float(msg[4])
                else:
                    continue
                if prog_tid not in team_statuses:
                    continue
                with status_render_lock:
                    team_statuses[prog_tid]["progressPercent"] = prog_pct
                    team_statuses[prog_tid]["lastFrame"] = prog_fr
                    team_statuses[prog_tid]["lastTimestampSec"] = prog_ts
                    team_statuses[prog_tid]["status"] = "running"
                render_live_status()

        progress_drainer = threading.Thread(target=_drain_progress_queue, daemon=True)
        progress_drainer.start()

        try:
            try:
                with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
                    submitted = [executor.submit(analyze_team_task, t) for t in tasks]
                    for fut in concurrent.futures.as_completed(submitted):
                        outcome = fut.result()
                        completed_team_ids.add(outcome.status.team_id)
                        with status_render_lock:
                            team_statuses[outcome.status.team_id] = {
                                "teamId": outcome.status.team_id,
                                "teamName": outcome.status.team_name,
                                "status": outcome.status.status,
                                "progressPercent": float(outcome.status.progress_percent),
                                "lastFrame": outcome.status.last_frame,
                                "lastTimestampSec": outcome.status.last_timestamp_sec,
                                "error": outcome.status.error,
                                "diagnostics": outcome.status.diagnostics,
                            }
                        if outcome.result is not None:
                            results.append(outcome.result)
                        if outcome.error is not None:
                            errors.append(asdict(outcome.error))
                        emit_progress_line(
                            outcome.status.team_id,
                            float(outcome.status.progress_percent),
                            "done" if outcome.error is None else "fail",
                            float(outcome.status.last_timestamp_sec or start_seconds),
                        )
                        render_live_status(force=True)
                        current_action = f"completed {outcome.status.team_id} status={outcome.status.status}"
                        persist_job("running", force=True)
            except BrokenProcessPool:
                current_action = "pool_broken_fallback_sequential"
                emit_progress_line("ALL", 0.0, "retry", float(start_seconds))
                persist_job("running", force=True)
                pending_items = [(team_id, team_config) for team_id, team_config in team_items if team_id not in completed_team_ids]
                for team_id, team_config in pending_items:
                    render_live_status(force=True)
                    outcome = analyze_team(
                        video_path_str,
                        fps,
                        team_id,
                        team_config,
                        normalized_map_name,
                        visualize=False,
                        start_seconds=start_seconds,
                        end_seconds=end_seconds,
                        max_seconds=args.max_seconds,
                        zone_rules=zone_rules,
                        selection_strategy=args.selection_strategy,
                        calibration_seconds=args.calibration_seconds,
                        predict_seconds=args.predict_seconds,
                        switch_confirm_frames=args.switch_confirm_frames,
                        max_step_px=effective_max_step_px,
                        frame_skip=effective_frame_skip,
                        elimination_info=elimination_by_team.get(team_id),
                        progress_callback=None,
                        zone_preview=zone_preview,
                        show_polygons_map=False,
                        progress_interval_sec=status_interval_sec,
                    )
                    with status_render_lock:
                        team_statuses[outcome.status.team_id] = {
                            "teamId": outcome.status.team_id,
                            "teamName": outcome.status.team_name,
                            "status": outcome.status.status,
                            "progressPercent": float(outcome.status.progress_percent),
                            "lastFrame": outcome.status.last_frame,
                            "lastTimestampSec": outcome.status.last_timestamp_sec,
                            "error": outcome.status.error,
                            "diagnostics": outcome.status.diagnostics,
                        }
                    if outcome.result is not None:
                        results.append(outcome.result)
                    if outcome.error is not None:
                        errors.append(asdict(outcome.error))
                    emit_progress_line(
                        outcome.status.team_id,
                        float(outcome.status.progress_percent),
                        "done" if outcome.error is None else "fail",
                        float(outcome.status.last_timestamp_sec or start_seconds),
                    )
                    render_live_status(force=True)
                    persist_job("running", force=True)
        finally:
            progress_stop_flag.set()
            progress_drainer.join(timeout=8.0)

    with status_render_lock:
        if last_live_line_len > 0:
            print("", flush=True)
            last_live_line_len = 0

    rings: list[dict[str, Any]] = []

    if output_time_offset_sec > 0.0:
        # Keep output timelines compatible with web player round windows (0..N sec).
        for item in results:
            normalized_points: list[dict[str, Any]] = []
            for point in item.points:
                ts = max(0.0, float(point.get("timestamp", 0.0)) - output_time_offset_sec)
                normalized_point = dict(point)
                normalized_point["timestamp"] = ts
                normalized_points.append(normalized_point)
            item.points = normalized_points
            if item.eliminationTimestampSec is not None:
                item.eliminationTimestampSec = max(0.0, float(item.eliminationTimestampSec) - output_time_offset_sec)

        for team_info in elimination_by_team.values():
            if isinstance(team_info, dict) and team_info.get("eliminationTimestampSec") is not None:
                team_info["eliminationTimestampSec"] = max(
                    0.0,
                    float(team_info["eliminationTimestampSec"]) - output_time_offset_sec,
                )

        for ring in rings:
            if ring.get("timestampSec") is not None:
                ring["timestampSec"] = round(
                    max(0.0, float(ring["timestampSec"]) - output_time_offset_sec),
                    3,
                )

    current_action = "writing output files"
    persist_job("running", force=True)

    def sort_tracks(team_result: TrackResult) -> tuple[int, str]:
        try:
            return (int(team_result.team_id.split("_", 1)[1]), str(team_result.team_id))
        except (IndexError, ValueError):
            return (10**9, str(getattr(team_result, "team_id", "")))

    results.sort(key=sort_tracks)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "mapId": effective_map_id,
                "map": normalized_map_name,
                "matchId": match_id,
                "tournamentId": tournament_id,
                "mapNumber": map_number,
                "fileName": output_path.name,
                "video": video_path_str,
                "teams": [asdict(item) for item in results],
                "eliminations": elimination_by_team,
                "errors": errors,
                "jobId": job_id,
                "rings": rings,
                "start_ring": start_ring,
                "map_start_db_record": map_start_record,
            },
            file,
            ensure_ascii=False,
            indent=2
        )

    finished_at = datetime.now()
    final_status = "failed" if errors and not results else "completed"
    upsert_job_record(
        job_id=job_id,
        patch={
            "status": final_status,
            "currentAction": "completed" if final_status == "completed" else "completed_with_errors",
            "lastHeartbeatAt": finished_at.isoformat(),
            "progressPercent": 100.0,
            "finishedAt": finished_at.isoformat(),
            "durationMs": int((time.perf_counter() - started_perf) * 1000),
            "teamStatuses": list(team_statuses.values()),
            "errors": [item.get("message", "") for item in errors],
        },
        create_if_missing=None,
    )

    if args.performance_report:
        report_dir = "output/performance"
        os.makedirs(report_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        team_label = f"team_{args.team}" if args.team is not None else "all_teams"
        map_label = normalized_map_name.replace("/", "_")
        report_path = os.path.join(report_dir, f"perf_{stamp}_{team_label}_{map_label}.json")

        report_payload = {
            "generated_at": datetime.now().isoformat(),
            "command": " ".join(sys.argv),
            "map": normalized_map_name,
            "team": args.team,
            "round": args.round,
            "video": video_path_str,
            "benchmark_streams": args.benchmark_streams,
            "results": benchmark_results,
        }
        with open(report_path, "w", encoding="utf-8") as report_file:
            json.dump(report_payload, report_file, ensure_ascii=False, indent=2)
        logger.info("Performance report saved: %s", report_path)

    logger.info("Analysis output saved: %s", output_path)


if __name__ == "__main__":
    main()
