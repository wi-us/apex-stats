from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    import pytesseract
except Exception:
    pytesseract = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]

MAP_LABEL_TO_MP_ID: dict[str, str] = {
    "OLYMPUS": "mp_olympus",
    "WORLDS EDGE": "mp_worlds_edge",
    "WORLD'S EDGE": "mp_worlds_edge",
    "STORM POINT": "mp_storm_point",
    "E DISTRICT": "mp_e_district",
    "E-DISTRICT": "mp_e_district",
}

MAP_ALIASES: dict[str, list[str]] = {
    "OLYMPUS": ["OLYMPUS"],
    "WORLD'S EDGE": ["WORLD'S EDGE", "WORLDS EDGE", "WORLD S EDGE", "WORLDS"],
    "STORM POINT": ["STORM POINT", "STORMPOINT", "STORM"],
    "E-DISTRICT": ["E-DISTRICT", "E DISTRICT", "EDISTRICT", "DISTRICT"],
}


@dataclass
class FrameSignal:
    map_label: str | None
    map_conf: float
    is_map_camera: bool
    camera_conf: float
    cond_conf: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect map camera start timestamps in ALGS VOD clips.")
    parser.add_argument("--records-dir", default="ffmpeg_downloader/records", help="Folder with mp4 clips.")
    parser.add_argument("--video", default=None, help="Optional single video path.")
    parser.add_argument("--db-path", default="output/map_start_detection.sqlite", help="Output SQLite path.")
    parser.add_argument("--frame-step", type=int, default=3, help="Analyze every N-th frame.")
    parser.add_argument("--coarse-jump-frames", type=int, default=3000, help="Fast-forward jump when no confidence at start.")
    parser.add_argument("--rollback-step-frames", type=int, default=100, help="Rollback step for backward search from first confident frame.")
    parser.add_argument("--refine-window-frames", type=int, default=300, help="Refinement window (+/- frames) around rollback anchor.")
    parser.add_argument("--stable-seconds", type=float, default=5.0, help="Required stable duration of both conditions.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write DB.")
    parser.add_argument("--debug", action="store_true", help="Save debug frames and JSON traces.")
    parser.add_argument("--visualize", action="store_true", help="Show live detection overlay window.")
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
        default=1,
        help="Limit enabled OCR zones to first N (default: 1 for fast iteration).",
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
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
        CREATE INDEX IF NOT EXISTS idx_teams_game_id ON Teams(game_id);
        CREATE INDEX IF NOT EXISTS idx_rings_game_id ON Rings(game_id);
        CREATE INDEX IF NOT EXISTS idx_rings_game_ring ON Rings(game_id, ring_number);
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


def upsert_rings_rows(conn: sqlite3.Connection, game_id: int, rings_rows: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM Rings WHERE game_id = ?", (int(game_id),))
    if not rings_rows:
        return
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
    zone_file = resolve_text_zones_file(explicit_file)
    if zone_file is None or not zone_file.exists():
        return None
    try:
        payload = json.loads(zone_file.read_text(encoding="utf-8"))
    except Exception:
        return None
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
        x = max(0, min(int(zone.get("x", 0) or 0), w - 1))
        y = max(0, min(int(zone.get("y", 0) or 0), h - 1))
        zw = max(1, int(zone.get("width", 1) or 1))
        zh = max(1, int(zone.get("height", 1) or 1))
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
            data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT, config="--psm 6")
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


def parse_ring_event(text: str) -> tuple[int | None, str | None]:
    norm = normalize_text(text or "")
    if not norm:
        return None, None
    compact = norm.replace(" ", "")
    m = re.search(r"RING(\d+)(CLOSING|COUNTDOWN)", compact)
    if m:
        try:
            return int(m.group(1)), str(m.group(2)).lower()
        except Exception:
            return None, None
    m2 = re.search(r"RING\s*(\d+)\s*(CLOSING|COUNTDOWN)", norm)
    if m2:
        try:
            return int(m2.group(1)), str(m2.group(2)).lower()
        except Exception:
            return None, None
    return None, None


def detect_ring_geometry_in_frame(frame: np.ndarray, zones_payload: dict[str, Any] | None) -> tuple[dict[str, Any] | None, float]:
    x1, y1, x2, y2 = pick_zone_pov_bounds(frame, zones_payload)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None, 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    ring_hsv_lower = np.array([0, 0, 67], dtype=np.uint8)
    ring_hsv_upper = np.array([180, 68, 89], dtype=np.uint8)
    mask_hsv = cv2.inRange(hsv, ring_hsv_lower, ring_hsv_upper)
    mask_gray = cv2.inRange(gray, 68, 88)
    mask = cv2.bitwise_and(mask_hsv, mask_gray)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (1, 1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    ring_like = cv2.GaussianBlur(mask, (13, 13), 1.6)
    circles = cv2.HoughCircles(
        ring_like,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(20, roi.shape[1] // 6),
        param1=90,
        param2=100,
        minRadius=max(5, int(roi.shape[1] * 0.04)),
        maxRadius=max(10, int(roi.shape[1] * 0.52)),
    )
    if circles is None:
        return None, 0.0
    best: dict[str, Any] | None = None
    best_score = -1e9
    for circle in circles[0]:
        cx = float(circle[0])
        cy = float(circle[1])
        radius = float(circle[2])
        area = float(np.pi * (radius ** 2))
        area_ratio = area / max(1.0, float(roi.shape[0] * roi.shape[1]))
        if area_ratio < 0.01 or area_ratio > 0.70:
            continue
        score = radius - (np.hypot(cx - (roi.shape[1] / 2.0), cy - (roi.shape[0] / 2.0)) * 0.10)
        if score > best_score:
            best_score = score
            best = {
                "x": round(float(x1 + cx), 2),
                "y": round(float(y1 + cy), 2),
                "radius": round(float(radius), 2),
            }
    if best is None:
        return None, 0.0
    conf = float(np.clip((best["radius"] / max(1.0, roi.shape[1])) * 2.5, 0.2, 1.0))
    return best, conf


def collect_enrichment_data(
    video_path: Path,
    fps: float,
    total_frames: int,
    frame_step: int,
    start_ts: float | None,
    zones_payload_full: dict[str, Any] | None,
    text_ocr_min_confidence: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if start_ts is None or zones_payload_full is None or pytesseract is None:
        return [], [], {"snapshot_observations": 0, "timeline_observations": 0}

    start_frame = max(0, int(float(start_ts) * fps))
    max_frame = max(0, total_frames - 1) if total_frames > 0 else start_frame
    snapshot_target = min(max_frame, max(0, int((float(start_ts) + 5.0) * fps)))
    snapshot_from = max(start_frame, snapshot_target - 300)
    snapshot_to = min(max_frame, snapshot_target + 300)
    snapshot_step = max(1, int(max(frame_step, 15)))
    scan_step = max(1, int(max(frame_step, 30)))

    name_labels = {f"t{i}_name" for i in range(1, 21)}
    elim_labels = {f"t{i}_iseliminated" for i in range(1, 21)}
    ring_labels = {"is_ringclosing"}
    payload_names = clone_payload_with_selected_zones(zones_payload_full, name_labels | elim_labels)
    payload_timeline = clone_payload_with_selected_zones(zones_payload_full, elim_labels | ring_labels)
    payload_ring = clone_payload_with_selected_zones(zones_payload_full, ring_labels)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [], [], {"snapshot_observations": 0, "timeline_observations": 0}

    snapshot_obs: list[dict[str, Any]] = []
    if payload_names is not None:
        for frame_idx in range(snapshot_from, snapshot_to + 1, snapshot_step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            snapshot_obs.extend(
                run_zone_ocr(
                    frame=frame,
                    frame_idx=int(frame_idx),
                    fps=fps,
                    zones_payload=payload_names,
                    min_confidence=max(0.0, float(text_ocr_min_confidence)),
                )
            )

    timeline_obs: list[dict[str, Any]] = []
    if payload_timeline is not None:
        for frame_idx in range(start_frame, max_frame + 1, scan_step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            timeline_obs.extend(
                run_zone_ocr(
                    frame=frame,
                    frame_idx=int(frame_idx),
                    fps=fps,
                    zones_payload=payload_timeline,
                    min_confidence=max(0.0, float(text_ocr_min_confidence)),
                )
            )

    cap.release()

    team_state: dict[int, dict[str, Any]] = {
        i: {"team_name": None, "is_eliminated": False, "time_eliminated": None}
        for i in range(1, 21)
    }
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
                if team_state[slot]["time_eliminated"] is None:
                    team_state[slot]["time_eliminated"] = round(ts, 3)

    for item in sorted(timeline_obs, key=lambda x: float(x.get("timestamp_sec", 0.0) or 0.0)):
        slot, field = extract_team_slot(str(item.get("zone_label") or ""))
        if slot is None or field != "iseliminated" or slot not in team_state:
            continue
        if not is_eliminated_text(str(item.get("normalized_text") or "")):
            continue
        ts = float(item.get("timestamp_sec", 0.0) or 0.0)
        team_state[slot]["is_eliminated"] = True
        if team_state[slot]["time_eliminated"] is None or ts < float(team_state[slot]["time_eliminated"]):
            team_state[slot]["time_eliminated"] = round(ts, 3)

    teams_rows: list[dict[str, Any]] = []
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

    closing_by_ring: dict[int, float] = {}
    countdown_by_ring: dict[int, float] = {}
    for item in sorted(timeline_obs, key=lambda x: float(x.get("timestamp_sec", 0.0) or 0.0)):
        label = str(item.get("zone_label") or "").strip().lower()
        if label != "is_ringclosing":
            continue
        ring_num, event_type = parse_ring_event(str(item.get("normalized_text") or ""))
        if ring_num is None or event_type is None:
            continue
        ts = float(item.get("timestamp_sec", 0.0) or 0.0)
        if event_type == "closing":
            if ring_num not in closing_by_ring:
                closing_by_ring[ring_num] = ts
        elif event_type == "countdown":
            if ring_num not in countdown_by_ring:
                countdown_by_ring[ring_num] = ts

    ring_numbers = sorted(closing_by_ring.keys())
    rings_rows: list[dict[str, Any]] = []
    if ring_numbers and payload_ring is not None:
        cap_ring = cv2.VideoCapture(str(video_path))
        for ring_number in ring_numbers:
            ts_start = float(closing_by_ring.get(ring_number))
            ts_end = countdown_by_ring.get(ring_number + 1)
            center_json = None
            radius = None
            if cap_ring.isOpened():
                frame_idx = max(0, int(ts_start * fps))
                cap_ring.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ok, frame = cap_ring.read()
                if ok and frame is not None:
                    geom, _ = detect_ring_geometry_in_frame(frame, zones_payload_full)
                    if geom is not None:
                        center_json = json.dumps({"x": geom["x"], "y": geom["y"]}, ensure_ascii=False)
                        radius = geom["radius"]
            rings_rows.append(
                {
                    "ring_number": int(ring_number),
                    "center": center_json,
                    "radius": radius,
                    "time_start": round(ts_start, 3),
                    "time_end": None if ts_end is None else round(float(ts_end), 3),
                }
            )
        cap_ring.release()

    diagnostics = {
        "snapshot_observations": len(snapshot_obs),
        "timeline_observations": len(timeline_obs),
        "rings_detected": len(rings_rows),
    }
    return teams_rows, rings_rows, diagnostics


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
    image_size = zones_payload.get("image_size", {})
    src_w = max(1, int((image_size or {}).get("width", w) or w))
    src_h = max(1, int((image_size or {}).get("height", h) or h))
    sx = w / float(src_w)
    sy = h / float(src_h)
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        if zone.get("enabled", True) is False:
            continue
        label = str(zone.get("label") or "").strip().lower()
        if "pov" not in label:
            continue
        x = int(max(0, zone.get("x", 0) or 0) * sx)
        y = int(max(0, zone.get("y", 0) or 0) * sy)
        zw = int(max(1, zone.get("width", 1) or 1) * sx)
        zh = int(max(1, zone.get("height", 1) or 1) * sy)
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
    window_name: str = "detect_map_start",
) -> bool:
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

    # Text-zones.
    if zones_payload is not None:
        zones = zones_payload.get("zones", [])
        img_size = zones_payload.get("image_size", {})
        src_w = max(1, int((img_size or {}).get("width", w) or w))
        src_h = max(1, int((img_size or {}).get("height", h) or h))
        sx = w / float(src_w)
        sy = h / float(src_h)
        if isinstance(zones, list):
            for zone in zones:
                if not isinstance(zone, dict):
                    continue
                x = int(max(0, zone.get("x", 0) or 0) * sx)
                y = int(max(0, zone.get("y", 0) or 0) * sy)
                zw = int(max(1, zone.get("width", 1) or 1) * sx)
                zh = int(max(1, zone.get("height", 1) or 1) * sy)
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
    line2 = f"cond1={cond1_ok} cond2={cond2_ok} both={both_ok}  [Q/ESC to stop]"
    cv2.putText(overlay, line1, (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    cv2.putText(overlay, line2, (14, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.58, status_color, 2)

    cv2.imshow(window_name, overlay)
    key = cv2.waitKey(1) & 0xFF
    return key in (27, ord("q"), ord("Q"))


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
    visualize_window: str = "detect_map_start",
) -> tuple[FrameSignal | None, np.ndarray | None, dict[str, Any] | None, list[dict[str, Any]], bool]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    if not ok:
        return None, prev_center_gray, None, [], False
    timestamp = frame_idx / fps if fps > 0 else 0.0
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
    stop_requested = False
    if visualize:
        stop_requested = render_visualization(
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
            window_name=visualize_window,
        )
    return signal, curr_gray, trace_item, text_observations, stop_requested


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
) -> tuple[float | None, str | None, str | None, float, float | None, bool]:
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
    while frame_idx <= frame_end:
        signal, prev_center_gray, trace_item, obs, stop_requested = evaluate_frame_signal(
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
        )
        if signal is None:
            break
        if stop_requested:
            return None, accepted_map_name, accepted_map_mp_id, 0.0, first_center_pov_sec, True
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
            return stable_start_sec, accepted_map_name, accepted_map_mp_id, avg_conf, first_center_pov_sec, False
        frame_idx += scan_step
    return None, accepted_map_name, accepted_map_mp_id, 0.0, first_center_pov_sec, False


def analyze_video(
    video_path: Path,
    frame_step: int,
    coarse_jump_frames: int,
    rollback_step_frames: int,
    refine_window_frames: int,
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
) -> dict[str, Any]:
    print(f"  [stage] scan_start video={video_path.name}")
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
        }

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(frame_step))
    stable_target = max(0.5, float(stable_seconds))
    coarse_jump = max(1, int(coarse_jump_frames))
    rollback_step = max(1, int(rollback_step_frames))
    refine_window = max(1, int(refine_window_frames))

    trace: list[dict[str, Any]] = []
    text_observations: list[dict[str, Any]] = []
    first_center_pov_sec: float | None = None
    zones_payload = load_text_zones(text_zones_file, max_enabled=text_zones_max_enabled)
    zones_payload_full = load_text_zones(text_zones_file, max_enabled=5000)
    text_zone_note = "" if zones_payload else ";text_zones_missing_or_empty"
    # Fast strategy requested by user:
    # 1) If no confidence at start, jump +3000 frames to find first confident frame.
    # 2) Roll back by 100 frames to locate earlier boundary.
    # 3) Refine inside +/-300 frame window.
    start_ts, start_map_name, start_mp_id, start_conf, first_center_pov_sec, interrupted = find_start_in_window(
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
        zones_payload=zones_payload,
        text_observations=text_observations,
        text_ocr_min_confidence=text_ocr_min_confidence,
        first_center_pov_sec_hint=first_center_pov_sec,
        visualize=visualize,
    )
    if interrupted:
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
            "notes": "stopped_by_user_visualize",
            "teams_json": "[]",
            "teams_rows": [],
            "rings_rows": [],
        }

    if start_ts is None:
        ref_cache: dict[str, np.ndarray | None] = {}
        prev_gray: np.ndarray | None = None
        first_confident_frame: int | None = None
        frame_idx = 0
        max_frame = max(0, total_frames - 1) if total_frames > 0 else 0
        while frame_idx <= max_frame:
            signal, prev_gray, trace_item, obs, stop_requested = evaluate_frame_signal(
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
                zones_payload=zones_payload,
                text_ocr_min_confidence=text_ocr_min_confidence,
                visualize=visualize,
            )
            if signal is None:
                break
            if stop_requested:
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

        if first_confident_frame is not None:
            anchor = first_confident_frame
            while anchor > 0:
                probe = max(0, anchor - rollback_step)
                signal, _, trace_item, obs, stop_requested = evaluate_frame_signal(
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
                    zones_payload=zones_payload,
                    text_ocr_min_confidence=text_ocr_min_confidence,
                    visualize=visualize,
                )
                if stop_requested:
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

            refine_start = max(0, anchor - refine_window)
            refine_end = min(max_frame, anchor + refine_window)
            start_ts, start_map_name, start_mp_id, start_conf, first_center_pov_sec, interrupted = find_start_in_window(
                cap=cap,
                start_frame=refine_start,
                end_frame=refine_end,
                step=max(1, min(step, 3)),
                fps=fps,
                stable_target=stable_target,
                ocr_min_conf=ocr_min_conf,
                camera_min_conf=camera_min_conf,
                debug=debug,
                debug_dir=debug_dir,
                debug_video_stem=video_path.stem,
                debug_save_every=debug_save_every,
                trace=trace,
                zones_payload=zones_payload,
                text_observations=text_observations,
                text_ocr_min_confidence=text_ocr_min_confidence,
                first_center_pov_sec_hint=first_center_pov_sec,
                visualize=visualize,
            )
            if interrupted:
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
                    "notes": "stopped_by_user_visualize",
                    "teams_json": "[]",
                    "teams_rows": [],
                    "rings_rows": [],
                }

    cap.release()
    if visualize:
        cv2.destroyAllWindows()
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
    print(f"  [stage] enrich_teams_rings video={video_path.name}")
    teams_rows, rings_rows, enrichment_diag = collect_enrichment_data(
        video_path=video_path,
        fps=fps,
        total_frames=total_frames,
        frame_step=step,
        start_ts=start_ts,
        zones_payload_full=zones_payload_full,
        text_ocr_min_confidence=text_ocr_min_confidence,
    )
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
            "source_file": zones_payload.get("file") if zones_payload else None,
            "map": zones_payload.get("map") if zones_payload else "all_maps",
            "image_size": zones_payload.get("image_size") if zones_payload else {"width": 0, "height": 0},
            "items": zones_payload.get("zones", []) if zones_payload else [],
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
    }


def main() -> int:
    args = parse_args()
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

    conn: sqlite3.Connection | None = None
    if not args.dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        init_db(conn)

    processed = 0
    for idx, video_path in enumerate(videos, start=1):
        print(f"[detect {idx}/{len(videos)}] {video_path.name}")
        result = analyze_video(
            video_path=video_path,
            frame_step=args.frame_step,
            coarse_jump_frames=args.coarse_jump_frames,
            rollback_step_frames=args.rollback_step_frames,
            refine_window_frames=args.refine_window_frames,
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
        )
        print(
            "  -> "
            f"status={result['status']} map={result['map_name']} mp_id={result['map_mp_id']} "
            f"start={result['start_timestamp_sec']} conf={result['confidence']:.3f}"
        )
        if conn is not None:
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
            upsert_rings_rows(conn, game_id=game_id, rings_rows=list(result.get("rings_rows", [])))
            conn.commit()
        processed += 1

    if conn is not None:
        conn.close()
    print(f"[detect] done: {processed} video(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

