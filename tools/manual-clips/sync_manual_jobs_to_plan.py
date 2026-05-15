from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MONTH_MAP = {
    "January": "01",
    "February": "02",
    "March": "03",
    "April": "04",
    "May": "05",
    "June": "06",
    "July": "07",
    "August": "08",
    "September": "09",
    "October": "10",
    "November": "11",
    "December": "12",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync done manual_clip_jobs into videos/matches/games/maps/tournaments plan tables."
    )
    parser.add_argument(
        "--db-path",
        default="output/youtube_ingest/tournaments.sqlite",
        help="SQLite DB path that contains manual_clip_jobs and plan tables.",
    )
    parser.add_argument(
        "--include-status",
        action="append",
        default=["done"],
        help="manual_clip_jobs statuses to import (repeatable). Default: done",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_published_at_from_source(source_file: str) -> str:
    # Example fragment in source filename: "- April 5, 2026 -"
    match = re.search(r"-\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s*-", source_file)
    if not match:
        return "2026-01-01T00:00:00Z"
    month_name, day, year = match.group(1), match.group(2), match.group(3)
    month = MONTH_MAP.get(month_name, "01")
    return f"{year}-{month}-{day.zfill(2)}T00:00:00Z"


def normalize_region(region_token: str) -> str:
    if region_token == "EMEA":
        return "EMEA"
    if region_token == "AMERICAS":
        return "Americas"
    return region_token.title()


def sync_plan_rows(conn: sqlite3.Connection, statuses: list[str]) -> dict[str, int]:
    statuses = [s.strip() for s in statuses if s.strip()]
    if not statuses:
        statuses = ["done"]

    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT
            id,
            source_file,
            user_start_sec,
            detected_start_sec,
            final_start_sec,
            duration_sec,
            tournament_id,
            output_file,
            status
        FROM manual_clip_jobs
        WHERE status IN ({placeholders})
        ORDER BY id
        """,
        tuple(statuses),
    ).fetchall()

    if not rows:
        return {
            "videos": 0,
            "matches": 0,
            "tournaments": 0,
            "games": 0,
            "maps": 0,
            "plan_rows": 0,
            "jobs_seen": 0,
        }

    now = utc_now()
    seen_video_ids: set[str] = set()

    stats = {
        "videos": 0,
        "matches": 0,
        "tournaments": 0,
        "games": 0,
        "maps": 0,
        "plan_rows": 0,
        "jobs_seen": len(rows),
    }

    for row in rows:
        output_file = str(row["output_file"])
        output_filename = os.path.basename(output_file)
        parsed = re.search(r"MANUAL_Y(\d+)_S(\d+)_([A-Z_]+)_D(\d+)_G(\d+)\.mp4$", output_filename)
        if not parsed:
            continue

        year_number = int(parsed.group(1))
        split_number = int(parsed.group(2))
        region_token = parsed.group(3)
        day = int(parsed.group(4))
        game_number = int(parsed.group(5))

        region = normalize_region(region_token)
        region_slug = region_token.lower()
        youtube_video_id = f"manual_{region_slug}_d{day}"
        match_id = f"match_{youtube_video_id}"
        tournament_id = str(row["tournament_id"])
        source_file = str(row["source_file"])
        published_at = parse_published_at_from_source(source_file)

        if youtube_video_id not in seen_video_ids:
            title = Path(source_file).stem
            conn.execute(
                """
                INSERT OR REPLACE INTO videos(
                    youtube_video_id,
                    title,
                    webpage_url,
                    description,
                    published_at,
                    channel_id,
                    has_map_keyword,
                    last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (youtube_video_id, title, "", source_file, published_at, "manual_import", 1, now),
            )
            stats["videos"] += 1

            conn.execute(
                """
                INSERT OR REPLACE INTO matches(
                    id,
                    youtube_video_id,
                    tournament_id,
                    catalog_tournament_id,
                    catalog_tournament_name,
                    catalog_confidence,
                    region,
                    split_number,
                    day,
                    year_number,
                    video_url,
                    match_status,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    youtube_video_id,
                    tournament_id,
                    None,
                    tournament_id.replace("_", " "),
                    None,
                    region,
                    split_number,
                    day,
                    year_number,
                    source_file,
                    "manual_ready",
                    now,
                ),
            )
            stats["matches"] += 1

            tournament_exists = conn.execute(
                """
                SELECT id
                FROM tournaments
                WHERE tournament_id = ?
                LIMIT 1
                """,
                (tournament_id,),
            ).fetchone()

            if tournament_exists is None:
                conn.execute(
                    """
                    INSERT INTO tournaments(
                        tournament_id,
                        tournament_name,
                        region,
                        split_number,
                        day,
                        year_number,
                        catalog_tournament_id,
                        catalog_confidence,
                        team_ids_json,
                        source_video_id,
                        published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tournament_id,
                        tournament_id.replace("_", " "),
                        region,
                        split_number,
                        day,
                        year_number,
                        None,
                        None,
                        "[]",
                        youtube_video_id,
                        published_at,
                    ),
                )
                stats["tournaments"] += 1

            seen_video_ids.add(youtube_video_id)

        detected_start_sec = int(row["detected_start_sec"] or 0)
        user_start_sec = int(row["user_start_sec"] or 0)
        final_start_sec = row["final_start_sec"]
        if final_start_sec is None:
            final_start_sec = detected_start_sec + user_start_sec
        duration_sec = int(row["duration_sec"] or 1200)

        game_id = f"game_{youtube_video_id}_g{game_number}"
        conn.execute(
            """
            INSERT OR REPLACE INTO games(
                id,
                tournament_id,
                youtube_video_id,
                game_number,
                start_sec,
                duration_sec,
                output_filename,
                output_path,
                download_status,
                error_message,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                tournament_id,
                youtube_video_id,
                game_number,
                int(final_start_sec),
                duration_sec,
                output_filename,
                output_file,
                "done",
                None,
                now,
            ),
        )
        stats["games"] += 1

        map_id = f"map_{youtube_video_id}_r{game_number}"
        conn.execute(
            """
            INSERT OR REPLACE INTO maps(
                id,
                match_id,
                mp_id,
                teams,
                round_number,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (map_id, match_id, None, "[]", game_number, now),
        )
        stats["maps"] += 1

    plan_rows = conn.execute(
        """
        SELECT count(1)
        FROM games g
        JOIN matches m
          ON m.youtube_video_id = g.youtube_video_id
        WHERE g.output_filename IS NOT NULL
          AND length(trim(g.output_filename)) > 0
        """
    ).fetchone()
    stats["plan_rows"] = int(plan_rows[0] if plan_rows else 0)
    return stats


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        stats = sync_plan_rows(conn, statuses=args.include_status)
        conn.commit()

    print(f"[sync] jobs_seen={stats['jobs_seen']}")
    print(f"[sync] upserted videos={stats['videos']} matches={stats['matches']} tournaments={stats['tournaments']}")
    print(f"[sync] upserted games={stats['games']} maps={stats['maps']}")
    print(f"[sync] plan_rows={stats['plan_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
