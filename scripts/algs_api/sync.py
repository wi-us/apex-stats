"""Walk the ALGS API and upsert into SQLite.

Sub-commands (all idempotent and safe to re-run):
    reference   - sync /v1/maps and /v1/characters
    seasons     - sync the season list only
    all         - walk every season (heavy: matches/POI/all stats)
    season      - walk one season
    event       - walk one event (incl. schedule/standings/teams/CC)
    series      - walk one series (matches/POI/stats deep)
    upcoming    - refresh upcoming series list
    live        - refresh live streams
    standings   - season + event + phase standings for one season
    cc          - CC leaderboard for a season/event
    refresh     - smart refresh: upcoming + live + recent series

Freshness:
- Completed series stats are written once (immutable) and never re-fetched
  unless --force is given.
- Live/upcoming and standings have short TTLs.
- The on-disk HTTP cache also throttles repeated calls.

Throttling: see client.py (token bucket + jittered backoff + disk cache).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import client, db


# Module-level run options (set from CLI in main()).
SKIP_EXISTING = False
FORCE = False

# Default freshness windows (seconds). Override with --max-age on `refresh`.
TTL_LIVE        = 120         # 2 min
TTL_UPCOMING    = 600         # 10 min
TTL_STANDINGS   = 6 * 3600    # 6 h
TTL_LEADERBOARD = 6 * 3600    # 6 h
TTL_TEAM        = 7 * 86400   # 7 d
TTL_SERIES_LIVE = 5 * 60      # 5 min for in-progress series
TTL_SERIES_DONE = 30 * 86400  # 30 d for completed series (essentially "once")


def _series_has_matches(conn, series_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM matches WHERE series_id=? LIMIT 1", (series_id,)
    ).fetchone()
    return row is not None


def _log_skip(kind: str, ident: str, reason: str) -> None:
    print(f"[sync] skip {kind} {ident}: {reason}", file=sys.stderr)


def _log(msg: str) -> None:
    print(f"[sync] {msg}", file=sys.stderr)


def _is_fresh(conn, kind: str, ident: str, ttl: int) -> bool:
    if FORCE:
        return False
    last = db.last_synced(conn, kind, ident)
    if not last:
        return False
    age = time.time() - last[0]
    return age < ttl


def _series_is_completed(conn, series_id: str) -> bool:
    row = conn.execute(
        "SELECT status, completed_at FROM series WHERE id=?", (series_id,)
    ).fetchone()
    if not row:
        return False
    return (row["status"] == "completed") or bool(row["completed_at"])


# --------------------------------- reference --------------------------------

def _sync_reference(conn) -> None:
    if _is_fresh(conn, "reference", "maps", 86400):
        return
    _log("/v1/maps")
    for m in client.maps():
        db.upsert_map(conn, m)
    _log("/v1/characters")
    for c in client.characters():
        db.upsert_character(conn, c)
    db.mark_synced(conn, "reference", "maps")


# --------------------------------- teams ------------------------------------

def _ensure_team(conn, team_id: str) -> None:
    """Fetch /v1/teams/{id} once per team_id (7d TTL)."""
    if not team_id:
        return
    if _is_fresh(conn, "team", team_id, TTL_TEAM):
        return
    try:
        t = client.team(team_id)
        db.upsert_team(conn, t)
        db.mark_synced(conn, "team", team_id, "ok")
    except client.AlgsNotFound:
        db.mark_synced(conn, "team", team_id, "not_available")
        _log_skip("team", team_id, "no /teams endpoint (404)")
    except client.AlgsApiError as e:
        _log(f"team {team_id}: {e}")


# --------------------------------- series deep ------------------------------

def _sync_series_deep_stats(conn, series_id: str) -> None:
    """Pull all per-series aggregates (weapons / characters / pois /
    players / banned-legends / compositions). Cheap-ish individually."""
    # weapons
    try:
        data = client.stats_weapons(seriesId=series_id)
        for w in data.get("stats") or []:
            db.upsert_weapon_stat(conn, series_id, w)
    except client.AlgsNotFound:
        _log_skip("weapons", series_id, "n/a")

    # characters
    try:
        data = client.stats_characters(seriesId=series_id)
        for c in data.get("characters") or []:
            db.upsert_character_stat(conn, series_id, c)
    except client.AlgsNotFound:
        _log_skip("characters", series_id, "n/a")

    # character compositions (popular legend trios)
    try:
        data = client.stats_characters_composition(seriesId=series_id)
        conn.execute(
            "DELETE FROM series_character_compositions WHERE series_id=?",
            (series_id,),
        )
        for idx, comp in enumerate(data.get("characterCompositions") or []):
            db.upsert_character_composition(
                conn, series_id, idx, comp.get("composition") or [],
            )
    except client.AlgsNotFound:
        _log_skip("compositions", series_id, "n/a")

    # player aggregates (kills / assists / averages)
    try:
        data = client.stats_players(seriesId=series_id)
        for p in data.get("stats") or []:
            db.upsert_player_agg(conn, series_id, p)
    except client.AlgsNotFound:
        _log_skip("player-agg", series_id, "n/a")

    # POI stats
    try:
        data = client.stats_pois(seriesId=series_id)
        for p in data.get("stats") or []:
            db.upsert_poi_stat(conn, series_id, p)
    except client.AlgsNotFound:
        _log_skip("poi-stats", series_id, "n/a")

    # banned legends (series-wide)
    try:
        for b in client.series_banned_legends(series_id):
            db.upsert_banned_legend_agg(conn, series_id, b)
    except client.AlgsNotFound:
        _log_skip("banned-legends", series_id, "n/a")


def _sync_series_full(conn, series_id: str) -> None:
    """Sync a series + matches + POI draft + match stats + deep aggregates."""
    if SKIP_EXISTING and _series_has_matches(conn, series_id):
        _log_skip("series", series_id, "already in DB (--skip-existing)")
        return

    # TTL gate: if the series is already completed and we synced it once,
    # don't refetch unless --force.
    if _series_is_completed(conn, series_id) and _is_fresh(
        conn, "series", series_id, TTL_SERIES_DONE
    ):
        return

    s = client.series(series_id)
    db.upsert_series(conn, s)

    # Roster from the series payload
    for t in s.get("teams") or []:
        db.upsert_team(conn, t)

    # Matches
    try:
        for m in client.series_matches(series_id):
            db.upsert_match(conn, m)
    except client.AlgsNotFound:
        _log_skip("matches", series_id, "n/a (404)")
    except client.AlgsApiError as e:
        _log(f"series {series_id}: matches: {e}")

    # Series teams (richer)
    try:
        for t in client.series_teams(series_id):
            db.upsert_team(conn, t)
            for pl in t.get("players") or []:
                db.upsert_player(conn, pl)
    except client.AlgsNotFound:
        _log_skip("teams", series_id, "n/a (404)")
    except client.AlgsApiError as e:
        _log(f"series {series_id}: teams: {e}")

    # Series stats (per-team standings)
    try:
        stats = client.stats_series(series_id)
        for t in stats.get("teams") or []:
            db.upsert_series_team_stats(conn, series_id, t)
    except client.AlgsNotFound:
        _log_skip("stats", series_id, "n/a (404)")
    except client.AlgsApiError as e:
        _log(f"series {series_id}: stats_series: {e}")

    # Per-match stats
    rows = conn.execute(
        "SELECT id, match_number FROM matches WHERE series_id=?", (series_id,)
    ).fetchall()
    for row in rows:
        if row["match_number"] is None:
            continue
        try:
            ms = client.stats_series_match(series_id, int(row["match_number"]))
            mid = ms.get("matchId") or row["id"]
            for t in ms.get("teams") or []:
                db.upsert_match_team_stats(conn, mid, t)
        except client.AlgsNotFound:
            _log_skip("match-stats",
                      f"{series_id}#{row['match_number']}",
                      "n/a (404)")
        except client.AlgsApiError as e:
            _log(f"series {series_id} match #{row['match_number']}: {e}")

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
        except client.AlgsNotFound:
            _log_skip("poi-draft", draft_id, "n/a (404)")
        except client.AlgsApiError as e:
            _log(f"series {series_id} poi {draft_id}: {e}")

    # Deep per-series aggregates
    _sync_series_deep_stats(conn, series_id)

    # Resolve any team ids we have logos missing for (covers the 404-team
    # warning by recording 'not_available' once).
    for r in conn.execute(
        "SELECT DISTINCT t.id FROM teams t "
        "LEFT JOIN team_versions v ON v.team_id=t.id "
        "WHERE v.team_id IS NULL"
    ).fetchall():
        _ensure_team(conn, r["id"])

    db.mark_synced(conn, "series", series_id, "ok")
    conn.commit()


# --------------------------------- event ------------------------------------

def _sync_event_aux(conn, event_id: str) -> None:
    """Schedule / standings / teams / phase teams + standings."""
    if not _is_fresh(conn, "event-schedule", event_id, TTL_STANDINGS):
        try:
            for ph in client.event_schedule(event_id):
                for t in ph.get("teams") or []:
                    db.upsert_event_schedule_team(conn, event_id, ph["id"], t)
            db.mark_synced(conn, "event-schedule", event_id)
        except client.AlgsNotFound:
            _log_skip("event-schedule", event_id, "n/a")

    if not _is_fresh(conn, "event-standings", event_id, TTL_STANDINGS):
        try:
            for s in client.event_standings(event_id):
                db.upsert_event_standing(conn, event_id, s)
            db.mark_synced(conn, "event-standings", event_id)
        except client.AlgsNotFound:
            _log_skip("event-standings", event_id, "n/a")

    if not _is_fresh(conn, "event-teams", event_id, TTL_STANDINGS):
        try:
            for t in client.event_teams(event_id):
                db.upsert_event_team(conn, event_id, t)
            db.mark_synced(conn, "event-teams", event_id)
        except client.AlgsNotFound:
            _log_skip("event-teams", event_id, "n/a")


def _sync_phase_aux(conn, phase_id: str) -> None:
    if not _is_fresh(conn, "phase-teams", phase_id, TTL_STANDINGS):
        try:
            for t in client.phase_teams(phase_id):
                db.upsert_phase_team(conn, phase_id, t)
            db.mark_synced(conn, "phase-teams", phase_id)
        except client.AlgsNotFound:
            _log_skip("phase-teams", phase_id, "n/a")

    if not _is_fresh(conn, "phase-standings", phase_id, TTL_STANDINGS):
        try:
            for s in client.stats_phase_standings(phase_id):
                db.upsert_phase_standing(conn, phase_id, s)
            db.mark_synced(conn, "phase-standings", phase_id)
        except client.AlgsNotFound:
            _log_skip("phase-standings", phase_id, "n/a")


def _sync_event_full(conn, event_id: str) -> None:
    try:
        ev = client.event(event_id)
    except client.AlgsNotFound:
        _log_skip("event", event_id, "not found (404)")
        return
    except client.AlgsApiError as e:
        _log(f"event {event_id}: {e}")
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

    # Event-level extras (schedule/standings/teams)
    _sync_event_aux(conn, event_id)

    # Event maps
    try:
        for m in client.event_maps(event_id):
            db.upsert_map(conn, {**m, "id": m.get("mapId") or m.get("id"),
                                  "active": True})
    except client.AlgsNotFound:
        pass
    except client.AlgsApiError as e:
        _log(f"event {event_id}: maps: {e}")

    # Event structure -> phases + series
    try:
        st = client.event_structure(event_id)
        for ph in st.get("phases") or []:
            db.upsert_phase(conn, {**ph, "eventId": event_id})
            _sync_phase_aux(conn, ph["id"])
            for s in ph.get("series") or []:
                db.upsert_series(conn, {**s, "phaseId": ph["id"],
                                         "eventId": event_id,
                                         "regionId": region.get("id"),
                                         "tournamentId": tournament.get("id"),
                                         "seasonId": season.get("id")})
                _sync_series_full(conn, s["id"])
    except client.AlgsNotFound:
        _log_skip("event-structure", event_id, "n/a")
    except client.AlgsApiError as e:
        _log(f"event {event_id}: structure: {e}")


# --------------------------------- season -----------------------------------

def _sync_season_full(conn, season_id: str) -> None:
    try:
        st = client.season_structure(season_id)
    except client.AlgsNotFound:
        _log_skip("season", season_id, "structure n/a (404)")
        return
    except client.AlgsApiError as e:
        _log(f"season {season_id}: {e}")
        return
    db.upsert_season(conn, st)
    for t in st.get("tournaments") or []:
        db.upsert_tournament(conn, {**t, "seasonId": season_id})
        for r in t.get("regions") or []:
            db.upsert_region(conn, r, t["id"])
            for ev in r.get("events") or []:
                db.upsert_event(conn, ev, tournament_id=t["id"],
                                region_id=r["id"])
                _sync_event_full(conn, ev["id"])


def _sync_season_standings(conn, season_id: str) -> None:
    if _is_fresh(conn, "season-standings-teams", season_id, TTL_STANDINGS):
        return
    try:
        for s in client.season_standings_teams(season_id):
            db.upsert_season_standing_team(conn, season_id, s)
        db.mark_synced(conn, "season-standings-teams", season_id)
    except client.AlgsNotFound:
        _log_skip("season-standings-teams", season_id, "n/a")
    try:
        for p in client.season_standings_players(season_id):
            db.upsert_season_standing_player(conn, season_id, p)
        db.mark_synced(conn, "season-standings-players", season_id)
    except client.AlgsNotFound:
        _log_skip("season-standings-players", season_id, "n/a")


# --------------------------------- live/upcoming ----------------------------

def _sync_upcoming(conn) -> None:
    if _is_fresh(conn, "global", "upcoming", TTL_UPCOMING):
        return
    try:
        for s in client.series_upcoming():
            db.upsert_series(conn, s)
        db.mark_synced(conn, "global", "upcoming")
    except client.AlgsNotFound:
        _log_skip("upcoming", "*", "n/a")


def _sync_live(conn) -> None:
    if _is_fresh(conn, "global", "live", TTL_LIVE):
        return
    try:
        for s in client.streams_live():
            sid = s["id"]
            db.upsert_series(conn, s)
            conn.execute(
                "DELETE FROM live_streams WHERE series_id=?", (sid,),
            )
            for st in s.get("streams") or []:
                db.upsert_live_stream(conn, sid, st)
        db.mark_synced(conn, "global", "live")
    except client.AlgsNotFound:
        _log_skip("live", "*", "n/a")


# --------------------------------- CC leaderboard ---------------------------

def _sync_cc(conn, season_id: str, event_id: str,
             region: str | None = None) -> None:
    key = f"{season_id}/{event_id}"
    if _is_fresh(conn, "cc-teams", key, TTL_LEADERBOARD):
        return
    try:
        d = client.cc_leaderboard_teams(season_id, event_id, region=region)
        for r in d.get("standings") or d.get("teams") or []:
            db.upsert_cc_team(conn, season_id, event_id, r)
        db.mark_synced(conn, "cc-teams", key)
    except client.AlgsNotFound:
        _log_skip("cc-teams", key, "n/a")
    try:
        d = client.cc_leaderboard_players(season_id, event_id, region=region)
        for r in d.get("standings") or d.get("players") or []:
            db.upsert_cc_player(conn, season_id, event_id, r)
        db.mark_synced(conn, "cc-players", key)
    except client.AlgsNotFound:
        _log_skip("cc-players", key, "n/a")


# --------------------------------- refresh ----------------------------------

def _refresh(conn) -> None:
    """Light, frequent refresh: upcoming + live, and re-sync each series
    that's listed as live so its match feed updates."""
    _sync_upcoming(conn)
    _sync_live(conn)

    live_series = [r["id"] for r in conn.execute(
        "SELECT DISTINCT series_id AS id FROM live_streams"
    ).fetchall()]
    for sid in live_series:
        # Bypass TTL for live series with a short window.
        last = db.last_synced(conn, "series", sid)
        if last and (time.time() - last[0] < TTL_SERIES_LIVE) and not FORCE:
            continue
        _log(f"refresh live series {sid}")
        try:
            _sync_series_full(conn, sid)
        except Exception as e:  # noqa: BLE001
            _log(f"refresh {sid} failed: {e}")


# ---------------------------------- CLI -------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(prog="scripts.algs_api.sync")
    ap.add_argument("--db", type=Path, default=None,
                    help="SQLite path (default: scripts/algs_api/data/algs.sqlite)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip series that already have matches in the local DB.")
    ap.add_argument("--force", action="store_true",
                    help="Bypass TTL freshness gates and re-fetch everything.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reference", help="sync /v1/maps and /v1/characters")
    sub.add_parser("seasons",   help="sync the season list only")
    sub.add_parser("all",       help="walk every season (heavy)")
    sub.add_parser("upcoming",  help="refresh the upcoming series list")
    sub.add_parser("live",      help="refresh live-streams + live series")
    sub.add_parser("refresh",   help="upcoming + live + live-series matches")
    sp = sub.add_parser("season", help="walk a full season tree")
    sp.add_argument("--id", required=True)
    sst = sub.add_parser("standings", help="season standings (teams+players)")
    sst.add_argument("--season", required=True)
    se = sub.add_parser("event",  help="walk one event")
    se.add_argument("--id", required=True)
    sr = sub.add_parser("series", help="walk one series")
    sr.add_argument("--id", required=True)
    cc = sub.add_parser("cc", help="CC leaderboard for a season/event")
    cc.add_argument("--season", required=True)
    cc.add_argument("--event",  required=True)
    cc.add_argument("--region", default=None)
    args = ap.parse_args()

    global SKIP_EXISTING, FORCE
    SKIP_EXISTING = bool(args.skip_existing)
    FORCE = bool(args.force)

    with db.connect(args.db) as conn:
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
                _log(f"season {s['id']} ({s.get('name')})")
                _sync_season_full(conn, s["id"])
                _sync_season_standings(conn, s["id"])
            return
        if args.cmd == "season":
            _sync_season_full(conn, args.id)
            _sync_season_standings(conn, args.id)
            return
        if args.cmd == "standings":
            _sync_season_standings(conn, args.season)
            return
        if args.cmd == "event":
            _sync_event_full(conn, args.id)
            return
        if args.cmd == "series":
            _sync_series_full(conn, args.id)
            return
        if args.cmd == "upcoming":
            _sync_upcoming(conn)
            return
        if args.cmd == "live":
            _sync_live(conn)
            return
        if args.cmd == "refresh":
            _refresh(conn)
            return
        if args.cmd == "cc":
            _sync_cc(conn, args.season, args.event, region=args.region)
            return

    _log("done")


if __name__ == "__main__":
    main()
