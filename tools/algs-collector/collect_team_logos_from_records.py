"""
Collect team logos from match records and resolve canonical team names.

Data flow:
1) Iterate *.text_observations.json from output/map_start_text.
2) Detect slot tags (prefer OCR from t*_tag/t*_name, fallback to summary.teams).
3) Crop t*_logo zones from record video frames.
4) Match each slot to a UNIQUE source team logo (one-to-one per match).
5) Store per-match slot rows and a compatibility Teams(name, tag, logo) table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORDS_DIR = ROOT / "ffmpeg_downloader" / "records"
DEFAULT_MAP_START_TEXT_DIR = ROOT / "output" / "map_start_text"
DEFAULT_TARGET_DB = ROOT / "output" / "teams_name_tag_logo.sqlite"
DEFAULT_SOURCE_DB = ROOT / "output" / "algs_tournaments.sqlite"

# Match t{number} inside labels like "t1_name", "t20_logo", "foo_t3_bar".
# We only block letters/digits around token; underscore is allowed.
TEAM_SLOT_RE = re.compile(r"(?<![A-Za-z0-9])t(\d+)(?!\d)", re.IGNORECASE)


@dataclass
class SourceTeamLogo:
    name: str
    feature: np.ndarray
    logo_bytes: bytes


def norm_tag(value: str | None) -> str:
    return str(value or "").strip().upper()


def clean_tag_text(value: str | None) -> str:
    text = norm_tag(value)
    text = text.replace("|", "I")
    text = re.sub(r"\s+", " ", text).strip()
    # Keep tags compact; remove noisy OCR punctuation.
    text = re.sub(r"[^A-Z0-9\- ]+", "", text)
    return text


def extract_slot_from_label(label: str | None) -> int | None:
    m = TEAM_SLOT_RE.search(str(label or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def zone_is_logo_candidate(label: str | None) -> bool:
    text = str(label or "").lower()
    slot = extract_slot_from_label(text)
    if slot is None:
        return False
    if "iseliminated" in text:
        return False
    # Collect only dedicated logo zones (e.g. t1_logo ... t20_logo).
    return "logo" in text


def make_logo_feature(image_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
    return resized.astype(np.float32) / 255.0


def image_score(image_bgr: np.ndarray) -> float:
    # Prefer sharp/non-empty crops.
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_32F).var())


def choose_logo_crop(zone_crop: np.ndarray, label: str) -> np.ndarray:
    if zone_crop.size == 0:
        return zone_crop
    # Label parameter kept for compatibility/future crop variants.
    _ = label
    return zone_crop


def encode_png_bytes(image_bgr: np.ndarray) -> bytes | None:
    ok, enc = cv2.imencode(".png", image_bgr)
    if not ok:
        return None
    return enc.tobytes()


def sha256_bytes(payload: bytes | None) -> str | None:
    if not payload:
        return None
    return hashlib.sha256(payload).hexdigest()


def resolve_video_path(payload: dict, records_dir: Path) -> Path | None:
    video_obj = payload.get("video", {}) if isinstance(payload, dict) else {}
    explicit_path = Path(str(video_obj.get("path") or "")).expanduser()
    if explicit_path.exists():
        return explicit_path
    name = str(video_obj.get("name") or "")
    if name:
        candidate = records_dir / name
        if candidate.exists():
            return candidate
    return None


def frame_time_candidates(payload: dict) -> list[float]:
    video_obj = payload.get("video", {}) if isinstance(payload, dict) else {}
    base = float(video_obj.get("start_timestamp_sec") or 0.0)
    pov = float(video_obj.get("pov_screenshot_timestamp_sec") or (base + 3.0))
    times = [base, base + 1.0, base + 2.0, pov, pov + 1.5, pov + 3.0]
    return sorted({max(0.0, float(t)) for t in times})


def load_source_team_logos(source_db_path: Path) -> list[SourceTeamLogo]:
    conn = sqlite3.connect(str(source_db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT name, logo FROM teams WHERE logo IS NOT NULL").fetchall()
    out: list[SourceTeamLogo] = []
    for row in rows:
        blob = row["logo"]
        if not isinstance(blob, (bytes, memoryview)):
            continue
        img = cv2.imdecode(np.frombuffer(bytes(blob), np.uint8), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            continue
        out.append(
            SourceTeamLogo(
                name=str(row["name"] or "").strip(),
                feature=make_logo_feature(img),
                logo_bytes=bytes(blob),
            )
        )
    conn.close()
    return out


def hungarian_min_cost(cost: list[list[float]]) -> list[int]:
    """Return assignment row->col for rectangular matrix (min cost)."""
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    if m == 0:
        return [-1] * n
    # Algorithm requires columns >= rows; pad with expensive dummy columns if needed.
    pad_cols = 0
    if m < n:
        pad_cols = n - m
        for row in cost:
            row.extend([1e6] * pad_cols)
        m = len(cost[0])
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j] == 0:
            continue
        row = p[j] - 1
        col = j - 1
        if pad_cols and col >= (m - pad_cols):
            assignment[row] = -1
        else:
            assignment[row] = col
    return assignment


def assign_unique_matches(
    slot_features: dict[int, np.ndarray],
    source_teams: list[SourceTeamLogo],
) -> dict[int, tuple[SourceTeamLogo, float]]:
    if not slot_features or not source_teams:
        return {}
    slots = sorted(slot_features.keys())
    cost: list[list[float]] = []
    for slot in slots:
        feature = slot_features[slot]
        row = [float(np.mean(np.abs(feature - team.feature))) for team in source_teams]
        cost.append(row)
    assignment = hungarian_min_cost([r[:] for r in cost])
    out: dict[int, tuple[SourceTeamLogo, float]] = {}
    for row_idx, col_idx in enumerate(assignment):
        if col_idx < 0:
            continue
        slot = slots[row_idx]
        out[slot] = (source_teams[col_idx], cost[row_idx][col_idx])
    return out


def extract_slot_tags(payload: dict) -> tuple[dict[int, str], dict[int, str]]:
    """Return (slot->tag, slot->tag_source)."""
    slot_to_tag: dict[int, str] = {}
    slot_to_source: dict[int, str] = {}
    ocr_tags = extract_ocr_slot_tags(payload)
    for slot, (text, _confidence, _zone_label) in ocr_tags.items():
        slot_to_tag[slot] = text
        slot_to_source[slot] = "observation"

    teams_summary = payload.get("summary", {}).get("teams", [])
    if isinstance(teams_summary, list):
        for team in teams_summary:
            if not isinstance(team, dict):
                continue
            slot = team.get("team_slot")
            if not isinstance(slot, int):
                continue
            if slot in slot_to_tag:
                continue
            tag = clean_tag_text(team.get("team_name"))
            if tag:
                slot_to_tag[slot] = tag
                slot_to_source[slot] = "summary"

    return slot_to_tag, slot_to_source


def extract_ocr_slot_tags(payload: dict) -> dict[int, tuple[str, float, str]]:
    """Return slot->(tag, confidence, zone_label) from OCR observations only."""
    best_score: dict[int, float] = {}
    out: dict[int, tuple[str, float, str]] = {}

    observations = payload.get("observations", [])
    if isinstance(observations, list):
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            zone_label = str(obs.get("zone_label") or "")
            slot = extract_slot_from_label(zone_label)
            if slot is None:
                continue
            lower = zone_label.lower()
            if "tag" not in lower and "name" not in lower:
                continue
            text = clean_tag_text(obs.get("normalized_text") or obs.get("raw_text"))
            if not text:
                continue
            if "name" in lower and len(text) > 8:
                continue
            confidence = float(obs.get("ocr_confidence") or 0.0)
            score = confidence + (0.35 if "tag" in lower else 0.0)
            if score >= best_score.get(slot, -1.0):
                best_score[slot] = score
                out[slot] = (text, confidence, zone_label)
    return out


def ensure_target_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS Teams (
            name TEXT,
            tag TEXT,
            logo BLOB
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS teams_ref (
            team_name TEXT PRIMARY KEY,
            logo BLOB NOT NULL,
            logo_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS match_team_slots (
            match_id TEXT NOT NULL,
            slot INTEGER NOT NULL,
            tag TEXT NOT NULL,
            team_name TEXT NOT NULL,
            logo BLOB NOT NULL,
            logo_hash TEXT NOT NULL,
            distance REAL NOT NULL,
            tag_source TEXT NOT NULL DEFAULT 'unknown',
            PRIMARY KEY (match_id, slot)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ocr_zone_teams (
            match_id TEXT NOT NULL,
            slot INTEGER NOT NULL,
            tag TEXT NOT NULL,
            logo BLOB NOT NULL,
            logo_hash TEXT NOT NULL,
            ocr_confidence REAL NOT NULL DEFAULT 0.0,
            zone_label TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (match_id, slot)
        )
        """
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_match_team_slots_tag ON "match_team_slots"(tag)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_match_team_slots_team ON "match_team_slots"(team_name)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ocr_zone_teams_tag ON "ocr_zone_teams"(tag)')
    conn.commit()


def load_existing_by_tag(conn: sqlite3.Connection) -> dict[str, tuple[int, str | None, str | None]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT rowid, name, tag, logo FROM "Teams"').fetchall()
    by_tag: dict[str, tuple[int, str | None, str | None]] = {}
    for row in rows:
        tag = norm_tag(row["tag"])
        blob = row["logo"]
        digest = sha256_bytes(bytes(blob)) if isinstance(blob, (bytes, memoryview)) else None
        if tag:
            by_tag[tag] = (int(row["rowid"]), row["name"], digest)
    return by_tag


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect team logos from records and write Teams DB.")
    parser.add_argument("--records-dir", type=Path, default=DEFAULT_RECORDS_DIR)
    parser.add_argument("--map-start-text-dir", type=Path, default=DEFAULT_MAP_START_TEXT_DIR)
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument(
        "--max-distance",
        type=float,
        default=1.0,
        help="Optional quality filter for matched logo distance (0..1, default keeps all).",
    )
    parser.add_argument("--limit-files", "--count", dest="limit_files", type=int, default=0, help="Limit processed JSON files.")
    parser.add_argument("--only", type=str, default="", help="Process only JSON files containing this substring.")
    parser.add_argument("--verbose", action="store_true", help="Print per-file progress.")
    args = parser.parse_args()

    args.target_db.parent.mkdir(parents=True, exist_ok=True)
    target_conn = sqlite3.connect(str(args.target_db), timeout=30)
    target_conn.execute("PRAGMA busy_timeout = 30000")
    try:
        target_conn.execute("BEGIN IMMEDIATE")
        target_conn.rollback()
    except sqlite3.OperationalError as exc:
        target_conn.close()
        raise RuntimeError(
            f"Target DB is locked: {args.target_db}. Close DB viewers/processes and retry."
        ) from exc
    ensure_target_tables(target_conn)
    existing_by_tag = load_existing_by_tag(target_conn)

    json_files = sorted(args.map_start_text_dir.glob("*.json"))
    if args.only:
        needle = args.only.lower()
        json_files = [p for p in json_files if needle in p.name.lower()]
    if args.limit_files > 0:
        json_files = json_files[: args.limit_files]

    teams_inserted = 0
    teams_updated = 0
    slots_written = 0
    processed_matches = 0
    ocr_rows_written = 0

    for json_path in json_files:
        if args.verbose:
            print(f"[file] {json_path.name}")
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            if args.verbose:
                print("  - skip: bad json")
            continue

        video_path = resolve_video_path(payload, args.records_dir)
        if video_path is None:
            if args.verbose:
                print("  - skip: video not found")
            continue

        zones = payload.get("zones", {}) if isinstance(payload, dict) else {}
        zone_items = zones.get("items", []) if isinstance(zones, dict) else []
        if not isinstance(zone_items, list):
            if args.verbose:
                print("  - skip: zones.items not list")
            continue

        slot_to_tag, slot_to_source = extract_slot_tags(payload)
        if not slot_to_tag:
            if args.verbose:
                print("  - skip: no team tags (observations/summary)")
            continue

        ref_w = int(zones.get("image_size", {}).get("width") or 0)
        ref_h = int(zones.get("image_size", {}).get("height") or 0)
        if ref_w <= 0 or ref_h <= 0:
            if args.verbose:
                print("  - skip: invalid reference image_size")
            continue

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            cap.release()
            if args.verbose:
                print("  - skip: cv2 cannot open video")
            continue
        video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if video_w <= 0 or video_h <= 0:
            cap.release()
            if args.verbose:
                print("  - skip: invalid video dimensions")
            continue

        sx = video_w / ref_w
        sy = video_h / ref_h

        best_crop_by_slot: dict[int, tuple[np.ndarray, float]] = {}
        for zone in zone_items:
            if not isinstance(zone, dict):
                continue
            if zone.get("enabled") is False:
                continue
            label = str(zone.get("label") or "")
            if not zone_is_logo_candidate(label):
                continue
            slot = extract_slot_from_label(label)
            if slot is None or slot not in slot_to_tag:
                continue
            tag = norm_tag(slot_to_tag[slot])
            if not tag:
                continue

            x = int(round(float(zone.get("x") or 0) * sx))
            y = int(round(float(zone.get("y") or 0) * sy))
            w = int(round(float(zone.get("width") or 0) * sx))
            h = int(round(float(zone.get("height") or 0) * sy))
            if w < 8 or h < 8:
                continue
            x = max(0, min(video_w - 1, x))
            y = max(0, min(video_h - 1, y))
            w = max(1, min(video_w - x, w))
            h = max(1, min(video_h - y, h))

            best_crop: np.ndarray | None = None
            best_score = -1.0
            for t in frame_time_candidates(payload):
                cap.set(cv2.CAP_PROP_POS_MSEC, float(t * 1000.0))
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                crop = frame[y : y + h, x : x + w]
                crop = choose_logo_crop(crop, label)
                if crop.size == 0:
                    continue
                score = image_score(crop)
                if score > best_score:
                    best_score = score
                    best_crop = crop

            if best_crop is None:
                continue
            prev_best = best_crop_by_slot.get(slot)
            if prev_best is None or best_score > prev_best[1]:
                best_crop_by_slot[slot] = (best_crop, best_score)

        match_id = json_path.name.replace(".text_observations.json", "")
        ocr_slot_tags = extract_ocr_slot_tags(payload)
        for slot, tag_value in slot_to_tag.items():
            best_entry = best_crop_by_slot.get(slot)
            if best_entry is None:
                continue
            best_crop, _score = best_entry
            logo_bytes = encode_png_bytes(best_crop)
            logo_hash = sha256_bytes(logo_bytes)
            if not logo_bytes or not logo_hash:
                continue
            ocr_meta = ocr_slot_tags.get(slot)
            ocr_confidence = float(ocr_meta[1]) if ocr_meta else 0.0
            zone_label = str(ocr_meta[2]) if ocr_meta else str(slot_to_source.get(slot, "summary"))
            target_conn.execute(
                """
                INSERT INTO "ocr_zone_teams"(match_id, slot, tag, logo, logo_hash, ocr_confidence, zone_label)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id, slot) DO UPDATE SET
                    tag=excluded.tag,
                    logo=excluded.logo,
                    logo_hash=excluded.logo_hash,
                    ocr_confidence=excluded.ocr_confidence,
                    zone_label=excluded.zone_label
                """,
                (
                    match_id,
                    int(slot),
                    norm_tag(tag_value),
                    sqlite3.Binary(logo_bytes),
                    logo_hash,
                    ocr_confidence,
                    zone_label,
                ),
            )
            ocr_rows_written += 1
        match_written = 0
        for slot, (best_crop, _score) in sorted(best_crop_by_slot.items()):
            if slot not in slot_to_tag:
                continue
            tag = norm_tag(slot_to_tag.get(slot))
            if not tag:
                continue
            matched_name = tag
            logo_bytes = encode_png_bytes(best_crop)
            logo_hash = sha256_bytes(logo_bytes)
            if not logo_hash or not logo_bytes:
                continue
            target_conn.execute(
                """
                INSERT INTO "teams_ref"(team_name, logo, logo_hash)
                VALUES(?, ?, ?)
                ON CONFLICT(team_name) DO UPDATE SET
                    logo=excluded.logo,
                    logo_hash=excluded.logo_hash
                """,
                (matched_name, sqlite3.Binary(logo_bytes), logo_hash),
            )

            target_conn.execute(
                """
                INSERT INTO "match_team_slots"(match_id, slot, tag, team_name, logo, logo_hash, distance, tag_source)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id, slot) DO UPDATE SET
                    tag=excluded.tag,
                    team_name=excluded.team_name,
                    logo=excluded.logo,
                    logo_hash=excluded.logo_hash,
                    distance=excluded.distance,
                    tag_source=excluded.tag_source
                """,
                (
                    match_id,
                    int(slot),
                    tag,
                    matched_name,
                    sqlite3.Binary(logo_bytes),
                    logo_hash,
                    0.0,
                    slot_to_source.get(slot, "unknown"),
                ),
            )
            slots_written += 1
            match_written += 1

            current = existing_by_tag.get(tag)
            if current is None:
                cur = target_conn.execute(
                    'INSERT INTO "Teams"(name, tag, logo) VALUES(?, ?, ?)',
                    (matched_name, tag, sqlite3.Binary(logo_bytes)),
                )
                existing_by_tag[tag] = (int(cur.lastrowid), matched_name, logo_hash)
                teams_inserted += 1
            else:
                rowid, current_name, current_hash = current
                if (current_hash != logo_hash) or (str(current_name or "").strip() != matched_name):
                    target_conn.execute(
                        'UPDATE "Teams" SET name = ?, logo = ? WHERE rowid = ?',
                        (matched_name, sqlite3.Binary(logo_bytes), rowid),
                    )
                    existing_by_tag[tag] = (rowid, matched_name, logo_hash)
                    teams_updated += 1

        if match_written > 0:
            processed_matches += 1

        cap.release()

    target_conn.commit()
    target_conn.close()
    print(
        "Done. "
        f"files={len(json_files)} matches_with_rows={processed_matches} "
        f"slots_written={slots_written} teams_inserted={teams_inserted} teams_updated={teams_updated} "
        f"ocr_rows_written={ocr_rows_written}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
