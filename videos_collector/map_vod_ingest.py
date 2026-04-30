from __future__ import annotations

import argparse
import difflib
import json
import locale
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


YEAR_BUCKETS: list[tuple[int, date, date]] = [
    (1, date(2020, 1, 25), date(2021, 6, 13)),
    (2, date(2021, 9, 10), date(2022, 7, 11)),
    (3, date(2022, 10, 7), date(2023, 9, 11)),
    (4, date(2023, 11, 26), date(2025, 2, 3)),
    (5, date(2025, 2, 21), date(2026, 1, 19)),
    (6, date(2026, 2, 27), date(2027, 2, 1)),
]

@dataclass
class VideoItem:
    youtube_video_id: str
    title: str
    description: str
    published_at: str
    channel_id: str
    webpage_url: str


@dataclass
class GameMark:
    game_number: int
    start_sec: int
    line_text: str


@dataclass
class ParsedMeta:
    region: str
    tournament_name: str
    day_number: int
    split_number: int
    year_number: int
    games: list[GameMark]
    missing_fields: list[str]


@dataclass
class CatalogMatch:
    catalog_tournament_id: int | None
    catalog_tournament_name: str | None
    confidence: float


KNOWN_MP_IDS: list[tuple[str, str]] = [
    ("storm point", "mp_storm_point"),
    ("world's edge", "mp_worlds_edge"),
    ("worlds edge", "mp_worlds_edge"),
    ("olympus", "mp_olympus"),
    ("broken moon", "mp_broken_moon"),
    ("e-district", "mp_e_district"),
    ("edistrict", "mp_e_district"),
    ("district", "mp_e_district"),
    ("kings canyon", "mp_kings_canyon"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download ALGS map videos by Game timestamps.")
    parser.add_argument(
        "--channel-url",
        default="https://www.youtube.com/@algs_vods/videos",
        help="YouTube channel videos URL.",
    )
    parser.add_argument(
        "--db-path",
        default="output/youtube_ingest/tournaments.sqlite",
        help="SQLite DB path for tournament metadata.",
    )
    parser.add_argument(
        "--output-dir",
        default="ffmpeg_downloader/records",
        help="Folder for downloaded game clips.",
    )
    parser.add_argument(
        "--duration-sec",
        type=int,
        default=1200,
        help="Clip duration in seconds (default: 1200).",
    )
    parser.add_argument(
        "--limit-videos",
        type=int,
        default=None,
        help="Optional cap for amount of videos to process.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and write DB only; do not download clips.",
    )
    parser.add_argument(
        "--cookies-file",
        default=None,
        help="Path to Netscape cookies.txt exported from browser.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Read cookies from browser profile (e.g. chrome, edge, firefox).",
    )
    parser.add_argument(
        "--js-runtimes",
        default="deno",
        help="yt-dlp JS runtime selector for challenge solving (default: deno).",
    )
    parser.add_argument(
        "--remote-components",
        default="ejs:npm",
        help="yt-dlp remote components source (default: ejs:npm).",
    )
    parser.add_argument(
        "--disable-ejs",
        action="store_true",
        help="Disable EJS runtime arguments (not recommended for YouTube).",
    )
    parser.add_argument(
        "--catalog-db-path",
        default="videos_collector/algs_tournaments.sqlite",
        help="SQLite with tournament catalog used for fuzzy matching.",
    )
    parser.add_argument(
        "--catalog-min-confidence",
        type=float,
        default=0.62,
        help="Minimum confidence to accept mapped catalog tournament. Lower confidence becomes NULL.",
    )
    parser.add_argument(
        "--stop-at-first-year",
        type=int,
        default=0,
        help="Stop processing when first parsed video reaches this Year bucket (0=disabled).",
    )
    parser.add_argument(
        "--disk-wait-minutes",
        type=int,
        default=10,
        help="Minutes to wait before retry when disk is full (default: 10).",
    )
    parser.add_argument(
        "--disk-wait-retries",
        type=int,
        default=6,
        help="Max retries on disk-full errors per operation (default: 6).",
    )
    parser.add_argument(
        "--sync-queue-threshold",
        type=int,
        default=7,
        help="Pause ingest while remote clip queue has more files than this threshold (default: 7).",
    )
    parser.add_argument(
        "--sync-queue-wait-sec",
        type=int,
        default=60,
        help="Sleep interval while waiting for sync queue to drain (default: 60 sec).",
    )
    parser.add_argument(
        "--hard-reset",
        action="store_true",
        help="Hard reset local ingest state: remove SQLite DB and clip/source files in output dir before run.",
    )
    return parser.parse_args()


def build_yt_dlp_auth_args(cookies_file: str | None, cookies_from_browser: str | None) -> list[str]:
    args: list[str] = []
    if cookies_file:
        args.extend(["--cookies", cookies_file])
    if cookies_from_browser:
        args.extend(["--cookies-from-browser", cookies_from_browser])
    return args


def build_yt_dlp_network_args() -> list[str]:
    return [
        "--ignore-config",
        "--force-ipv4",
        "--extractor-retries",
        "10",
        "--retries",
        "10",
        "--retry-sleep",
        "http:2:12",
        "--socket-timeout",
        "40",
    ]


def build_yt_dlp_ejs_args(
    js_runtimes: str,
    remote_components: str,
    disable_ejs: bool,
) -> list[str]:
    if disable_ejs:
        return []
    args: list[str] = []
    if js_runtimes.strip():
        args.extend(["--js-runtimes", js_runtimes.strip()])
    if remote_components.strip():
        args.extend(["--remote-components", remote_components.strip()])
    return args


def run_yt_dlp_json_with_retries(command: list[str], attempts: int = 3) -> dict[str, Any]:
    last_error: RuntimeError | None = None
    for idx in range(attempts):
        try:
            if idx > 0:
                print(f"[yt-dlp] retry {idx + 1}/{attempts}: {' '.join(command[:6])} ...")
            return run_yt_dlp_json(command)
        except RuntimeError as exc:
            last_error = exc
            message = str(exc).lower()
            retryable = any(
                token in message
                for token in (
                    "connection reset",
                    "connection aborted",
                    "timed out",
                    "unable to download api page",
                    "transporterror",
                    "http error 429",
                    "too many requests",
                )
            )
            if (idx + 1) >= attempts or not retryable:
                break
            print(f"[yt-dlp] transient error, waiting before retry: {exc}")
            time.sleep(min(3.0 * (idx + 1), 12.0))
    if last_error is None:
        raise RuntimeError("yt-dlp failed without specific error.")
    raise last_error


def run_yt_dlp_json(command: list[str]) -> dict[str, Any]:
    def decode_output(raw: bytes | None) -> str:
        if not raw:
            return ""
        candidates = ["utf-8", locale.getpreferredencoding(False), "cp1251", "cp866", "latin-1"]
        for enc in candidates:
            if not enc:
                continue
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=False)
    except FileNotFoundError as exc:
        raise RuntimeError("yt-dlp is not installed or not available in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = decode_output(exc.stderr).strip()
        stdout = decode_output(exc.stdout).strip()
        details = stderr or stdout or f"exit code {exc.returncode}"
        details_lc = details.lower()
        if "not a bot" in details_lc and "--cookies" not in " ".join(command):
            details += " | Hint: run with --cookies-from-browser edge (or chrome/firefox)."
        raise RuntimeError(f"yt-dlp command failed: {' '.join(command)} | {details}") from exc

    try:
        return json.loads(decode_output(completed.stdout))
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned non-JSON output.") from exc


def sanitize_token(value: str) -> str:
    normalized = re.sub(r"\s+", "_", value.strip())
    normalized = re.sub(r"[^A-Za-z0-9_\-]", "", normalized)
    return normalized or "UNKNOWN"


def parse_video_date(raw_value: str) -> date | None:
    if not raw_value:
        return None
    raw_value = raw_value.strip()
    if re.fullmatch(r"\d{8}", raw_value):
        return datetime.strptime(raw_value, "%Y%m%d").date()
    if "T" in raw_value:
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).date()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_value):
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    return None


def resolve_year_bucket(published: date) -> int | None:
    for year_id, start_date, end_date in YEAR_BUCKETS:
        if start_date <= published <= end_date:
            return year_id
    return None


def timestamp_to_seconds(text: str) -> int:
    chunks = [int(part) for part in text.split(":")]
    if len(chunks) == 2:
        return chunks[0] * 60 + chunks[1]
    if len(chunks) == 3:
        return chunks[0] * 3600 + chunks[1] * 60 + chunks[2]
    raise ValueError(f"Unsupported timestamp format: {text}")


def seconds_to_hhmmss(seconds: int) -> str:
    sec = max(0, int(seconds))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_games_from_description(description: str) -> list[GameMark]:
    marks: list[GameMark] = []
    seen_games: set[int] = set()
    for line in description.splitlines():
        row = line.strip()
        if not row:
            continue
        match = re.search(r"(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(?P<label>.+)$", row, flags=re.IGNORECASE)
        if not match:
            continue
        label = match.group("label").strip()
        if "game" not in label.lower():
            continue
        game_match = re.search(r"game\s*(\d+)", label, flags=re.IGNORECASE)
        if not game_match:
            continue
        game_number = int(game_match.group(1))
        if game_number in seen_games:
            continue
        seen_games.add(game_number)
        marks.append(
            GameMark(
                game_number=game_number,
                start_sec=timestamp_to_seconds(match.group("ts")),
                line_text=row,
            )
        )
    return sorted(marks, key=lambda item: item.game_number)


def parse_split_number(title: str, description: str) -> int | None:
    text = f"{title}\n{description}"
    match = re.search(r"split\s*([12])", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def parse_region(description: str) -> str | None:
    for line in description.splitlines():
        match = re.match(r"\s*Region\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def parse_tournament_name(description: str) -> str | None:
    for line in description.splitlines():
        match = re.match(r"\s*Tournament\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def parse_day_number(title: str, description: str) -> int | None:
    match = re.search(r"\bDay\s*(\d+)\b", f"{description}\n{title}", flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def parse_year_bucket_from_title(title: str) -> int | None:
    match = re.search(r"\bY([1-6])\b", title, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def parse_tournament_name_from_title(title: str) -> str | None:
    # Example: "ALGS Map POV - Y5 Championship Day 6 (Group A vs D) - January 16, 2026"
    match = re.search(r"ALGS\s+Map\s+POV\s*-\s*(.+?)\s*-\s*[A-Za-z]+\s+\d{1,2},\s+\d{4}", title, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def build_tournament_id(year_number: int, split_number: int, tournament_name: str) -> str:
    name_token = sanitize_token(tournament_name)
    return f"Y{max(0, int(year_number))}_Split_{max(0, int(split_number))}_{name_token}"


def extract_video_item(video_id: str, auth_args: list[str], ejs_args: list[str]) -> VideoItem:
    base_command = [
        "yt-dlp",
        *build_yt_dlp_network_args(),
        *ejs_args,
        *auth_args,
        "--ignore-no-formats-error",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        f"https://www.youtube.com/watch?v={video_id}",
    ]

    try:
        payload = run_yt_dlp_json_with_retries(base_command, attempts=3)
    except RuntimeError:
        fallback_command = [
            "yt-dlp",
            *build_yt_dlp_network_args(),
            *ejs_args,
            "--extractor-args",
            "youtube:player_client=android",
            *auth_args,
            "--ignore-no-formats-error",
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        payload = run_yt_dlp_json_with_retries(fallback_command, attempts=2)
    return VideoItem(
        youtube_video_id=str(payload.get("id", video_id)),
        title=str(payload.get("title", "")).strip(),
        description=str(payload.get("description", "")),
        published_at=str(payload.get("upload_date", "")).strip(),
        channel_id=str(payload.get("channel_id", "")).strip(),
        webpage_url=str(payload.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}")),
    )


def list_channel_video_ids(channel_url: str, limit_videos: int | None, auth_args: list[str], ejs_args: list[str]) -> list[str]:
    candidate_urls = [channel_url]
    if channel_url.endswith("/videos"):
        candidate_urls.append(channel_url.removesuffix("/videos"))
    elif not channel_url.endswith("/videos"):
        candidate_urls.append(f"{channel_url.rstrip('/')}/videos")

    last_error: str | None = None
    for url in candidate_urls:
        command = [
            "yt-dlp",
            *build_yt_dlp_network_args(),
            *ejs_args,
            "--extractor-args",
            "youtubetab:skip=authcheck",
            *auth_args,
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--ignore-errors",
        ]
        if limit_videos and limit_videos > 0:
            command.extend(["--playlist-end", str(limit_videos)])
        command.append(url)
        try:
            payload = run_yt_dlp_json_with_retries(command, attempts=4)
        except RuntimeError as exc:
            last_error = str(exc)
            continue
        entries = payload.get("entries", [])
        video_ids = [str(entry.get("id")) for entry in entries if entry and entry.get("id")]
        if video_ids:
            return video_ids

    raise RuntimeError(last_error or "Unable to list videos from channel URL.")


def compute_file_stem(
    year_number: int,
    split_number: int,
    region: str,
    day_number: int,
    game_number: int,
    youtube_video_id: str,
) -> str:
    region_token = sanitize_token(region.upper())
    video_token = sanitize_token(youtube_video_id)
    return f"Y{year_number}_S{split_number}_{region_token}_D{day_number}_G{game_number}_VID_{video_token}"


def normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def token_set(value: str) -> set[str]:
    tokens = [t for t in normalize_for_match(value).split(" ") if len(t) >= 3]
    return set(tokens)


def infer_mp_id(item: VideoItem) -> str | None:
    haystack = f"{item.title} {item.description}".lower()
    for needle, mp_id in KNOWN_MP_IDS:
        if needle in haystack:
            return mp_id
    return None


def build_match_id(video_id: str) -> str:
    return f"match_{video_id}"


def parse_catalog_split(value: str | None) -> int:
    if not value:
        return 0
    lowered = value.lower()
    if "split 1" in lowered:
        return 1
    if "split 2" in lowered:
        return 2
    return 0


def map_catalog_region(region: str | None) -> str:
    if not region:
        return "UNKNOWN"
    normalized = sanitize_token(region.upper())
    aliases = {
        "APAC_NORTH": "APAC_NORTH",
        "APAC_SOUTH": "APAC_SOUTH",
        "AMERICAS": "AMERICAS",
        "EMEA": "EMEA",
        "NORTH_AMERICA": "AMERICAS",
        "SOUTH_AMERICA": "AMERICAS",
        "WORLD": "WORLD",
    }
    return aliases.get(normalized, normalized)


def find_best_catalog_tournament_match(
    conn: sqlite3.Connection | None,
    item: VideoItem,
    parsed: ParsedMeta,
    min_confidence: float,
) -> CatalogMatch:
    if conn is None:
        return CatalogMatch(catalog_tournament_id=None, catalog_tournament_name=None, confidence=0.0)
    rows = conn.execute(
        "SELECT id, name, split, region FROM tournaments",
    ).fetchall()
    if not rows:
        return CatalogMatch(catalog_tournament_id=None, catalog_tournament_name=None, confidence=0.0)

    query_name = parsed.tournament_name if parsed.tournament_name and parsed.tournament_name != "UNKNOWN" else item.title
    query_name_norm = normalize_for_match(query_name)
    query_tokens = token_set(f"{item.title} {item.description} {parsed.tournament_name}")
    query_region = map_catalog_region(parsed.region)
    query_split = int(parsed.split_number)

    best: tuple[int | None, str | None, float] = (None, None, 0.0)
    for row in rows:
        tid = int(row[0])
        name = str(row[1])
        split_val = parse_catalog_split(str(row[2]) if row[2] is not None else None)
        region_val = map_catalog_region(str(row[3]) if row[3] is not None else None)

        name_norm = normalize_for_match(name)
        name_ratio = difflib.SequenceMatcher(a=query_name_norm, b=name_norm).ratio() if query_name_norm and name_norm else 0.0
        name_tokens = token_set(name)
        overlap = (len(query_tokens & name_tokens) / float(len(name_tokens))) if name_tokens else 0.0

        score = (0.6 * name_ratio) + (0.4 * overlap)
        if query_split > 0 and split_val == query_split:
            score += 0.08
        if query_region != "UNKNOWN" and query_region == region_val:
            score += 0.08
        score = min(score, 1.0)

        if score > best[2]:
            best = (tid, name, score)

    if best[2] < max(0.0, min(1.0, float(min_confidence))):
        return CatalogMatch(catalog_tournament_id=None, catalog_tournament_name=best[1], confidence=best[2])
    return CatalogMatch(catalog_tournament_id=best[0], catalog_tournament_name=best[1], confidence=best[2])


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS videos (
            youtube_video_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            webpage_url TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            published_at TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            has_map_keyword INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL
        );
        """
    )

    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()}
    needs_migration = not cols or ("tournament_id" not in cols) or ("day" not in cols)

    if needs_migration:
        old_tournaments: list[tuple[Any, ...]] = []
        old_tournament_cols: list[str] = []
        if cols:
            old_tournament_cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()]
            old_tournaments = conn.execute("SELECT * FROM tournaments").fetchall()

        games_exists = bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='games'").fetchone())
        old_games: list[tuple[Any, ...]] = conn.execute("SELECT * FROM games").fetchall() if games_exists else []

        conn.execute("DROP TABLE IF EXISTS games")
        conn.execute("DROP TABLE IF EXISTS tournaments")

        conn.executescript(
            """
            CREATE TABLE tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id TEXT NOT NULL UNIQUE,
                tournament_name TEXT NOT NULL,
                region TEXT NOT NULL,
                split_number INTEGER NOT NULL,
                day INTEGER NOT NULL,
                year_number INTEGER NOT NULL,
                catalog_tournament_id INTEGER,
                catalog_confidence REAL,
                team_ids_json TEXT NOT NULL DEFAULT '[]',
                source_video_id TEXT NOT NULL UNIQUE REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
                published_at TEXT NOT NULL
            );

            CREATE TABLE games (
                id TEXT PRIMARY KEY,
                tournament_id TEXT REFERENCES tournaments(tournament_id) ON DELETE SET NULL,
                youtube_video_id TEXT NOT NULL REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
                game_number INTEGER NOT NULL,
                start_sec INTEGER NOT NULL,
                duration_sec INTEGER NOT NULL,
                output_filename TEXT NOT NULL,
                output_path TEXT NOT NULL,
                download_status TEXT NOT NULL,
                error_message TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_games_video_game
                ON games (youtube_video_id, game_number);
            """
        )

        mapping: dict[str, str] = {}
        if old_tournaments and old_tournament_cols:
            idx = {name: i for i, name in enumerate(old_tournament_cols)}
            for row in old_tournaments:
                old_id = str(row[idx["id"]]) if "id" in idx else ""
                t_name = str(row[idx["tournament_name"]]) if "tournament_name" in idx else "UNKNOWN"
                region = str(row[idx["region"]]) if "region" in idx else "UNKNOWN"
                split_number = int(row[idx["split_number"]]) if "split_number" in idx else 0
                day = int(row[idx["day_number"]]) if "day_number" in idx else (int(row[idx["day"]]) if "day" in idx else 0)
                year_number = int(row[idx["year_bucket"]]) if "year_bucket" in idx else (int(row[idx["year_number"]]) if "year_number" in idx else 0)
                team_ids_json = str(row[idx["team_ids_json"]]) if "team_ids_json" in idx else "[]"
                source_video_id = str(row[idx["source_video_id"]]) if "source_video_id" in idx else ""
                published_at = str(row[idx["published_at"]]) if "published_at" in idx else ""
                tournament_id = build_tournament_id(year_number, split_number, t_name)
                suffix = 1
                base_tid = tournament_id
                while conn.execute("SELECT 1 FROM tournaments WHERE tournament_id = ? LIMIT 1", (tournament_id,)).fetchone():
                    suffix += 1
                    tournament_id = f"{base_tid}_{suffix}"
                conn.execute(
                    """
                    INSERT INTO tournaments (
                        tournament_id, tournament_name, region, split_number, day, year_number,
                        team_ids_json, source_video_id, published_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tournament_id, t_name, region, split_number, day, year_number, team_ids_json, source_video_id, published_at),
                )
                if old_id:
                    mapping[old_id] = tournament_id

        if old_games:
            for row in old_games:
                game_id = str(row[0])
                old_tournament_id = str(row[1])
                tournament_id = mapping.get(old_tournament_id, old_tournament_id)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO games (
                        id, tournament_id, youtube_video_id, game_number, start_sec, duration_sec,
                        output_filename, output_path, download_status, error_message, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game_id,
                        tournament_id,
                        str(row[2]),
                        int(row[3]),
                        int(row[4]),
                        int(row[5]),
                        str(row[6]),
                        str(row[7]),
                        str(row[8]),
                        row[9] if row[9] is None else str(row[9]),
                        str(row[10]),
                    ),
                )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS video_marks (
            youtube_video_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            note TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY,
            youtube_video_id TEXT NOT NULL UNIQUE REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
            tournament_id TEXT REFERENCES tournaments(tournament_id) ON DELETE SET NULL,
            catalog_tournament_id INTEGER,
            catalog_tournament_name TEXT,
            catalog_confidence REAL,
            region TEXT,
            split_number INTEGER,
            day INTEGER,
            year_number INTEGER,
            video_url TEXT NOT NULL,
            match_status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS maps (
            id TEXT PRIMARY KEY,
            match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
            mp_id TEXT,
            teams TEXT NOT NULL DEFAULT '[]',
            round_number INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(match_id, round_number)
        );
        """
    )
    video_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
    if "webpage_url" not in video_cols:
        conn.execute("ALTER TABLE videos ADD COLUMN webpage_url TEXT NOT NULL DEFAULT ''")
    tournament_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()}
    if "catalog_tournament_id" not in tournament_cols:
        conn.execute("ALTER TABLE tournaments ADD COLUMN catalog_tournament_id INTEGER")
    if "catalog_confidence" not in tournament_cols:
        conn.execute("ALTER TABLE tournaments ADD COLUMN catalog_confidence REAL")

    games_info = conn.execute("PRAGMA table_info(games)").fetchall()
    needs_games_nullable_tournament = any(str(row[1]) == "tournament_id" and int(row[3]) == 1 for row in games_info)
    if needs_games_nullable_tournament:
        old_games = conn.execute(
            """
            SELECT id, tournament_id, youtube_video_id, game_number, start_sec, duration_sec,
                   output_filename, output_path, download_status, error_message, updated_at
            FROM games
            """
        ).fetchall()
        conn.execute("DROP TABLE IF EXISTS games")
        conn.executescript(
            """
            CREATE TABLE games (
                id TEXT PRIMARY KEY,
                tournament_id TEXT REFERENCES tournaments(tournament_id) ON DELETE SET NULL,
                youtube_video_id TEXT NOT NULL REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
                game_number INTEGER NOT NULL,
                start_sec INTEGER NOT NULL,
                duration_sec INTEGER NOT NULL,
                output_filename TEXT NOT NULL,
                output_path TEXT NOT NULL,
                download_status TEXT NOT NULL,
                error_message TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_games_video_game
                ON games (youtube_video_id, game_number);
            """
        )
        for row in old_games:
            conn.execute(
                """
                INSERT INTO games (
                    id, tournament_id, youtube_video_id, game_number, start_sec, duration_sec,
                    output_filename, output_path, download_status, error_message, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row[0]),
                    None if row[1] is None else str(row[1]),
                    str(row[2]),
                    int(row[3]),
                    int(row[4]),
                    int(row[5]),
                    str(row[6]),
                    str(row[7]),
                    str(row[8]),
                    row[9] if row[9] is None else str(row[9]),
                    str(row[10]),
                ),
            )

    match_cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(matches)").fetchall()]
    if "inferred_tournament_name" in match_cols:
        old_maps = conn.execute(
            "SELECT id, match_id, mp_id, teams, round_number, updated_at FROM maps"
        ).fetchall() if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='maps'").fetchone() else []
        old_matches = conn.execute(
            """
            SELECT id, youtube_video_id, tournament_id, catalog_tournament_id, catalog_tournament_name,
                   catalog_confidence, region, split_number, day, year_number, video_url, match_status, updated_at
            FROM matches
            """
        ).fetchall()
        conn.execute("DROP TABLE IF EXISTS maps")
        conn.execute("DROP TABLE IF EXISTS matches")
        conn.executescript(
            """
            CREATE TABLE matches (
                id TEXT PRIMARY KEY,
                youtube_video_id TEXT NOT NULL UNIQUE REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
                tournament_id TEXT REFERENCES tournaments(tournament_id) ON DELETE SET NULL,
                catalog_tournament_id INTEGER,
                catalog_tournament_name TEXT,
                catalog_confidence REAL,
                region TEXT,
                split_number INTEGER,
                day INTEGER,
                year_number INTEGER,
                video_url TEXT NOT NULL,
                match_status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE maps (
                id TEXT PRIMARY KEY,
                match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                mp_id TEXT,
                teams TEXT NOT NULL DEFAULT '[]',
                round_number INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(match_id, round_number)
            );
            """
        )
        for row in old_matches:
            conn.execute(
                """
                INSERT INTO matches (
                    id, youtube_video_id, tournament_id, catalog_tournament_id, catalog_tournament_name,
                    catalog_confidence, region, split_number, day, year_number, video_url, match_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row[0]),
                    str(row[1]),
                    None if row[2] is None else str(row[2]),
                    row[3],
                    row[4] if row[4] is None else str(row[4]),
                    row[5],
                    row[6] if row[6] is None else str(row[6]),
                    int(row[7]) if row[7] is not None else 0,
                    int(row[8]) if row[8] is not None else 0,
                    int(row[9]) if row[9] is not None else 0,
                    str(row[10]),
                    str(row[11]),
                    str(row[12]),
                ),
            )
        for row in old_maps:
            conn.execute(
                "INSERT INTO maps (id, match_id, mp_id, teams, round_number, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(row[0]),
                    str(row[1]),
                    row[2] if row[2] is None else str(row[2]),
                    row[3] if row[3] is not None else "[]",
                    int(row[4]),
                    str(row[5]),
                ),
            )
    conn.execute("PRAGMA foreign_keys = ON")


def upsert_video(conn: sqlite3.Connection, item: VideoItem, has_map_keyword: bool) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        """
        INSERT INTO videos (
            youtube_video_id, title, webpage_url, description, published_at, channel_id, has_map_keyword, last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(youtube_video_id) DO UPDATE SET
            title = excluded.title,
            webpage_url = excluded.webpage_url,
            description = excluded.description,
            published_at = excluded.published_at,
            channel_id = excluded.channel_id,
            has_map_keyword = excluded.has_map_keyword,
            last_seen_at = excluded.last_seen_at
        """,
        (
            item.youtube_video_id,
            item.title,
            item.webpage_url,
            item.description,
            item.published_at,
            item.channel_id,
            1 if has_map_keyword else 0,
            now,
        ),
    )


def is_known_non_map_video(conn: sqlite3.Connection, video_id: str) -> bool:
    row = conn.execute(
        "SELECT has_map_keyword FROM videos WHERE youtube_video_id = ? LIMIT 1",
        (video_id,),
    ).fetchone()
    if row is None:
        return False
    return int(row[0]) == 0


def upsert_video_mark(conn: sqlite3.Connection, video_id: str, status: str, note: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        """
        INSERT INTO video_marks (youtube_video_id, status, note, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(youtube_video_id) DO UPDATE SET
            status = excluded.status,
            note = excluded.note,
            updated_at = excluded.updated_at
        """,
        (video_id, status, note, now),
    )


def get_video_mark(conn: sqlite3.Connection, video_id: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM video_marks WHERE youtube_video_id = ? LIMIT 1",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def upsert_tournament(
    conn: sqlite3.Connection,
    video_id: str,
    parsed: ParsedMeta,
    published_at_iso: str,
    catalog_match: CatalogMatch,
) -> str | None:
    _ = (conn, video_id, parsed, published_at_iso, catalog_match)
    # Tournament linkage now comes from catalog_tournament_id only.
    # We no longer create inferred/local tournament rows from YouTube metadata.
    return None


def upsert_game(
    conn: sqlite3.Connection,
    game_id: str,
    tournament_id: str | None,
    video_id: str,
    game_number: int,
    start_sec: int,
    duration_sec: int,
    output_filename: str,
    output_path: str,
    download_status: str,
    error_message: str | None = None,
) -> None:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        """
        INSERT INTO games (
            id, tournament_id, youtube_video_id, game_number, start_sec, duration_sec, output_filename,
            output_path, download_status, error_message, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            start_sec = excluded.start_sec,
            duration_sec = excluded.duration_sec,
            output_filename = excluded.output_filename,
            output_path = excluded.output_path,
            download_status = excluded.download_status,
            error_message = excluded.error_message,
            updated_at = excluded.updated_at
        """,
        (
            game_id,
            tournament_id,
            video_id,
            game_number,
            start_sec,
            duration_sec,
            output_filename,
            output_path,
            download_status,
            error_message,
            updated_at,
        ),
    )


def upsert_match(
    conn: sqlite3.Connection,
    item: VideoItem,
    tournament_id: str | None,
    parsed: ParsedMeta,
    catalog_match: CatalogMatch,
    match_status: str,
) -> None:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    match_id = build_match_id(item.youtube_video_id)
    conn.execute(
        """
        INSERT INTO matches (
            id, youtube_video_id, tournament_id, catalog_tournament_id, catalog_tournament_name, catalog_confidence,
            region, split_number, day, year_number, video_url, match_status, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(youtube_video_id) DO UPDATE SET
            tournament_id = excluded.tournament_id,
            catalog_tournament_id = excluded.catalog_tournament_id,
            catalog_tournament_name = excluded.catalog_tournament_name,
            catalog_confidence = excluded.catalog_confidence,
            region = excluded.region,
            split_number = excluded.split_number,
            day = excluded.day,
            year_number = excluded.year_number,
            video_url = excluded.video_url,
            match_status = excluded.match_status,
            updated_at = excluded.updated_at
        """,
        (
            match_id,
            item.youtube_video_id,
            tournament_id,
            catalog_match.catalog_tournament_id,
            catalog_match.catalog_tournament_name,
            float(catalog_match.confidence),
            parsed.region,
            int(parsed.split_number),
            int(parsed.day_number),
            int(parsed.year_number),
            item.webpage_url,
            match_status,
            updated_at,
        ),
    )


def upsert_map(
    conn: sqlite3.Connection,
    match_id: str,
    mp_id: str | None,
    round_number: int,
    teams_json: str = "[]",
) -> None:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    map_id = f"{match_id}_r{int(round_number)}"
    conn.execute(
        """
        INSERT INTO maps (id, match_id, mp_id, teams, round_number, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(match_id, round_number) DO UPDATE SET
            mp_id = excluded.mp_id,
            teams = excluded.teams,
            updated_at = excluded.updated_at
        """,
        (
            map_id,
            match_id,
            mp_id,
            teams_json,
            int(round_number),
            updated_at,
        ),
    )


def parse_best_effort_meta(item: VideoItem) -> ParsedMeta | None:
    published_date = parse_video_date(item.published_at)
    year_bucket = resolve_year_bucket(published_date) if published_date else None
    if year_bucket is None:
        year_bucket = parse_year_bucket_from_title(item.title)
    if year_bucket is None:
        year_bucket = 0

    split_number = parse_split_number(item.title, item.description) or 0
    region = parse_region(item.description) or "UNKNOWN"
    tournament_name = parse_tournament_name(item.description) or parse_tournament_name_from_title(item.title) or "UNKNOWN"
    day_number = parse_day_number(item.title, item.description) or 0
    games = parse_games_from_description(item.description)

    if not games:
        return None

    missing_fields: list[str] = []
    if region == "UNKNOWN":
        missing_fields.append("region")
    if tournament_name == "UNKNOWN":
        missing_fields.append("tournament")
    if split_number == 0:
        missing_fields.append("split")
    if day_number == 0:
        missing_fields.append("day")
    if year_bucket == 0:
        missing_fields.append("year")

    return ParsedMeta(
        region=region,
        tournament_name=tournament_name,
        day_number=day_number,
        split_number=split_number,
        year_number=year_bucket,
        games=games,
        missing_fields=missing_fields,
    )


def file_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return float(path.stat().st_size) / (1024.0 * 1024.0)


def is_disk_full_error(message: str | None) -> bool:
    if not message:
        return False
    haystack = str(message).lower()
    probes = (
        "no space left on device",
        "disk full",
        "enospc",
        "not enough space",
        "insufficient space",
        "cannot allocate memory for output",
        "no free space",
    )
    return any(token in haystack for token in probes)


def is_bot_check_error(message: str | None) -> bool:
    if not message:
        return False
    haystack = str(message).lower()
    probes = (
        "not a bot",
        "sign in to confirm",
        "pass --cookies-from-browser",
        "pass --cookies",
        "requires authentication",
        "this content isn't available",
    )
    return any(token in haystack for token in probes)


def wait_for_disk_recovery(wait_minutes: int, reason: str) -> None:
    minutes = max(1, int(wait_minutes))
    print(f"  -> disk full detected ({reason}), waiting {minutes} minute(s) for sync cleanup...")
    time.sleep(minutes * 60)


def count_sync_queue_files(output_dir: Path) -> int:
    if not output_dir.exists():
        return 0
    return sum(1 for _ in output_dir.glob("*.mp4"))


def wait_for_sync_queue_capacity(output_dir: Path, threshold: int, wait_sec: int) -> None:
    limit = max(0, int(threshold))
    sleep_sec = max(5, int(wait_sec))
    while True:
        queued = count_sync_queue_files(output_dir)
        if queued <= limit:
            return
        print(
            f"  -> sync queue is busy: queued={queued} > limit={limit}; "
            f"waiting {sleep_sec}s for vps_records_sync to drain..."
        )
        time.sleep(sleep_sec)


def apply_hard_reset(db_path: Path, output_dir: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    if output_dir.exists():
        for mp4_file in output_dir.rglob("*.mp4"):
            try:
                mp4_file.unlink()
            except OSError:
                pass
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)


def ensure_catalog_match_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS videos (
            youtube_video_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            video_url TEXT NOT NULL,
            description TEXT NOT NULL,
            published_at TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY,
            youtube_video_id TEXT NOT NULL UNIQUE REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
            tournament_ref_id INTEGER REFERENCES tournaments(id) ON DELETE SET NULL,
            tournament_confidence REAL,
            region TEXT,
            split_number INTEGER,
            day INTEGER,
            year_number INTEGER,
            video_url TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS maps (
            id TEXT PRIMARY KEY,
            match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
            mp_id TEXT,
            teams TEXT NOT NULL DEFAULT '[]',
            round_number INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(match_id, round_number)
        );
        """
    )
    match_cols = [str(row[1]) for row in conn.execute("PRAGMA table_info(matches)").fetchall()]
    if "inferred_tournament_name" in match_cols:
        old_maps = conn.execute(
            "SELECT id, match_id, mp_id, teams, round_number, updated_at FROM maps"
        ).fetchall() if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='maps'").fetchone() else []
        old_matches = conn.execute(
            """
            SELECT id, youtube_video_id, tournament_ref_id, tournament_confidence,
                   region, split_number, day, year_number, video_url, updated_at
            FROM matches
            """
        ).fetchall()
        conn.execute("DROP TABLE IF EXISTS maps")
        conn.execute("DROP TABLE IF EXISTS matches")
        conn.executescript(
            """
            CREATE TABLE matches (
                id TEXT PRIMARY KEY,
                youtube_video_id TEXT NOT NULL UNIQUE REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
                tournament_ref_id INTEGER REFERENCES tournaments(id) ON DELETE SET NULL,
                tournament_confidence REAL,
                region TEXT,
                split_number INTEGER,
                day INTEGER,
                year_number INTEGER,
                video_url TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE maps (
                id TEXT PRIMARY KEY,
                match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                mp_id TEXT,
                teams TEXT NOT NULL DEFAULT '[]',
                round_number INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(match_id, round_number)
            );
            """
        )
        for row in old_matches:
            conn.execute(
                """
                INSERT INTO matches (
                    id, youtube_video_id, tournament_ref_id, tournament_confidence,
                    region, split_number, day, year_number, video_url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row[0]),
                    str(row[1]),
                    row[2],
                    row[3],
                    row[4] if row[4] is None else str(row[4]),
                    int(row[5]) if row[5] is not None else 0,
                    int(row[6]) if row[6] is not None else 0,
                    int(row[7]) if row[7] is not None else 0,
                    str(row[8]),
                    str(row[9]),
                ),
            )
        for row in old_maps:
            conn.execute(
                "INSERT INTO maps (id, match_id, mp_id, teams, round_number, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(row[0]),
                    str(row[1]),
                    row[2] if row[2] is None else str(row[2]),
                    row[3] if row[3] is not None else "[]",
                    int(row[4]),
                    str(row[5]),
                ),
            )


def upsert_catalog_video(conn: sqlite3.Connection, item: VideoItem) -> None:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        """
        INSERT INTO videos (youtube_video_id, title, video_url, description, published_at, channel_id, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(youtube_video_id) DO UPDATE SET
            title = excluded.title,
            video_url = excluded.video_url,
            description = excluded.description,
            published_at = excluded.published_at,
            channel_id = excluded.channel_id,
            last_seen_at = excluded.last_seen_at
        """,
        (
            item.youtube_video_id,
            item.title,
            item.webpage_url,
            item.description,
            item.published_at,
            item.channel_id,
            updated_at,
        ),
    )


def upsert_catalog_match(
    conn: sqlite3.Connection,
    item: VideoItem,
    parsed: ParsedMeta,
    catalog_match: CatalogMatch,
) -> None:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        """
        INSERT INTO matches (
            id, youtube_video_id, tournament_ref_id, tournament_confidence,
            region, split_number, day, year_number, video_url, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(youtube_video_id) DO UPDATE SET
            tournament_ref_id = excluded.tournament_ref_id,
            tournament_confidence = excluded.tournament_confidence,
            region = excluded.region,
            split_number = excluded.split_number,
            day = excluded.day,
            year_number = excluded.year_number,
            video_url = excluded.video_url,
            updated_at = excluded.updated_at
        """,
        (
            f"catalog_match_{item.youtube_video_id}",
            item.youtube_video_id,
            catalog_match.catalog_tournament_id,
            float(catalog_match.confidence),
            parsed.region,
            int(parsed.split_number),
            int(parsed.day_number),
            int(parsed.year_number),
            item.webpage_url,
            updated_at,
        ),
    )


def upsert_catalog_map(
    conn: sqlite3.Connection,
    match_id: str,
    mp_id: str | None,
    round_number: int,
    teams_json: str = "[]",
) -> None:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        """
        INSERT INTO maps (id, match_id, mp_id, teams, round_number, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(match_id, round_number) DO UPDATE SET
            mp_id = excluded.mp_id,
            teams = excluded.teams,
            updated_at = excluded.updated_at
        """,
        (
            f"catalog_{match_id}_r{int(round_number)}",
            match_id,
            mp_id,
            teams_json,
            int(round_number),
            updated_at,
        ),
    )


def download_segment(
    video_url: str,
    output_file: Path,
    start_sec: int,
    duration_sec: int,
    auth_args: list[str],
    ejs_args: list[str],
) -> tuple[bool, str | None]:
    end_sec = start_sec + duration_sec
    base_prefix = [
        "yt-dlp",
        *build_yt_dlp_network_args(),
        *ejs_args,
        *auth_args,
        "--no-warnings",
        "--force-overwrites",
        "--download-sections",
        f"*{start_sec}-{end_sec}",
        "--merge-output-format",
        "mp4",
        "-o",
        str(output_file),
    ]
    profiles: list[tuple[str, list[str]]] = [
        ("default", ["-f", "bv*+ba/b"]),
        ("mp4_best", ["-f", "best[ext=mp4]/best"]),
        ("legacy_progressive", ["-f", "22/18/b"]),
        (
            "android_client",
            [
                "--extractor-args",
                "youtube:player_client=android",
                "-f",
                "best[ext=mp4]/best",
            ],
        ),
    ]
    profile_errors: list[str] = []

    for profile_name, profile_args in profiles:
        command = [*base_prefix, *profile_args, video_url]
        print(f"    [download-profile] trying '{profile_name}'")
        completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode == 0:
            return True, None

        stderr_tail = (completed.stderr or "").strip().splitlines()[-1:] or [""]
        stdout_tail = (completed.stdout or "").strip().splitlines()[-1:] or [""]
        tail = (stderr_tail[0] or stdout_tail[0]).strip()
        profile_errors.append(f"{profile_name}: code={completed.returncode} {tail}")

    return False, " | ".join(profile_errors)


def download_source_video(
    video_url: str,
    source_file: Path,
    auth_args: list[str],
    ejs_args: list[str],
) -> tuple[bool, str | None]:
    source_file.parent.mkdir(parents=True, exist_ok=True)
    base_prefix = [
        "yt-dlp",
        *build_yt_dlp_network_args(),
        *ejs_args,
        *auth_args,
        "--no-warnings",
        "--force-overwrites",
        "-o",
        str(source_file),
    ]
    profiles: list[tuple[str, list[str]]] = [
        ("video_only_1080_pref", ["-f", "299/137/136/135/134/160/bestvideo[ext=mp4]"]),
        (
            "android_video_only",
            [
                "--extractor-args",
                "youtube:player_client=android",
                "-f",
                "299/137/136/135/134/160/bestvideo[ext=mp4]",
            ],
        ),
    ]
    profile_errors: list[str] = []
    for profile_name, profile_args in profiles:
        command = [*base_prefix, *profile_args, video_url]
        print(f"    [source-profile] trying '{profile_name}'")
        completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode == 0 and source_file.exists():
            return True, None
        stderr_tail = (completed.stderr or "").strip().splitlines()[-1:] or [""]
        stdout_tail = (completed.stdout or "").strip().splitlines()[-1:] or [""]
        tail = (stderr_tail[0] or stdout_tail[0]).strip()
        profile_errors.append(f"{profile_name}: code={completed.returncode} {tail}")
    return False, " | ".join(profile_errors)


def cut_segment_from_source(
    source_file: Path,
    output_file: Path,
    start_sec: int,
    duration_sec: int,
) -> tuple[bool, str | None]:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start_sec),
        "-t",
        str(duration_sec),
        "-i",
        str(source_file),
        "-c",
        "copy",
        str(output_file),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode == 0 and output_file.exists():
        return True, None
    stderr_tail = (completed.stderr or "").strip().splitlines()[-1:] or [""]
    return False, f"ffmpeg failed code={completed.returncode}: {stderr_tail[0].strip()}"


def main() -> int:
    args = parse_args()
    auth_args = build_yt_dlp_auth_args(args.cookies_file, args.cookies_from_browser)
    ejs_args = build_yt_dlp_ejs_args(args.js_runtimes, args.remote_components, args.disable_ejs)
    db_path = Path(args.db_path)
    catalog_db_path = Path(args.catalog_db_path)
    output_dir = Path(args.output_dir)
    stop_at_first_year = max(0, int(args.stop_at_first_year))
    if args.hard_reset:
        print("[ingest] hard-reset: removing DB and existing mp4 files in output dir")
        apply_hard_reset(db_path, output_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[ingest] channel: {args.channel_url}")
    print(f"[ingest] db: {db_path}")
    print(f"[ingest] output dir: {output_dir}")
    print(f"[ingest] mode: {'dry-run' if args.dry_run else 'full-run'}")
    if auth_args:
        print("[ingest] yt-dlp auth: enabled")
    else:
        print("[ingest] yt-dlp auth: disabled")
    if ejs_args:
        print(f"[ingest] ejs: enabled ({' '.join(ejs_args)})")
    else:
        print("[ingest] ejs: disabled")

    with sqlite3.connect(db_path) as conn:
        catalog_conn: sqlite3.Connection | None = None
        if catalog_db_path.exists():
            catalog_conn = sqlite3.connect(catalog_db_path)
            ensure_catalog_match_tables(catalog_conn)
        else:
            print(f"[ingest] catalog db not found, matcher disabled: {catalog_db_path}")
        init_db(conn)
        try:
            video_ids = list_channel_video_ids(args.channel_url, args.limit_videos, auth_args, ejs_args)
        except RuntimeError as exc:
            print(f"[error] failed to list channel videos: {exc}")
            if catalog_conn is not None:
                catalog_conn.close()
            return 2
        print(f"[ingest] total videos in listing: {len(video_ids)}")
        print("[ingest] starting per-video metadata scan...")

        total_map_videos = 0
        parsed_videos = 0
        failed_videos = 0
        cached_non_map_skips = 0
        cached_viewed_skips = 0
        planned_games = 0
        downloaded_games = 0
        skipped_games = 0
        failed_games = 0

        for index, video_id in enumerate(video_ids, start=1):
            mark = get_video_mark(conn, video_id)
            if mark in ("viewed", "completed"):
                cached_viewed_skips += 1
                if mark == "completed":
                    print(f"[video {index}/{len(video_ids)}] skip cached completed video_id={video_id}")
                else:
                    print(f"[video {index}/{len(video_ids)}] skip cached viewed video_id={video_id}")
                conn.commit()
                continue
            if is_known_non_map_video(conn, video_id):
                cached_non_map_skips += 1
                print(f"[video {index}/{len(video_ids)}] skip cached non-map video_id={video_id}")
                conn.commit()
                continue

            print(f"[video {index}/{len(video_ids)}] loading metadata for video_id={video_id}...")
            try:
                item = extract_video_item(video_id, auth_args, ejs_args)
            except RuntimeError as exc:
                failed_videos += 1
                print(f"[video {index}/{len(video_ids)}] {video_id} | metadata failed: {exc}")
                if is_bot_check_error(str(exc)):
                    print("[fatal] anti-bot/auth challenge detected during metadata fetch. Stopping ingest.")
                    if catalog_conn is not None:
                        catalog_conn.commit()
                        catalog_conn.close()
                    conn.commit()
                    return 3
                conn.commit()
                continue
            has_map = "map" in item.title.lower()
            upsert_video(conn, item, has_map_keyword=has_map)
            if catalog_conn is not None:
                upsert_catalog_video(catalog_conn, item)

            if not has_map:
                print(f"[video {index}/{len(video_ids)}] skip (title does not contain 'Map').")
                upsert_video_mark(conn, video_id, "viewed", "non_map_title")
                if catalog_conn is not None:
                    catalog_conn.commit()
                conn.commit()
                continue
            total_map_videos += 1
            print(f"[video {index}/{len(video_ids)}] {item.youtube_video_id} | {item.title}")

            parsed = parse_best_effort_meta(item)
            if not parsed:
                print("  -> skipped: missing Game timestamps in description")
                upsert_video_mark(conn, video_id, "viewed", "missing_games")
                if catalog_conn is not None:
                    catalog_conn.commit()
                conn.commit()
                continue
            if stop_at_first_year > 0 and parsed.year_number == stop_at_first_year:
                print(
                    f"[ingest] stop condition met: first video with Year={stop_at_first_year} "
                    f"encountered at {item.youtube_video_id}. Stopping run."
                )
                if catalog_conn is not None:
                    catalog_conn.commit()
                    catalog_conn.close()
                conn.commit()
                return 0

            parsed_videos += 1
            match_id = build_match_id(item.youtube_video_id)
            mp_id = infer_mp_id(item)
            catalog_match = find_best_catalog_tournament_match(
                conn=catalog_conn,
                item=item,
                parsed=parsed,
                min_confidence=args.catalog_min_confidence,
            )
            tournament_id = upsert_tournament(conn, item.youtube_video_id, parsed, item.published_at, catalog_match)
            if catalog_match.catalog_tournament_id is None:
                print(
                    "  -> catalog match: LOW confidence "
                    f"{catalog_match.confidence:.3f}; tournament_ref=NULL"
                )
            else:
                print(
                    "  -> catalog match: "
                    f"id={catalog_match.catalog_tournament_id} "
                    f"name={catalog_match.catalog_tournament_name} "
                    f"confidence={catalog_match.confidence:.3f}"
                )
            print(
                "  -> parsed: "
                f"region={parsed.region}, split={parsed.split_number}, day={parsed.day_number}, "
                f"year=Y{parsed.year_number}, games={len(parsed.games)}"
            )
            if parsed.missing_fields:
                print(f"  -> partial metadata: missing={','.join(parsed.missing_fields)} (continue by games)")
            # Parent rows for FK in maps must exist before first upsert_map call.
            upsert_match(conn, item, tournament_id, parsed, catalog_match, "viewed")
            if catalog_conn is not None:
                upsert_catalog_match(catalog_conn, item, parsed, catalog_match)

            source_file = output_dir / "_sources" / f"{item.youtube_video_id}.mp4"
            source_ready = False
            source_error: str | None = None
            if not args.dry_run:
                wait_for_sync_queue_capacity(
                    output_dir=output_dir,
                    threshold=args.sync_queue_threshold,
                    wait_sec=args.sync_queue_wait_sec,
                )
                if source_file.exists():
                    source_ready = True
                    print(f"  -> source exists: {source_file.name}")
                else:
                    print(f"  -> downloading source video-only once: {source_file.name}")
                    source_ready, source_error = download_source_video(item.webpage_url, source_file, auth_args, ejs_args)
                    if source_ready:
                        print("  -> source downloaded")
                    else:
                        print(f"  -> source download failed: {source_error}")
                        if is_bot_check_error(source_error):
                            print("[fatal] anti-bot/auth challenge detected during source download. Stopping ingest.")
                            if catalog_conn is not None:
                                catalog_conn.commit()
                                catalog_conn.close()
                            conn.commit()
                            return 3

            for game_idx, game in enumerate(parsed.games, start=1):
                planned_games += 1
                file_stem = compute_file_stem(
                    year_number=parsed.year_number,
                    split_number=parsed.split_number,
                    region=parsed.region,
                    day_number=parsed.day_number,
                    game_number=game.game_number,
                    youtube_video_id=item.youtube_video_id,
                )
                output_file = output_dir / f"{file_stem}.mp4"
                game_id = f"{item.youtube_video_id}_g{game.game_number}"
                game_start = seconds_to_hhmmss(game.start_sec)
                game_end = seconds_to_hhmmss(game.start_sec + args.duration_sec)
                print(
                    f"  -> game {game_idx}/{len(parsed.games)} (Game {game.game_number}) "
                    f"time={game_start}-{game_end} file={output_file.name}"
                )

                if args.dry_run:
                    upsert_game(
                        conn=conn,
                        game_id=game_id,
                        tournament_id=tournament_id,
                        video_id=item.youtube_video_id,
                        game_number=game.game_number,
                        start_sec=game.start_sec,
                        duration_sec=args.duration_sec,
                        output_filename=output_file.name,
                        output_path=str(output_file),
                        download_status="planned",
                    )
                    upsert_map(conn, match_id, mp_id, game.game_number, teams_json="[]")
                    if catalog_conn is not None:
                        upsert_catalog_map(catalog_conn, match_id, mp_id, game.game_number, teams_json="[]")
                    skipped_games += 1
                    print(f"  -> dry-run game {game.game_number}: {output_file.name}")
                    done_games = downloaded_games + skipped_games + failed_games
                    print(f"  -> progress games: done={done_games} planned={planned_games}")
                    continue

                wait_for_sync_queue_capacity(
                    output_dir=output_dir,
                    threshold=args.sync_queue_threshold,
                    wait_sec=args.sync_queue_wait_sec,
                )
                if output_file.exists():
                    upsert_game(
                        conn=conn,
                        game_id=game_id,
                        tournament_id=tournament_id,
                        video_id=item.youtube_video_id,
                        game_number=game.game_number,
                        start_sec=game.start_sec,
                        duration_sec=args.duration_sec,
                        output_filename=output_file.name,
                        output_path=str(output_file),
                        download_status="downloaded",
                    )
                    skipped_games += 1
                    print(f"  -> skip existing game {game.game_number}: {output_file.name}")
                    done_games = downloaded_games + skipped_games + failed_games
                    print(f"  -> progress games: done={done_games} planned={planned_games}")
                    upsert_map(conn, match_id, mp_id, game.game_number, teams_json="[]")
                    if catalog_conn is not None:
                        upsert_catalog_map(catalog_conn, match_id, mp_id, game.game_number, teams_json="[]")
                    continue

                print("  -> segment processing started (local ffmpeg cut):")
                if not source_ready:
                    ok = False
                    error_message = source_error or "source video unavailable for local cutting"
                else:
                    ok, error_message = cut_segment_from_source(
                        source_file=source_file,
                        output_file=output_file,
                        start_sec=game.start_sec,
                        duration_sec=args.duration_sec,
                    )
                if ok:
                    downloaded_games += 1
                    status = "downloaded"
                    print(f"  -> saved game {game.game_number}: {output_file.name} ({file_size_mb(output_file):.1f} MB)")
                else:
                    failed_games += 1
                    status = "failed"
                    print(f"  -> failed game {game.game_number}: {error_message}")
                done_games = downloaded_games + skipped_games + failed_games
                print(f"  -> progress games: done={done_games} planned={planned_games}")

                upsert_game(
                    conn=conn,
                    game_id=game_id,
                    tournament_id=tournament_id,
                    video_id=item.youtube_video_id,
                    game_number=game.game_number,
                    start_sec=game.start_sec,
                    duration_sec=args.duration_sec,
                    output_filename=output_file.name,
                    output_path=str(output_file),
                    download_status=status,
                    error_message=error_message,
                )
                upsert_map(conn, match_id, mp_id, game.game_number, teams_json="[]")
                if catalog_conn is not None:
                    upsert_catalog_map(catalog_conn, match_id, mp_id, game.game_number, teams_json="[]")

            video_game_rows = conn.execute(
                "SELECT download_status FROM games WHERE youtube_video_id = ?",
                (item.youtube_video_id,),
            ).fetchall()
            if video_game_rows:
                statuses = {str(row[0]) for row in video_game_rows}
                if statuses.issubset({"downloaded", "completed"}):
                    upsert_video_mark(conn, item.youtube_video_id, "completed", "all_games_done")
                    upsert_match(conn, item, tournament_id, parsed, catalog_match, "completed")
                else:
                    upsert_video_mark(conn, item.youtube_video_id, "viewed", "processed_with_failures_or_pending")
                    upsert_match(conn, item, tournament_id, parsed, catalog_match, "viewed")
            else:
                upsert_video_mark(conn, item.youtube_video_id, "viewed", "processed_no_games_rows")
                upsert_match(conn, item, tournament_id, parsed, catalog_match, "viewed")
            if catalog_conn is not None:
                upsert_catalog_match(catalog_conn, item, parsed, catalog_match)
                catalog_conn.commit()
            if not args.dry_run and source_file.exists():
                try:
                    source_file.unlink()
                    print(f"  -> source removed after slicing: {source_file.name}")
                except OSError as exc:
                    print(f"  -> source cleanup warning ({source_file.name}): {exc}")
            conn.commit()

        conn.commit()
        if catalog_conn is not None:
            catalog_conn.commit()
            catalog_conn.close()
        print("========== ingest summary ==========")
        print(f"map videos matched title filter: {total_map_videos}")
        print(f"videos parsed with required metadata: {parsed_videos}")
        print(f"videos failed on metadata fetch: {failed_videos}")
        print(f"videos skipped from viewed/completed cache: {cached_viewed_skips}")
        print(f"videos skipped from non-map cache: {cached_non_map_skips}")
        print(f"games planned: {planned_games}")
        print(f"games downloaded: {downloaded_games}")
        print(f"games skipped/planned: {skipped_games}")
        print(f"games failed: {failed_games}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

