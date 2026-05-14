from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import json
import math
import re
import sqlite3
import traceback
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import camera_tracker as ct
import rings_detector as rd

try:
    import pytesseract
except Exception:
    pytesseract = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEBUG_LOG_PATH_B91 = Path("debug-b91ec1.log")
DEBUG_SESSION_ID_B91 = "b91ec1"
DEBUG_LOG_PATH_63C8EC = Path("debug-63c8ec.log")
DEBUG_SESSION_ID_63C8EC = "63c8ec"

CONTROL_FILE_PATH: Path | None = None


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


def _debug_log_63c8ec(run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    try:
        payload = {
            "sessionId": DEBUG_SESSION_ID_63C8EC,
            "id": f"log_{int(time.time() * 1000)}_{hypothesis_id}",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
        }
        with DEBUG_LOG_PATH_63C8EC.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass

MAP_LABEL_TO_MP_ID: dict[str, str] = {
    "OLYMPUS": "mp_olympus",
    "WORLDS EDGE": "mp_worlds_edge",
    "WORLD'S EDGE": "mp_worlds_edge",
    "STORM POINT": "mp_storm_point",
    "E DISTRICT": "mp_e_district",
    "E-DISTRICT": "mp_e_district",
}
MP_ID_TO_MAP_LABEL: dict[str, str] = {}
for _label, _mp in MAP_LABEL_TO_MP_ID.items():
    MP_ID_TO_MAP_LABEL.setdefault(_mp, _label)

MAP_ALIASES: dict[str, list[str]] = {
    "OLYMPUS": ["OLYMPUS"],
    "WORLD'S EDGE": ["WORLD'S EDGE", "WORLDS EDGE", "WORLD S EDGE", "WORLDS"],
    "STORM POINT": ["STORM POINT", "STORMPOINT", "STORM"],
    "E-DISTRICT": ["E-DISTRICT", "E DISTRICT", "EDISTRICT", "DISTRICT"],
}

MAP_ROI_X = 420
MAP_ROI_Y = 0
MAP_ROI_WIDTH = 1080
MAP_ROI_HEIGHT = 1080
RING_COARSE_JUMP_FRAMES = 3000
RING_ROLLBACK_STEP_FRAMES = 100
RING_REFINE_WINDOW_FRAMES = 300
RING_STABLE_SECONDS = 2.0
RING_GEOMETRY_WINDOW_SECONDS = 2.0
RING_GEOMETRY_STEP_SECONDS = 1.0
RING_REFINE_STEP_SECONDS = 1.0
RING_MIN_GAP_SECONDS = 20.0
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
DETECTED_RING_COUNT = 5
# Canonical phase durations (seconds), in match timeline order.
# Used as fallback timing model when OCR events are missing/noisy.
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
# Test threshold anchor requested by user:
# ring1 minimum diameter is 900m, other rings scale proportionally
# to their canonical diameters.
RING_MIN_DIAMETER_BASE_METERS = 900.0
# Manual calibration anchors (map-space radii) from user-verified frames.
MAP_RING_RADIUS_MAP_OVERRIDE: dict[str, dict[int, float]] = {
    "mp_storm_point": {
        1: 329.0,
        2: 162.0,
    }
}
MAP_RING_RADIUS_METERS_OVERRIDE: dict[str, dict[int, float]] = {
    # User-validated proportional model for Storm Point.
    "mp_storm_point": {
        1: 550.0,
        2: 275.0,
    }
}
# Default map-space scale fallback when ring1 calibration is unavailable.
DEFAULT_METERS_TO_MAP_UNITS = 0.94
RING_RADIUS_TOLERANCE_RATIO = 0.35
RING_RADIUS_TOLERANCE_ABS = 35.0
ELIM_COARSE_SECONDS = 5.0
ELIM_REFINE_SECONDS = 5.0
ELIM_REFINE_STEP_SECONDS = 1.0


@dataclass
class FrameSignal:
    map_label: str | None
    map_conf: float
    is_map_camera: bool
    camera_conf: float
    cond_conf: float


class LiveProgress:
    def __init__(self, video_name: str, total_frames: int) -> None:
        self.video_name = video_name
        self.total_frames = max(1, int(total_frames))
        self.started_at = time.monotonic()
        self.stage_started_at = self.started_at
        self.stage = "init"
        self.last_emit_at = 0.0

    def set_stage(self, stage: str) -> None:
        if stage != self.stage:
            self.stage = stage
            self.stage_started_at = time.monotonic()
            self.emit_structured(0, force=True, extra="stage_start")

    def emit_structured(self, frame_idx: int, *, force: bool = False, extra: str = "") -> None:
        now = time.monotonic()
        if not force and (now - self.last_emit_at) < 1.0:
            return
        progress = max(0.0, min(1.0, float(frame_idx) / float(self.total_frames)))
        payload = {
            "video": self.video_name,
            "stage": self.stage,
            "frame": int(max(0, frame_idx)),
            "totalFrames": int(self.total_frames),
            "percent": round(progress * 100.0, 2),
            "stageElapsedSec": round(now - self.stage_started_at, 2),
            "totalElapsedSec": round(now - self.started_at, 2),
            "extra": str(extra or ""),
        }
        print(f"PROGRESS_JSON {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}", flush=True)

    def update(self, frame_idx: int, *, force: bool = False, extra: str = "") -> None:
        check_control()
        now = time.monotonic()
        if not force and (now - self.last_emit_at) < 1.0:
            return
        self.emit_structured(frame_idx, force=True, extra=extra)
        progress = max(0.0, min(1.0, float(frame_idx) / float(self.total_frames)))
        width = 24
        filled = int(round(progress * width))
        bar = "#" * filled + "-" * max(0, width - filled)
        stage_elapsed = now - self.stage_started_at
        total_elapsed = now - self.started_at
        line = (
            f"\r[{self.video_name}] {self.stage:<18} "
            f"[{bar}] {progress * 100:5.1f}% "
            f"| stage {stage_elapsed:6.1f}s | total {total_elapsed:6.1f}s"
        )
        if extra:
            line += f" | {extra}"
        print(line, end="", flush=True)
        self.last_emit_at = now

    def finish(self, note: str = "done") -> None:
        self.update(self.total_frames, force=True, extra=note)
        print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect map camera start timestamps in ALGS VOD clips.")
    parser.add_argument("--records-dir", default="ffmpeg_downloader/records", help="Folder with mp4 clips.")
    parser.add_argument("--video", default=None, help="Optional single video path.")
    parser.add_argument("--db-path", default="output/map_start_detection.sqlite", help="Output SQLite path.")
    parser.add_argument("--video-workers", type=int, default=1, help="Number of videos to process in parallel.")
    parser.add_argument("--team-workers", type=int, default=1, help="Number of team elimination OCR workers inside one video.")
    parser.add_argument("--control-file", default=None, help="JSON control file for pause/resume/cancel commands.")
    parser.add_argument("--fast-approx", action="store_true", help="Enable fast approximate profile (~5s tolerance).")
    parser.add_argument(
        "--fast-approx-small-steps",
        action="store_true",
        help="Use smaller internal skips with --fast-approx for short/problem videos.",
    )
    parser.add_argument("--frame-step", type=int, default=120, help="Analyze every N-th frame.")
    parser.add_argument("--coarse-jump-frames", type=int, default=3000, help="Fast-forward jump when no confidence at start.")
    parser.add_argument("--rollback-step-frames", type=int, default=100, help="Rollback step for backward search from first confident frame.")
    parser.add_argument("--refine-window-frames", type=int, default=300, help="Refinement window (+/- frames) around rollback anchor.")
    parser.add_argument("--start-refine-step-frames", type=int, default=3, help="Frame step for start-time refine scan.")
    parser.add_argument("--stable-seconds", type=float, default=5.0, help="Required stable duration of both conditions.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write DB.")
    parser.add_argument("--debug", action="store_true", help="Save debug frames and JSON traces.")
    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Show live detector windows. Hotkeys: Space pause, N next, P prev, Q/ESC quit, 1/2 select C1/C2, I/J/K/L move, U/O resize, R save calibration.",
    )
    parser.add_argument(
        "--visualize-delay-ms",
        type=int,
        default=16,
        help="Playback delay between visualization frames (ms).",
    )
    parser.add_argument(
        "--visualize-no-ocr",
        action="store_true",
        help="Visualization debug mode: skip OCR calls to avoid OCR blocking while tuning geometry.",
    )
    parser.add_argument(
        "--post-run-compare-view",
        action="store_true",
        help="After analysis, replay two windows: web view with detected rings + minimap ROI for comparison.",
    )
    parser.add_argument(
        "--post-run-compare-fps",
        type=float,
        default=6.0,
        help="Playback FPS for post-run compare windows.",
    )
    parser.add_argument(
        "--calib-lock-ratio-r1-r2",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep C1/C2 radius ratio at 2:1 (Storm Point calibrated mode).",
    )
    parser.add_argument(
        "--disable-calibration-ui",
        action="store_true",
        help="Disable calibration circles, mouse controls and calibration hotkeys in visualization.",
    )
    parser.add_argument("--debug-dir", default="output/map_start_debug", help="Debug output directory.")
    parser.add_argument("--debug-save-every", type=int, default=500, help="Deprecated (ignored): visual frames are no longer saved.")
    parser.add_argument("--ocr-min-confidence", type=float, default=0.62, help="Minimum OCR fuzzy confidence.")
    parser.add_argument("--camera-min-confidence", type=float, default=0.58, help="Minimum camera confidence.")
    parser.add_argument("--text-zones-file", default=None, help="Path to OCR text-zones JSON (optional).")
    parser.add_argument("--text-json-dir", default="output/map_start_text", help="Where to save OCR observations JSON.")
    parser.add_argument("--text-summary-top-n", type=int, default=3, help="Top-N confident lines per zone.")
    parser.add_argument("--text-ocr-min-confidence", type=float, default=0.0, help="Drop OCR observations below this confidence.")
    parser.add_argument(
        "--text-zones-max-enabled",
        type=int,
        default=5000,
        help="Limit enabled OCR zones to first N (default: 5000 = effectively all enabled zones).",
    )
    parser.add_argument(
        "--stop-on-first-both",
        action="store_true",
        help="Stop video analysis immediately on first frame where both conditions are true.",
    )
    parser.add_argument(
        "--pov-screenshot-offset-sec",
        type=float,
        default=3.0,
        help="Save screenshot at detected POV start + offset seconds.",
    )
    parser.add_argument(
        "--pov-screenshot-dir",
        default="output/map_start_pov",
        help="Directory for saved POV screenshots.",
    )
    parser.add_argument("--ring-coarse-sec", type=float, default=5.0, help="Coarse ring scan step in seconds.")
    parser.add_argument("--ring-rollback-sec", type=float, default=5.0, help="Rollback step for ring search in seconds.")
    parser.add_argument("--ring-refine-window-sec", type=float, default=5.0, help="Refine window (+/-) for ring search in seconds.")
    parser.add_argument("--ring-refine-step-sec", type=float, default=RING_REFINE_STEP_SECONDS, help="Refine step for ring search in seconds.")
    parser.add_argument("--ring-stable-seconds", type=float, default=1.0, help="Required stable duration for ring event.")
    parser.add_argument(
        "--ring-geometry-window-seconds",
        type=float,
        default=RING_GEOMETRY_WINDOW_SECONDS,
        help="Window duration used to estimate ring geometry.",
    )
    parser.add_argument(
        "--ring-geometry-step-sec",
        type=float,
        default=RING_GEOMETRY_STEP_SECONDS,
        help="Sampling step in seconds for ring geometry estimation.",
    )
    parser.add_argument("--elim-coarse-sec", type=float, default=ELIM_COARSE_SECONDS, help="Coarse step in seconds for eliminated timing search.")
    parser.add_argument("--elim-refine-sec", type=float, default=ELIM_REFINE_SECONDS, help="Refine backward window in seconds for eliminated timing search.")
    parser.add_argument("--elim-refine-step-sec", type=float, default=ELIM_REFINE_STEP_SECONDS, help="Refine step in seconds for eliminated timing search.")
    parser.add_argument(
        "--force-clear-rings",
        action="store_true",
        help="Clear stored rings for game_id when current run detected no rings.",
    )
    parser.add_argument(
        "--persist-rings-only",
        action="store_true",
        help="Only upsert Rings and Camreman for an existing map_start_detection row; do not replace detection/teams.",
    )
    parser.add_argument(
        "--ring-countdown-zone-mode",
        action="store_true",
        help="Experimental ring geometry mode: detect circle by red-zone boundary near 'RING N COUNTDOWN' events.",
    )
    parser.add_argument(
        "--ring-strict-line-profile",
        action="store_true",
        help="Strict red-zone profile: prioritize line-pair geometry, tighten constraints, and de-prioritize bbox fallbacks.",
    )
    parser.add_argument(
        "--ring-arc-only-mode",
        action="store_true",
        help="Disable legacy ring detectors and fit circle only from visible arc boundary.",
    )
    parser.add_argument(
        "--camera-tracking-mode",
        choices=("geometry", "edge_residual"),
        default="geometry",
        help="Camera tracking strategy: existing geometry transform or experimental signed edge residuals.",
    )
    parser.add_argument(
        "--disable-start-detection",
        action="store_true",
        help="Skip start search and use assumed start/map values.",
    )
    parser.add_argument(
        "--disable-team-detection",
        action="store_true",
        help="Disable team-name OCR extraction.",
    )
    parser.add_argument(
        "--disable-elimination-detection",
        action="store_true",
        help="Disable elimination OCR/timing search.",
    )
    parser.add_argument(
        "--disable-ring-detection",
        action="store_true",
        help="Disable ring timing/geometry detection.",
    )
    parser.add_argument(
        "--disable-camera-tracking",
        action="store_true",
        help="Disable camera tracking rows derived from ring geometry.",
    )
    parser.add_argument(
        "--assume-start-sec",
        type=float,
        default=0.0,
        help="Used with --disable-start-detection, default video start is 0.",
    )
    parser.add_argument(
        "--assume-map-name",
        default=None,
        help="Used with --disable-start-detection (example: 'STORM POINT').",
    )
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def check_control() -> None:
    global CONTROL_FILE_PATH
    if CONTROL_FILE_PATH is None or not CONTROL_FILE_PATH.exists():
        return
    action = ""
    try:
        payload = json.loads(CONTROL_FILE_PATH.read_text(encoding="utf-8"))
        action = str(payload.get("action") or "")
    except Exception:
        return
    if action == "cancel":
        print("PROGRESS_JSON " + json.dumps({"stage": "cancelled", "percent": 0, "extra": "cancel_requested"}, separators=(",", ":")), flush=True)
        raise KeyboardInterrupt("cancel_requested")
    while action == "pause":
        print("PROGRESS_JSON " + json.dumps({"stage": "paused", "percent": 0, "extra": "pause_requested"}, separators=(",", ":")), flush=True)
        time.sleep(1.0)
        try:
            payload = json.loads(CONTROL_FILE_PATH.read_text(encoding="utf-8"))
            action = str(payload.get("action") or "")
        except Exception:
            action = ""


def emit_team_progress(slot: int, status: str, progress_percent: float, *, frame_idx: int | None = None, extra: str = "") -> None:
    payload = {
        "kind": "team",
        "slot": int(slot),
        "status": str(status),
        "progressPercent": round(max(0.0, min(100.0, float(progress_percent))), 2),
        "frame": None if frame_idx is None else int(frame_idx),
        "extra": str(extra or ""),
    }
    print(f"PROGRESS_JSON {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}", flush=True)


def emit_error_progress(stage: str, message: str, *, extra: str = "") -> None:
    payload = {
        "kind": "error",
        "stage": str(stage),
        "message": str(message),
        "extra": str(extra or ""),
    }
    print(f"PROGRESS_JSON {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}", flush=True)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS map_start_detection (
            video_name TEXT PRIMARY KEY,
            video_path TEXT NOT NULL,
            map_name TEXT,
            map_mp_id TEXT,
            start_timestamp_sec REAL,
            confidence REAL,
            status TEXT NOT NULL,
            notes TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS Teams (
            game_id INTEGER NOT NULL,
            team_name TEXT,
            is_eliminated INTEGER NOT NULL DEFAULT 0,
            time_eliminated REAL
        );
        CREATE TABLE IF NOT EXISTS Rings (
            game_id INTEGER NOT NULL,
            ring_number INTEGER NOT NULL,
            center TEXT,
            radius REAL,
            time_start REAL,
            time_end REAL
        );
        CREATE TABLE IF NOT EXISTS Camreman (
            game_id INTEGER NOT NULL,
            timestamp_sec REAL NOT NULL,
            x REAL,
            y REAL,
            camera_size REAL
        );
        CREATE INDEX IF NOT EXISTS idx_teams_game_id ON Teams(game_id);
        CREATE INDEX IF NOT EXISTS idx_rings_game_id ON Rings(game_id);
        CREATE INDEX IF NOT EXISTS idx_rings_game_ring ON Rings(game_id, ring_number);
        CREATE INDEX IF NOT EXISTS idx_camreman_game_id ON Camreman(game_id);
        CREATE INDEX IF NOT EXISTS idx_camreman_game_ts ON Camreman(game_id, timestamp_sec);
        """
    )
    columns = {
        str(row[1]).lower()
        for row in conn.execute("PRAGMA table_info(map_start_detection)")
    }
    if "teams" not in columns:
        conn.execute("ALTER TABLE map_start_detection ADD COLUMN teams TEXT")


def upsert_detection(
    conn: sqlite3.Connection,
    *,
    video_name: str,
    video_path: str,
    map_name: str | None,
    map_mp_id: str | None,
    start_timestamp_sec: float | None,
    confidence: float | None,
    status: str,
    notes: str,
    teams_json: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO map_start_detection (
            video_name, video_path, map_name, map_mp_id, start_timestamp_sec,
            confidence, status, notes, teams, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_name) DO UPDATE SET
            video_path = excluded.video_path,
            map_name = excluded.map_name,
            map_mp_id = excluded.map_mp_id,
            start_timestamp_sec = excluded.start_timestamp_sec,
            confidence = excluded.confidence,
            status = excluded.status,
            notes = excluded.notes,
            teams = excluded.teams,
            updated_at = excluded.updated_at
        """,
        (
            video_name,
            video_path,
            map_name,
            map_mp_id,
            start_timestamp_sec,
            confidence,
            status,
            notes,
            teams_json,
            now_iso(),
        ),
    )


def parse_game_number(video_name: str) -> int | None:
    match = re.search(r"_G(\d+)_", video_name, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def resolve_game_id(conn: sqlite3.Connection, video_name: str) -> int:
    row = conn.execute(
        "SELECT rowid FROM map_start_detection WHERE video_name = ?",
        (video_name,),
    ).fetchone()
    if row and row[0] is not None:
        return int(row[0])
    parsed = parse_game_number(video_name)
    if parsed is not None:
        return parsed
    return abs(hash(video_name)) % (10**9)


def upsert_teams_rows(conn: sqlite3.Connection, game_id: int, teams_rows: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM Teams WHERE game_id = ?", (int(game_id),))
    if not teams_rows:
        return
    payload = [
        (
            int(game_id),
            row.get("team_name"),
            1 if bool(row.get("is_eliminated", False)) else 0,
            row.get("time_eliminated"),
        )
        for row in teams_rows
    ]
    conn.executemany(
        """
        INSERT INTO Teams (game_id, team_name, is_eliminated, time_eliminated)
        VALUES (?, ?, ?, ?)
        """,
        payload,
    )


def upsert_rings_rows(
    conn: sqlite3.Connection,
    game_id: int,
    rings_rows: list[dict[str, Any]],
    *,
    force_clear_when_empty: bool = False,
) -> None:
    if not rings_rows:
        if force_clear_when_empty:
            conn.execute("DELETE FROM Rings WHERE game_id = ?", (int(game_id),))
        return
    conn.execute("DELETE FROM Rings WHERE game_id = ?", (int(game_id),))
    payload = [
        (
            int(game_id),
            int(row.get("ring_number", 0)),
            row.get("center"),
            row.get("radius"),
            row.get("time_start"),
            row.get("time_end"),
        )
        for row in rings_rows
    ]
    conn.executemany(
        """
        INSERT INTO Rings (game_id, ring_number, center, radius, time_start, time_end)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        payload,
    )


def upsert_camreman_rows(
    conn: sqlite3.Connection,
    game_id: int,
    camreman_rows: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM Camreman WHERE game_id = ?", (int(game_id),))
    if not camreman_rows:
        return
    payload = [
        (
            int(game_id),
            float(row.get("timestamp_sec", 0.0) or 0.0),
            row.get("x"),
            row.get("y"),
            row.get("camera_size"),
        )
        for row in camreman_rows
    ]
    conn.executemany(
        """
        INSERT INTO Camreman (game_id, timestamp_sec, x, y, camera_size)
        VALUES (?, ?, ?, ?, ?)
        """,
        payload,
    )


def persist_result(
    conn: sqlite3.Connection,
    result: dict[str, Any],
    *,
    force_clear_rings: bool = False,
    rings_only: bool = False,
) -> None:
    if rings_only:
        existing = conn.execute(
            "SELECT rowid FROM map_start_detection WHERE video_name = ?",
            (str(result["video_name"]),),
        ).fetchone()
        if existing and existing[0] is not None:
            game_id = int(existing[0])
            upsert_rings_rows(
                conn,
                game_id=game_id,
                rings_rows=list(result.get("rings_rows", [])),
                force_clear_when_empty=force_clear_rings,
            )
            upsert_camreman_rows(
                conn,
                game_id=game_id,
                camreman_rows=list(result.get("camreman_rows", [])),
            )
            conn.commit()
            return

    upsert_detection(
        conn,
        video_name=result["video_name"],
        video_path=result["video_path"],
        map_name=result["map_name"],
        map_mp_id=result["map_mp_id"],
        start_timestamp_sec=result["start_timestamp_sec"],
        confidence=result["confidence"],
        status=result["status"],
        notes=result["notes"],
        teams_json=result.get("teams_json"),
    )
    game_id = resolve_game_id(conn, str(result["video_name"]))
    upsert_teams_rows(conn, game_id=game_id, teams_rows=list(result.get("teams_rows", [])))
    upsert_rings_rows(
        conn,
        game_id=game_id,
        rings_rows=list(result.get("rings_rows", [])),
        force_clear_when_empty=force_clear_rings,
    )
    upsert_camreman_rows(
        conn,
        game_id=game_id,
        camreman_rows=list(result.get("camreman_rows", [])),
    )
    conn.commit()


def persist_camera_result(
    camera_conn: sqlite3.Connection,
    detection_conn: sqlite3.Connection,
    result: dict[str, Any],
) -> None:
    game_id = resolve_game_id(detection_conn, str(result["video_name"]))
    ct.upsert_camera_rows(
        camera_conn,
        game_id=game_id,
        camera_rows=list(result.get("camera_rows", [])),
    )
    camera_conn.commit()


def analyze_video_task(
    *,
    video_path: Path,
    args: argparse.Namespace,
    debug_dir: Path,
    text_json_dir: Path,
    pov_screenshot_dir: Path,
    enable_progress: bool,
) -> dict[str, Any]:
    return analyze_video(
        video_path=video_path,
        frame_step=args.frame_step,
        coarse_jump_frames=args.coarse_jump_frames,
        rollback_step_frames=args.rollback_step_frames,
        refine_window_frames=args.refine_window_frames,
        start_refine_step_frames=args.start_refine_step_frames,
        stable_seconds=args.stable_seconds,
        ocr_min_conf=args.ocr_min_confidence,
        camera_min_conf=args.camera_min_confidence,
        debug=args.debug,
        debug_dir=debug_dir,
        debug_save_every=args.debug_save_every,
        text_zones_file=args.text_zones_file,
        text_json_dir=text_json_dir,
        text_summary_top_n=args.text_summary_top_n,
        text_ocr_min_confidence=args.text_ocr_min_confidence,
        text_zones_max_enabled=args.text_zones_max_enabled,
        stop_on_first_both=args.stop_on_first_both,
        pov_screenshot_offset_sec=args.pov_screenshot_offset_sec,
        pov_screenshot_dir=pov_screenshot_dir,
        visualize=args.visualize,
        visualize_no_ocr=bool(args.visualize_no_ocr),
        elim_coarse_sec=args.elim_coarse_sec,
        elim_refine_sec=args.elim_refine_sec,
        elim_refine_step_sec=args.elim_refine_step_sec,
        ring_coarse_sec=args.ring_coarse_sec,
        ring_rollback_sec=args.ring_rollback_sec,
        ring_refine_window_sec=args.ring_refine_window_sec,
        ring_refine_step_sec=args.ring_refine_step_sec,
        ring_stable_seconds=args.ring_stable_seconds,
        ring_geometry_window_seconds=args.ring_geometry_window_seconds,
        ring_geometry_step_sec=args.ring_geometry_step_sec,
        ring_countdown_zone_mode=bool(args.ring_countdown_zone_mode),
        ring_strict_line_profile=bool(args.ring_strict_line_profile),
        ring_arc_only_mode=bool(args.ring_arc_only_mode),
        camera_tracking_mode=str(args.camera_tracking_mode),
        team_workers=max(1, int(args.team_workers)),
        disable_start_detection=bool(args.disable_start_detection),
        disable_team_detection=bool(args.disable_team_detection),
        disable_elimination_detection=bool(args.disable_elimination_detection),
        disable_ring_detection=bool(args.disable_ring_detection),
        disable_camera_tracking=bool(args.disable_camera_tracking),
        assume_start_sec=float(args.assume_start_sec),
        assume_map_name=args.assume_map_name,
        enable_progress=enable_progress,
    )


def _row_active_at_timestamp(rows: list[dict[str, Any]], ts: float) -> dict[str, Any] | None:
    active: dict[str, Any] | None = None
    for row in rows:
        try:
            t0 = float(row.get("time_start", 0.0) or 0.0)
            t1 = float(row.get("time_end", 0.0) or 0.0)
        except Exception:
            continue
        if t1 < t0:
            t0, t1 = t1, t0
        if t0 <= ts <= t1:
            active = row
    return active


def replay_post_run_compare_view(
    *,
    video_path: Path,
    map_mp_id: str | None,
    rings_rows: list[dict[str, Any]],
    playback_fps: float,
) -> None:
    rd.set_map_context(map_mp_id)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    target_fps = float(max(0.5, playback_fps))
    step = max(1, int(round(float(fps) / target_fps))) if fps > 0 else 1
    delay_ms = max(1, int(round(1000.0 / target_fps)))
    map_ref = resolve_map_reference(map_mp_id)
    if map_ref is None:
        map_ref = np.zeros((1080, 1080, 3), dtype=np.uint8)
        cv2.putText(map_ref, "map not found", (30, 540), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (180, 180, 180), 2)
    else:
        map_ref = cv2.resize(map_ref, (1080, 1080), interpolation=cv2.INTER_AREA)

    window_web = "post_run :: web_view"
    window_roi = "post_run :: roi"
    frame_idx = 0
    while frame_idx < max(1, total_frames):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        ts = float(frame_idx) / fps if fps > 0 else 0.0
        active = _row_active_at_timestamp(rings_rows, ts)

        web = map_ref.copy()
        x1, y1, x2, y2 = ring_minimap_bounds(frame)
        roi = frame[y1:y2, x1:x2].copy()
        cv2.rectangle(frame, (x1, y1), (x2, y2), (220, 120, 255), 2)

        if active is not None:
            center = parse_center_json(str(active.get("center")) if active.get("center") is not None else None)
            center_payload: dict[str, Any] = {}
            center_raw = active.get("center")
            if center_raw is not None:
                try:
                    parsed_payload = json.loads(str(center_raw))
                    if isinstance(parsed_payload, dict):
                        center_payload = parsed_payload
                except Exception:
                    center_payload = {}
            try:
                rad = float(active.get("radius", 0.0) or 0.0)
            except Exception:
                rad = 0.0
            if center is not None and rad > 0:
                cx = int(round(float(np.clip(center[0], 0.0, 1079.0))))
                cy = int(round(float(np.clip(center[1], 0.0, 1079.0))))
                cr = int(round(max(1.0, rad)))
                red_layer = np.full_like(web, (0, 0, 200), dtype=np.uint8)
                ring_mask = np.zeros((1080, 1080), dtype=np.uint8)
                cv2.circle(ring_mask, (cx, cy), cr, 255, thickness=-1)
                outside_mask = cv2.bitwise_not(ring_mask)
                red_mix = cv2.addWeighted(web, 0.75, red_layer, 0.25, 0.0)
                web[outside_mask > 0] = red_mix[outside_mask > 0]
                cv2.circle(web, (cx, cy), cr, (255, 255, 255), 2)
                cv2.circle(web, (cx, cy), 3, (255, 255, 255), -1)
                cv2.putText(
                    web,
                    f"ring={int(active.get('ring_number', 0) or 0)} t={ts:.2f}s",
                    (14, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (240, 240, 240),
                    2,
                )
                ring_number = int(active.get("ring_number", 0) or 0)
                if ring_number <= 0:
                    ring_number = int(center_payload.get("ring_number") or 0)
                scale_hint_raw = center_payload.get("meters_to_map_units")
                if scale_hint_raw is None:
                    scale_hint = float(initial_meters_to_map_units(map_mp_id))
                else:
                    try:
                        scale_hint = float(scale_hint_raw)
                    except Exception:
                        scale_hint = float(initial_meters_to_map_units(map_mp_id))
                observed_d_px = 0.0
                try:
                    observed_d_px = float(center_payload.get("diameter_px", 0.0) or 0.0)
                except Exception:
                    observed_d_px = 0.0
                if observed_d_px <= 0.0:
                    observed_d_map = float(max(1.0, rad) * 2.0)
                    observed_d_px = (observed_d_map / 1080.0) * float(max(1.0, MAP_ROI_WIDTH))
                expected_d_px = 0.0
                if ring_number > 0:
                    expected_r_map = rd.expected_ring_radius_map_units(ring_number, scale_hint)
                    if expected_r_map is not None and expected_r_map > 0:
                        expected_d_map = float(expected_r_map) * 2.0
                        expected_d_px = (expected_d_map / 1080.0) * float(max(1.0, MAP_ROI_WIDTH))
                zoom_ratio = 1.0
                if observed_d_px > 0.0 and expected_d_px > 0.0:
                    zoom_ratio = float(np.clip(observed_d_px / expected_d_px, 0.25, 4.0))
                observer_size = int(round(float(np.clip(1080.0 / max(0.2, zoom_ratio), 120.0, 1080.0))))
                half = observer_size // 2
                ox1 = int(max(0, min(1079, cx - half)))
                oy1 = int(max(0, min(1079, cy - half)))
                ox2 = int(max(0, min(1079, ox1 + observer_size)))
                oy2 = int(max(0, min(1079, oy1 + observer_size)))
                cv2.rectangle(web, (ox1, oy1), (ox2, oy2), (255, 220, 80), 2)
                cv2.putText(
                    web,
                    f"observer_roi {observer_size} zoom={zoom_ratio:.3f}",
                    (max(8, ox1), max(20, oy1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (255, 220, 80),
                    1,
                )
        else:
            cv2.putText(
                web,
                f"no rings detected t={ts:.2f}s",
                (14, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (230, 230, 230),
                2,
            )

        cv2.putText(web, "post-run web view", (14, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1)
        cv2.putText(
            web,
            f"playback_fps={target_fps:.2f} step={step}",
            (14, 78),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (220, 220, 220),
            1,
        )
        cv2.putText(web, "ROI window is second view", (14, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
        if roi.size > 0:
            cv2.putText(roi, f"frame={frame_idx} t={ts:.2f}s", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

        cv2.imshow(window_web, web)
        if roi.size > 0:
            cv2.imshow(window_roi, roi)
        key = cv2.waitKey(max(1, int(delay_ms))) & 0xFF
        if key in (27, ord("q"), ord("Q")):
            break
        if key in (ord("p"), ord("P"), ord("[")):
            frame_idx = max(0, frame_idx - (step * 10))
            continue
        frame_idx += step

    cap.release()
    cv2.destroyWindow(window_web)
    try:
        cv2.destroyWindow(window_roi)
    except Exception:
        pass


def iter_videos(records_dir: Path, one_video: str | None) -> list[Path]:
    if one_video:
        v = Path(one_video)
        if not v.is_absolute():
            v = PROJECT_ROOT / v
        return [v]
    if not records_dir.exists():
        return []
    return sorted(
        p
        for p in records_dir.glob("*.mp4")
        if p.is_file() and not p.name.startswith(".")
    )


def normalize_text(raw: str) -> str:
    txt = raw.upper().replace("`", "'")
    txt = "".join(ch if (ch.isalnum() or ch in {" ", "'", "-"}) else " " for ch in txt)
    txt = " ".join(txt.split())
    txt = txt.replace("E DISTRICT", "E-DISTRICT")
    return txt


def fuzzy_map_match(text: str) -> tuple[str | None, float]:
    if not text:
        return None, 0.0
    tokens = [t for t in text.split(" ") if t]
    candidates = {text}
    # Evaluate short phrase windows so "MATCH 1 STORM POINT" can still match "STORM POINT".
    for n in (1, 2, 3, 4):
        for i in range(0, max(0, len(tokens) - n + 1)):
            candidates.add(" ".join(tokens[i : i + n]))

    best_name: str | None = None
    best_score = 0.0
    for candidate in candidates:
        for canon, aliases in MAP_ALIASES.items():
            for alias in aliases:
                score = difflib.SequenceMatcher(a=candidate, b=alias).ratio()
                if alias in candidate:
                    score = max(score, 0.92)
                if score > best_score:
                    best_score = score
                    best_name = canon
    return best_name, best_score


def canonicalize_zone_text_with_map_dict(
    normalized_text: str,
    zone_label: str | None,
    min_conf: float = 0.72,
) -> tuple[str, str | None, float]:
    text = normalize_text(normalized_text)
    if not text:
        return "", None, 0.0
    map_name, conf = fuzzy_map_match(text)
    if not map_name:
        return text, None, 0.0
    label = (zone_label or "").strip().lower()
    label_is_map = ("map" in label) or ("карта" in label)
    # Strong global match OR map-related zone label -> canonical map name.
    if conf >= 0.90 or (label_is_map and conf >= min_conf):
        return map_name, map_name, float(conf)
    # Do not propagate weak fuzzy matches as map candidates.
    return text, None, 0.0


def resolve_text_zones_file(explicit_file: str | None) -> Path | None:
    if explicit_file:
        p = Path(explicit_file)
        candidate = p if p.is_absolute() else (PROJECT_ROOT / p)
        return candidate if candidate.exists() else None
    zones_dir = PROJECT_ROOT / "output" / "text_zones"
    if not zones_dir.exists():
        return None
    preferred = [
        zones_dir / "global.text-zones.json",
        zones_dir / "all_maps.text-zones.json",
        zones_dir / "shared.text-zones.json",
    ]
    for candidate in preferred:
        if candidate.exists():
            return candidate
    candidates = sorted(
        zones_dir.glob("*.text-zones.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    return None


def load_text_zones(explicit_file: str | None, max_enabled: int = 1) -> dict[str, Any] | None:
    def _read_payload(candidate: Path) -> dict[str, Any] | None:
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            return None
        return loaded if isinstance(loaded, dict) else None

    zone_file = resolve_text_zones_file(explicit_file)
    if zone_file is None or not zone_file.exists():
        return None
    payload = _read_payload(zone_file)
    if payload is None:
        return None

    # Auto-fallback for empty auto-selected files:
    # choose the most recently updated non-empty text-zones file.
    if explicit_file is None:
        zones_raw_probe = payload.get("zones", [])
        if not isinstance(zones_raw_probe, list) or len(zones_raw_probe) == 0:
            zones_dir = PROJECT_ROOT / "output" / "text_zones"
            if zones_dir.exists():
                candidates = sorted(
                    zones_dir.glob("*.text-zones.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for candidate in candidates:
                    if candidate.resolve() == zone_file.resolve():
                        continue
                    alt_payload = _read_payload(candidate)
                    if not isinstance(alt_payload, dict):
                        continue
                    alt_zones = alt_payload.get("zones", [])
                    if isinstance(alt_zones, list) and len(alt_zones) > 0:
                        zone_file = candidate
                        payload = alt_payload
                        break

    image_size = payload.get("image_size") if isinstance(payload, dict) else None
    width = int((image_size or {}).get("width", 0) or 0)
    height = int((image_size or {}).get("height", 0) or 0)
    zones_raw = payload.get("zones", []) if isinstance(payload, dict) else []
    zones: list[dict[str, Any]] = []
    for idx, zone in enumerate(zones_raw if isinstance(zones_raw, list) else []):
        if not isinstance(zone, dict):
            continue
        z_width = max(1, int(zone.get("width", 1) or 1))
        z_height = max(1, int(zone.get("height", 1) or 1))
        zones.append(
            {
                "id": str(zone.get("id") or f"text_zone_{idx + 1}"),
                "x": max(0, int(zone.get("x", 0) or 0)),
                "y": max(0, int(zone.get("y", 0) or 0)),
                "width": z_width,
                "height": z_height,
                "label": str(zone.get("label") or "").strip() or None,
                "enabled": bool(zone.get("enabled", True)),
            }
        )
    max_count = max(0, int(max_enabled))
    if max_count > 0:
        enabled_seen = 0
        for zone in zones:
            if zone.get("enabled", True):
                enabled_seen += 1
                if enabled_seen > max_count:
                    zone["enabled"] = False
    else:
        for zone in zones:
            zone["enabled"] = False

    return {
        "file": str(zone_file),
        "map": str(payload.get("map") or "all_maps"),
        "image_size": {"width": width, "height": height},
        "zones": zones,
    }


def run_zone_ocr(
    frame: np.ndarray,
    frame_idx: int,
    fps: float,
    zones_payload: dict[str, Any] | None,
    min_confidence: float,
) -> list[dict[str, Any]]:
    if zones_payload is None or pytesseract is None:
        return []
    zones = zones_payload.get("zones", [])
    if not isinstance(zones, list) or not zones:
        return []
    h, w = frame.shape[:2]
    out: list[dict[str, Any]] = []
    ts = frame_idx / fps if fps > 0 else 0.0
    for zone in zones:
        if not isinstance(zone, dict) or zone.get("enabled", True) is False:
            continue
        x, y, zw, zh = zone_rect_to_frame(zone, zones_payload, w, h)
        x2 = min(w, x + zw)
        y2 = min(h, y + zh)
        if x2 <= x or y2 <= y:
            continue
        roi = frame[y:y2, x:x2]
        if roi.size == 0:
            continue
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        text_raw = ""
        conf = 0.0
        try:
            data = pytesseract.image_to_data(
                gray,
                output_type=pytesseract.Output.DICT,
                config="--psm 6",
                timeout=1.5,
            )
            text_parts: list[str] = []
            conf_values: list[float] = []
            for txt, val in zip(data.get("text", []), data.get("conf", [])):
                clean = str(txt or "").strip()
                if not clean:
                    continue
                try:
                    fval = float(val)
                except Exception:
                    fval = -1.0
                if fval < 0:
                    continue
                text_parts.append(clean)
                conf_values.append(fval / 100.0)
            text_raw = " ".join(text_parts).strip()
            conf = float(np.mean(conf_values)) if conf_values else 0.0
        except Exception:
            text_raw = ""
            conf = 0.0
        norm = normalize_text(text_raw)
        if not norm:
            continue
        if conf < min_confidence:
            continue
        canonical_text, matched_map_name, matched_map_conf = canonicalize_zone_text_with_map_dict(
            norm,
            zone.get("label"),
        )
        out.append(
            {
                "frame": int(frame_idx),
                "timestamp_sec": round(float(ts), 3),
                "zone_id": str(zone.get("id") or ""),
                "zone_label": zone.get("label"),
                "raw_text": text_raw,
                "normalized_text": canonical_text,
                "matched_map_name": matched_map_name,
                "matched_map_confidence": round(float(matched_map_conf), 4) if matched_map_name else 0.0,
                "ocr_confidence": round(float(conf), 4),
            }
        )
    return out


def zone_rect_to_frame(
    zone: dict[str, Any],
    zones_payload: dict[str, Any] | None,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int, int, int]:
    image_size = (zones_payload or {}).get("image_size", {}) if zones_payload else {}
    src_w = max(1, int((image_size or {}).get("width", frame_w) or frame_w))
    src_h = max(1, int((image_size or {}).get("height", frame_h) or frame_h))

    zx = float(zone.get("x", 0) or 0)
    zy = float(zone.get("y", 0) or 0)
    zw = max(1.0, float(zone.get("width", 1) or 1))
    zh = max(1.0, float(zone.get("height", 1) or 1))

    # If zone image is map-like (square), interpret coordinates as map-space
    # and project them into the map ROI inside the video frame.
    src_aspect = float(src_w) / float(max(1, src_h))
    frame_aspect = float(frame_w) / float(max(1, frame_h))
    map_like_source = abs(src_aspect - 1.0) <= 0.2 and frame_aspect >= 1.4
    if map_like_source:
        sx = MAP_ROI_WIDTH / float(src_w)
        sy = MAP_ROI_HEIGHT / float(src_h)
        fx = MAP_ROI_X + zx * sx
        fy = MAP_ROI_Y + zy * sy
        fw = zw * sx
        fh = zh * sy
    else:
        sx = frame_w / float(src_w)
        sy = frame_h / float(src_h)
        fx = zx * sx
        fy = zy * sy
        fw = zw * sx
        fh = zh * sy

    x = max(0, min(int(round(fx)), frame_w - 1))
    y = max(0, min(int(round(fy)), frame_h - 1))
    w_out = max(1, int(round(fw)))
    h_out = max(1, int(round(fh)))
    return x, y, w_out, h_out


def write_image_safe(path: Path, image: np.ndarray) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
        path = path.with_suffix(ext)
    try:
        ok, encoded = cv2.imencode(ext, image)
        if not ok or encoded is None:
            return False
        encoded.tofile(str(path))
        return True
    except Exception:
        return False


def save_screenshot_at_timestamp(
    video_path: Path,
    timestamp_sec: float,
    output_path: Path,
) -> Path | None:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frame_idx = int(max(0.0, timestamp_sec) * fps) if fps > 0 else int(max(0.0, timestamp_sec) * 60.0)
    if total_frames > 0:
        frame_idx = min(frame_idx, total_frames - 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    if write_image_safe(output_path, frame):
        return output_path
    return None


def aggregate_confident_lines(observations: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in observations:
        zone_id = str(item.get("zone_id") or "")
        text = str(item.get("normalized_text") or "").strip()
        if not zone_id or not text:
            continue
        key = (zone_id, text)
        ts = float(item.get("timestamp_sec", 0.0) or 0.0)
        conf = float(item.get("ocr_confidence", 0.0) or 0.0)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "zone_id": zone_id,
                "zone_label": item.get("zone_label"),
                "text": text,
                "hits": 0,
                "conf_sum": 0.0,
                "first_ts": ts,
                "last_ts": ts,
            }
            grouped[key] = bucket
        bucket["hits"] += 1
        bucket["conf_sum"] += conf
        bucket["first_ts"] = min(float(bucket["first_ts"]), ts)
        bucket["last_ts"] = max(float(bucket["last_ts"]), ts)

    per_zone: dict[str, list[dict[str, Any]]] = {}
    for bucket in grouped.values():
        hits = int(bucket["hits"])
        avg_conf = float(bucket["conf_sum"]) / max(1, hits)
        span = max(0.0, float(bucket["last_ts"]) - float(bucket["first_ts"]))
        stability = min(1.0, span / max(1.0, hits))
        score = float(hits) + (avg_conf * 3.0) + (stability * 2.0)
        row = {
            "zone_id": bucket["zone_id"],
            "zone_label": bucket.get("zone_label"),
            "text": bucket["text"],
            "score": round(score, 4),
            "avg_confidence": round(avg_conf, 4),
            "hits": hits,
            "stability_sec": round(span, 3),
        }
        per_zone.setdefault(str(bucket["zone_id"]), []).append(row)

    result: list[dict[str, Any]] = []
    for zone_id in sorted(per_zone.keys()):
        top = sorted(per_zone[zone_id], key=lambda x: (x["score"], x["hits"]), reverse=True)[: max(1, top_n)]
        result.extend(top)
    return result


def clone_payload_with_selected_zones(
    zones_payload: dict[str, Any] | None,
    labels: set[str],
) -> dict[str, Any] | None:
    if zones_payload is None:
        return None
    zones = zones_payload.get("zones", [])
    if not isinstance(zones, list):
        return None
    wanted = {label.strip().lower() for label in labels}
    selected = []
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        label = str(zone.get("label") or "").strip().lower()
        if label in wanted:
            copied = dict(zone)
            copied["enabled"] = True
            selected.append(copied)
    if not selected:
        return None
    return {
        "file": zones_payload.get("file"),
        "map": zones_payload.get("map"),
        "image_size": zones_payload.get("image_size", {}),
        "zones": selected,
    }


def extract_team_slot(label: str) -> tuple[int | None, str | None]:
    txt = str(label or "").strip().lower()
    match = re.match(r"^t(\d+)_(name|iseliminated)$", txt)
    if not match:
        return None, None
    try:
        slot = int(match.group(1))
    except Exception:
        return None, None
    return slot, match.group(2)


def is_eliminated_text(text: str) -> bool:
    norm = normalize_text(text or "")
    compact = norm.replace(" ", "")
    return ("ELIMINATED" in compact) or ("ELIMINAT" in compact)


def normalize_ring_ocr_text(text: str) -> str:
    norm = normalize_text(text or "")
    if not norm:
        return ""
    compact = norm.replace(" ", "")
    # Common OCR noise for COUNTDOWN from ALGS feed.
    compact = compact.replace("COUNTOOWN", "COUNTDOWN")
    compact = compact.replace("COUNTODWN", "COUNTDOWN")
    compact = compact.replace("COUNTD0WN", "COUNTDOWN")
    compact = compact.replace("COUNTDWN", "COUNTDOWN")
    compact = compact.replace("COUNTDCWN", "COUNTDOWN")
    compact = compact.replace("RlNG", "RING")
    compact = compact.replace("R1NG", "RING")
    compact = compact.replace("CLOSlNG", "CLOSING")
    return compact


def parse_ring_event(text: str) -> tuple[int | None, str | None]:
    compact = normalize_ring_ocr_text(text)
    if not compact:
        return None, None
    m = re.search(r"RING(\d+)?(CLOSING|COUNTDOWN)", compact)
    if m:
        event = str(m.group(2)).lower()
        ring_num: int | None = None
        if m.group(1) is not None:
            try:
                ring_num = int(m.group(1))
            except Exception:
                ring_num = None
        return ring_num, event
    return None, None


def run_ring_zone_ocr(
    frame: np.ndarray,
    frame_idx: int,
    fps: float,
    payload_ring: dict[str, Any] | None,
    min_confidence: float,
) -> list[dict[str, Any]]:
    if payload_ring is None or pytesseract is None:
        return []
    zones = payload_ring.get("zones", [])
    if not isinstance(zones, list) or not zones:
        return []
    h, w = frame.shape[:2]
    out: list[dict[str, Any]] = []
    ts = frame_idx / fps if fps > 0 else 0.0
    config = "--oem 1 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
    for zone in zones:
        if not isinstance(zone, dict) or zone.get("enabled", True) is False:
            continue
        label = str(zone.get("label") or "").strip().lower()
        if label != "is_ringclosing":
            continue
        x, y, zw, zh = zone_rect_to_frame(zone, payload_ring, w, h)
        x2 = min(w, x + zw)
        y2 = min(h, y + zh)
        if x2 <= x or y2 <= y:
            continue
        roi = frame[y:y2, x:x2]
        if roi.size == 0:
            continue
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text_raw = ""
        conf = 0.0
        try:
            data = pytesseract.image_to_data(
                bw,
                output_type=pytesseract.Output.DICT,
                config=config,
                timeout=1.2,
            )
            text_parts: list[str] = []
            conf_values: list[float] = []
            for txt, val in zip(data.get("text", []), data.get("conf", [])):
                clean = str(txt or "").strip()
                if not clean:
                    continue
                try:
                    fval = float(val)
                except Exception:
                    fval = -1.0
                if fval < 0:
                    continue
                text_parts.append(clean)
                conf_values.append(fval / 100.0)
            text_raw = " ".join(text_parts).strip()
            conf = float(np.mean(conf_values)) if conf_values else 0.0
        except Exception:
            text_raw = ""
            conf = 0.0
        norm = normalize_text(text_raw)
        if not norm or conf < min_confidence:
            continue
        out.append(
            {
                "frame": int(frame_idx),
                "timestamp_sec": round(float(ts), 3),
                "zone_id": str(zone.get("id") or ""),
                "zone_label": zone.get("label"),
                "raw_text": text_raw,
                "normalized_text": norm,
                "ocr_confidence": round(float(conf), 4),
            }
        )
    return out


def has_ring_event(
    frame: np.ndarray,
    frame_idx: int,
    fps: float,
    payload_ring: dict[str, Any] | None,
    text_ocr_min_confidence: float,
    target_ring_number: int,
    target_event_type: str,
) -> tuple[bool, bool]:
    observations = run_ring_zone_ocr(
        frame=frame,
        frame_idx=int(frame_idx),
        fps=fps,
        payload_ring=payload_ring,
        min_confidence=max(0.0, float(text_ocr_min_confidence)),
    )
    matched_explicit = False
    matched_inferred = False
    for item in observations:
        label = str(item.get("zone_label") or "").strip().lower()
        if label != "is_ringclosing":
            continue
        normalized_text = str(item.get("normalized_text") or "")
        ring_num, event_type = parse_ring_event(normalized_text)
        if event_type is None:
            compact = normalize_ring_ocr_text(normalized_text)
            if target_event_type == "closing" and difflib.SequenceMatcher(None, compact, "RINGCLOSING").ratio() >= 0.88:
                matched_inferred = True
            if target_event_type == "countdown" and (
                difflib.SequenceMatcher(None, compact, "RINGCOUNTDOWN").ratio() >= 0.80
                or "COUNT" in compact
            ):
                matched_inferred = True
            continue
        if event_type != target_event_type:
            continue
        if ring_num is None:
            matched_inferred = True
            continue
        if ring_num == target_ring_number:
            matched_explicit = True
            continue
        if target_event_type == "countdown" and abs(int(ring_num) - int(target_ring_number)) <= 1:
            matched_inferred = True
    return matched_explicit, matched_inferred


def find_first_explicit_ring_number(
    cap: cv2.VideoCapture,
    fps: float,
    start_frame: int,
    max_frame: int,
    payload_ring: dict[str, Any] | None,
    text_ocr_min_confidence: float,
    coarse_step_frames: int,
    event_type: str = "closing",
) -> int | None:
    if payload_ring is None or not cap.isOpened():
        return None
    target_event = str(event_type or "closing").strip().lower()
    if target_event not in {"closing", "countdown"}:
        target_event = "closing"
    frame_idx = max(0, int(start_frame))
    step = max(1, int(coarse_step_frames))
    while frame_idx <= max_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        observations = run_ring_zone_ocr(
            frame=frame,
            frame_idx=int(frame_idx),
            fps=fps,
            payload_ring=payload_ring,
            min_confidence=max(0.0, float(text_ocr_min_confidence)),
        )
        for item in observations:
            ring_num, found_event_type = parse_ring_event(str(item.get("normalized_text") or ""))
            if found_event_type != target_event or ring_num is None:
                continue
            if int(ring_num) in RING_DIAMETERS_METERS:
                return int(ring_num)
        frame_idx += step
    return None


def find_first_stable_ring_event(
    cap: cv2.VideoCapture,
    fps: float,
    start_frame: int,
    max_frame: int,
    payload_ring: dict[str, Any] | None,
    text_ocr_min_confidence: float,
    target_ring_number: int,
    target_event_type: str,
    coarse_step_frames: int,
    rollback_step_frames: int,
    refine_window_frames: int,
    refine_step_frames: int,
    stable_target_sec: float,
    require_explicit_number: bool = False,
    progress: LiveProgress | None = None,
) -> float | None:
    if payload_ring is None or not cap.isOpened():
        return None
    coarse_step = max(1, int(coarse_step_frames))
    rollback_step = max(1, int(rollback_step_frames))
    refine_window = max(1, int(refine_window_frames))
    refine_step = max(1, int(refine_step_frames))
    stable_target = max(0.25, float(stable_target_sec))
    first_match_ts: float | None = None

    first_hit: int | None = None
    frame_idx = max(0, int(start_frame))
    if progress is not None:
        progress.set_stage(f"ring{target_ring_number}_{target_event_type}_coarse")
    while frame_idx <= max_frame:
        if progress is not None:
            progress.update(frame_idx)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        matched_explicit, matched_inferred = has_ring_event(
            frame=frame,
            frame_idx=frame_idx,
            fps=fps,
            payload_ring=payload_ring,
            text_ocr_min_confidence=text_ocr_min_confidence,
            target_ring_number=target_ring_number,
            target_event_type=target_event_type,
        )
        matched = bool(matched_explicit or (matched_inferred and not require_explicit_number))
        if matched:
            first_hit = frame_idx
            break
        frame_idx += coarse_step
    if first_hit is None:
        return None

    anchor = first_hit
    if progress is not None:
        progress.set_stage(f"ring{target_ring_number}_{target_event_type}_rollback")
    while anchor > start_frame:
        if progress is not None:
            progress.update(anchor)
        probe = max(int(start_frame), anchor - rollback_step)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(probe))
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        matched_explicit, matched_inferred = has_ring_event(
            frame=frame,
            frame_idx=probe,
            fps=fps,
            payload_ring=payload_ring,
            text_ocr_min_confidence=text_ocr_min_confidence,
            target_ring_number=target_ring_number,
            target_event_type=target_event_type,
        )
        matched = bool(matched_explicit or (matched_inferred and not require_explicit_number))
        if matched:
            anchor = probe
            continue
        break

    refine_start = max(int(start_frame), anchor - refine_window)
    refine_end = min(int(max_frame), anchor + refine_window)
    stable_run_sec = 0.0
    stable_start_ts: float | None = None
    frame_idx = refine_start
    if progress is not None:
        progress.set_stage(f"ring{target_ring_number}_{target_event_type}_refine")
    while frame_idx <= refine_end:
        if progress is not None:
            progress.update(frame_idx)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        matched_explicit, matched_inferred = has_ring_event(
            frame=frame,
            frame_idx=frame_idx,
            fps=fps,
            payload_ring=payload_ring,
            text_ocr_min_confidence=text_ocr_min_confidence,
            target_ring_number=target_ring_number,
            target_event_type=target_event_type,
        )
        matched = bool(matched_explicit or (matched_inferred and not require_explicit_number))
        if matched:
            if first_match_ts is None:
                first_match_ts = frame_idx / fps if fps > 0 else 0.0
            if stable_start_ts is None:
                stable_start_ts = frame_idx / fps if fps > 0 else 0.0
                stable_run_sec = 0.0
            stable_run_sec += (refine_step / fps) if fps > 0 else 0.0
            if stable_run_sec >= stable_target:
                return float(stable_start_ts)
        else:
            stable_start_ts = None
            stable_run_sec = 0.0
        frame_idx += refine_step
    # Fallback: keep the first observed event timestamp even when stable window
    # wasn't reached. This avoids dropping later rings due to short/noisy OCR spans.
    return float(first_match_ts) if first_match_ts is not None else None


def estimate_ring_geometry_over_window(
    cap: cv2.VideoCapture,
    fps: float,
    start_ts: float,
    max_frame: int,
    zones_payload_full: dict[str, Any] | None,
    window_seconds: float,
    step_frames: int,
    expected_center: tuple[float, float] | None = None,
    expected_radius: float | None = None,
    min_radius_map_units: float | None = None,
    countdown_zone_mode: bool = False,
    strict_line_profile: bool = False,
    arc_only_mode: bool = False,
) -> tuple[str | None, float | None, dict[str, Any] | None]:
    if not cap.isOpened():
        return None, None, None
    start_frame = max(0, int(start_ts * fps))
    end_frame = min(max_frame, int((start_ts + float(window_seconds)) * fps))
    step = max(1, int(step_frames))
    xs: list[float] = []
    ys: list[float] = []
    rs: list[float] = []
    ws: list[float] = []
    source_hist: dict[str, float] = {}
    fit_errors: list[float] = []
    diam_map_values: list[float] = []
    diam_px_values: list[float] = []
    radius_px_values: list[float] = []
    prev_geom: tuple[float, float, float] | None = None
    ema_geom: tuple[float, float, float] | None = None
    prev_source: str | None = None
    prev_raw_arc: tuple[float, float, float] | None = None
    arc_streak = 0
    bbox_streak = 0
    bbox_sources = {"largest_bbox", "diagonal_bbox"}
    arc_annulus: list[float] = []
    arc_coverage: list[float] = []
    arc_residual_p95: list[float] = []
    for frame_idx in range(start_frame, end_frame + 1, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        geom, conf = rd.detect_ring_geometry_in_frame(
            frame,
            zones_payload_full,
            expected_center=expected_center,
            expected_radius=expected_radius,
            min_radius_map_units=min_radius_map_units,
            countdown_zone_mode=bool(countdown_zone_mode),
            strict_line_profile=bool(strict_line_profile),
            arc_only_mode=bool(arc_only_mode),
        )
        if geom is None:
            continue
        gx = float(geom["x"])
        gy = float(geom["y"])
        gr = float(geom["radius"])
        weight = float(max(1e-3, conf))
        source = str(geom.get("geometry_source", "unknown") or "unknown")
        fit_err = float(geom.get("fit_error", 0.0) or 0.0)
        diam_map = float(geom.get("diameter_map_units", 0.0) or 0.0)
        diam_px = float(geom.get("diameter_px", 0.0) or 0.0)
        radius_px = float(geom.get("radius_px", 0.0) or 0.0)
        arc_ann = float(geom.get("arc_annulus_score", 0.0) or 0.0)
        arc_cov = float(geom.get("arc_coverage", 0.0) or 0.0)
        arc_p95 = float(geom.get("arc_residual_p95", 0.0) or 0.0)

        if bool(arc_only_mode):
            if source != "arc_boundary":
                continue
            if prev_raw_arc is not None:
                dx_arc = float(np.hypot(gx - prev_raw_arc[0], gy - prev_raw_arc[1]))
                dr_arc = abs(gr - prev_raw_arc[2])
                lim_xy_arc = max(22.0, 0.18 * max(prev_raw_arc[2], gr))
                lim_r_arc = max(14.0, 0.14 * max(prev_raw_arc[2], gr))
                if dx_arc > lim_xy_arc or dr_arc > lim_r_arc:
                    arc_streak = 1
                    prev_raw_arc = (gx, gy, gr)
                    if weight < 0.85:
                        continue
                    weight *= 0.6
                else:
                    arc_streak += 1
            else:
                arc_streak = 1
            prev_raw_arc = (gx, gy, gr)
            if arc_cov < 55.0 or arc_ann < 0.03:
                continue
            if arc_streak < 2:
                continue

        if source in bbox_sources and prev_source is not None and prev_source not in bbox_sources:
            bbox_streak += 1
            if prev_geom is not None:
                dx_bbox = float(np.hypot(gx - prev_geom[0], gy - prev_geom[1]))
                dr_bbox = abs(gr - prev_geom[2])
                tight_xy = max(24.0, 0.22 * max(prev_geom[2], gr))
                tight_r = max(14.0, 0.16 * max(prev_geom[2], gr))
                if dx_bbox > tight_xy or dr_bbox > tight_r:
                    continue
            if bbox_streak < 3:
                # Hysteresis: do not switch to bbox source on single-frame spikes.
                continue
            weight *= 0.5
        else:
            bbox_streak = 0

        if prev_geom is not None:
            dx = float(np.hypot(gx - prev_geom[0], gy - prev_geom[1]))
            dr = abs(gr - prev_geom[2])
            jump_limit_xy = max(50.0, 0.35 * max(prev_geom[2], gr))
            jump_limit_r = max(30.0, 0.30 * max(prev_geom[2], gr))
            if dx > jump_limit_xy or dr > jump_limit_r:
                # Temporal gate for jittery outliers.
                if weight < 0.6:
                    continue
                weight *= 0.5

        if ema_geom is None:
            ema_geom = (gx, gy, gr)
        else:
            alpha = float(np.clip(0.15 + (weight * 0.5), 0.15, 0.75))
            ema_geom = (
                (alpha * gx) + ((1.0 - alpha) * ema_geom[0]),
                (alpha * gy) + ((1.0 - alpha) * ema_geom[1]),
                (alpha * gr) + ((1.0 - alpha) * ema_geom[2]),
            )
        prev_geom = ema_geom
        prev_source = source
        xs.append(float(ema_geom[0]))
        ys.append(float(ema_geom[1]))
        rs.append(float(ema_geom[2]))
        ws.append(float(weight))
        source_hist[source] = float(source_hist.get(source, 0.0) + weight)
        fit_errors.append(float(fit_err))
        if diam_map > 0:
            diam_map_values.append(float(diam_map))
        if diam_px > 0:
            diam_px_values.append(float(diam_px))
        if radius_px > 0:
            radius_px_values.append(float(radius_px))
        if source == "arc_boundary":
            arc_annulus.append(float(arc_ann))
            arc_coverage.append(float(arc_cov))
            arc_residual_p95.append(float(arc_p95))
    if not xs or not ys or not rs:
        return None, None, None
    def weighted_median(values: list[float], weights: list[float]) -> float:
        order = np.argsort(np.asarray(values, dtype=np.float64))
        vals = np.asarray(values, dtype=np.float64)[order]
        wts = np.asarray(weights, dtype=np.float64)[order]
        acc = np.cumsum(wts)
        cut = 0.5 * float(np.sum(wts))
        idx = int(np.searchsorted(acc, cut, side="left"))
        idx = max(0, min(idx, len(vals) - 1))
        return float(vals[idx])
    center_json = json.dumps(
        {
            "x": round(weighted_median(xs, ws), 2),
            "y": round(weighted_median(ys, ws), 2),
            "space": "map",
        },
        ensure_ascii=False,
    )
    radius = round(weighted_median(rs, ws), 2)
    quality = {
        "samples": int(len(rs)),
        "confidence": round(float(np.mean(ws)), 4),
        "geometry_source": max(source_hist.items(), key=lambda p: p[1])[0] if source_hist else "unknown",
        "fit_error": round(float(np.median(np.asarray(fit_errors, dtype=np.float64))) if fit_errors else 0.0, 3),
    }
    if diam_map_values:
        quality["diameter_map_units"] = round(float(np.median(np.asarray(diam_map_values, dtype=np.float64))), 3)
    if diam_px_values:
        quality["diameter_px"] = round(float(np.median(np.asarray(diam_px_values, dtype=np.float64))), 3)
    if radius_px_values:
        quality["radius_px"] = round(float(np.median(np.asarray(radius_px_values, dtype=np.float64))), 3)
    if arc_annulus:
        quality["arc_annulus_score"] = round(float(np.median(np.asarray(arc_annulus, dtype=np.float64))), 4)
    if arc_coverage:
        quality["arc_coverage"] = round(float(np.median(np.asarray(arc_coverage, dtype=np.float64))), 3)
    if arc_residual_p95:
        quality["arc_residual_p95"] = round(float(np.median(np.asarray(arc_residual_p95, dtype=np.float64))), 3)
    return center_json, radius, quality


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


def estimate_ring_phase_timestamp(
    *,
    anchor_event_type: str,
    anchor_ring_number: int,
    anchor_timestamp_sec: float,
    target_event_type: str,
    target_ring_number: int,
) -> float | None:
    anchor_idx = RING_PHASE_INDEX.get((str(anchor_event_type), int(anchor_ring_number)))
    target_idx = RING_PHASE_INDEX.get((str(target_event_type), int(target_ring_number)))
    if anchor_idx is None or target_idx is None:
        return None
    ts = float(anchor_timestamp_sec)
    if target_idx == anchor_idx:
        return ts
    if target_idx > anchor_idx:
        for idx in range(anchor_idx, target_idx):
            ts += float(RING_PHASE_SEQUENCE[idx][2])
        return ts
    for idx in range(target_idx, anchor_idx):
        ts -= float(RING_PHASE_SEQUENCE[idx][2])
    return ts


def estimate_countdown_timestamp_from_anchor(
    anchor_countdown_ring: int,
    anchor_timestamp_sec: float,
    target_countdown_ring: int,
) -> float | None:
    return estimate_ring_phase_timestamp(
        anchor_event_type="countdown",
        anchor_ring_number=int(anchor_countdown_ring),
        anchor_timestamp_sec=float(anchor_timestamp_sec),
        target_event_type="countdown",
        target_ring_number=int(target_countdown_ring),
    )


def infer_camreman_rows_from_rings(rings_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev_center: tuple[float, float] | None = None
    prev_radius: float | None = None
    for row in rings_rows:
        center = parse_center_json(str(row.get("center")) if row.get("center") is not None else None)
        if center is None:
            continue
        try:
            radius = float(row.get("radius", 0.0) or 0.0)
        except Exception:
            radius = 0.0
        if radius <= 0.0:
            continue
        if prev_center is not None and prev_radius is not None and prev_radius > 0.0:
            dxy = float(np.hypot(center[0] - prev_center[0], center[1] - prev_center[1]))
            dz = abs((2.0 * radius) - (2.0 * prev_radius)) / max(1e-6, (2.0 * prev_radius))
            if dz >= 0.01 or dxy >= max(1.0, radius * 0.015):
                ts_raw = row.get("time_start", 0.0)
                try:
                    ts = float(ts_raw or 0.0)
                except Exception:
                    ts = 0.0
                out.append(
                    {
                        "timestamp_sec": round(float(ts), 3),
                        "x": round(float(center[0]), 3),
                        "y": round(float(center[1]), 3),
                        "camera_size": round(float(np.clip(1080.0 / max(1e-6, radius / max(1.0, prev_radius)), 120.0, 1080.0)), 3),
                    }
                )
        prev_center = center
        prev_radius = float(radius)
    return out


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
    # Build 10 virtual points over last <=2s to stabilize vector from recent red-zone trend.
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


def ring_radius_meters(ring_number: int) -> float | None:
    map_mp_id = str(getattr(ring_radius_meters, "_map_mp_id", "") or "")
    if map_mp_id:
        per_map = MAP_RING_RADIUS_METERS_OVERRIDE.get(map_mp_id)
        if isinstance(per_map, dict) and int(ring_number) in per_map:
            return float(per_map[int(ring_number)])
    if ring_number in RING_DIAMETERS_METERS:
        return float(RING_DIAMETERS_METERS[ring_number]) * 0.5
    if ring_number > 0:
        # Do not extrapolate beyond known ring model: use last known tiny ring.
        return float(RING_DIAMETERS_METERS[max(RING_DIAMETERS_METERS.keys())]) * 0.5
    return None


def initial_meters_to_map_units(map_mp_id: str | None) -> float:
    # Optional manual calibration from interactive visualizer.
    manual_vals: list[float] = []
    calib_file = PROJECT_ROOT / "output" / "map_start_roi" / "manual_ring_calibration.jsonl"
    try:
        if calib_file.exists():
            lines = calib_file.read_text(encoding="utf-8").splitlines()
            if lines:
                payload = json.loads(lines[-1])
                if isinstance(payload, dict):
                    c1 = payload.get("c1")
                    c2 = payload.get("c2")
                    if isinstance(c1, dict):
                        rm = ring_radius_meters(1)
                        rv = float(c1.get("r", 0.0) or 0.0)
                        if rm is not None and rm > 0 and rv > 0:
                            manual_vals.append(rv / float(rm))
                    if isinstance(c2, dict):
                        rm = ring_radius_meters(2)
                        rv = float(c2.get("r", 0.0) or 0.0)
                        if rm is not None and rm > 0 and rv > 0:
                            manual_vals.append(rv / float(rm))
    except Exception:
        manual_vals = []

    if map_mp_id:
        overrides = MAP_RING_RADIUS_MAP_OVERRIDE.get(str(map_mp_id))
        if isinstance(overrides, dict):
            vals: list[float] = []
            for rn, r_map in overrides.items():
                rm = ring_radius_meters(int(rn))
                if rm is None or float(rm) <= 0:
                    continue
                vals.append(float(r_map) / float(rm))
            vals.extend(manual_vals)
            if vals:
                return float(np.median(np.asarray(vals, dtype=np.float64)))
    if manual_vals:
        return float(np.median(np.asarray(manual_vals, dtype=np.float64)))
    return float(DEFAULT_METERS_TO_MAP_UNITS)


def default_calibration_radii(map_mp_id: str | None) -> tuple[float, float]:
    if map_mp_id:
        overrides = MAP_RING_RADIUS_MAP_OVERRIDE.get(str(map_mp_id))
        if isinstance(overrides, dict):
            r1 = float(overrides.get(1, 0.0) or 0.0)
            r2 = float(overrides.get(2, 0.0) or 0.0)
            if r1 > 0 and r2 > 0:
                return r1, r2
    r1 = expected_ring_radius_map_units(1, DEFAULT_METERS_TO_MAP_UNITS) or 200.0
    r2 = expected_ring_radius_map_units(2, DEFAULT_METERS_TO_MAP_UNITS) or 120.0
    return float(r1), float(r2)


def apply_calib_ratio_lock(calib_state: dict[str, Any], active_key: str) -> dict[str, Any]:
    if active_key not in {"c1", "c2"}:
        return calib_state
    c1 = dict(calib_state.get("c1", {"x": 540.0, "y": 540.0, "r": 200.0}))
    c2 = dict(calib_state.get("c2", {"x": 540.0, "y": 540.0, "r": 100.0}))
    if active_key == "c1":
        r1 = float(max(1.0, c1.get("r", 200.0)))
        c2["r"] = float(max(1.0, r1 * 0.5))
    else:
        r2 = float(max(1.0, c2.get("r", 100.0)))
        c1["r"] = float(max(1.0, r2 * 2.0))
    calib_state["c1"] = c1
    calib_state["c2"] = c2
    return calib_state


def expected_ring_radius_map_units(ring_number: int, meters_to_map_units: float) -> float | None:
    # Prefer explicit map calibration anchors when available.
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
    # Minimum size scales relative to each ring's canonical diameter.
    # Base: ring1 minimum diameter = RING_MIN_DIAMETER_BASE_METERS.
    base_d = float(RING_DIAMETERS_METERS.get(1, 1100.0))
    ring_d = float(RING_DIAMETERS_METERS.get(int(ring_number), RING_DIAMETERS_METERS[max(RING_DIAMETERS_METERS.keys())]))
    if base_d <= 0.0 or ring_d <= 0.0:
        return None
    ratio = float(RING_MIN_DIAMETER_BASE_METERS) / base_d
    min_d_meters = ring_d * ratio
    min_r_meters = 0.5 * min_d_meters
    return float(min_r_meters) * max(1e-6, float(meters_to_map_units))


def is_ring_radius_plausible(radius: float | None, expected_radius: float | None) -> bool:
    if radius is None or expected_radius is None:
        return True
    rad = float(radius)
    exp = float(expected_radius)
    tol = max(float(RING_RADIUS_TOLERANCE_ABS), abs(exp) * float(RING_RADIUS_TOLERANCE_RATIO))
    return abs(rad - exp) <= tol


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
    return (center_dist + float(radius)) <= (float(prev_radius) + 6.0)


def is_team_eliminated_at_frame(
    frame: np.ndarray,
    frame_idx: int,
    fps: float,
    payload_elim: dict[str, Any] | None,
    text_ocr_min_confidence: float,
) -> bool:
    if payload_elim is None:
        return False
    observations = run_zone_ocr(
        frame=frame,
        frame_idx=frame_idx,
        fps=fps,
        zones_payload=payload_elim,
        min_confidence=max(0.0, float(text_ocr_min_confidence)),
    )
    for item in observations:
        if is_eliminated_text(str(item.get("normalized_text") or "")):
            return True
    return False


def find_team_elimination_time(
    cap: cv2.VideoCapture,
    fps: float,
    start_frame: int,
    max_frame: int,
    payload_elim: dict[str, Any] | None,
    text_ocr_min_confidence: float,
    coarse_step_frames: int,
    refine_window_frames: int,
    refine_step_frames: int,
    progress: LiveProgress | None = None,
    stage_label: str = "elim_refine",
) -> tuple[float | None, int]:
    if payload_elim is None or not cap.isOpened():
        return None, 0
    checks = 0
    coarse_step = max(1, int(coarse_step_frames))
    refine_window = max(1, int(refine_window_frames))
    refine_step = max(1, int(refine_step_frames))
    first_hit: int | None = None
    frame_idx = max(0, int(start_frame))
    while frame_idx <= max_frame:
        if progress is not None:
            progress.update(frame_idx)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        checks += 1
        if is_team_eliminated_at_frame(
            frame=frame,
            frame_idx=frame_idx,
            fps=fps,
            payload_elim=payload_elim,
            text_ocr_min_confidence=text_ocr_min_confidence,
        ):
            first_hit = frame_idx
            break
        frame_idx += coarse_step
    if first_hit is None:
        return None, checks

    if progress is not None:
        progress.set_stage(stage_label)
    refine_from = max(int(start_frame), int(first_hit) - refine_window)
    refined_hit = int(first_hit)
    for frame_idx in range(refine_from, int(first_hit) + 1, refine_step):
        if progress is not None:
            progress.update(frame_idx)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        checks += 1
        if is_team_eliminated_at_frame(
            frame=frame,
            frame_idx=frame_idx,
            fps=fps,
            payload_elim=payload_elim,
            text_ocr_min_confidence=text_ocr_min_confidence,
        ):
            refined_hit = int(frame_idx)
            break
    ts = refined_hit / fps if fps > 0 else 0.0
    return float(ts), checks


def ring_minimap_bounds(frame: np.ndarray) -> tuple[int, int, int, int]:
    h, w = frame.shape[:2]
    sx = float(w) / 1920.0 if w > 0 else 1.0
    sy = float(h) / 1080.0 if h > 0 else 1.0
    x1 = int(round(MAP_ROI_X * sx))
    y1 = int(round(MAP_ROI_Y * sy))
    x2 = int(round((MAP_ROI_X + MAP_ROI_WIDTH) * sx))
    y2 = int(round((MAP_ROI_Y + MAP_ROI_HEIGHT) * sy))
    x1 = max(0, min(w - 1, x1))
    y1 = max(0, min(h - 1, y1))
    x2 = max(x1 + 1, min(w, x2))
    y2 = max(y1 + 1, min(h, y2))
    return x1, y1, x2, y2


def _visualization_mouse_callback(event: int, x: int, y: int, flags: int, param: Any) -> None:
    if not isinstance(param, dict):
        return
    width = max(1, int(param.get("width", 1080)))
    height = max(1, int(param.get("height", 1080)))
    mx = float(np.clip((float(x) / float(width)) * 1080.0, 0.0, 1079.0))
    my = float(np.clip((float(y) / float(height)) * 1080.0, 0.0, 1079.0))
    calib_state = getattr(render_visualization, "_calib_state", None)
    if not isinstance(calib_state, dict):
        return
    drag_state = getattr(render_visualization, "_drag_state", {"dragging": False, "key": "c1"})
    ratio_lock = bool(getattr(render_visualization, "_calib_ratio_enabled", False))
    if not isinstance(drag_state, dict):
        drag_state = {"dragging": False, "key": "c1"}

    def _pick_active_key(px: float, py: float) -> str:
        c1 = calib_state.get("c1", {"x": 540.0, "y": 540.0})
        c2 = calib_state.get("c2", {"x": 540.0, "y": 540.0})
        d1 = float(np.hypot(px - float(c1.get("x", 540.0)), py - float(c1.get("y", 540.0))))
        d2 = float(np.hypot(px - float(c2.get("x", 540.0)), py - float(c2.get("y", 540.0))))
        return "c1" if d1 <= d2 else "c2"

    if event == cv2.EVENT_LBUTTONDOWN:
        key = _pick_active_key(mx, my)
        calib_state["active"] = 1 if key == "c1" else 2
        active = dict(calib_state.get(key, {"x": 540.0, "y": 540.0, "r": 120.0}))
        active["x"] = mx
        active["y"] = my
        calib_state[key] = active
        drag_state["dragging"] = True
        drag_state["key"] = key
        setattr(render_visualization, "_ui_dirty", True)
    elif event == cv2.EVENT_MOUSEMOVE and bool(drag_state.get("dragging", False)):
        key = str(drag_state.get("key", "c1"))
        active = dict(calib_state.get(key, {"x": 540.0, "y": 540.0, "r": 120.0}))
        active["x"] = mx
        active["y"] = my
        calib_state[key] = active
        setattr(render_visualization, "_ui_dirty", True)
    elif event == cv2.EVENT_LBUTTONUP:
        drag_state["dragging"] = False
        setattr(render_visualization, "_ui_dirty", True)
    elif event == cv2.EVENT_MOUSEWHEEL:
        key = str(drag_state.get("key") or ("c1" if int(calib_state.get("active", 1)) == 1 else "c2"))
        active = dict(calib_state.get(key, {"x": 540.0, "y": 540.0, "r": 120.0}))
        delta = 4.0 if int(flags) > 0 else -4.0
        active["r"] = float(np.clip(float(active.get("r", 120.0)) + delta, 1.0, 1079.0))
        calib_state[key] = active
        if ratio_lock:
            calib_state = apply_calib_ratio_lock(calib_state, key)
        setattr(render_visualization, "_ui_dirty", True)

    setattr(render_visualization, "_calib_state", calib_state)
    setattr(render_visualization, "_drag_state", drag_state)


def _fit_circle_from_contour(cnt: np.ndarray) -> tuple[float, float, float] | None:
    if cnt is None or len(cnt) < 5:
        return None
    pts = cnt.reshape(-1, 2).astype(np.float32)
    cx = float(np.mean(pts[:, 0]))
    cy = float(np.mean(pts[:, 1]))
    dists = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
    if dists.size == 0:
        return None
    # Median distance follows the visual "wall" better than enclosing circle.
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
    A = np.column_stack((x, y, np.ones_like(x)))
    b = -(x * x + y * y)
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None
    a, c, d = float(sol[0]), float(sol[1]), float(sol[2])
    cx = -0.5 * a
    cy = -0.5 * c
    r_sq = (cx * cx) + (cy * cy) - d
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
    ux = (
        ((x1 * x1 + y1 * y1) * (y2 - y3))
        + ((x2 * x2 + y2 * y2) * (y3 - y1))
        + ((x3 * x3 + y3 * y3) * (y1 - y2))
    ) / d
    uy = (
        ((x1 * x1 + y1 * y1) * (x3 - x2))
        + ((x2 * x2 + y2 * y2) * (x1 - x3))
        + ((x3 * x3 + y3 * y3) * (x2 - x1))
    ) / d
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

    contour_candidates = sorted(
        (cnt for cnt in contours if cnt is not None and len(cnt) >= 10),
        key=cv2.contourArea,
        reverse=True,
    )[:14]

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
        # Countdown masks are often fragmented by HUD/text overlays.
        # Build a connected mask variant to recover the dominant red-zone area.
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
    # Ring geometry is drawn on the minimap, so detect only inside minimap ROI.
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
        # Alternative hypothesis for countdown mode:
        # red-zone mask often highlights "outside", so safe zone is largest
        # non-red connected component that does not touch the minimap border.
        safe_mask = cv2.bitwise_and(cv2.bitwise_not(mask_connected), map_circle_mask)
        safe_mask = cv2.morphologyEx(
            safe_mask,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        )
        safe_mask = cv2.morphologyEx(
            safe_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
        )
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
            # Skip components touching minimap square border; ring safe area is
            # expected to be an internal island.
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
        # Additional requested mode: pick two longest lines with sufficient
        # angular separation and construct circle from their endpoints.
        edges = cv2.Canny(contour_mask, 60, 160)
        min_line_len = max(30, int(min(roi.shape[0], roi.shape[1]) * (0.25 if bool(strict_line_profile) else 0.18)))
        if expected_radius is not None and np.isfinite(float(expected_radius)):
            expected_radius_px = (float(expected_radius) / 1080.0) * float(roi.shape[1])
            dyn_factor = 0.62 if bool(strict_line_profile) else 0.48
            min_line_len = max(min_line_len, int(max(24.0, expected_radius_px * dyn_factor)))
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180.0,
            threshold=80,
            minLineLength=min_line_len,
            maxLineGap=20,
        )
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
                points = np.asarray(
                    [
                        [l1[0], l1[1]],
                        [l1[2], l1[3]],
                        [l2[0], l2[1]],
                        [l2[2], l2[3]],
                    ],
                    dtype=np.float64,
                )
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
        # Radial fallback from safe-zone interior boundary.
        line_pair_candidates = [c for c in candidates if str(c.get("source", "")) == "line_pair"]
        line_pair_best_err = min([float(c.get("fit_error", 9999.0) or 9999.0) for c in line_pair_candidates], default=9999.0)
        should_try_radial = (not line_pair_candidates) or (line_pair_best_err > (12.0 if bool(strict_line_profile) else 24.0))
        if should_try_radial:
            seed_points: list[tuple[float, float]] = []
            if candidates:
                best_seed = max(candidates, key=lambda c: float(c.get("area_ratio", 0.0)))
                seed_points.append((float(best_seed.get("cx", roi.shape[1] * 0.5)), float(best_seed.get("cy", roi.shape[0] * 0.5))))
            if expected_center is not None:
                seed_points.append(
                    (
                        (float(expected_center[0]) / 1080.0) * float(roi.shape[1]),
                        (float(expected_center[1]) / 1080.0) * float(roi.shape[0]),
                    )
                )
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
                radial_fit = _radial_boundary_circle(
                    safe_mask,
                    cx_seed=seed_x,
                    cy_seed=seed_y,
                )
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
        # In strict profile, bbox candidates are fallback-only.
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
            # In strict profile bbox can only help as fallback.
            score += -220.0 if bool(strict_line_profile) else 80.0
        if bool(countdown_zone_mode) and source == "diagonal_bbox":
            score += -180.0 if bool(strict_line_profile) else 140.0
        if bool(countdown_zone_mode) and source == "safe_component":
            # Most robust option when red zone heavily floods minimap.
            score += 70.0 if bool(strict_line_profile) else 120.0
        if bool(countdown_zone_mode) and source == "line_pair":
            # Two longest lines with >=30 degree angle is user-priority geometry.
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


def collect_enrichment_data(
    video_path: Path,
    fps: float,
    total_frames: int,
    frame_step: int,
    start_ts: float | None,
    map_mp_id: str | None,
    video_duration_sec: float,
    zones_payload_full: dict[str, Any] | None,
    text_ocr_min_confidence: float,
    elim_coarse_sec: float,
    elim_refine_sec: float,
    elim_refine_step_sec: float,
    ring_coarse_sec: float,
    ring_rollback_sec: float,
    ring_refine_window_sec: float,
    ring_refine_step_sec: float,
    ring_stable_seconds: float,
    ring_geometry_window_seconds: float,
    ring_geometry_step_sec: float,
    ring_countdown_zone_mode: bool = False,
    ring_strict_line_profile: bool = False,
    ring_arc_only_mode: bool = False,
    disable_ring_detection: bool = False,
    disable_team_detection: bool = False,
    disable_elimination_detection: bool = False,
    team_workers: int = 1,
    visualize: bool = False,
    visualize_no_ocr: bool = False,
    ocr_min_conf: float = 0.62,
    camera_min_conf: float = 0.58,
    progress: LiveProgress | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if start_ts is None or zones_payload_full is None or pytesseract is None:
        return [], [], [], {"snapshot_observations": 0, "timeline_observations": 0}

    start_frame = max(0, int(float(start_ts) * fps))
    max_frame = max(0, total_frames - 1) if total_frames > 0 else start_frame
    # Team names are most stable near match start HUD, not in final seconds.
    snapshot_from = start_frame
    snapshot_to = min(max_frame, start_frame + max(600, int(20.0 * fps)))
    snapshot_step = max(1, int(max(frame_step, fps * 0.5)))
    elim_coarse_frames = max(1, int(max(0.5, float(elim_coarse_sec)) * fps))
    elim_refine_frames = max(1, int(max(0.5, float(elim_refine_sec)) * fps))
    elim_refine_step_frames = max(1, int(max(0.25, float(elim_refine_step_sec)) * fps))
    ring_coarse_frames = max(1, int(max(0.5, float(ring_coarse_sec)) * fps))
    ring_rollback_frames = max(1, int(max(0.5, float(ring_rollback_sec)) * fps))
    ring_refine_window_frames = max(1, int(max(0.5, float(ring_refine_window_sec)) * fps))
    ring_refine_step_frames = max(1, int(max(0.25, float(ring_refine_step_sec)) * fps))
    ring_geometry_step_frames = max(1, int(max(0.25, float(ring_geometry_step_sec)) * fps))

    detect_teams = not bool(disable_team_detection)
    detect_elims = detect_teams and (not bool(disable_elimination_detection))
    name_labels = {f"t{i}_name" for i in range(1, 21)} if detect_teams else set()
    elim_labels = {f"t{i}_iseliminated" for i in range(1, 21)} if detect_elims else set()
    detect_rings = not bool(disable_ring_detection)
    ring_labels = {"is_ringclosing"} if detect_rings else set()
    payload_names = clone_payload_with_selected_zones(zones_payload_full, name_labels | elim_labels) if (name_labels or elim_labels) else None
    payload_ring = clone_payload_with_selected_zones(zones_payload_full, ring_labels) if ring_labels else None
    # region agent log
    _debug_log_63c8ec(
        "pre-fix",
        "H3-H4-H5",
        "detect_map_start.py:collect_enrichment_data:init",
        "team/ring enrichment inputs",
        {
            "video": str(video_path.name),
            "fps": float(fps),
            "total_frames": int(total_frames),
            "start_frame": int(start_frame),
            "max_frame": int(max_frame),
            "snapshot_from": int(snapshot_from),
            "snapshot_to": int(snapshot_to),
            "snapshot_step": int(snapshot_step),
            "detect_teams": bool(detect_teams),
            "detect_elims": bool(detect_elims),
            "detect_rings": bool(detect_rings),
            "team_workers": int(team_workers),
            "disable_team_detection": bool(disable_team_detection),
            "disable_elimination_detection": bool(disable_elimination_detection),
            "disable_ring_detection": bool(disable_ring_detection),
        },
    )
    # endregion

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [], [], [], {"snapshot_observations": 0, "timeline_observations": 0}
    ref_cache: dict[str, np.ndarray | None] = {}
    viz_prev_gray: np.ndarray | None = None
    viz_step_frames = max(1, int(max(1.0, fps * 0.5)))

    def maybe_visualize(frame_idx: int) -> None:
        nonlocal viz_prev_gray
        if not bool(visualize):
            return
        if int(frame_idx) % int(viz_step_frames) != 0:
            return
        _signal, viz_prev_gray, _trace, _obs, _action = evaluate_frame_signal(
            cap=cap,
            frame_idx=int(frame_idx),
            fps=fps,
            prev_center_gray=viz_prev_gray,
            ocr_min_conf=ocr_min_conf,
            camera_min_conf=camera_min_conf,
            ref_cache=ref_cache,
            debug_dir=None,
            debug_video_stem=None,
            debug_save_every=0,
            zones_payload=zones_payload_full,
            text_ocr_min_confidence=text_ocr_min_confidence,
            visualize=True,
            visualize_no_ocr=True if bool(visualize) else bool(visualize_no_ocr),
            ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
            ring_strict_line_profile=bool(ring_strict_line_profile),
            ring_arc_only_mode=bool(ring_arc_only_mode),
        )

    snapshot_obs: list[dict[str, Any]] = []
    if payload_names is not None:
        if progress is not None:
            progress.set_stage("ocr_snapshot")
        for frame_idx in range(snapshot_from, snapshot_to + 1, snapshot_step):
            if progress is not None:
                progress.update(frame_idx)
            maybe_visualize(int(frame_idx))
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            try:
                snapshot_obs.extend(
                    run_zone_ocr(
                        frame=frame,
                        frame_idx=int(frame_idx),
                        fps=fps,
                        zones_payload=payload_names,
                        min_confidence=max(0.0, float(text_ocr_min_confidence)),
                    )
                )
            except Exception as exc:
                emit_error_progress("ocr_snapshot", f"team snapshot OCR failed: {exc}", extra=traceback.format_exc(limit=4))

    team_state: dict[int, dict[str, Any]] = {
        i: {"team_name": None, "is_eliminated": False, "time_eliminated": None}
        for i in range(1, 21)
    }
    if detect_teams:
        for slot in range(1, 21):
            emit_team_progress(slot, "pending", 0.0, extra="waiting_for_snapshot")
    if detect_teams:
        for item in sorted(snapshot_obs, key=lambda x: float(x.get("ocr_confidence", 0.0) or 0.0), reverse=True):
            slot, field = extract_team_slot(str(item.get("zone_label") or ""))
            if slot is None or field is None or slot not in team_state:
                continue
            text = str(item.get("normalized_text") or "").strip()
            ts = float(item.get("timestamp_sec", 0.0) or 0.0)
            if field == "name":
                if team_state[slot]["team_name"] is None and text:
                    team_state[slot]["team_name"] = text
            elif field == "iseliminated":
                if is_eliminated_text(text):
                    team_state[slot]["is_eliminated"] = True
                    team_state[slot]["time_eliminated"] = round(ts, 3)
        # region agent log
        _debug_log_63c8ec(
            "pre-fix",
            "H3-H4",
            "detect_map_start.py:collect_enrichment_data:snapshot",
            "team snapshot OCR result",
            {
                "snapshot_obs": int(len(snapshot_obs)),
                "names_found": [slot for slot in range(1, 21) if team_state[slot]["team_name"] is not None],
                "elim_markers_found": [slot for slot in range(1, 21) if bool(team_state[slot]["is_eliminated"])],
                "team_names": {str(slot): team_state[slot]["team_name"] for slot in range(1, 21) if team_state[slot]["team_name"] is not None},
            },
        )
        # endregion
        for slot in range(1, 21):
            info = team_state[slot]
            if info["team_name"] is None:
                emit_team_progress(slot, "error", 100.0, extra="team_name_not_found_in_snapshot")
            elif bool(info["is_eliminated"]):
                emit_team_progress(slot, "pending", 35.0, extra=f"snapshot_found:{info['team_name']}; eliminated_marker_found")
            else:
                emit_team_progress(slot, "completed", 100.0, extra=f"snapshot_found:{info['team_name']}; no_elimination_marker")

    timeline_checks = 0
    if progress is not None and detect_elims:
        progress.set_stage("elim_search")
    def refine_team_slot(slot: int) -> tuple[int, float | None, int]:
        emit_team_progress(slot, "running", 5.0, extra="elim_refine_start")
        local_cap = cap if int(team_workers) <= 1 else cv2.VideoCapture(str(video_path))
        try:
            info = team_state[slot]
            if not bool(info["is_eliminated"]):
                return slot, None, 0
            maybe_visualize(int(start_frame + (slot * max(1, int(fps)))))
            payload_slot = clone_payload_with_selected_zones(zones_payload_full, {f"t{slot}_iseliminated"})
            ts_found, checks = find_team_elimination_time(
                cap=local_cap,
                fps=fps,
                start_frame=start_frame,
                max_frame=max_frame,
                payload_elim=payload_slot,
                text_ocr_min_confidence=text_ocr_min_confidence,
                coarse_step_frames=elim_coarse_frames,
                refine_window_frames=elim_refine_frames,
                refine_step_frames=elim_refine_step_frames,
                progress=progress,
                stage_label=f"elim{slot}_refine",
            )
            emit_team_progress(slot, "completed", 100.0, extra="elim_refine_done")
            return slot, ts_found, checks
        except Exception as exc:
            message = f"TEAM_{slot} elimination refine failed: {exc}"
            emit_team_progress(slot, "error", 100.0, extra=message)
            emit_error_progress(f"elim{slot}_refine", message, extra=traceback.format_exc(limit=4))
            return slot, None, 0
        finally:
            if local_cap is not cap:
                local_cap.release()

    if detect_elims:
        slots_to_refine = [slot for slot in range(1, 21) if bool(team_state[slot]["is_eliminated"])]
        worker_count = max(1, min(int(team_workers), len(slots_to_refine) or 1))
        # region agent log
        _debug_log_63c8ec(
            "pre-fix",
            "H3-H4",
            "detect_map_start.py:collect_enrichment_data:refine_plan",
            "team elimination refinement plan",
            {
                "slots_to_refine": slots_to_refine,
                "worker_count": int(worker_count),
                "will_scan_full_video_for_missing_elims": False,
                "reason": "current code refines only slots with snapshot elimination marker",
            },
        )
        # endregion
        if worker_count <= 1:
            results = [refine_team_slot(slot) for slot in slots_to_refine]
        else:
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                    results = list(executor.map(refine_team_slot, slots_to_refine))
            except Exception as exc:
                emit_error_progress("elim_search", f"team refinement worker pool failed: {exc}", extra=traceback.format_exc(limit=4))
                results = []
        for slot, ts_found, checks in results:
            timeline_checks += int(checks)
            if ts_found is not None:
                team_state[slot]["time_eliminated"] = round(float(ts_found), 3)

    teams_rows: list[dict[str, Any]] = []
    if detect_teams:
        for slot in range(1, 21):
            info = team_state[slot]
            teams_rows.append(
                {
                    "team_slot": slot,
                    "team_name": info["team_name"],
                    "is_eliminated": bool(info["is_eliminated"]),
                    "time_eliminated": info["time_eliminated"],
                }
            )

    rings_rows: list[dict[str, Any]] = []
    if payload_ring is not None:
        if cap.isOpened():
            cursor_frame = int(start_frame)
            max_ring_number = min(DETECTED_RING_COUNT + 1, max(RING_DIAMETERS_METERS.keys()))
            consecutive_start_misses = 0
            max_consecutive_start_misses = 2
            last_ring_start_sec: float | None = None
            rd.set_map_context(map_mp_id)
            meters_to_map_units = initial_meters_to_map_units(map_mp_id)
            scale_candidates: list[tuple[float, float]] = []
            explicit_start_countdown = find_first_explicit_ring_number(
                cap=cap,
                fps=fps,
                start_frame=start_frame,
                max_frame=max_frame,
                payload_ring=payload_ring,
                text_ocr_min_confidence=text_ocr_min_confidence,
                coarse_step_frames=ring_coarse_frames,
                event_type="countdown",
            )
            countdown_start = int(explicit_start_countdown) if explicit_start_countdown is not None else 2
            countdown_start = max(2, min(countdown_start, max_ring_number))
            # region agent log
            _debug_log_63c8ec(
                "pre-fix",
                "H1-H2",
                "detect_map_start.py:collect_enrichment_data:ring_setup",
                "ring detection loop setup",
                {
                    "detected_ring_count_constant": int(DETECTED_RING_COUNT),
                    "max_ring_number": int(max_ring_number),
                    "explicit_start_countdown": None if explicit_start_countdown is None else int(explicit_start_countdown),
                    "countdown_start": int(countdown_start),
                    "loop_countdown_numbers": list(range(int(countdown_start), int(max_ring_number) + 1)),
                },
            )
            # endregion
            start_ring_detected = max(1, countdown_start - 1)
            anchor_countdown_ring: int | None = None
            anchor_countdown_ts: float | None = None
            for countdown_ring_number in range(int(countdown_start), max_ring_number + 1):
                ring_number = int(countdown_ring_number) - 1
                if ring_number <= 0:
                    continue
                # region agent log
                _debug_log_63c8ec(
                    "pre-fix",
                    "H1-H2",
                    "detect_map_start.py:collect_enrichment_data:ring_loop",
                    "ring countdown iteration start",
                    {
                        "countdown_ring_number": int(countdown_ring_number),
                        "ring_number_to_persist": int(ring_number),
                        "cursor_frame": int(cursor_frame),
                        "is_last_detected_ring": bool(int(ring_number) >= int(DETECTED_RING_COUNT)),
                    },
                )
                # endregion
                maybe_visualize(int(cursor_frame))
                timing_source = "ocr_countdown"
                expected_start_ts: float | None = None
                if anchor_countdown_ring is not None and anchor_countdown_ts is not None:
                    expected_start_ts = estimate_countdown_timestamp_from_anchor(
                        int(anchor_countdown_ring),
                        float(anchor_countdown_ts),
                        int(countdown_ring_number),
                    )
                ts_start = find_first_stable_ring_event(
                    cap=cap,
                    fps=fps,
                    start_frame=cursor_frame,
                    max_frame=max_frame,
                    payload_ring=payload_ring,
                    text_ocr_min_confidence=text_ocr_min_confidence,
                    target_ring_number=int(countdown_ring_number),
                    target_event_type="countdown",
                    coarse_step_frames=ring_coarse_frames,
                    rollback_step_frames=ring_rollback_frames,
                    refine_window_frames=ring_refine_window_frames,
                    refine_step_frames=ring_refine_step_frames,
                    stable_target_sec=ring_stable_seconds,
                    require_explicit_number=True,
                    progress=progress,
                )
                if ts_start is None:
                    if expected_start_ts is None:
                        consecutive_start_misses += 1
                        cursor_frame = min(max_frame, cursor_frame + max(ring_coarse_frames, ring_refine_step_frames))
                        if consecutive_start_misses > max_consecutive_start_misses and cursor_frame >= max_frame:
                            break
                        continue
                    ts_start = float(expected_start_ts)
                    timing_source = "timing_extrapolated" if float(ts_start) > float(video_duration_sec) else "timing_fallback"
                consecutive_start_misses = 0
                if anchor_countdown_ring is None:
                    anchor_countdown_ring = int(countdown_ring_number)
                    anchor_countdown_ts = float(ts_start)
                if last_ring_start_sec is not None and (float(ts_start) - float(last_ring_start_sec)) < float(RING_MIN_GAP_SECONDS):
                    cursor_frame = min(max_frame, int((float(ts_start) + float(RING_MIN_GAP_SECONDS) * 0.5) * fps))
                    continue

                is_last_detected_ring = int(ring_number) >= int(DETECTED_RING_COUNT)
                ts_end = None
                # region agent log
                _debug_log_63c8ec(
                    "pre-fix",
                    "H2",
                    "detect_map_start.py:collect_enrichment_data:ring_end_search",
                    "ring end search decision",
                    {
                        "countdown_ring_number": int(countdown_ring_number),
                        "ring_number": int(ring_number),
                        "ts_start": float(ts_start),
                        "is_last_detected_ring": bool(is_last_detected_ring),
                        "will_search_next_countdown": bool(not is_last_detected_ring),
                        "next_countdown_target": int(countdown_ring_number) + 1,
                    },
                )
                # endregion
                if not is_last_detected_ring:
                    ts_end = find_first_stable_ring_event(
                        cap=cap,
                        fps=fps,
                        start_frame=max(cursor_frame, int(ts_start * fps)),
                        max_frame=max_frame,
                        payload_ring=payload_ring,
                        text_ocr_min_confidence=text_ocr_min_confidence,
                        target_ring_number=int(countdown_ring_number) + 1,
                        target_event_type="countdown",
                        coarse_step_frames=ring_coarse_frames,
                        rollback_step_frames=ring_rollback_frames,
                        refine_window_frames=ring_refine_window_frames,
                        refine_step_frames=ring_refine_step_frames,
                        stable_target_sec=ring_stable_seconds,
                        require_explicit_number=True,
                        progress=progress,
                    )
                if ts_end is None:
                    expected_next_ts: float | None = None
                    if anchor_countdown_ring is not None and anchor_countdown_ts is not None:
                        expected_next_ts = estimate_countdown_timestamp_from_anchor(
                            int(anchor_countdown_ring),
                            float(anchor_countdown_ts),
                            int(countdown_ring_number) + 1,
                        )
                    if expected_next_ts is not None:
                        ts_end = float(expected_next_ts)
                        timing_source = "timing_extrapolated" if float(ts_end) > float(video_duration_sec) else timing_source
                    else:
                        phase_dur = rd.ring_phase_duration_seconds("closing", int(countdown_ring_number))
                        ts_end = float(ts_start) + float(phase_dur if phase_dur is not None else 120.0)
                if float(ts_end) <= float(ts_start):
                    phase_dur = rd.ring_phase_duration_seconds("closing", int(countdown_ring_number))
                    ts_end = float(ts_start) + float(phase_dur if phase_dur is not None else 120.0)
                stop_after_current_ring = False
                if float(ts_end) > float(video_duration_sec):
                    # Ring extends beyond video duration: keep full phase timing and stop after it.
                    stop_after_current_ring = True
                # For countdown anchor model:
                # ts_start = start of RING N COUNTDOWN = end of closing for ring (N-1).
                # Thus current ring timeline must be reconstructed from closing duration, not countdown duration.
                closing_duration_curr = rd.ring_phase_duration_seconds("closing", int(ring_number))
                ring_time_end = float(ts_start)
                ring_time_start = float(ring_time_end - float(closing_duration_curr if closing_duration_curr is not None else 60.0))
                if ring_time_start < 0.0:
                    ring_time_start = 0.0
                if ring_time_end <= ring_time_start:
                    ring_time_end = float(ring_time_start + float(closing_duration_curr if closing_duration_curr is not None else 60.0))
                if ring_time_start >= float(video_duration_sec):
                    # Current ring starts beyond available video range after countdown->closing conversion.
                    break
                if rings_rows:
                    prev_row = rings_rows[-1]
                    prev_ring_no = int(prev_row.get("ring_number", 1) or 1)
                    prev_start = float(prev_row.get("time_start", 0.0) or 0.0)
                    prev_closing = float(rd.ring_phase_duration_seconds("closing", int(prev_ring_no)) or 0.0)
                    prev_next_countdown = float(rd.ring_phase_duration_seconds("countdown", int(prev_ring_no) + 1) or 0.0)
                    min_start_allowed = float(prev_start + prev_closing + prev_next_countdown)
                    # region agent log
                    _debug_log_b91(
                        "pre-fix-2",
                        "H2",
                        "detect_map_start.py:collect_enrichment_data",
                        "ring_timing_before_guard",
                        {
                            "ring_number": int(ring_number),
                            "countdown_ring_number": int(countdown_ring_number),
                            "ts_start": float(ts_start),
                            "computed_start": float(ring_time_start),
                            "computed_end": float(ring_time_end),
                            "prev_ring_no": int(prev_ring_no),
                            "min_start_allowed": float(min_start_allowed),
                        },
                    )
                    # endregion
                    if ring_time_start < min_start_allowed:
                        shift = float(min_start_allowed - ring_time_start)
                        ring_time_start = float(min_start_allowed)
                        ring_time_end = float(ring_time_end + shift)

                center_json = None
                radius = None
                prev_center: tuple[float, float] | None = None
                prev_radius: float | None = None
                if rings_rows:
                    prev = rings_rows[-1]
                    prev_center = parse_center_json(str(prev.get("center")) if prev.get("center") is not None else None)
                    prev_radius_raw = prev.get("radius")
                    if prev_radius_raw is not None:
                        try:
                            prev_radius = float(prev_radius_raw)
                        except Exception:
                            prev_radius = None
                expected_radius = rd.expected_ring_radius_map_units(
                    ring_number=int(ring_number),
                    meters_to_map_units=meters_to_map_units,
                )
                min_radius_for_ring = rd.min_ring_radius_map_units(
                    ring_number=int(ring_number),
                    meters_to_map_units=meters_to_map_units,
                )
                if bool(ring_countdown_zone_mode):
                    # Countdown N is the primary ringN anchor in countdown mode.
                    anchor_ts = float(ts_start)
                    closing_dur_probe = float(rd.ring_phase_duration_seconds("closing", int(ring_number)) or 60.0)
                    closing_mid_probe = max(0.0, anchor_ts - max(6.0, closing_dur_probe * 0.4))
                    probe_points = [
                        anchor_ts,
                        closing_mid_probe,
                        max(0.0, anchor_ts - 4.0),
                        min(float(video_duration_sec), anchor_ts + 4.0),
                        max(0.0, anchor_ts - 8.0),
                        min(float(video_duration_sec), anchor_ts + 8.0),
                    ]
                else:
                    probe_points = [
                        float(ts_start),
                        max(0.0, float(ts_start) - 5.0),
                        min(float(video_duration_sec), float(ts_start) + 5.0),
                        float(ts_end),
                        (float(ts_start) + float(ts_end)) * 0.5,
                    ]
                retry_profiles: list[tuple[tuple[float, float] | None, float | None]] = [
                    (prev_center, expected_radius),
                    (None, expected_radius),
                    (prev_center, (prev_radius * 0.75) if prev_radius is not None else None),
                ]
                for expected_center_hint, expected_radius_hint in retry_profiles:
                    seen_probe: set[float] = set()
                    center_json = None
                    radius = None
                    geom_quality: dict[str, Any] | None = None
                    for probe_ts in probe_points:
                        probe_ts_rounded = round(float(probe_ts), 3)
                        if probe_ts_rounded in seen_probe:
                            continue
                        seen_probe.add(probe_ts_rounded)
                        center_json, radius, geom_quality = estimate_ring_geometry_over_window(
                            cap=cap,
                            fps=fps,
                            start_ts=float(probe_ts_rounded),
                            max_frame=max_frame,
                            zones_payload_full=zones_payload_full,
                            window_seconds=ring_geometry_window_seconds,
                            step_frames=ring_geometry_step_frames,
                            expected_center=expected_center_hint,
                            expected_radius=expected_radius_hint,
                            min_radius_map_units=min_radius_for_ring,
                            countdown_zone_mode=bool(ring_countdown_zone_mode),
                            strict_line_profile=bool(ring_strict_line_profile),
                            arc_only_mode=bool(ring_arc_only_mode),
                        )
                        if center_json is not None and radius is not None:
                            break
                    if center_json is None or radius is None:
                        continue
                    if (not bool(ring_countdown_zone_mode)) and min_radius_for_ring is not None and float(radius) < float(min_radius_for_ring):
                        center_json = None
                        radius = None
                        continue
                    if (not bool(ring_countdown_zone_mode)) and (not is_ring_radius_plausible(radius=radius, expected_radius=expected_radius)):
                        center_json = None
                        radius = None
                        continue
                    if bool(ring_countdown_zone_mode) and expected_radius is not None:
                        if abs(float(radius) - float(expected_radius)) > max(25.0, float(expected_radius) * 0.20):
                            center_json = None
                            radius = None
                            continue
                    if rings_rows and (not bool(ring_countdown_zone_mode)):
                        prev = rings_rows[-1]
                        prev_radius_raw = prev.get("radius")
                        prev_radius_val: float | None = None
                        if prev_radius_raw is not None:
                            try:
                                prev_radius_val = float(prev_radius_raw)
                            except Exception:
                                prev_radius_val = None
                        if not rd.is_ring_nested(
                            str(prev.get("center")) if prev.get("center") is not None else None,
                            prev_radius_val,
                            center_json,
                            radius,
                        ):
                            center_json = None
                            radius = None
                            continue
                    break

                is_low_conf_geometry = False
                if geom_quality is not None:
                    g_conf = float(geom_quality.get("confidence", 0.0) or 0.0)
                    g_samples = int(geom_quality.get("samples", 0) or 0)
                    if bool(ring_countdown_zone_mode):
                        is_low_conf_geometry = bool(g_conf < 0.20 or g_samples < 1)
                    else:
                        is_low_conf_geometry = bool(g_conf < 0.45 or g_samples < 2)
                if center_json is None or radius is None or is_low_conf_geometry:
                    dynamic_extrap_center = None
                    dynamic_extrap_radius = None
                    if rings_rows:
                        prev = rings_rows[-1]
                        prev_c_str = str(prev.get("center")) if prev.get("center") is not None else None
                        prev_c = parse_center_json(prev_c_str)
                        try:
                            prev_r = float(prev.get("radius", 0.0) or 0.0)
                        except Exception:
                            prev_r = 0.0
                        if prev_c is not None and prev_r > 0.0 and expected_radius is not None:
                            sample_end = min(float(video_duration_sec), ring_time_end)
                            sample_start = ring_time_start
                            moving_samples = []
                            if sample_end > sample_start + 1.0:
                                step = max(1.0, (sample_end - sample_start) / 12.0)
                                for t in np.arange(sample_start, sample_end + 0.1, step):
                                    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                                    ret, sample_frame = cap.read()
                                    if not ret: continue
                                    moving_geom, moving_conf = rd.detect_ring_geometry_in_frame(
                                        sample_frame,
                                        zones_payload=None,
                                        expected_center=None,
                                        expected_radius=None,
                                        min_radius_map_units=expected_radius,
                                        countdown_zone_mode=bool(ring_countdown_zone_mode),
                                        strict_line_profile=True,
                                        arc_only_mode=True,
                                    )
                                    if moving_geom is not None:
                                        moving_geom["confidence"] = moving_conf
                                        if moving_conf >= 0.05:
                                            curr_r = float(moving_geom["radius"])
                                            curr_c = (float(moving_geom["x"]), float(moving_geom["y"]))
                                            
                                            # Red zone radius must not exceed prev_r significantly
                                            if curr_r > prev_r + 15.0:
                                                continue
                                                
                                            moving_samples.append({
                                                "t": float(t),
                                                "x": curr_c[0],
                                                "y": curr_c[1],
                                                "r": curr_r,
                                                "conf": float(moving_conf),
                                                "geom": moving_geom,
                                            })
                            if len(moving_samples) >= 1:
                                moving_samples.sort(key=lambda s: s["r"])
                                best_s = moving_samples[0] # Smallest radius is closest to final ring
                                
                                curr_r = best_s["r"]
                                curr_c = (best_s["x"], best_s["y"])
                                
                                # If it hasn't shrunk much, extrapolation is too noisy
                                if prev_r - curr_r > 15.0:
                                    frac = (prev_r - curr_r) / max(1.0, prev_r - expected_radius)
                                    frac = max(0.05, min(1.0, frac))
                                    target_x = prev_c[0] + (curr_c[0] - prev_c[0]) / frac
                                    target_y = prev_c[1] + (curr_c[1] - prev_c[1]) / frac
                                else:
                                    # Fallback if almost no shrink detected
                                    target_x = prev_c[0]
                                    target_y = prev_c[1]

                                dynamic_extrap_center = (float(np.clip(target_x, 0.0, 1079.0)), float(np.clip(target_y, 0.0, 1079.0)))
                                dynamic_extrap_radius = expected_radius
                                geom_quality = {
                                    "samples": len(moving_samples),
                                    "confidence": float(best_s["conf"]),
                                    "geometry_source": "moving_zone_extrapolation",
                                    "fit_error": float(best_s["geom"].get("fit_error", 999.0)),
                                }
                                if "arc_annulus_score" in best_s["geom"]:
                                    geom_quality["arc_annulus_score"] = float(best_s["geom"]["arc_annulus_score"])
                                if "arc_coverage" in best_s["geom"]:
                                    geom_quality["arc_coverage"] = float(best_s["geom"]["arc_coverage"])

                    if dynamic_extrap_center is not None and dynamic_extrap_radius is not None:
                        fallback_center = dynamic_extrap_center
                        fallback_radius = dynamic_extrap_radius
                        timing_source = "timing_extrapolated" if float(ts_start) > float(video_duration_sec) else "timing_fallback"
                    else:
                        fallback_center, fallback_radius = rd.extrapolate_ring_pattern(
                            rings_rows=rings_rows,
                            target_ts_start=float(ring_time_start),
                            expected_radius=expected_radius,
                        )
                        geom_quality = {
                            "samples": 0,
                            "confidence": 0.25,
                            "geometry_source": "timing_fallback_pattern",
                            "fit_error": 999.0,
                        }
                        timing_source = "timing_extrapolated" if float(ts_start) > float(video_duration_sec) else "timing_fallback"

                    center_json = json.dumps(
                        {
                            "x": round(float(np.clip(fallback_center[0], 0.0, 1079.0)), 2),
                            "y": round(float(np.clip(fallback_center[1], 0.0, 1079.0)), 2),
                            "space": "map",
                        },
                        ensure_ascii=False,
                    )
                    radius = round(float(max(1.0, fallback_radius)), 2)

                nested_clamped = False
                nested_clamp_delta = 0.0
                if rings_rows:
                    prev = rings_rows[-1]
                    center_json, radius, nested_clamped, nested_clamp_delta = rd.clamp_ring_inside_parent(
                        prev_center_json=str(prev.get("center")) if prev.get("center") is not None else None,
                        prev_radius=float(prev.get("radius", 0.0) or 0.0) if prev.get("radius") is not None else None,
                        center_json=center_json,
                        radius=radius,
                        min_radius=min_radius_for_ring,
                    )

                if rings_rows and (not bool(ring_countdown_zone_mode)):
                    prev = rings_rows[-1]
                    prev_radius_raw = prev.get("radius")
                    prev_radius_val: float | None = None
                    if prev_radius_raw is not None:
                        try:
                            prev_radius_val = float(prev_radius_raw)
                        except Exception:
                            prev_radius_val = None
                    if not rd.is_ring_nested(
                        str(prev.get("center")) if prev.get("center") is not None else None,
                        prev_radius_val,
                        center_json,
                        radius,
                    ):
                        cursor_frame = min(max_frame, cursor_frame + max(ring_coarse_frames, ring_refine_step_frames))
                        continue
                center_payload: dict[str, Any]
                try:
                    center_payload = json.loads(center_json)
                except Exception:
                    center_payload = {"x": 0.0, "y": 0.0, "space": "map"}
                if not isinstance(center_payload, dict):
                    center_payload = {"x": 0.0, "y": 0.0, "space": "map"}
                center_payload["space"] = "map"
                center_payload["ring_number_source"] = "countdown_minus_one"
                center_payload["countdown_ring_number"] = int(countdown_ring_number)
                center_payload["ring_event_type"] = "closing"
                if int(ring_number) == 1:
                    # R1 countdown timestamp is inferred from closing-size model:
                    # start(R1 closing) = end(R1 closing) - duration(R1 closing).
                    center_payload["r1_countdown_ts"] = round(float(ring_time_start), 3)
                    center_payload["r1_countdown_source"] = "inferred_from_r1_closing_duration"
                    # region agent log
                    _debug_log_b91(
                        "post-fix-2",
                        "H1",
                        "detect_map_start.py:collect_enrichment_data",
                        "r1_payload_anchor_saved",
                        {
                            "ring_number": int(ring_number),
                            "countdown_ring_number": int(countdown_ring_number),
                            "saved_r1_countdown_ts": float(ring_time_start),
                            "ring_time_start": float(ring_time_start),
                            "ring_time_end": float(ring_time_end),
                        },
                    )
                    # endregion
                center_payload["start_ring_detected"] = int(start_ring_detected)
                center_payload["timing_source"] = str(timing_source)
                center_payload["countdown_zone_mode"] = bool(ring_countdown_zone_mode)
                center_payload["arc_only_mode"] = bool(ring_arc_only_mode)
                center_payload["meters_to_map_units"] = round(float(meters_to_map_units), 5)
                if min_radius_for_ring is not None:
                    center_payload["min_radius_map_units"] = round(float(min_radius_for_ring), 3)
                center_payload["map_mp_id"] = map_mp_id
                center_payload["nested_clamped"] = bool(nested_clamped)
                if nested_clamped:
                    center_payload["nested_clamp_delta"] = round(float(max(0.0, nested_clamp_delta)), 3)
                center_payload["radius_override_anchor"] = bool(
                    isinstance(MAP_RING_RADIUS_MAP_OVERRIDE.get(str(map_mp_id or "")), dict)
                )
                if geom_quality is not None:
                    center_payload["geometry_confidence"] = float(geom_quality.get("confidence", 0.0) or 0.0)
                    center_payload["geometry_samples"] = int(geom_quality.get("samples", 0) or 0)
                    center_payload["geometry_source"] = str(geom_quality.get("geometry_source", "unknown") or "unknown")
                    center_payload["fit_error"] = float(geom_quality.get("fit_error", 0.0) or 0.0)
                    if "diameter_map_units" in geom_quality:
                        center_payload["diameter_map_units"] = float(geom_quality.get("diameter_map_units", 0.0) or 0.0)
                    if "diameter_px" in geom_quality:
                        center_payload["diameter_px"] = float(geom_quality.get("diameter_px", 0.0) or 0.0)
                    if "radius_px" in geom_quality:
                        center_payload["radius_px"] = float(geom_quality.get("radius_px", 0.0) or 0.0)
                    if "arc_annulus_score" in geom_quality:
                        center_payload["arc_annulus_score"] = float(geom_quality.get("arc_annulus_score", 0.0) or 0.0)
                    if "arc_coverage" in geom_quality:
                        center_payload["arc_coverage"] = float(geom_quality.get("arc_coverage", 0.0) or 0.0)
                    if "arc_residual_p95" in geom_quality:
                        center_payload["arc_residual_p95"] = float(geom_quality.get("arc_residual_p95", 0.0) or 0.0)
                center_json = json.dumps(center_payload, ensure_ascii=False)
                if progress is not None:
                    progress.set_stage(f"ring{ring_number}_geometry")
                    progress.update(int(ring_time_start * fps))
                rings_rows.append(
                    {
                        "ring_number": int(ring_number),
                        "center": center_json,
                        "radius": radius,
                        "time_start": round(float(ring_time_start), 3),
                        "time_end": round(float(ring_time_end), 3),
                    }
                )
                if int(ring_number) == 1:
                    ring1_m = rd.ring_radius_meters(1)
                    if ring1_m is not None and float(ring1_m) > 0 and radius is not None:
                        meters_to_map_units = float(radius) / float(ring1_m)
                ring_m = rd.ring_radius_meters(int(ring_number))
                if ring_m is not None and float(ring_m) > 0 and radius is not None:
                    cand = float(radius) / float(ring_m)
                    if np.isfinite(cand) and 0.05 <= cand <= 8.0:
                        q_conf = float(geom_quality.get("confidence", 0.0) if geom_quality is not None else 0.0)
                        q_samples = int(geom_quality.get("samples", 0) if geom_quality is not None else 0)
                        weight = max(0.05, q_conf) * min(1.0, max(1, q_samples) / 3.0)
                        scale_candidates.append((cand, weight))
                        stable_scale_points = [item for item in scale_candidates if item[1] >= 0.25]
                        if stable_scale_points:
                            vals = np.asarray([v for v, _ in stable_scale_points], dtype=np.float64)
                            wts = np.asarray([w for _, w in stable_scale_points], dtype=np.float64)
                            order = np.argsort(vals)
                            vals = vals[order]
                            wts = wts[order]
                            acc = np.cumsum(wts)
                            cut = 0.5 * float(np.sum(wts))
                            idx = int(np.searchsorted(acc, cut, side="left"))
                            idx = max(0, min(idx, len(vals) - 1))
                            robust_scale = float(vals[idx])
                            # Avoid aggressive jumps when geometry confidence is weak.
                            if q_conf >= 0.45:
                                meters_to_map_units = float(robust_scale)
                last_ring_start_sec = float(ring_time_start)
                next_cursor_sec = max(float(ring_time_start) + max(1.0, float(ring_coarse_sec)), float(ts_end))
                cursor_frame = min(max_frame, max(int(next_cursor_sec * fps), int((float(ring_time_start) + 0.25) * fps)))
                if stop_after_current_ring:
                    break

            # Fallback ring1 backfill when countdown1 is absent in OCR.
            has_ring1 = any(int(row.get("ring_number") or 0) == 1 for row in rings_rows)
            ring2_row = next((row for row in rings_rows if int(row.get("ring_number") or 0) == 2), None)
            if (not has_ring1) and ring2_row is not None and (
                explicit_start_countdown is None or int(explicit_start_countdown) > 2
            ):
                ring2_center = parse_center_json(str(ring2_row.get("center")) if ring2_row.get("center") is not None else None)
                ring2_start = float(ring2_row.get("time_start") or 0.0)
                countdown2_duration = float(rd.ring_phase_duration_seconds("countdown", 2) or 0.0)
                closing1_duration = float(rd.ring_phase_duration_seconds("closing", 1) or 260.0)
                ring1_end = max(0.0, float(ring2_start) - max(0.0, countdown2_duration))
                ring1_start = max(0.0, float(ring1_end) - max(1.0, closing1_duration))
                # region agent log
                _debug_log_b91(
                    "pre-fix-2",
                    "H3",
                    "detect_map_start.py:collect_enrichment_data",
                    "ring1_backfill_timing",
                    {
                        "ring2_start": float(ring2_start),
                        "countdown2_duration": float(countdown2_duration),
                        "closing1_duration": float(closing1_duration),
                        "ring1_start": float(ring1_start),
                        "ring1_end": float(ring1_end),
                    },
                )
                # endregion
                if ring1_end <= ring1_start:
                    ring1_end = min(float(video_duration_sec), float(ring1_start) + max(1.0, closing1_duration))
                ring1_anchor_ts = max(0.0, float(ring1_end) - max(0.0, ring_geometry_window_seconds * 0.5))
                expected_ring1 = rd.expected_ring_radius_map_units(1, meters_to_map_units)
                min_ring1 = rd.min_ring_radius_map_units(1, meters_to_map_units)
                ring1_center, ring1_radius, ring1_quality = estimate_ring_geometry_over_window(
                    cap=cap,
                    fps=fps,
                    start_ts=ring1_anchor_ts,
                    max_frame=max_frame,
                    zones_payload_full=zones_payload_full,
                    window_seconds=ring_geometry_window_seconds,
                    step_frames=ring_geometry_step_frames,
                    expected_center=ring2_center,
                    expected_radius=expected_ring1,
                    min_radius_map_units=min_ring1,
                    countdown_zone_mode=True,
                    strict_line_profile=bool(ring_strict_line_profile),
                    arc_only_mode=bool(ring_arc_only_mode),
                )
                detected_ring1_ok = bool(
                    ring1_center is not None
                    and ring1_radius is not None
                    and (min_ring1 is None or float(ring1_radius) >= float(min_ring1))
                    and (ring1_quality is None or float(ring1_quality.get("confidence", 0.0) or 0.0) >= 0.20)
                )
                if not detected_ring1_ok:
                    fallback_center, fallback_radius = rd.backfill_previous_ring_pattern(
                        rings_rows=rings_rows,
                        target_ring_number=1,
                        target_ts_start=float(ring1_start),
                        expected_radius=expected_ring1,
                    )
                    ring1_center = json.dumps(
                        {
                            "x": round(float(fallback_center[0]), 2),
                            "y": round(float(fallback_center[1]), 2),
                            "space": "map",
                        },
                        ensure_ascii=False,
                    )
                    ring1_radius = float(fallback_radius)
                    ring1_quality = {
                        "samples": 0,
                        "confidence": 0.25,
                        "geometry_source": "timing_backfill_pattern",
                        "fit_error": 999.0,
                    }
                if ring1_center is not None and ring1_radius is not None:
                    center_payload: dict[str, Any]
                    try:
                        center_payload = json.loads(ring1_center)
                    except Exception:
                        center_payload = {"x": 0.0, "y": 0.0, "space": "map"}
                    if not isinstance(center_payload, dict):
                        center_payload = {"x": 0.0, "y": 0.0, "space": "map"}
                    center_payload["space"] = "map"
                    center_payload["ring_number_source"] = "backfill_r2_countdown"
                    center_payload["timing_source"] = "ring1_backfill"
                    center_payload["arc_only_mode"] = bool(ring_arc_only_mode)
                    center_payload["meters_to_map_units"] = round(float(meters_to_map_units), 5)
                    if min_ring1 is not None:
                        center_payload["min_radius_map_units"] = round(float(min_ring1), 3)
                    if ring1_quality is not None:
                        center_payload["geometry_confidence"] = float(ring1_quality.get("confidence", 0.0) or 0.0)
                        center_payload["geometry_samples"] = int(ring1_quality.get("samples", 0) or 0)
                        center_payload["geometry_source"] = str(ring1_quality.get("geometry_source", "unknown") or "unknown")
                        center_payload["fit_error"] = float(ring1_quality.get("fit_error", 0.0) or 0.0)
                        if "diameter_map_units" in ring1_quality:
                            center_payload["diameter_map_units"] = float(ring1_quality.get("diameter_map_units", 0.0) or 0.0)
                        if "diameter_px" in ring1_quality:
                            center_payload["diameter_px"] = float(ring1_quality.get("diameter_px", 0.0) or 0.0)
                        if "radius_px" in ring1_quality:
                            center_payload["radius_px"] = float(ring1_quality.get("radius_px", 0.0) or 0.0)
                        if "arc_annulus_score" in ring1_quality:
                            center_payload["arc_annulus_score"] = float(ring1_quality.get("arc_annulus_score", 0.0) or 0.0)
                        if "arc_coverage" in ring1_quality:
                            center_payload["arc_coverage"] = float(ring1_quality.get("arc_coverage", 0.0) or 0.0)
                        if "arc_residual_p95" in ring1_quality:
                            center_payload["arc_residual_p95"] = float(ring1_quality.get("arc_residual_p95", 0.0) or 0.0)
                    ring1_row = {
                        "ring_number": 1,
                        "center": json.dumps(center_payload, ensure_ascii=False),
                        "radius": round(float(ring1_radius), 2),
                        "time_start": round(float(ring1_start), 3),
                        "time_end": round(float(ring1_end), 3),
                    }
                    rings_rows = [ring1_row] + rings_rows

    camreman_rows = infer_camreman_rows_from_rings(rings_rows)
    cap.release()
    diagnostics = {
        "snapshot_observations": len(snapshot_obs),
        "timeline_observations": int(timeline_checks),
        "rings_detected": len(rings_rows),
        "camreman_jumps": len(camreman_rows),
    }
    return teams_rows, rings_rows, camreman_rows, diagnostics


def infer_map_from_zone_observations(observations: list[dict[str, Any]]) -> tuple[str | None, float, int]:
    scores: dict[str, dict[str, float]] = {}
    for item in observations:
        map_name = item.get("matched_map_name")
        if not map_name:
            continue
        key = str(map_name)
        conf = float(item.get("matched_map_confidence", 0.0) or 0.0)
        bucket = scores.get(key)
        if bucket is None:
            bucket = {"hits": 0.0, "conf_sum": 0.0}
            scores[key] = bucket
        bucket["hits"] += 1.0
        bucket["conf_sum"] += conf
    if not scores:
        return None, 0.0, 0
    best_name = None
    best_hits = -1
    best_avg = 0.0
    for name, bucket in scores.items():
        hits = int(bucket["hits"])
        avg = float(bucket["conf_sum"]) / max(1.0, bucket["hits"])
        if hits > best_hits or (hits == best_hits and avg > best_avg):
            best_name = name
            best_hits = hits
            best_avg = avg
    return best_name, best_avg, max(0, best_hits)


def detect_first_center_pov_by_map_reference(
    video_path: Path,
    mp_id: str,
    frame_step: int,
    camera_min_conf: float,
) -> tuple[float | None, float]:
    ref_img = resolve_map_reference(mp_id)
    if ref_img is None:
        return None, 0.0
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None, 0.0
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    max_frame = max(0, total_frames - 1) if total_frames > 0 else 0
    step = max(1, int(frame_step))
    prev_gray: np.ndarray | None = None
    best_conf = 0.0
    frame_idx = 0
    while frame_idx <= max_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        roi_center = center_roi(frame)
        is_cam, cam_conf, curr_gray = map_camera_confidence(roi_center, prev_gray, ref_img)
        prev_gray = curr_gray
        best_conf = max(best_conf, float(cam_conf))
        if is_cam and cam_conf >= camera_min_conf:
            cap.release()
            ts = frame_idx / fps if fps > 0 else 0.0
            return float(ts), float(cam_conf)
        frame_idx += step
    cap.release()
    return None, float(best_conf)


def pick_zone_pov_roi(frame: np.ndarray, zones_payload: dict[str, Any] | None) -> np.ndarray:
    x1, y1, x2, y2 = pick_zone_pov_bounds(frame, zones_payload)
    return frame[y1:y2, x1:x2]


def pick_zone_pov_bounds(frame: np.ndarray, zones_payload: dict[str, Any] | None) -> tuple[int, int, int, int]:
    if zones_payload is None:
        h, w = frame.shape[:2]
        x1 = int(w * 0.22)
        x2 = int(w * 0.78)
        y1 = int(h * 0.06)
        y2 = int(h * 0.94)
        return x1, y1, x2, y2
    zones = zones_payload.get("zones", [])
    if not isinstance(zones, list):
        h, w = frame.shape[:2]
        x1 = int(w * 0.22)
        x2 = int(w * 0.78)
        y1 = int(h * 0.06)
        y2 = int(h * 0.94)
        return x1, y1, x2, y2
    h, w = frame.shape[:2]
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        if zone.get("enabled", True) is False:
            continue
        label = str(zone.get("label") or "").strip().lower()
        if "pov" not in label:
            continue
        x, y, zw, zh = zone_rect_to_frame(zone, zones_payload, w, h)
        x2 = min(w, x + max(1, zw))
        y2 = min(h, y + max(1, zh))
        if x2 > x and y2 > y:
            return x, y, x2, y2
    x1 = int(w * 0.22)
    x2 = int(w * 0.78)
    y1 = int(h * 0.06)
    y2 = int(h * 0.94)
    return x1, y1, x2, y2


def infer_frame_map_from_zone_observations(observations: list[dict[str, Any]]) -> tuple[str | None, float]:
    best_name: str | None = None
    best_conf = 0.0
    for item in observations:
        name = item.get("matched_map_name")
        if not name:
            continue
        conf = float(item.get("matched_map_confidence", 0.0) or 0.0)
        if conf > best_conf:
            best_conf = conf
            best_name = str(name)
    return best_name, best_conf


def ring_map_to_frame(
    x_map: float,
    y_map: float,
    r_map: float,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> tuple[int, int, int]:
    width = max(1.0, float(x2 - x1))
    height = max(1.0, float(y2 - y1))
    px = int(round(float(x1) + (float(x_map) / 1080.0) * width))
    py = int(round(float(y1) + (float(y_map) / 1080.0) * height))
    pr = int(round((float(r_map) / 1080.0) * min(width, height)))
    return px, py, max(1, pr)


def render_visualization(
    frame: np.ndarray,
    *,
    frame_idx: int,
    timestamp: float,
    map_name: str | None,
    map_conf: float,
    camera_conf: float,
    cond1_ok: bool,
    cond2_ok: bool,
    both_ok: bool,
    zones_payload: dict[str, Any] | None,
    ring_event_info: dict[str, Any] | None = None,
    window_name: str = "detect_map_start",
    ring_countdown_zone_mode: bool = False,
    ring_strict_line_profile: bool = False,
    ring_arc_only_mode: bool = False,
) -> str | None:
    show_calibration_ui = not bool(getattr(render_visualization, "_disable_calibration_ui", False))
    mp_id = MAP_LABEL_TO_MP_ID.get(str(map_name or ""), None)
    if not mp_id:
        forced_mp_id = str(getattr(render_visualization, "_forced_map_mp_id", "") or "")
        if forced_mp_id:
            mp_id = forced_mp_id
    session_token = str(getattr(render_visualization, "_session_token", "") or "")
    calib_map_key = str(getattr(render_visualization, "_calib_map_key", "") or "")
    calib_session_key = str(getattr(render_visualization, "_calib_session_key", "") or "")
    need_init_calib = show_calibration_ui and (
        (not hasattr(render_visualization, "_calib_state"))
        or (calib_map_key != str(mp_id or ""))
        or (session_token and calib_session_key != session_token)
    )
    if need_init_calib:
        default_r1, default_r2 = default_calibration_radii(mp_id)
        setattr(
            render_visualization,
            "_calib_state",
            {
                "active": 1,
                "c1": {"x": 540.0, "y": 540.0, "r": float(default_r1)},
                "c2": {"x": 540.0, "y": 540.0, "r": float(default_r2)},
            },
        )
        setattr(render_visualization, "_calib_map_key", str(mp_id or ""))
        setattr(render_visualization, "_calib_session_key", session_token)
        setattr(render_visualization, "_drag_state", {"dragging": False, "key": "c1"})
        setattr(render_visualization, "_ring_geom_cache", {"frame": -1, "geom": None})
    calib_state = getattr(render_visualization, "_calib_state", {"active": 1, "c1": {"x": 540.0, "y": 540.0, "r": 200.0}, "c2": {"x": 540.0, "y": 540.0, "r": 100.0}})
    ratio_lock_enabled = bool(getattr(render_visualization, "_calib_lock_ratio", True))
    storm_ratio = bool(str(mp_id or "") == "mp_storm_point")
    setattr(render_visualization, "_calib_ratio_enabled", bool(ratio_lock_enabled and storm_ratio))
    if not hasattr(render_visualization, "_web_map_cache"):
        setattr(render_visualization, "_web_map_cache", {})
    if not hasattr(render_visualization, "_web_preview_cache"):
        setattr(render_visualization, "_web_preview_cache", {})
    if not hasattr(render_visualization, "_ring_geom_cache"):
        setattr(render_visualization, "_ring_geom_cache", {"frame": -1, "geom": None})
    if not hasattr(render_visualization, "_camera_zoom_state"):
        setattr(
            render_visualization,
            "_camera_zoom_state",
            {
                "baseline_zoom": None,
                "baseline_diameter_px": None,
                "baseline_center": None,
                "baseline_ring_number": None,
                "baseline_frame": None,
                "baseline_ts": None,
                "zoom_ratio": 1.0,
                "jump_start_frame": None,
                "jump_start_ts": None,
                "jump_type": None,
                "jump_active": False,
                "last_center": None,
                "last_diameter_px": None,
                "compensated": None,
            },
        )
    web_map_cache: dict[str, np.ndarray | None] = getattr(render_visualization, "_web_map_cache")
    web_preview_cache: dict[str, np.ndarray] = getattr(render_visualization, "_web_preview_cache")
    camera_state: dict[str, Any] = getattr(render_visualization, "_camera_zoom_state")
    paused = bool(getattr(render_visualization, "_paused", False))
    delay_ms = max(1, int(getattr(render_visualization, "_delay_ms", 16)))
    overlay = frame.copy()
    h, w = overlay.shape[:2]

    # Map-name OCR ROI.
    rx1, ry1, rx2, ry2 = map_name_roi_bounds(w, h)
    cv2.rectangle(overlay, (rx1, ry1), (rx2, ry2), (0, 255, 255), 2)
    cv2.putText(overlay, "map_roi", (rx1, max(18, ry1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    # POV ROI (zone_pov if provided, else center fallback).
    px1, py1, px2, py2 = pick_zone_pov_bounds(overlay, zones_payload)
    cv2.rectangle(overlay, (px1, py1), (px2, py2), (255, 150, 0), 2)
    cv2.putText(overlay, "pov_roi", (px1, max(18, py1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 150, 0), 1)

    ring_views = rd.build_ring_detection_views(frame, countdown_zone_mode=bool(ring_countdown_zone_mode))
    ring_cache = getattr(render_visualization, "_ring_geom_cache", {"frame": -1, "geom": None})
    drag_state = getattr(render_visualization, "_drag_state", {"dragging": False})
    should_refresh_geom = (
        ring_cache.get("geom") is None
        or abs(int(frame_idx) - int(ring_cache.get("frame", -1))) >= 3
    ) and (not bool(drag_state.get("dragging", False)))
    if should_refresh_geom:
        ring_geom, _ = rd.detect_ring_geometry_in_frame(
            frame,
            zones_payload,
            countdown_zone_mode=bool(ring_countdown_zone_mode),
            strict_line_profile=bool(ring_strict_line_profile),
            arc_only_mode=bool(ring_arc_only_mode),
        )
        ring_cache = {"frame": int(frame_idx), "geom": ring_geom}
        setattr(render_visualization, "_ring_geom_cache", ring_cache)
    else:
        ring_geom = ring_cache.get("geom")
    ring_diameter_px = 0.0
    ring_diameter_map = 0.0
    ring_number_dbg = ring_event_info.get("ring_number") if isinstance(ring_event_info, dict) else None
    ring_event_dbg = ring_event_info.get("event_type") if isinstance(ring_event_info, dict) else None
    if ring_geom is not None:
        ring_diameter_px = float(ring_geom.get("diameter_px", 0.0) or 0.0)
        ring_diameter_map = float(ring_geom.get("diameter_map_units", 0.0) or 0.0)
        if ring_diameter_px <= 0.0:
            ring_diameter_px = float(ring_geom.get("radius_px", 0.0) or 0.0) * 2.0
        if ring_diameter_px <= 0.0:
            ring_diameter_px = float(ring_geom.get("radius", 0.0) or 0.0) * 2.0
        if ring_diameter_map <= 0.0:
            ring_diameter_map = float(ring_geom.get("radius", 0.0) or 0.0) * 2.0

        cx_raw = float(ring_geom.get("x", 540.0) or 540.0)
        cy_raw = float(ring_geom.get("y", 540.0) or 540.0)
        r_raw = float(ring_geom.get("radius", 1.0) or 1.0)
        prev_center = camera_state.get("last_center")
        prev_diameter = camera_state.get("last_diameter_px")
        jump_active = False
        jump_started_now = False
        jump_type = None
        if isinstance(prev_center, tuple) and prev_diameter is not None:
            dxy = float(np.hypot(cx_raw - float(prev_center[0]), cy_raw - float(prev_center[1])))
            dz = abs(float(ring_diameter_px) - float(prev_diameter)) / max(1e-6, float(prev_diameter))
            if dz >= 0.03:
                jump_active = True
                jump_type = "zoom"
            if dxy >= max(3.0, r_raw * 0.05):
                jump_active = True
                jump_type = "shift" if jump_type is None else "zoom+shift"
        if jump_active and (not bool(camera_state.get("jump_active", False))):
            jump_started_now = True
            camera_state["jump_start_frame"] = int(frame_idx)
            camera_state["jump_start_ts"] = float(timestamp)
            camera_state["jump_type"] = str(jump_type or "unknown")
            print(
                f"[visualize] jump_start frame={frame_idx} ts={timestamp:.3f}s "
                f"type={camera_state['jump_type']}"
            )
        camera_state["jump_active"] = bool(jump_active)

        expected_d_map = None
        expected_d_px = None
        if ring_number_dbg is not None:
            scale_hint = initial_meters_to_map_units(mp_id)
            exp_r = rd.expected_ring_radius_map_units(int(ring_number_dbg), float(scale_hint))
            if exp_r is not None:
                expected_d_map = float(exp_r) * 2.0
                expected_d_px = (float(expected_d_map) / 1080.0) * float(max(1.0, (MAP_ROI_WIDTH)))
        raw_zoom = 1.0
        if expected_d_px is not None and expected_d_px > 0 and ring_diameter_px > 0:
            raw_zoom = float(ring_diameter_px) / float(expected_d_px)
        baseline_zoom = camera_state.get("baseline_zoom")
        if ring_event_dbg == "countdown" and ring_number_dbg is not None and ring_diameter_px > 0:
            if baseline_zoom is None:
                camera_state["baseline_zoom"] = float(max(1e-6, raw_zoom))
                camera_state["baseline_diameter_px"] = float(ring_diameter_px)
                camera_state["baseline_center"] = (float(cx_raw), float(cy_raw))
                camera_state["baseline_ring_number"] = int(ring_number_dbg)
                camera_state["baseline_frame"] = int(frame_idx)
                camera_state["baseline_ts"] = float(timestamp)
                baseline_zoom = camera_state["baseline_zoom"]
                print(
                    f"[visualize] countdown_baseline ring={int(ring_number_dbg)} "
                    f"frame={frame_idx} ts={timestamp:.3f}s "
                    f"diam_px={ring_diameter_px:.3f} zoom={float(baseline_zoom):.4f}"
                )
        if baseline_zoom is None or not np.isfinite(float(baseline_zoom)):
            zoom_ratio = float(np.clip(raw_zoom, 0.25, 4.0))
        else:
            zoom_ratio = float(np.clip(raw_zoom / max(1e-6, float(baseline_zoom)), 0.25, 4.0))
        camera_state["zoom_ratio"] = float(zoom_ratio)

        anchor_x = 540.0
        anchor_y = 540.0
        cx_aff = anchor_x + ((cx_raw - anchor_x) / max(1e-6, zoom_ratio))
        cy_aff = anchor_y + ((cy_raw - anchor_y) / max(1e-6, zoom_ratio))
        r_aff = r_raw / max(1e-6, zoom_ratio)

        base_center = camera_state.get("baseline_center")
        if isinstance(base_center, tuple):
            bcx, bcy = float(base_center[0]), float(base_center[1])
            cx_fb = bcx + ((cx_raw - bcx) * max(1e-6, zoom_ratio))
            cy_fb = bcy + ((cy_raw - bcy) * max(1e-6, zoom_ratio))
        else:
            cx_fb, cy_fb = cx_raw, cy_raw
        affine_stable = 0.25 <= float(zoom_ratio) <= 3.5
        cx_comp = cx_aff if affine_stable else cx_fb
        cy_comp = cy_aff if affine_stable else cy_fb
        r_comp = r_aff if affine_stable else r_raw
        camera_state["compensated"] = {
            "x": float(np.clip(cx_comp, 0.0, 1079.0)),
            "y": float(np.clip(cy_comp, 0.0, 1079.0)),
            "radius": float(max(1.0, min(1079.0, r_comp))),
            "method": "affine" if affine_stable else "delta_zoom_fallback",
            "dx": float(cx_comp - cx_raw),
            "dy": float(cy_comp - cy_raw),
        }
        if jump_started_now:
            observer_size = float(np.clip(1080.0 / max(0.2, zoom_ratio), 120.0, 1080.0))
            jump_events = list(getattr(render_visualization, "_camera_jump_events", []))
            jump_events.append(
                {
                    "timestamp_sec": round(float(timestamp), 3),
                    "x": round(float(np.clip(cx_raw, 0.0, 1079.0)), 3),
                    "y": round(float(np.clip(cy_raw, 0.0, 1079.0)), 3),
                    "camera_size": round(float(observer_size), 3),
                    "frame_idx": int(frame_idx),
                }
            )
            setattr(render_visualization, "_camera_jump_events", jump_events)
        # Micro-motion logging: record even very small observer movements.
        last_log = camera_state.get("last_motion_log")
        should_log_micro = False
        if isinstance(last_log, dict):
            try:
                log_x = float(last_log.get("x", cx_raw))
                log_y = float(last_log.get("y", cy_raw))
                log_d = float(last_log.get("diameter", ring_diameter_px))
                log_ts = float(last_log.get("ts", timestamp))
            except Exception:
                log_x, log_y, log_d, log_ts = float(cx_raw), float(cy_raw), float(ring_diameter_px), float(timestamp)
            micro_dxy = float(np.hypot(cx_raw - log_x, cy_raw - log_y))
            micro_dz = abs(float(ring_diameter_px - log_d)) / max(1.0, abs(float(log_d)))
            dt_log = float(timestamp - log_ts)
            if dt_log >= 0.35 and (micro_dxy >= 0.6 or micro_dz >= 0.004):
                should_log_micro = True
        else:
            should_log_micro = True
        if should_log_micro:
            observer_size = float(np.clip(1080.0 / max(0.2, zoom_ratio), 120.0, 1080.0))
            jump_events = list(getattr(render_visualization, "_camera_jump_events", []))
            jump_events.append(
                {
                    "timestamp_sec": round(float(timestamp), 3),
                    "x": round(float(np.clip(cx_raw, 0.0, 1079.0)), 3),
                    "y": round(float(np.clip(cy_raw, 0.0, 1079.0)), 3),
                    "camera_size": round(float(observer_size), 3),
                    "frame_idx": int(frame_idx),
                }
            )
            setattr(render_visualization, "_camera_jump_events", jump_events)
            camera_state["last_motion_log"] = {
                "x": float(cx_raw),
                "y": float(cy_raw),
                "diameter": float(ring_diameter_px),
                "ts": float(timestamp),
            }
        camera_state["last_center"] = (float(cx_raw), float(cy_raw))
        camera_state["last_diameter_px"] = float(max(1.0, ring_diameter_px))
    else:
        camera_state["jump_active"] = False
    setattr(render_visualization, "_camera_zoom_state", camera_state)
    if ring_views is not None:
        rx1, ry1, rx2, ry2 = int(ring_views["x1"]), int(ring_views["y1"]), int(ring_views["x2"]), int(ring_views["y2"])
        cv2.rectangle(overlay, (rx1, ry1), (rx2, ry2), (220, 120, 255), 2)
        cv2.putText(overlay, "ring_roi", (rx1, max(18, ry1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 120, 255), 1)
        vis_mask = ring_views.get("mask_connected", ring_views["mask"]) if bool(ring_countdown_zone_mode) else ring_views["mask"]
        contours_vis, _ = cv2.findContours(vis_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours_vis:
            largest_vis = max(contours_vis, key=cv2.contourArea)
            bx, by, bw, bh = cv2.boundingRect(largest_vis)
            cv2.rectangle(overlay, (rx1 + bx, ry1 + by), (rx1 + bx + bw, ry1 + by + bh), (0, 220, 220), 2)
            cv2.putText(
                overlay,
                "largest_mask_bbox",
                (rx1 + bx, max(18, ry1 + by - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 220, 220),
                1,
            )
        if ring_geom is not None:
            cx, cy, cr = ring_map_to_frame(
                x_map=float(ring_geom["x"]),
                y_map=float(ring_geom["y"]),
                r_map=float(ring_geom["radius"]),
                x1=rx1,
                y1=ry1,
                x2=rx2,
                y2=ry2,
            )
            cv2.circle(overlay, (cx, cy), cr, (255, 255, 255), 2)
            cv2.circle(overlay, (cx, cy), 3, (255, 255, 255), -1)
        if show_calibration_ui:
            for idx, key, color in ((1, "c1", (0, 255, 255)), (2, "c2", (255, 0, 255))):
                c = calib_state.get(key, {})
                ccx, ccy, ccr = ring_map_to_frame(
                    x_map=float(c.get("x", 540.0)),
                    y_map=float(c.get("y", 540.0)),
                    r_map=float(c.get("r", 120.0)),
                    x1=rx1,
                    y1=ry1,
                    x2=rx2,
                    y2=ry2,
                )
                thick = 3 if int(calib_state.get("active", 1)) == idx else 1
                cv2.circle(overlay, (ccx, ccy), ccr, color, thick)
                cv2.putText(
                    overlay,
                    f"C{idx} r={float(c.get('r', 0.0)):.1f}",
                    (ccx + 6, ccy - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    color,
                    1,
                )

    # Text-zones.
    if zones_payload is not None:
        zones = zones_payload.get("zones", [])
        if isinstance(zones, list):
            for zone in zones:
                if not isinstance(zone, dict):
                    continue
                x, y, zw, zh = zone_rect_to_frame(zone, zones_payload, w, h)
                x2 = min(w, x + max(1, zw))
                y2 = min(h, y + max(1, zh))
                if x2 <= x or y2 <= y:
                    continue
                enabled = zone.get("enabled", True) is not False
                color = (60, 220, 80) if enabled else (120, 120, 120)
                cv2.rectangle(overlay, (x, y), (x2, y2), color, 1)
                label = str(zone.get("label") or zone.get("id") or "zone")
                cv2.putText(overlay, label[:28], (x, min(h - 8, y + 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

    status_color = (0, 220, 0) if both_ok else (0, 180, 255) if cond2_ok else (0, 0, 255)
    line1 = f"f={frame_idx} t={timestamp:.2f}s map={map_name} mapConf={map_conf:.3f} camConf={camera_conf:.3f}"
    ring_text = "ring=none"
    ring_diag = "src=none fit=0.0"
    if ring_geom is not None:
        ring_text = f"ring=({ring_geom['x']:.1f},{ring_geom['y']:.1f}) r={ring_geom['radius']:.1f}"
        ring_diag = (
            f"src={str(ring_geom.get('geometry_source', 'unknown'))} "
            f"fit={float(ring_geom.get('fit_error', 0.0) or 0.0):.2f} "
            f"ang={float(ring_geom.get('angle_deg', 0.0) or 0.0):.1f}"
        )
        if str(ring_geom.get("geometry_source", "")) == "arc_boundary":
            ring_diag += (
                f" arc_cov={float(ring_geom.get('arc_coverage', 0.0) or 0.0):.1f}"
                f" annulus={float(ring_geom.get('arc_annulus_score', 0.0) or 0.0):.3f}"
                f" inliers={int(ring_geom.get('arc_inliers', 0) or 0)}"
            )
        ring_text += (
            f" d_map={float(ring_geom.get('diameter_map_units', ring_diameter_map) or 0.0):.1f}"
            f" d_px={float(ring_geom.get('diameter_px', ring_diameter_px) or 0.0):.1f}"
        )
    line2 = (
        f"cond1={cond1_ok} cond2={cond2_ok} both={both_ok} paused={paused} "
        "[Space pause][N next][P prev][Q/ESC stop]"
    )
    zoom_ratio_dbg = float(camera_state.get("zoom_ratio", 1.0) or 1.0)
    jump_start_frame = camera_state.get("jump_start_frame")
    jump_type = str(camera_state.get("jump_type") or "none")
    ring_num_str = str(ring_number_dbg) if ring_number_dbg is not None else "-"
    ring_evt_str = str(ring_event_dbg or "-")
    line3 = (
        f"{ring_text} {ring_diag} countdown_zone={'on' if ring_countdown_zone_mode else 'off'} "
        f"strict_line={'on' if ring_strict_line_profile else 'off'} arc_only={'on' if ring_arc_only_mode else 'off'} "
        f"calib_ui={'on' if show_calibration_ui else 'off'}"
    )
    line4 = (
        f"ring_no={ring_num_str} ring_evt={ring_evt_str} zoom={zoom_ratio_dbg:.3f} "
        f"jump={'on' if bool(camera_state.get('jump_active', False)) else 'off'} "
        f"jump_start={jump_start_frame if jump_start_frame is not None else '-'} type={jump_type}"
    )
    cv2.putText(overlay, line1, (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    cv2.putText(overlay, line2, (14, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.58, status_color, 2)
    cv2.putText(overlay, line3, (14, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (220, 220, 220), 2)
    cv2.putText(overlay, line4, (14, 109), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (200, 200, 60), 2)

    cv2.imshow(window_name, overlay)
    if ring_views is not None:
        roi_view = ring_views["roi"].copy()
        if ring_geom is not None:
            cx, cy, cr = ring_map_to_frame(
                x_map=float(ring_geom["x"]),
                y_map=float(ring_geom["y"]),
                r_map=float(ring_geom["radius"]),
                x1=0,
                y1=0,
                x2=roi_view.shape[1],
                y2=roi_view.shape[0],
            )
            cv2.circle(roi_view, (cx, cy), cr, (255, 255, 255), 2)
            cv2.circle(roi_view, (cx, cy), 3, (255, 255, 255), -1)
        cv2.imshow(f"{window_name} :: ring_roi", roi_view)
        hsv_bgr = cv2.cvtColor(ring_views["hsv"], cv2.COLOR_HSV2BGR)
        mask_bgr = cv2.cvtColor(ring_views["mask"], cv2.COLOR_GRAY2BGR)
        mask_conn_bgr = cv2.cvtColor(
            ring_views.get("mask_connected", ring_views["mask"]),
            cv2.COLOR_GRAY2BGR,
        )
        ring_like_bgr = cv2.cvtColor(ring_views["ring_like"], cv2.COLOR_GRAY2BGR)
        hsv_panel = cv2.hconcat([hsv_bgr, mask_bgr, mask_conn_bgr, ring_like_bgr])
        cv2.imshow(f"{window_name} :: ring_hsv", hsv_panel)
    # Web-like preview: draw circles over map texture.
    if mp_id not in web_map_cache:
        web_map_cache[mp_id or ""] = resolve_map_reference(mp_id)
    web_map = web_map_cache.get(mp_id or "")
    web_key = str(mp_id or "__none__")
    if web_key not in web_preview_cache:
        if web_map is None:
            base = np.zeros((1080, 1080, 3), dtype=np.uint8)
            cv2.putText(base, "web preview: map not found", (20, 540), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (180, 180, 180), 2)
        else:
            base = cv2.resize(web_map, (1080, 1080), interpolation=cv2.INTER_AREA)
        web_preview_cache[web_key] = base
    web_preview = web_preview_cache[web_key].copy()
    if ring_geom is not None:
        cx = int(round(float(np.clip(float(ring_geom["x"]), 0.0, 1079.0))))
        cy = int(round(float(np.clip(float(ring_geom["y"]), 0.0, 1079.0))))
        cr = int(round(max(1.0, float(ring_geom["radius"]))))
        # Red zone overlay outside active ring.
        red_layer = np.full_like(web_preview, (0, 0, 200), dtype=np.uint8)
        ring_mask = np.zeros((1080, 1080), dtype=np.uint8)
        cv2.circle(ring_mask, (cx, cy), cr, 255, thickness=-1)
        outside_mask = cv2.bitwise_not(ring_mask)
        red_mix = cv2.addWeighted(web_preview, 0.75, red_layer, 0.25, 0.0)
        web_preview[outside_mask > 0] = red_mix[outside_mask > 0]
        cv2.circle(web_preview, (cx, cy), cr, (255, 255, 255), 2)
        cv2.circle(web_preview, (cx, cy), 3, (255, 255, 255), -1)
        comp = camera_state.get("compensated")
        if isinstance(comp, dict):
            ccx = int(round(float(np.clip(float(comp.get("x", cx)), 0.0, 1079.0))))
            ccy = int(round(float(np.clip(float(comp.get("y", cy)), 0.0, 1079.0))))
            ccr = int(round(max(1.0, float(comp.get("radius", cr)))))
            cv2.circle(web_preview, (ccx, ccy), ccr, (80, 255, 80), 2)
            cv2.arrowedLine(web_preview, (cx, cy), (ccx, ccy), (60, 240, 240), 2, tipLength=0.18)
            cv2.putText(
                web_preview,
                f"comp={str(comp.get('method','-'))}",
                (min(980, ccx + 8), max(20, ccy - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (80, 255, 80),
                1,
            )

        zoom_ratio = float(camera_state.get("zoom_ratio", 1.0) or 1.0)
        observer_size = int(round(float(np.clip(1080.0 / max(0.2, zoom_ratio), 120.0, 1080.0))))
        half = observer_size // 2
        ox1 = int(max(0, min(1079, cx - half)))
        oy1 = int(max(0, min(1079, cy - half)))
        ox2 = int(max(0, min(1079, ox1 + observer_size)))
        oy2 = int(max(0, min(1079, oy1 + observer_size)))
        cv2.rectangle(web_preview, (ox1, oy1), (ox2, oy2), (255, 220, 80), 2)
        cv2.putText(
            web_preview,
            f"observer_roi {observer_size} zoom={zoom_ratio:.3f}",
            (max(8, ox1), max(20, oy1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 220, 80),
            1,
        )
    if show_calibration_ui:
        for idx, key, color in ((1, "c1", (0, 255, 255)), (2, "c2", (255, 0, 255))):
            c = calib_state.get(key, {})
            ccx = int(round(float(np.clip(float(c.get("x", 540.0)), 0.0, 1079.0))))
            ccy = int(round(float(np.clip(float(c.get("y", 540.0)), 0.0, 1079.0))))
            ccr = int(round(max(1.0, float(c.get("r", 120.0)))))
            thick = 3 if int(calib_state.get("active", 1)) == idx else 1
            cv2.circle(web_preview, (ccx, ccy), ccr, color, thick)
            cv2.putText(web_preview, f"C{idx}", (ccx + 6, ccy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.putText(web_preview, "web preview", (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (240, 240, 240), 2)
    cv2.putText(
        web_preview,
        f"ring_no={ring_num_str} evt={ring_evt_str} jump={str(camera_state.get('jump_type') or '-')}",
        (14, 52),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (220, 220, 220),
        1,
    )
    cv2.imshow(f"{window_name} :: web_preview", web_preview)
    if show_calibration_ui:
        cv2.setMouseCallback(
            window_name,
            _visualization_mouse_callback,
            {"width": int(overlay.shape[1]), "height": int(overlay.shape[0])},
        )
        cv2.setMouseCallback(
            f"{window_name} :: web_preview",
            _visualization_mouse_callback,
            {"width": int(web_preview.shape[1]), "height": int(web_preview.shape[0])},
        )

    while True:
        key = cv2.waitKey(30 if paused else delay_ms) & 0xFF
        if key == 255:
            if paused and bool(getattr(render_visualization, "_ui_dirty", False)):
                setattr(render_visualization, "_ui_dirty", False)
                return None
            if paused:
                continue
            break
        if key in (27, ord("q"), ord("Q")):
            setattr(render_visualization, "_paused", paused)
            return "quit"
        if key in (ord("n"), ord("N"), ord("]")):
            setattr(render_visualization, "_paused", False)
            return "next"
        if key in (ord("p"), ord("P"), ord("[")):
            setattr(render_visualization, "_paused", False)
            return "prev"
        if show_calibration_ui and key in (ord("1"),):
            calib_state["active"] = 1
            setattr(render_visualization, "_calib_state", calib_state)
            return None
        if show_calibration_ui and key in (ord("2"),):
            calib_state["active"] = 2
            setattr(render_visualization, "_calib_state", calib_state)
            return None
        active_key = "c1" if int(calib_state.get("active", 1)) == 1 else "c2"
        active = dict(calib_state.get(active_key, {"x": 540.0, "y": 540.0, "r": 120.0}))
        move_step = 4.0
        radius_step = 3.0
        moved = False
        if show_calibration_ui and key in (ord("i"), ord("I"), ord("w"), ord("W"), 82):
            active["y"] = float(np.clip(float(active.get("y", 540.0)) - move_step, 0.0, 1079.0))
            moved = True
        elif show_calibration_ui and key in (ord("k"), ord("K"), ord("s"), ord("S"), 84):
            active["y"] = float(np.clip(float(active.get("y", 540.0)) + move_step, 0.0, 1079.0))
            moved = True
        elif show_calibration_ui and key in (ord("j"), ord("J"), ord("a"), ord("A"), 81):
            active["x"] = float(np.clip(float(active.get("x", 540.0)) - move_step, 0.0, 1079.0))
            moved = True
        elif show_calibration_ui and key in (ord("l"), ord("L"), ord("d"), ord("D"), 83):
            active["x"] = float(np.clip(float(active.get("x", 540.0)) + move_step, 0.0, 1079.0))
            moved = True
        elif show_calibration_ui and key in (ord("u"), ord("U")):
            active["r"] = float(np.clip(float(active.get("r", 120.0)) - radius_step, 1.0, 1079.0))
            moved = True
        elif show_calibration_ui and key in (ord("o"), ord("O")):
            active["r"] = float(np.clip(float(active.get("r", 120.0)) + radius_step, 1.0, 1079.0))
            moved = True
        elif show_calibration_ui and key in (ord("r"), ord("R")):
            payload = {
                "timestamp_sec": round(float(timestamp), 3),
                "frame_idx": int(frame_idx),
                "c1": calib_state.get("c1", {}),
                "c2": calib_state.get("c2", {}),
                "detected_ring": ring_geom,
            }
            out_dir = PROJECT_ROOT / "output" / "map_start_roi"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "manual_ring_calibration.jsonl"
            with out_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            print(f"[visualize] calibration_saved file={out_file} frame={frame_idx} t={timestamp:.3f}s")
            setattr(render_visualization, "_calib_state", calib_state)
            return None
        if moved:
            calib_state[active_key] = active
            if bool(getattr(render_visualization, "_calib_ratio_enabled", False)):
                calib_state = apply_calib_ratio_lock(calib_state, active_key)
            setattr(render_visualization, "_calib_state", calib_state)
            return None
        if key in (ord(" "),):
            paused = not paused
            setattr(render_visualization, "_paused", paused)
            return None
        if not paused:
            break
    setattr(render_visualization, "_paused", paused)
    return None


def map_name_roi_bounds(width: int, height: int) -> tuple[int, int, int, int]:
    # Fixed ROI for 1920x1080 (user-confirmed constant resolution):
    # map label line ("STORM POINT", "OLYMPUS", ...) in top-left panel.
    if width == 1920 and height == 1080:
        x1, y1, x2, y2 = 34, 78, 338, 166
    else:
        # Fallback relative ROI for non-1080p inputs.
        x1 = int(width * 0.018)
        y1 = int(height * 0.072)
        x2 = int(width * 0.176)
        y2 = int(height * 0.154)
    x1 = max(0, min(x1, width - 1))
    x2 = max(x1 + 1, min(x2, width))
    y1 = max(0, min(y1, height - 1))
    y2 = max(y1 + 1, min(y2, height))
    return x1, y1, x2, y2


def read_map_name_roi(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map_name_roi_bounds(w, h)
    return frame[y1:y2, x1:x2]


def read_image_safe(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def detect_map_name(frame: np.ndarray, min_conf: float) -> tuple[str | None, float, str]:
    roi = read_map_name_roi(frame)
    if roi.size == 0:
        return None, 0.0, ""
    h, w = roi.shape[:2]
    # Focus on lower label line where map text is displayed (avoid "MATCH 1" header).
    y1 = int(h * 0.50)
    y2 = int(h * 0.98)
    x1 = int(w * 0.03)
    x2 = int(w * 0.97)
    map_line_roi = roi[y1:y2, x1:x2]
    if map_line_roi.size == 0:
        map_line_roi = roi

    def build_ocr_images(src: np.ndarray) -> list[np.ndarray]:
        gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        bw1 = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 7
        )
        bw2 = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 7
        )
        _, bw3 = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
        kernels = [
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 2)),
        ]
        imgs = [bw1, bw2, bw3]
        for k in kernels:
            imgs.append(cv2.morphologyEx(bw1, cv2.MORPH_CLOSE, k))
            imgs.append(cv2.morphologyEx(bw2, cv2.MORPH_CLOSE, k))
        return imgs

    images = build_ocr_images(map_line_roi) + build_ocr_images(roi)

    best_text = ""
    best_name: str | None = None
    best_conf = 0.0
    if pytesseract is not None:
        for img in images:
            try:
                ocr_text = pytesseract.image_to_string(
                    img,
                    config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ-' ",
                )
            except Exception:
                ocr_text = ""
            normalized = normalize_text(ocr_text)
            map_label, conf = fuzzy_map_match(normalized)
            if conf > best_conf:
                best_conf = conf
                best_name = map_label
                best_text = normalized
    if best_name is None or best_conf < min_conf:
        return None, best_conf, best_text
    return best_name, best_conf, best_text


def resolve_map_reference(mp_id: str | None) -> np.ndarray | None:
    if not mp_id:
        return None
    short_name = mp_id.removeprefix("mp_")
    candidates = [
        PROJECT_ROOT / "maps" / f"{mp_id}.png",
        PROJECT_ROOT / "maps" / f"{mp_id}.webp",
        PROJECT_ROOT / "maps" / f"{short_name}.png",
        PROJECT_ROOT / "maps" / f"{short_name}.webp",
        PROJECT_ROOT / "output" / f"map_background_{mp_id}.png",
        PROJECT_ROOT / "output" / f"map_background_{short_name}.png",
    ]
    for path in candidates:
        if path.exists():
            ref = read_image_safe(path)
            if ref is not None:
                return ref
    return None


def center_roi(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    # Central region to distinguish map-camera from player POV.
    x1 = int(w * 0.22)
    x2 = int(w * 0.78)
    y1 = int(h * 0.06)
    y2 = int(h * 0.94)
    return frame[y1:y2, x1:x2]


def map_camera_confidence(
    roi: np.ndarray,
    prev_roi_gray: np.ndarray | None,
    ref_map_img: np.ndarray | None,
) -> tuple[bool, float, np.ndarray]:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    motion_conf = 0.5
    if prev_roi_gray is not None and prev_roi_gray.shape == gray.shape:
        diff = cv2.absdiff(gray, prev_roi_gray)
        mean_diff = float(np.mean(diff))
        # Lower diff => more static overview camera.
        motion_conf = float(np.clip((30.0 - mean_diff) / 30.0, 0.0, 1.0))

    ref_conf = 0.5
    if ref_map_img is not None:
        ref = cv2.resize(ref_map_img, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_AREA)
        hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hsv_ref = cv2.cvtColor(ref, cv2.COLOR_BGR2HSV)
        hist_roi = cv2.calcHist([hsv_roi], [0, 1], None, [24, 24], [0, 180, 0, 256])
        hist_ref = cv2.calcHist([hsv_ref], [0, 1], None, [24, 24], [0, 180, 0, 256])
        cv2.normalize(hist_roi, hist_roi)
        cv2.normalize(hist_ref, hist_ref)
        sim = cv2.compareHist(hist_roi, hist_ref, cv2.HISTCMP_CORREL)
        ref_conf = float(np.clip((sim + 1.0) / 2.0, 0.0, 1.0))

    conf = (0.6 * motion_conf) + (0.4 * ref_conf)
    return conf >= 0.58, conf, gray


def evaluate_frame_signal(
    cap: cv2.VideoCapture,
    frame_idx: int,
    fps: float,
    prev_center_gray: np.ndarray | None,
    ocr_min_conf: float,
    camera_min_conf: float,
    ref_cache: dict[str, np.ndarray | None],
    debug_dir: Path | None = None,
    debug_video_stem: str | None = None,
    debug_save_every: int = 500,
    zones_payload: dict[str, Any] | None = None,
    text_ocr_min_confidence: float = 0.0,
    visualize: bool = False,
    visualize_no_ocr: bool = False,
    visualize_window: str = "detect_map_start",
    ring_countdown_zone_mode: bool = False,
    ring_strict_line_profile: bool = False,
    ring_arc_only_mode: bool = False,
) -> tuple[FrameSignal | None, np.ndarray | None, dict[str, Any] | None, list[dict[str, Any]], str | None]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok:
        return None, prev_center_gray, None, [], False
    timestamp = frame_idx / fps if fps > 0 else 0.0
    if bool(visualize_no_ocr):
        text_observations = []
        ocr_text = ""
        map_name_from_roi = None
        map_conf_from_roi = 0.0
        map_name_from_zone = None
        map_conf_from_zone = 0.0
        forced_mp = str(getattr(render_visualization, "_forced_map_mp_id", "") or "")
        effective_map_name = MP_ID_TO_MAP_LABEL.get(forced_mp)
        effective_map_conf = 1.0 if effective_map_name is not None else 0.0
    else:
        text_observations = run_zone_ocr(
            frame=frame,
            frame_idx=frame_idx,
            fps=fps,
            zones_payload=zones_payload,
            min_confidence=text_ocr_min_confidence,
        )
        map_name_from_roi, map_conf_from_roi, ocr_text = detect_map_name(frame, ocr_min_conf)
        map_name_from_zone, map_conf_from_zone = infer_frame_map_from_zone_observations(text_observations)
        effective_map_name = map_name_from_roi
        effective_map_conf = map_conf_from_roi
        if map_name_from_zone is not None and map_conf_from_zone >= effective_map_conf:
            effective_map_name = map_name_from_zone
            effective_map_conf = map_conf_from_zone

    mp_id = MAP_LABEL_TO_MP_ID.get(effective_map_name or "", None)
    if mp_id not in ref_cache:
        ref_cache[mp_id or ""] = resolve_map_reference(mp_id)
    ref_img = ref_cache[mp_id or ""]
    roi_center = pick_zone_pov_roi(frame, zones_payload)
    is_cam, cam_conf, curr_gray = map_camera_confidence(roi_center, prev_center_gray, ref_img)
    signal = FrameSignal(
        map_label=effective_map_name,
        map_conf=float(effective_map_conf),
        is_map_camera=bool(is_cam and cam_conf >= camera_min_conf),
        camera_conf=float(cam_conf),
        cond_conf=float((effective_map_conf + cam_conf) / 2.0),
    )
    ring_event_info: dict[str, Any] | None = None
    if text_observations:
        best_score = -1e9
        for item in text_observations:
            raw = str(item.get("normalized_text") or "")
            ring_num, event_type = parse_ring_event(raw)
            if event_type not in {"closing", "countdown"}:
                continue
            conf = float(item.get("ocr_confidence", 0.0) or 0.0)
            explicit = 1.0 if ring_num is not None else 0.0
            score = (conf * 2.0) + explicit
            if score <= best_score:
                continue
            best_score = score
            ring_event_info = {
                "ring_number": int(ring_num) if ring_num is not None else None,
                "event_type": str(event_type),
                "explicit_number": bool(ring_num is not None),
                "text": raw,
                "confidence": round(float(conf), 4),
            }
    trace_item = {
        "frame": int(frame_idx),
        "timestamp_sec": round(timestamp, 3),
        "ocr_text": ocr_text,
        "map_name_roi": map_name_from_roi,
        "map_conf_roi": round(map_conf_from_roi, 4),
        "map_name_zone": map_name_from_zone,
        "map_conf_zone": round(map_conf_from_zone, 4),
        "map_name": effective_map_name,
        "map_conf": round(effective_map_conf, 4),
        "camera_conf": round(cam_conf, 4),
        "cond1_ok": bool(effective_map_name is not None and effective_map_conf >= ocr_min_conf),
        "cond2_ok": bool(is_cam and cam_conf >= camera_min_conf),
        "both_ok": bool((effective_map_name is not None and effective_map_conf >= ocr_min_conf) and (is_cam and cam_conf >= camera_min_conf)),
    }
    visualize_action: str | None = None
    if visualize:
        visualize_action = render_visualization(
            frame,
            frame_idx=frame_idx,
            timestamp=timestamp,
            map_name=effective_map_name,
            map_conf=effective_map_conf,
            camera_conf=cam_conf,
            cond1_ok=trace_item["cond1_ok"],
            cond2_ok=trace_item["cond2_ok"],
            both_ok=trace_item["both_ok"],
            zones_payload=zones_payload,
            ring_event_info=ring_event_info,
            window_name=visualize_window,
            ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
            ring_strict_line_profile=bool(ring_strict_line_profile),
            ring_arc_only_mode=bool(ring_arc_only_mode),
        )
    return signal, curr_gray, trace_item, text_observations, visualize_action


def find_start_in_window(
    cap: cv2.VideoCapture,
    start_frame: int,
    end_frame: int,
    step: int,
    fps: float,
    stable_target: float,
    ocr_min_conf: float,
    camera_min_conf: float,
    debug: bool,
    debug_dir: Path,
    debug_video_stem: str,
    debug_save_every: int,
    trace: list[dict[str, Any]],
    zones_payload: dict[str, Any] | None,
    text_observations: list[dict[str, Any]],
    text_ocr_min_confidence: float,
    first_center_pov_sec_hint: float | None,
    visualize: bool = False,
    visualize_no_ocr: bool = False,
    ring_countdown_zone_mode: bool = False,
    ring_strict_line_profile: bool = False,
    ring_arc_only_mode: bool = False,
    progress: LiveProgress | None = None,
    progress_stage: str = "scan",
) -> tuple[float | None, str | None, str | None, float, float | None, str | None]:
    prev_center_gray: np.ndarray | None = None
    stable_run_sec = 0.0
    stable_start_sec: float | None = None
    accepted_map_name: str | None = None
    accepted_map_mp_id: str | None = None
    confidence_acc: list[float] = []
    ref_cache: dict[str, np.ndarray | None] = {}
    first_center_pov_sec = first_center_pov_sec_hint

    frame_idx = max(0, int(start_frame))
    frame_end = max(frame_idx, int(end_frame))
    scan_step = max(1, int(step))
    if progress is not None:
        progress.set_stage(progress_stage)
    while frame_idx <= frame_end:
        if progress is not None:
            progress.update(frame_idx)
        signal, prev_center_gray, trace_item, obs, visualize_action = evaluate_frame_signal(
            cap=cap,
            frame_idx=frame_idx,
            fps=fps,
            prev_center_gray=prev_center_gray,
            ocr_min_conf=ocr_min_conf,
            camera_min_conf=camera_min_conf,
            ref_cache=ref_cache,
            debug_dir=debug_dir if debug else None,
            debug_video_stem=debug_video_stem if debug else None,
            debug_save_every=debug_save_every,
            zones_payload=zones_payload,
            text_ocr_min_confidence=text_ocr_min_confidence,
            visualize=visualize,
            visualize_no_ocr=bool(visualize_no_ocr),
            ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
            ring_strict_line_profile=bool(ring_strict_line_profile),
            ring_arc_only_mode=bool(ring_arc_only_mode),
        )
        if signal is None:
            break
        if visualize_action in {"quit", "next", "prev"}:
            return None, accepted_map_name, accepted_map_mp_id, 0.0, first_center_pov_sec, visualize_action
        if obs:
            text_observations.extend(obs)

        both_ok = bool(signal.map_label is not None and signal.map_conf >= ocr_min_conf and signal.is_map_camera)
        if signal.is_map_camera and first_center_pov_sec is None:
            first_center_pov_sec = frame_idx / fps if fps > 0 else 0.0
        if both_ok:
            if stable_start_sec is None:
                stable_start_sec = frame_idx / fps if fps > 0 else 0.0
                stable_run_sec = 0.0
                confidence_acc.clear()
            stable_run_sec += (scan_step / fps) if fps > 0 else 0.0
            confidence_acc.append(signal.cond_conf)
            accepted_map_name = signal.map_label
            accepted_map_mp_id = MAP_LABEL_TO_MP_ID.get(signal.map_label or "", None)
        else:
            stable_start_sec = None
            stable_run_sec = 0.0
            confidence_acc.clear()

        if debug and trace_item is not None:
            trace_item["stable_run_sec"] = round(stable_run_sec, 3)
            trace.append(trace_item)

        if stable_start_sec is not None and stable_run_sec >= stable_target:
            avg_conf = float(np.mean(confidence_acc)) if confidence_acc else 0.0
            return stable_start_sec, accepted_map_name, accepted_map_mp_id, avg_conf, first_center_pov_sec, None
        frame_idx += scan_step
    return None, accepted_map_name, accepted_map_mp_id, 0.0, first_center_pov_sec, None


def analyze_video(
    video_path: Path,
    frame_step: int,
    coarse_jump_frames: int,
    rollback_step_frames: int,
    refine_window_frames: int,
    start_refine_step_frames: int,
    stable_seconds: float,
    ocr_min_conf: float,
    camera_min_conf: float,
    debug: bool,
    debug_dir: Path,
    debug_save_every: int,
    text_zones_file: str | None,
    text_json_dir: Path,
    text_summary_top_n: int,
    text_ocr_min_confidence: float,
    text_zones_max_enabled: int,
    stop_on_first_both: bool,
    pov_screenshot_offset_sec: float,
    pov_screenshot_dir: Path,
    visualize: bool,
    visualize_no_ocr: bool,
    elim_coarse_sec: float,
    elim_refine_sec: float,
    elim_refine_step_sec: float,
    ring_coarse_sec: float,
    ring_rollback_sec: float,
    ring_refine_window_sec: float,
    ring_refine_step_sec: float,
    ring_stable_seconds: float,
    ring_geometry_window_seconds: float,
    ring_geometry_step_sec: float,
    ring_countdown_zone_mode: bool = False,
    ring_strict_line_profile: bool = False,
    ring_arc_only_mode: bool = False,
    camera_tracking_mode: str = "geometry",
    disable_start_detection: bool = False,
    disable_team_detection: bool = False,
    disable_elimination_detection: bool = False,
    disable_ring_detection: bool = False,
    disable_camera_tracking: bool = False,
    team_workers: int = 1,
    assume_start_sec: float = 0.0,
    assume_map_name: str | None = None,
    enable_progress: bool = True,
) -> dict[str, Any]:
    print(f"  [stage] scan_start video={video_path.name}")
    setattr(render_visualization, "_session_token", f"{video_path.name}:{time.monotonic():.6f}")
    setattr(render_visualization, "_calib_session_key", "")
    setattr(render_visualization, "_ring_geom_cache", {"frame": -1, "geom": None})
    setattr(render_visualization, "_camera_jump_events", [])
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {
            "video_name": video_path.name,
            "video_path": str(video_path),
            "map_name": None,
            "map_mp_id": None,
            "start_timestamp_sec": None,
            "confidence": 0.0,
            "status": "error_open_video",
            "notes": "Cannot open video",
            "teams_json": "[]",
            "teams_rows": [],
            "rings_rows": [],
            "visualize_action": None,
        }

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    progress = LiveProgress(video_path.name, total_frames=max(1, total_frames)) if enable_progress else None
    step = max(1, int(frame_step))
    stable_target = max(0.5, float(stable_seconds))
    coarse_jump = max(1, int(coarse_jump_frames))
    rollback_step = max(1, int(rollback_step_frames))
    refine_window = max(1, int(refine_window_frames))
    start_refine_step = max(1, int(start_refine_step_frames))

    trace: list[dict[str, Any]] = []
    text_observations: list[dict[str, Any]] = []
    first_center_pov_sec: float | None = None
    zones_payload_all = load_text_zones(text_zones_file, max_enabled=text_zones_max_enabled)
    zones_payload_start = clone_payload_with_selected_zones(zones_payload_all, {"map_zone"})
    if zones_payload_start is None:
        zones_payload_start = zones_payload_all
    zones_payload_full = load_text_zones(text_zones_file, max_enabled=5000)
    text_zone_note = "" if zones_payload_all else ";text_zones_missing_or_empty"
    setattr(render_visualization, "_forced_map_mp_id", None)
    if bool(disable_start_detection):
        start_ts = max(0.0, float(assume_start_sec))
        start_map_name: str | None = None
        start_mp_id: str | None = None
        if assume_map_name:
            candidate = normalize_text(str(assume_map_name))
            canon, _ = fuzzy_map_match(candidate)
            start_map_name = canon if canon is not None else candidate
            start_mp_id = MAP_LABEL_TO_MP_ID.get(start_map_name, None)
        if start_map_name is None:
            start_map_name = "STORM POINT"
            start_mp_id = MAP_LABEL_TO_MP_ID.get(start_map_name, None)
        if start_mp_id:
            setattr(render_visualization, "_forced_map_mp_id", str(start_mp_id))
        start_conf = 1.0
        first_center_pov_sec = float(start_ts)
        visualize_action = None
        if bool(visualize):
            ref_cache: dict[str, np.ndarray | None] = {}
            prev_gray: np.ndarray | None = None
            frame_idx = max(0, int(start_ts * fps))
            frame_end = max(0, total_frames - 1)
            preview_step = max(1, int(step))
            if progress is not None:
                progress.set_stage("visualize_only")
            while frame_idx <= frame_end:
                if progress is not None:
                    progress.update(frame_idx)
                signal, prev_gray, trace_item, obs, visualize_action = evaluate_frame_signal(
                    cap=cap,
                    frame_idx=frame_idx,
                    fps=fps,
                    prev_center_gray=prev_gray,
                    ocr_min_conf=ocr_min_conf,
                    camera_min_conf=camera_min_conf,
                    ref_cache=ref_cache,
                    debug_dir=debug_dir if debug else None,
                    debug_video_stem=video_path.stem if debug else None,
                    debug_save_every=debug_save_every,
                    zones_payload=zones_payload_start,
                    text_ocr_min_confidence=text_ocr_min_confidence,
                    visualize=True,
                    visualize_no_ocr=bool(visualize_no_ocr),
                    ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
                    ring_strict_line_profile=bool(ring_strict_line_profile),
                    ring_arc_only_mode=bool(ring_arc_only_mode),
                )
                if signal is None:
                    break
                if visualize_action in {"quit", "next", "prev"}:
                    break
                if debug and trace_item is not None:
                    trace_item["stable_run_sec"] = 0.0
                    trace.append(trace_item)
                if obs:
                    text_observations.extend(obs)
                frame_idx += preview_step
            if visualize_action in {"quit", "next", "prev"}:
                if progress is not None:
                    progress.finish("interrupted")
                cap.release()
                if visualize:
                    cv2.destroyAllWindows()
                return {
                    "video_name": video_path.name,
                    "video_path": str(video_path),
                    "map_name": start_map_name,
                    "map_mp_id": start_mp_id,
                    "start_timestamp_sec": None,
                    "confidence": 0.0,
                    "status": "interrupted_visualize",
                    "notes": f"stopped_by_user_visualize:{visualize_action}",
                    "teams_json": "[]",
                    "teams_rows": [],
                    "rings_rows": [],
                    "visualize_action": visualize_action,
                }
    else:
    # Fast strategy requested by user:
    # 1) If no confidence at start, jump +3000 frames to find first confident frame.
    # 2) Roll back by 100 frames to locate earlier boundary.
    # 3) Refine inside +/-300 frame window.
        start_ts, start_map_name, start_mp_id, start_conf, first_center_pov_sec, visualize_action = find_start_in_window(
            cap=cap,
            start_frame=0,
            end_frame=min(total_frames - 1, int(stable_target * fps * 2)) if total_frames > 0 else int(stable_target * fps * 2),
            step=step,
            fps=fps,
            stable_target=stable_target,
            ocr_min_conf=ocr_min_conf,
            camera_min_conf=camera_min_conf,
            debug=debug,
            debug_dir=debug_dir,
            debug_video_stem=video_path.stem,
            debug_save_every=debug_save_every,
            trace=trace,
            zones_payload=zones_payload_start,
            text_observations=text_observations,
            text_ocr_min_confidence=text_ocr_min_confidence,
            first_center_pov_sec_hint=first_center_pov_sec,
            visualize=visualize,
            visualize_no_ocr=bool(visualize_no_ocr),
            ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
            ring_strict_line_profile=bool(ring_strict_line_profile),
            ring_arc_only_mode=bool(ring_arc_only_mode),
            progress=progress,
            progress_stage="start_scan",
        )
        if visualize_action in {"quit", "next", "prev"}:
            if progress is not None:
                progress.finish("interrupted")
            cap.release()
            if visualize:
                cv2.destroyAllWindows()
            return {
                "video_name": video_path.name,
                "video_path": str(video_path),
                "map_name": start_map_name,
                "map_mp_id": start_mp_id,
                "start_timestamp_sec": None,
                "confidence": 0.0,
                "status": "interrupted_visualize",
                "notes": f"stopped_by_user_visualize:{visualize_action}",
                "teams_json": "[]",
                "teams_rows": [],
                "rings_rows": [],
                "visualize_action": visualize_action,
            }

    if start_ts is None and (not bool(disable_start_detection)):
        ref_cache: dict[str, np.ndarray | None] = {}
        prev_gray: np.ndarray | None = None
        first_confident_frame: int | None = None
        frame_idx = 0
        max_frame = max(0, total_frames - 1) if total_frames > 0 else 0
        if progress is not None:
            progress.set_stage("coarse_jump")
        while frame_idx <= max_frame:
            if progress is not None:
                progress.update(frame_idx)
            signal, prev_gray, trace_item, obs, visualize_action = evaluate_frame_signal(
                cap=cap,
                frame_idx=frame_idx,
                fps=fps,
                prev_center_gray=prev_gray,
                ocr_min_conf=ocr_min_conf,
                camera_min_conf=camera_min_conf,
                ref_cache=ref_cache,
                debug_dir=debug_dir if debug else None,
                debug_video_stem=video_path.stem if debug else None,
                debug_save_every=debug_save_every,
                zones_payload=zones_payload_start,
                text_ocr_min_confidence=text_ocr_min_confidence,
                visualize=visualize,
                visualize_no_ocr=bool(visualize_no_ocr),
                ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
                ring_strict_line_profile=bool(ring_strict_line_profile),
                ring_arc_only_mode=bool(ring_arc_only_mode),
            )
            if signal is None:
                break
            if visualize_action in {"quit", "next", "prev"}:
                break
            if obs:
                text_observations.extend(obs)
            both_ok = bool(signal.map_label is not None and signal.map_conf >= ocr_min_conf and signal.is_map_camera)
            if signal.is_map_camera and first_center_pov_sec is None:
                first_center_pov_sec = frame_idx / fps if fps > 0 else 0.0
            if debug and trace_item is not None:
                trace_item["stable_run_sec"] = 0.0
                trace.append(trace_item)
            if both_ok:
                if stop_on_first_both:
                    start_ts = frame_idx / fps if fps > 0 else 0.0
                    start_map_name = signal.map_label
                    start_mp_id = MAP_LABEL_TO_MP_ID.get(signal.map_label or "", None)
                    start_conf = float(signal.cond_conf)
                    break
                first_confident_frame = frame_idx
                break
            frame_idx += coarse_jump

        if visualize_action in {"quit", "next", "prev"}:
            if progress is not None:
                progress.finish("interrupted")
            cap.release()
            if visualize:
                cv2.destroyAllWindows()
            return {
                "video_name": video_path.name,
                "video_path": str(video_path),
                "map_name": start_map_name,
                "map_mp_id": start_mp_id,
                "start_timestamp_sec": None,
                "confidence": 0.0,
                "status": "interrupted_visualize",
                "notes": f"stopped_by_user_visualize:{visualize_action}",
                "teams_json": "[]",
                "teams_rows": [],
                "rings_rows": [],
                "visualize_action": visualize_action,
            }

        if first_confident_frame is not None:
            anchor = first_confident_frame
            if progress is not None:
                progress.set_stage("rollback")
            while anchor > 0:
                if progress is not None:
                    progress.update(anchor)
                probe = max(0, anchor - rollback_step)
                signal, _, trace_item, obs, visualize_action = evaluate_frame_signal(
                    cap=cap,
                    frame_idx=probe,
                    fps=fps,
                    prev_center_gray=None,
                    ocr_min_conf=ocr_min_conf,
                    camera_min_conf=camera_min_conf,
                    ref_cache=ref_cache,
                    debug_dir=debug_dir if debug else None,
                    debug_video_stem=video_path.stem if debug else None,
                    debug_save_every=debug_save_every,
                    zones_payload=zones_payload_start,
                    text_ocr_min_confidence=text_ocr_min_confidence,
                    visualize=visualize,
                    visualize_no_ocr=bool(visualize_no_ocr),
                    ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
                    ring_strict_line_profile=bool(ring_strict_line_profile),
                    ring_arc_only_mode=bool(ring_arc_only_mode),
                )
                if visualize_action in {"quit", "next", "prev"}:
                    break
                if debug and trace_item is not None:
                    trace_item["stable_run_sec"] = 0.0
                    trace.append(trace_item)
                if obs:
                    text_observations.extend(obs)
                if signal is None:
                    break
                both_ok = bool(signal.map_label is not None and signal.map_conf >= ocr_min_conf and signal.is_map_camera)
                if signal.is_map_camera and first_center_pov_sec is None:
                    first_center_pov_sec = probe / fps if fps > 0 else 0.0
                if both_ok:
                    anchor = probe
                else:
                    break

            if visualize_action in {"quit", "next", "prev"}:
                if progress is not None:
                    progress.finish("interrupted")
                cap.release()
                if visualize:
                    cv2.destroyAllWindows()
                return {
                    "video_name": video_path.name,
                    "video_path": str(video_path),
                    "map_name": start_map_name,
                    "map_mp_id": start_mp_id,
                    "start_timestamp_sec": None,
                    "confidence": 0.0,
                    "status": "interrupted_visualize",
                    "notes": f"stopped_by_user_visualize:{visualize_action}",
                    "teams_json": "[]",
                    "teams_rows": [],
                    "rings_rows": [],
                    "visualize_action": visualize_action,
                }

            refine_start = max(0, anchor - refine_window)
            refine_end = min(max_frame, anchor + refine_window)
            start_ts, start_map_name, start_mp_id, start_conf, first_center_pov_sec, visualize_action = find_start_in_window(
                cap=cap,
                start_frame=refine_start,
                end_frame=refine_end,
                step=start_refine_step,
                fps=fps,
                stable_target=stable_target,
                ocr_min_conf=ocr_min_conf,
                camera_min_conf=camera_min_conf,
                debug=debug,
                debug_dir=debug_dir,
                debug_video_stem=video_path.stem,
                debug_save_every=debug_save_every,
                trace=trace,
                zones_payload=zones_payload_start,
                text_observations=text_observations,
                text_ocr_min_confidence=text_ocr_min_confidence,
                first_center_pov_sec_hint=first_center_pov_sec,
                visualize=visualize,
                visualize_no_ocr=bool(visualize_no_ocr),
                ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
                ring_strict_line_profile=bool(ring_strict_line_profile),
                ring_arc_only_mode=bool(ring_arc_only_mode),
                progress=progress,
                progress_stage="refine_scan",
            )
            if visualize_action in {"quit", "next", "prev"}:
                if progress is not None:
                    progress.finish("interrupted")
                cap.release()
                if visualize:
                    cv2.destroyAllWindows()
                return {
                    "video_name": video_path.name,
                    "video_path": str(video_path),
                    "map_name": start_map_name,
                    "map_mp_id": start_mp_id,
                    "start_timestamp_sec": None,
                    "confidence": 0.0,
                    "status": "interrupted_visualize",
                    "notes": f"stopped_by_user_visualize:{visualize_action}",
                    "teams_json": "[]",
                    "teams_rows": [],
                    "rings_rows": [],
                    "visualize_action": visualize_action,
                }

    screenshot_target_sec: float | None = None
    screenshot_base_sec: float | None = None
    screenshot_rel_path: str | None = None
    if start_ts is not None:
        screenshot_base_sec = float(start_ts)
    elif first_center_pov_sec is not None and start_map_name is not None:
        # Fallback: only when map is already resolved.
        screenshot_base_sec = float(first_center_pov_sec)
    if screenshot_base_sec is not None:
        screenshot_target_sec = max(0.0, float(screenshot_base_sec) + max(0.0, float(pov_screenshot_offset_sec)))
        pov_screenshot_dir.mkdir(parents=True, exist_ok=True)
        shot_path = pov_screenshot_dir / f"{video_path.stem}.pov_plus_{int(max(0.0, pov_screenshot_offset_sec))}s.jpg"
        saved = save_screenshot_at_timestamp(video_path, screenshot_target_sec, shot_path)
        if saved is not None:
            try:
                screenshot_rel_path = str(saved.relative_to(PROJECT_ROOT))
            except Exception:
                screenshot_rel_path = str(saved)
    text_json_dir.mkdir(parents=True, exist_ok=True)
    summary_lines = aggregate_confident_lines(text_observations, text_summary_top_n)
    zone_map_name, zone_map_avg_conf, zone_map_hits = infer_map_from_zone_observations(text_observations)
    # Fallback path: if center POV exists and map from text-zones is stable,
    # treat this as a successful detection for status/console/db outputs.
    if (
        start_ts is None
        and first_center_pov_sec is not None
        and zone_map_name
        and zone_map_hits >= 2
        and zone_map_avg_conf >= 0.90
    ):
        start_ts = float(first_center_pov_sec)
        start_map_name = zone_map_name
        start_mp_id = MAP_LABEL_TO_MP_ID.get(start_map_name, None)
        start_conf = float(zone_map_avg_conf)
    print(
        "  [stage] start_detected "
        f"start={None if start_ts is None else round(float(start_ts), 3)} "
        f"map={start_map_name} mp_id={start_mp_id}"
    )
    if start_mp_id:
        setattr(render_visualization, "_forced_map_mp_id", str(start_mp_id))
    if bool(visualize_no_ocr and visualize):
        print("  [stage] enrich_teams_rings skipped (visualize_no_ocr)")
        teams_rows = []
        rings_rows = []
        camreman_rows = []
        enrichment_diag = {"snapshot_observations": 0, "timeline_observations": 0, "rings_detected": 0}
    else:
        print(f"  [stage] enrich_teams_rings video={video_path.name}")
        teams_rows, rings_rows, camreman_rows, enrichment_diag = collect_enrichment_data(
            video_path=video_path,
            fps=fps,
            total_frames=total_frames,
            frame_step=step,
            start_ts=start_ts,
            map_mp_id=start_mp_id,
            video_duration_sec=(float(total_frames) / float(fps)) if fps > 0 else 0.0,
            zones_payload_full=zones_payload_full,
            text_ocr_min_confidence=text_ocr_min_confidence,
            elim_coarse_sec=elim_coarse_sec,
            elim_refine_sec=elim_refine_sec,
            elim_refine_step_sec=elim_refine_step_sec,
            ring_coarse_sec=ring_coarse_sec,
            ring_rollback_sec=ring_rollback_sec,
            ring_refine_window_sec=ring_refine_window_sec,
            ring_refine_step_sec=ring_refine_step_sec,
            ring_stable_seconds=ring_stable_seconds,
            ring_geometry_window_seconds=ring_geometry_window_seconds,
            ring_geometry_step_sec=ring_geometry_step_sec,
            ring_countdown_zone_mode=bool(ring_countdown_zone_mode),
            ring_strict_line_profile=bool(ring_strict_line_profile),
            ring_arc_only_mode=bool(ring_arc_only_mode),
            disable_ring_detection=bool(disable_ring_detection),
            disable_team_detection=bool(disable_team_detection),
            disable_elimination_detection=bool(disable_elimination_detection),
            team_workers=max(1, int(team_workers)),
            visualize=bool(visualize),
            visualize_no_ocr=bool(visualize_no_ocr),
            ocr_min_conf=float(ocr_min_conf),
            camera_min_conf=float(camera_min_conf),
            progress=progress,
        )
        if bool(disable_ring_detection):
            rings_rows = []
            camreman_rows = []
            enrichment_diag = {**enrichment_diag, "rings_detected": 0, "ring_detection_disabled": True}
    visual_cam_rows_raw = list(getattr(render_visualization, "_camera_jump_events", []))
    if visual_cam_rows_raw:
        for item in visual_cam_rows_raw:
            camreman_rows.append(
                {
                    "timestamp_sec": float(item.get("timestamp_sec", 0.0) or 0.0),
                    "x": float(item.get("x", 0.0) or 0.0),
                    "y": float(item.get("y", 0.0) or 0.0),
                    "camera_size": float(item.get("camera_size", 0.0) or 0.0),
                }
            )
    if bool(disable_camera_tracking):
        camreman_rows = []
    if camreman_rows:
        seen_cam: set[tuple[int, int, int]] = set()
        uniq_cam: list[dict[str, Any]] = []
        for row in sorted(camreman_rows, key=lambda r: float(r.get("timestamp_sec", 0.0) or 0.0)):
            key = (
                int(round(float(row.get("timestamp_sec", 0.0) or 0.0) * 10.0)),
                int(round(float(row.get("x", 0.0) or 0.0))),
                int(round(float(row.get("y", 0.0) or 0.0))),
            )
            if key in seen_cam:
                continue
            seen_cam.add(key)
            uniq_cam.append(row)
        camreman_rows = uniq_cam
    video_duration_sec = (float(total_frames) / float(fps)) if fps > 0 else 0.0
    if bool(disable_camera_tracking):
        camera_rows = []
    else:
        camera_rows = ct.build_camera_rows_from_video(
            video_path=video_path,
            rings_rows=rings_rows,
            video_duration_sec=video_duration_sec,
            step_sec=max(0.25, float(ring_geometry_step_sec)),
            map_mp_id=start_mp_id,
            countdown_zone_mode=bool(ring_countdown_zone_mode),
            strict_line_profile=bool(ring_strict_line_profile),
            arc_only_mode=bool(ring_arc_only_mode),
            camera_tracking_mode=str(camera_tracking_mode),
        )
    cap.release()
    if visualize:
        cv2.destroyAllWindows()
    if progress is not None:
        progress.finish(f"rings={len(rings_rows)}")
    print(
        "  [stage] enrich_done "
        f"teams={len(teams_rows)} rings={len(rings_rows)} "
        f"snapshot_obs={enrichment_diag.get('snapshot_observations', 0)} "
        f"timeline_obs={enrichment_diag.get('timeline_observations', 0)}"
    )
    teams_json = json.dumps(teams_rows, ensure_ascii=False)
    text_report = {
        "video": {
            "name": video_path.name,
            "path": str(video_path),
            "detected_map_name": start_map_name,
            "detected_map_mp_id": start_mp_id,
            "start_timestamp_sec": None if start_ts is None else round(float(max(0.0, start_ts)), 3),
            "center_pov_start_sec": None if first_center_pov_sec is None else round(float(first_center_pov_sec), 3),
            "pov_screenshot_timestamp_sec": None if screenshot_target_sec is None else round(float(screenshot_target_sec), 3),
            "pov_screenshot_path": screenshot_rel_path,
        },
        "zones": {
            "source_file": zones_payload_all.get("file") if zones_payload_all else None,
            "map": zones_payload_all.get("map") if zones_payload_all else "all_maps",
            "image_size": zones_payload_all.get("image_size") if zones_payload_all else {"width": 0, "height": 0},
            "items": zones_payload_all.get("zones", []) if zones_payload_all else [],
        },
        "observations": text_observations,
        "summary": {
            "most_confident_lines": summary_lines,
            "observations_count": len(text_observations),
            "zone_map_candidate": {
                "map_name": zone_map_name,
                "avg_confidence": round(float(zone_map_avg_conf), 4) if zone_map_name else 0.0,
                "hits": int(zone_map_hits),
            },
            "teams": teams_rows,
            "rings": rings_rows,
            "camreman": camreman_rows,
            "camera_tracking": camera_rows,
            "enrichment": enrichment_diag,
        },
    }
    (text_json_dir / f"{video_path.stem}.text_observations.json").write_text(
        json.dumps(text_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if start_ts is not None:
        if debug:
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / f"{video_path.stem}.trace.json").write_text(
                json.dumps(trace, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return {
            "video_name": video_path.name,
            "video_path": str(video_path),
            "map_name": start_map_name,
            "map_mp_id": start_mp_id,
            "start_timestamp_sec": float(max(0.0, start_ts)),
            "confidence": float(start_conf),
            "status": "ok",
            "notes": (
                f"stable_for_{stable_target:.1f}s;"
                f"coarse{coarse_jump}_back{rollback_step}_refine{refine_window}"
            )
            + text_zone_note,
            "teams_json": teams_json,
            "teams_rows": teams_rows,
            "rings_rows": rings_rows,
            "camreman_rows": camreman_rows,
            "camera_rows": camera_rows,
            "visualize_action": None,
        }

    if debug:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"{video_path.stem}.trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    note = "no_stable_map_camera_5s" + text_zone_note
    if zone_map_name and zone_map_hits >= 2:
        note += f";zone_map_candidate={zone_map_name};zone_hits={zone_map_hits}"
    if pytesseract is None:
        note += ";pytesseract_missing"
    return {
        "video_name": video_path.name,
        "video_path": str(video_path),
        "map_name": None,
        "map_mp_id": None,
        "start_timestamp_sec": None,
        "confidence": 0.0,
        "status": "not_confident",
        "notes": note,
        "teams_json": teams_json,
        "teams_rows": teams_rows,
        "rings_rows": rings_rows,
        "camreman_rows": camreman_rows,
        "camera_rows": camera_rows,
        "visualize_action": None,
    }


def main() -> int:
    args = parse_args()
    global CONTROL_FILE_PATH
    CONTROL_FILE_PATH = Path(args.control_file) if args.control_file else None
    setattr(render_visualization, "_delay_ms", max(1, int(args.visualize_delay_ms)))
    setattr(render_visualization, "_calib_lock_ratio", bool(args.calib_lock_ratio_r1_r2))
    setattr(
        render_visualization,
        "_disable_calibration_ui",
        bool(args.disable_calibration_ui or args.ring_strict_line_profile),
    )
    if bool(args.fast_approx):
        if bool(args.fast_approx_small_steps):
            args.frame_step = max(int(args.frame_step), 120)
            args.start_refine_step_frames = min(int(args.start_refine_step_frames), 2)
            args.coarse_jump_frames = min(int(args.coarse_jump_frames), 180)
        else:
            args.frame_step = max(int(args.frame_step), 120)
            args.start_refine_step_frames = max(int(args.start_refine_step_frames), 30)
        args.stable_seconds = min(float(args.stable_seconds), 2.0)
        if bool(args.fast_approx_small_steps):
            args.ring_coarse_sec = min(float(args.ring_coarse_sec), 1.0)
            args.ring_rollback_sec = min(float(args.ring_rollback_sec), 1.0)
            args.ring_refine_window_sec = min(float(args.ring_refine_window_sec), 2.0)
            args.ring_refine_step_sec = min(float(args.ring_refine_step_sec), 0.25)
            args.ring_geometry_step_sec = min(float(args.ring_geometry_step_sec), 0.25)
            args.elim_coarse_sec = min(float(args.elim_coarse_sec), 1.0)
            args.elim_refine_sec = min(float(args.elim_refine_sec), 1.0)
            args.elim_refine_step_sec = min(float(args.elim_refine_step_sec), 0.25)
        else:
            args.ring_coarse_sec = max(float(args.ring_coarse_sec), 5.0)
            args.ring_rollback_sec = max(float(args.ring_rollback_sec), 5.0)
            args.ring_refine_window_sec = max(float(args.ring_refine_window_sec), 5.0)
            args.ring_refine_step_sec = max(float(args.ring_refine_step_sec), 1.0)
            args.ring_geometry_step_sec = max(float(args.ring_geometry_step_sec), 1.0)
            args.elim_coarse_sec = max(float(args.elim_coarse_sec), 5.0)
            args.elim_refine_sec = max(float(args.elim_refine_sec), 5.0)
            args.elim_refine_step_sec = max(float(args.elim_refine_step_sec), 1.0)
        args.ring_stable_seconds = min(float(args.ring_stable_seconds), 1.0)
        args.ring_geometry_window_seconds = max(float(args.ring_geometry_window_seconds), 3.0)
        print(
            "[detect] profile=fast_approx "
            f"frame_step={args.frame_step} start_refine_step={args.start_refine_step_frames} "
            f"coarse_jump={args.coarse_jump_frames} "
            f"elim_coarse={args.elim_coarse_sec}s ring_coarse={args.ring_coarse_sec}s "
            f"small_steps={'on' if bool(args.fast_approx_small_steps) else 'off'}"
        )
    if bool(args.ring_countdown_zone_mode):
        print("[detect] ring geometry mode: countdown-zone anchor enabled")
    if bool(args.ring_arc_only_mode):
        print("[detect] ring geometry mode: arc-only (legacy detectors disabled)")
    if bool(args.disable_ring_detection):
        print("[detect] ring detection disabled")
    if bool(args.disable_camera_tracking):
        print("[detect] camera tracking disabled")
    if str(args.camera_tracking_mode) != "geometry":
        print(f"[detect] camera tracking mode: {args.camera_tracking_mode}")
    if bool(args.visualize_no_ocr):
        print("[detect] visualize mode: OCR disabled")
    post_run_compare_enabled = bool(args.post_run_compare_view) and (not bool(args.visualize))
    if bool(args.post_run_compare_view) and bool(args.visualize):
        print("[detect] post-run compare disabled while --visualize is active")
    records_dir = Path(args.records_dir)
    if not records_dir.is_absolute():
        records_dir = PROJECT_ROOT / records_dir
    db_path = Path(args.db_path)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    debug_dir = Path(args.debug_dir)
    if not debug_dir.is_absolute():
        debug_dir = PROJECT_ROOT / debug_dir
    text_json_dir = Path(args.text_json_dir)
    if not text_json_dir.is_absolute():
        text_json_dir = PROJECT_ROOT / text_json_dir
    pov_screenshot_dir = Path(args.pov_screenshot_dir)
    if not pov_screenshot_dir.is_absolute():
        pov_screenshot_dir = PROJECT_ROOT / pov_screenshot_dir

    videos = iter_videos(records_dir, args.video)
    if not videos:
        print("[detect] no videos found.")
        return 1

    print(f"[detect] videos to process: {len(videos)}")
    print(f"[detect] db: {db_path}")
    default_db_path = PROJECT_ROOT / "output" / "map_start_detection.sqlite"
    print(f"[detect] mirror db: {default_db_path}")
    camera_db_path = PROJECT_ROOT / "output" / "camera.sqlite"
    print(f"[detect] camera db: {camera_db_path}")

    conn: sqlite3.Connection | None = None
    mirror_conn: sqlite3.Connection | None = None
    camera_conn: sqlite3.Connection | None = None
    if not args.dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        init_db(conn)
        camera_db_path.parent.mkdir(parents=True, exist_ok=True)
        camera_conn = sqlite3.connect(camera_db_path)
        ct.init_db(camera_conn)
        default_db_path.parent.mkdir(parents=True, exist_ok=True)
        if default_db_path.resolve() != db_path.resolve():
            mirror_conn = sqlite3.connect(default_db_path)
            init_db(mirror_conn)

    processed = 0
    video_workers = max(1, int(args.video_workers))
    if bool(args.visualize) and video_workers != 1:
        print("[detect] --visualize requires single worker, forcing --video-workers=1")
        video_workers = 1
    if video_workers == 1:
        idx = 0
        while 0 <= idx < len(videos):
            video_path = videos[idx]
            print(f"[detect {idx + 1}/{len(videos)}] {video_path.name}")
            result = analyze_video_task(
                video_path=video_path,
                args=args,
                debug_dir=debug_dir,
                text_json_dir=text_json_dir,
                pov_screenshot_dir=pov_screenshot_dir,
                enable_progress=True,
            )
            print(
                "  -> "
                f"status={result['status']} map={result['map_name']} mp_id={result['map_mp_id']} "
                f"start={result['start_timestamp_sec']} conf={result['confidence']:.3f}"
            )
            visualize_action = str(result.get("visualize_action") or "")
            should_persist = str(result.get("status") or "") != "interrupted_visualize"
            if conn is not None and should_persist:
                persist_result(
                    conn,
                    result,
                    force_clear_rings=bool(args.force_clear_rings),
                    rings_only=bool(args.persist_rings_only),
                )
                if mirror_conn is not None:
                    persist_result(
                        mirror_conn,
                        result,
                        force_clear_rings=bool(args.force_clear_rings),
                        rings_only=bool(args.persist_rings_only),
                    )
                if camera_conn is not None and (not bool(args.disable_camera_tracking)):
                    persist_camera_result(camera_conn, conn, result)
            if post_run_compare_enabled and should_persist:
                replay_post_run_compare_view(
                    video_path=video_path,
                    map_mp_id=result.get("map_mp_id"),
                    rings_rows=list(result.get("rings_rows", [])),
                    playback_fps=float(args.post_run_compare_fps),
                )
            processed += 1
            if bool(args.visualize) and visualize_action in {"next", "prev", "quit"}:
                if visualize_action == "next":
                    idx += 1
                    continue
                if visualize_action == "prev":
                    idx = max(0, idx - 1)
                    continue
                break
            idx += 1
    else:
        print(f"[detect] parallel video workers: {video_workers}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=video_workers) as executor:
            future_to_video: dict[concurrent.futures.Future[dict[str, Any]], Path] = {}
            for video_path in videos:
                future = executor.submit(
                    analyze_video_task,
                    video_path=video_path,
                    args=args,
                    debug_dir=debug_dir,
                    text_json_dir=text_json_dir,
                    pov_screenshot_dir=pov_screenshot_dir,
                    enable_progress=False,
                )
                future_to_video[future] = video_path
            for idx, future in enumerate(concurrent.futures.as_completed(future_to_video), start=1):
                video_path = future_to_video[future]
                result = future.result()
                print(
                    f"[detect {idx}/{len(videos)}] {video_path.name} -> "
                    f"status={result['status']} map={result['map_name']} mp_id={result['map_mp_id']} "
                    f"start={result['start_timestamp_sec']} conf={result['confidence']:.3f}"
                )
                should_persist = str(result.get("status") or "") != "interrupted_visualize"
                if conn is not None and should_persist:
                    persist_result(
                        conn,
                        result,
                        force_clear_rings=bool(args.force_clear_rings),
                        rings_only=bool(args.persist_rings_only),
                    )
                    if mirror_conn is not None:
                        persist_result(
                            mirror_conn,
                            result,
                            force_clear_rings=bool(args.force_clear_rings),
                            rings_only=bool(args.persist_rings_only),
                        )
                    if camera_conn is not None and (not bool(args.disable_camera_tracking)):
                        persist_camera_result(camera_conn, conn, result)
                if post_run_compare_enabled and should_persist:
                    replay_post_run_compare_view(
                        video_path=video_path,
                        map_mp_id=result.get("map_mp_id"),
                        rings_rows=list(result.get("rings_rows", [])),
                        playback_fps=float(args.post_run_compare_fps),
                    )
                processed += 1

    if conn is not None:
        conn.close()
    if mirror_conn is not None:
        mirror_conn.close()
    if camera_conn is not None:
        camera_conn.close()
    print(f"[detect] done: {processed} video(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

