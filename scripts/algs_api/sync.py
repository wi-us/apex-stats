"""Walk the ALGS API and upsert into SQLite.

Sub-commands:
    reference   - sync /v1/maps and /v1/characters
    seasons     - sync all seasons (without recursing)
    season --id - sync a season's full tree (events, series, matches, POI, stats)
    event  --id - sync a single event (series, matches, POI, stats)
    series --id - sync a single series (matches, POI, stats)

All sub-commands are idempotent and safe to re-run. They are throttled by
`client.py` (token bucket + on-disk cache).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import client, db


# -------------------------------- helpers ------------------------------------

def _sync_reference(conn) -> None:
    print("[sync] /v1/maps", file=sys.stderr)
    for m in client.maps():
        db.upsert_map(conn, m)
    print("[sync] /v1/characters", file=sys.stderr)
    for c in client.characters():
        db.upsert_character(conn, c)


def _sync_series_full(conn, series_id: str) -> None:
    """Sync a series + its matches + POI draft + stats."""
    s = client.series(series_id)
    db.upsert_series(conn, s)

    # Series-level rosters
    for t in s.get("teams") or []:
        db.upsert_team(conn, t)

    # Matches
    try:
        for m in client.series_matches(series_id):
            db.upsert_match(conn, m)
    except client.AlgsApiError as e:
        print(f"[sync] series {series_id}: matches: {e}", file=sys.stderr)

    # Series teams (richer)
    try:
        for t in client.series_teams(series_id):
            db.upsert_team(conn, t)
            for pl in t.get("players") or []:
                db.upsert_player(conn, pl)
    except client.AlgsApiError as e:
        print(f"[sync] series {series_id}: teams: {e}", file=sys.stderr)

    # Series stats
    try:
        stats = client.stats_series(series_id)
        for t in stats.get("teams") or []:
            db.upsert_series_team_stats(conn, series_id, t)
    except client.AlgsApiError as e:
        print(f"[sync] series {series_id}: stats_series: {e}", file=sys.stderr)

    # Per-match stats
    matches = conn.execute(
        "SELECT id, match_number FROM matches WHERE series_id=?", (series_id,)
    ).fetchall()
    for row in matches:
        if row["match_number"] is None:
            continue
        try:
            ms = client.stats_series_match(series_id, int(row["match_number"]))
            mid = ms.get("matchId") or row["id"]
            for t in ms.get("teams") or []:
                db.upsert_match_team_stats(conn, mid, t)
        except client.AlgsApiError as e:
            print(f"[sync] series {series_id} match #{row['match_number']}: {e}",
                  file=sys.stderr)

    # POI draft
    draft_id = s.get("poiDraftId")
    if draft_id:
        try:
            d = client.poi_draft(draft_id)
            db.upsert_poi_draft(conn, d)
            for loc in client.poi_draft_locations(draft_id):
                db.upsert_spawn_location(conn, loc)
            for pick in client.poi_draft_picks(draft_id):
                db.upsert_poi_pick(conn, draft_id, pick)
        except client.AlgsApiError as e:
            print(f"[sync] series {series_id} poi {draft_id}: {e}",
                  file=sys.stderr)

    conn.commit()


def _sync_event_full(conn, event_id: str) -> None:
    try:
        ev = client.event(event_id)
    except client.AlgsApiError as e:
        print(f"[sync] event {event_id}: {e}", file=sys.stderr)
        return
    tournament = ev.get("tournament") or {}
    region = ev.get("region") or {}
    season = ev.get("season") or {}
    if season.get("id"):
        db.upsert_season(conn, season)
    if tournament.get("id"):
        db.upsert_tournament(conn, {**tournament,
                                    "seasonId": season.get("id")})
    if region.get("id"):
        db.upsert_region(conn, region, tournament.get("id"))
    db.upsert_event(conn, ev,
                    tournament_id=tournament.get("id"),
                    region_id=region.get("id"))

    # Event maps
    try:
        for m in client.event_maps(event_id):
            db.upsert_map(conn, {**m, "id": m.get("mapId") or m.get("id"),
                                  "active": True})
    except client.AlgsApiError as e:
        print(f"[sync] event {event_id}: maps: {e}", file=sys.stderr)

    # Event structure -> phases + series
    try:
        st = client.event_structure(event_id)
        for ph in st.get("phases") or []:
            db.upsert_phase(conn, {**ph, "eventId": event_id})
            for s in ph.get("series") or []:
                # Stub upsert so we have at least name/poi_draft for filtering;
                # _sync_series_full will overwrite with the full record.
                db.upsert_series(conn, {**s, "phaseId": ph["id"],
                                         "eventId": event_id,
                                         "regionId": region.get("id"),
                                         "tournamentId": tournament.get("id"),
                                         "seasonId": season.get("id")})
                _sync_series_full(conn, s["id"])
    except client.AlgsApiError as e:
        print(f"[sync] event {event_id}: structure: {e}", file=sys.stderr)


def _sync_season_full(conn, season_id: str) -> None:
    try:
        st = client.season_structure(season_id)
    except client.AlgsApiError as e:
        print(f"[sync] season {season_id}: {e}", file=sys.stderr)
        return
    db.upsert_season(conn, st)
    for t in st.get("tournaments") or []:
        db.upsert_tournament(conn, {**t, "seasonId": season_id})
        for r in t.get("regions") or []:
            db.upsert_region(conn, r, t["id"])
            for ev in r.get("events") or []:
                # Stub event row first, then full sync (which will fill
                # tournament/region/season ids correctly).
                db.upsert_event(conn, ev, tournament_id=t["id"],
                                region_id=r["id"])
                _sync_event_full(conn, ev["id"])


# ---------------------------------- CLI --------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(prog="scripts.algs_api.sync")
    ap.add_argument("--db", type=Path, default=None,
                    help="SQLite path (default: scripts/algs_api/data/algs.sqlite)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reference", help="sync /v1/maps and /v1/characters")
    sub.add_parser("seasons",   help="sync the season list only")
    sub.add_parser("all",       help="sync every season returned by /v1/seasons")
    sp = sub.add_parser("season", help="sync a full season tree")
    sp.add_argument("--id", required=True)
    se = sub.add_parser("event",  help="sync one event")
    se.add_argument("--id", required=True)
    sr = sub.add_parser("series", help="sync one series")
    sr.add_argument("--id", required=True)
    args = ap.parse_args()

    with db.connect(args.db) as conn:
        # Reference is cheap and harmless; always refresh.
        _sync_reference(conn)

        if args.cmd == "reference":
            return
        if args.cmd == "seasons":
            for s in client.seasons():
                db.upsert_season(conn, s)
            return
        if args.cmd == "all":
            for s in client.seasons():
                db.upsert_season(conn, s)
                print(f"[sync] season {s['id']} ({s.get('name')})",
                      file=sys.stderr)
                _sync_season_full(conn, s["id"])
            return
        if args.cmd == "season":
            _sync_season_full(conn, args.id)
            return
        if args.cmd == "event":
            _sync_event_full(conn, args.id)
            return
        if args.cmd == "series":
            _sync_series_full(conn, args.id)
            return

    print("[sync] done", file=sys.stderr)


if __name__ == "__main__":
    main()