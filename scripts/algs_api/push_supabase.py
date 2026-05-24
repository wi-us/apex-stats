"""Push the local ALGS SQLite cache into Supabase (Lovable Cloud).

Reads every `algs_*`-mirrored table from `scripts/algs_api/data/algs.sqlite`
and bulk-upserts it into the matching `public.algs_*` table in Supabase.

Requires (env or .env in project root):
    SUPABASE_URL                  (already in .env)
    SUPABASE_SERVICE_ROLE_KEY     (NOT public — see README)

Usage:
    pip install -r scripts/algs_api/requirements.txt
    python -m scripts.algs_api.push_supabase                # push everything
    python -m scripts.algs_api.push_supabase --only matches series
    python -m scripts.algs_api.push_supabase --chunk 500

It is idempotent: every row is sent with `upsert(on_conflict=<pk>)`, so
re-running just refreshes whatever has changed locally.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:  # noqa: BLE001
    pass

from supabase import Client, create_client  # type: ignore

from . import db


# (sqlite_table, supabase_table, pk_tuple, json_columns)
TABLES: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("seasons",                       "algs_seasons",                       ("id",), ()),
    ("tournaments",                   "algs_tournaments",                   ("id",), ()),
    ("regions",                       "algs_regions",                       ("id",), ()),
    ("events",                        "algs_events",                        ("id",), ()),
    ("phases",                        "algs_phases",                        ("id",), ()),
    ("series",                        "algs_series",                        ("id",), ()),
    ("maps",                          "algs_maps",                          ("id_ulid",), ()),
    ("teams",                         "algs_teams",                         ("id",), ()),
    ("team_versions",                 "algs_team_versions",                 ("version_id",), ()),
    ("characters",                    "algs_characters",                    ("id",), ()),
    ("players",                       "algs_players",                       ("id",), ()),
    ("spawn_locations",               "algs_spawn_locations",               ("id",), ()),
    ("poi_drafts",                    "algs_poi_drafts",                    ("id",), ("raw_json",)),
    ("poi_picks",                     "algs_poi_picks",                     ("id",), ()),
    ("matches",                       "algs_matches",                       ("id",), ("raw_json",)),
    ("match_banned_legends",          "algs_match_banned_legends",          ("match_id", "character_id"), ()),
    ("series_team_stats",             "algs_series_team_stats",             ("series_id", "team_id"), ("raw_json",)),
    ("match_team_stats",              "algs_match_team_stats",              ("match_id", "team_id"), ("raw_json",)),
    ("match_player_stats",            "algs_match_player_stats",            ("match_id", "player_id"), ("raw_json",)),
    ("series_weapon_stats",           "algs_series_weapon_stats",           ("series_id", "weapon"), ()),
    ("series_character_stats",        "algs_series_character_stats",        ("series_id", "character_id"), ()),
    ("series_character_compositions", "algs_series_character_compositions", ("series_id", "comp_idx", "slot_idx"), ()),
    ("series_player_agg",             "algs_series_player_agg",             ("series_id", "player_id"), ("raw_json",)),
    ("series_banned_legends_agg",     "algs_series_banned_legends_agg",     ("series_id", "character_id"), ()),
    ("series_poi_stats",              "algs_series_poi_stats",              ("series_id", "spawn_location_id"), ("raw_json",)),
    ("event_teams",                   "algs_event_teams",                   ("event_id", "team_id"), ("raw_json",)),
    ("event_standings",               "algs_event_standings",               ("event_id", "team_id"), ("raw_json",)),
    ("event_schedule",                "algs_event_schedule",                ("event_id", "phase_id", "team_name"), ()),
    ("phase_teams",                   "algs_phase_teams",                   ("phase_id", "team_id"), ("raw_json",)),
    ("phase_standings",               "algs_phase_standings",               ("phase_id", "team_id"), ("raw_json",)),
    ("season_standings_teams",        "algs_season_standings_teams",        ("season_id", "team_id"), ("raw_json",)),
    ("season_standings_players",      "algs_season_standings_players",      ("season_id", "player_id"), ("raw_json",)),
    ("cc_leaderboard_teams",          "algs_cc_leaderboard_teams",          ("season_id", "event_id", "team_id"), ("raw_json",)),
    ("cc_leaderboard_players",        "algs_cc_leaderboard_players",        ("season_id", "event_id", "player_id"), ("raw_json",)),
    ("live_streams",                  "algs_live_streams",                  ("series_id", "stream_id"), ("raw_json",)),
    ("sync_state",                    "algs_sync_state",                    ("kind", "ident"), ()),
]

# Columns that SQLite stores as 0/1 INTEGER but Supabase expects boolean.
BOOL_COLUMNS: set[str] = {
    "is_main", "has_standings", "is_match_point", "winner_determined",
    "completed", "timed_out", "disbanded", "active",
    "match_point_eligible", "won_match_point", "eliminated",
    "qualified", "in_live_series",
}


def _row_to_supabase(row: dict, json_cols: tuple[str, ...]) -> dict:
    out: dict = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
            continue
        if k in json_cols and isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except Exception:  # noqa: BLE001
                out[k] = None
            continue
        if k in BOOL_COLUMNS:
            out[k] = bool(v)
            continue
        # epoch seconds -> ISO for sync_state.fetched_at
        if k == "fetched_at" and isinstance(v, (int, float)):
            out[k] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(v))
            )
            continue
        out[k] = v
    return out


def _push_table(sb: Client, conn, sqlite_table: str, sb_table: str,
                pk: tuple[str, ...], json_cols: tuple[str, ...],
                chunk: int) -> int:
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {sqlite_table}")]
    if not rows:
        return 0
    on_conflict = ",".join(pk)
    pushed = 0
    for i in range(0, len(rows), chunk):
        batch = [_row_to_supabase(r, json_cols) for r in rows[i:i + chunk]]
        sb.table(sb_table).upsert(batch, on_conflict=on_conflict).execute()
        pushed += len(batch)
        print(f"  {sb_table}: {pushed}/{len(rows)}", file=sys.stderr)
    return pushed


def main() -> None:
    ap = argparse.ArgumentParser(prog="scripts.algs_api.push_supabase")
    ap.add_argument("--db", type=Path, default=None,
                    help="SQLite path (default: scripts/algs_api/data/algs.sqlite)")
    ap.add_argument("--chunk", type=int, default=500,
                    help="Rows per upsert batch (default 500).")
    ap.add_argument("--only", nargs="*", default=None,
                    help="Only push these SQLite tables (space-separated).")
    args = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit(
            "ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.\n"
            "See scripts/algs_api/README.md (section: Push to Lovable Cloud)."
        )
    sb: Client = create_client(url, key)

    only = set(args.only) if args.only else None
    total = 0
    with db.connect(args.db) as conn:
        for sqlite_table, sb_table, pk, json_cols in TABLES:
            if only and sqlite_table not in only:
                continue
            print(f"[push] {sqlite_table} -> {sb_table}", file=sys.stderr)
            try:
                total += _push_table(sb, conn, sqlite_table, sb_table,
                                     pk, json_cols, args.chunk)
            except Exception as e:  # noqa: BLE001
                print(f"[push] FAILED {sb_table}: {e}", file=sys.stderr)

    print(f"[push] done. Upserted ~{total} rows.", file=sys.stderr)


if __name__ == "__main__":
    main()