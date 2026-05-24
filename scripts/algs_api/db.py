"""SQLite cache for ALGS API data.

Designed for easy migration to Postgres later:
- All ids are TEXT (ULID from upstream)
- Timestamps stored as TEXT in ISO-8601 UTC
- All upserts are INSERT ... ON CONFLICT DO UPDATE
- Only TEXT / INTEGER / REAL column types
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = Path(os.environ.get(
    "ALGS_API_DB",
    str(Path(__file__).resolve().parent / "data" / "algs.sqlite"),
))


# ----------------------- map ULID -> canonical short id ----------------------
MAP_ID_BY_ULID: dict[str, str] = {
    "01J6508ZVM8PZKJ9VSKA9SF33P": "olympus",
    "01J6508ZVMQGRZDC3XSNER795R": "kings_canyon",
    "01J6508ZVME92QPVXGJN21ZWCA": "storm_point",
    "01J6508ZVM9M8WFR5KVFB6R1FD": "worlds_edge",
    "01J6M00SDXM1G05TA8D96559MJ": "e_district",
    "01J6508ZVMSXSMEN6J4M5G5V38": "broken_moon",
}


def canonical_map_id(map_ulid: str | None) -> str | None:
    if not map_ulid:
        return None
    return MAP_ID_BY_ULID.get(map_ulid)


# ---------------------------------- schema -----------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS seasons (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    is_main       INTEGER,
    start_date    TEXT,
    end_date      TEXT,
    fetched_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tournaments (
    id            TEXT PRIMARY KEY,
    season_id     TEXT,
    vendor_id     TEXT,
    name          TEXT,
    start_date    TEXT,
    end_date      TEXT,
    fetched_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS regions (
    id            TEXT PRIMARY KEY,
    tournament_id TEXT,
    name          TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,
    tournament_id TEXT,
    region_id     TEXT,
    name          TEXT,
    start_date    TEXT,
    end_date      TEXT,
    has_standings INTEGER,
    fetched_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS phases (
    id            TEXT PRIMARY KEY,
    event_id      TEXT,
    name          TEXT,
    phase_number  INTEGER,
    format        TEXT,
    starts_at     TEXT,
    completed_at  TEXT,
    has_standings INTEGER
);

CREATE TABLE IF NOT EXISTS series (
    id                  TEXT PRIMARY KEY,
    name                TEXT,
    status              TEXT,
    series_number       INTEGER,
    phase_id            TEXT,
    event_id            TEXT,
    region_id           TEXT,
    tournament_id       TEXT,
    season_id           TEXT,
    poi_draft_id        TEXT,
    starts_at           TEXT,
    completed_at        TEXT,
    is_match_point      INTEGER,
    match_point_threshold INTEGER,
    vod_url             TEXT,
    fetched_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS maps (
    id_ulid       TEXT PRIMARY KEY,
    name          TEXT,
    in_game_name  TEXT,
    canonical_id  TEXT,
    active        INTEGER
);

CREATE TABLE IF NOT EXISTS matches (
    id                TEXT PRIMARY KEY,
    match_number      INTEGER,
    series_id         TEXT,
    phase_id          TEXT,
    event_id          TEXT,
    region_id         TEXT,
    tournament_id     TEXT,
    season_id         TEXT,
    map_id_ulid       TEXT,
    status            TEXT,
    in_game_status    TEXT,
    winner_determined INTEGER,
    winner_team_id    TEXT,
    winner_damage     INTEGER,
    winner_kills      INTEGER,
    started_at        TEXT,
    play_started_at   TEXT,
    completed_at      TEXT,
    raw_json          TEXT
);

CREATE TABLE IF NOT EXISTS match_banned_legends (
    match_id       TEXT,
    character_id   TEXT,
    PRIMARY KEY (match_id, character_id)
);

CREATE TABLE IF NOT EXISTS teams (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    short_name    TEXT,
    region        TEXT,
    disbanded     INTEGER
);

CREATE TABLE IF NOT EXISTS team_versions (
    version_id    TEXT PRIMARY KEY,
    team_id       TEXT,
    logo_light    TEXT,
    logo_dark     TEXT
);

CREATE TABLE IF NOT EXISTS characters (
    id              TEXT PRIMARY KEY,
    name            TEXT,
    image           TEXT,
    character_type  TEXT,
    internal_name   TEXT
);

CREATE TABLE IF NOT EXISTS players (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    front_image   TEXT,
    personality_image TEXT
);

CREATE TABLE IF NOT EXISTS spawn_locations (
    id              TEXT PRIMARY KEY,
    map_id_ulid     TEXT,
    name            TEXT,
    x_norm          REAL,
    y_norm          REAL,
    in_game_drop_id INTEGER
);

CREATE TABLE IF NOT EXISTS poi_drafts (
    id              TEXT PRIMARY KEY,
    series_id       TEXT,
    event_id        TEXT,
    region_id       TEXT,
    completed       INTEGER,
    completed_at    TEXT,
    date            TEXT,
    time_to_pick    INTEGER,
    raw_json        TEXT
);

CREATE TABLE IF NOT EXISTS poi_picks (
    id                  TEXT PRIMARY KEY,
    draft_id            TEXT,
    pick_number         INTEGER,
    actual_pick_number  INTEGER,
    timed_out           INTEGER,
    pick_by_time        TEXT,
    picked_at           TEXT,
    map_id_ulid         TEXT,
    spawn_location_id   TEXT,
    team_id             TEXT,
    team_version_id     TEXT,
    player_id           TEXT
);

CREATE TABLE IF NOT EXISTS series_team_stats (
    series_id              TEXT,
    team_id                TEXT,
    version_id             TEXT,
    position               INTEGER,
    points                 INTEGER,
    placement_points       INTEGER,
    kills                  INTEGER,
    match_point_eligible   INTEGER,
    won_match_point        INTEGER,
    eliminated             INTEGER,
    raw_json               TEXT,
    PRIMARY KEY (series_id, team_id)
);

CREATE TABLE IF NOT EXISTS match_team_stats (
    match_id               TEXT,
    team_id                TEXT,
    version_id             TEXT,
    placement              INTEGER,
    placement_points       INTEGER,
    points                 INTEGER,
    kills                  INTEGER,
    eliminated             INTEGER,
    match_point_eligible   INTEGER,
    raw_json               TEXT,
    PRIMARY KEY (match_id, team_id)
);

CREATE TABLE IF NOT EXISTS match_player_stats (
    match_id     TEXT,
    player_id    TEXT,
    team_id      TEXT,
    kills        INTEGER,
    killed       INTEGER,
    knocked_down INTEGER,
    character_id TEXT,
    raw_json     TEXT,
    PRIMARY KEY (match_id, player_id)
);

-- ---------------------------- new aggregates ------------------------------

CREATE TABLE IF NOT EXISTS series_weapon_stats (
    series_id    TEXT,
    weapon       TEXT,
    ammo_type    TEXT,
    gun_type     TEXT,
    kills        INTEGER,
    PRIMARY KEY (series_id, weapon)
);

CREATE TABLE IF NOT EXISTS series_character_stats (
    series_id    TEXT,
    character_id TEXT,
    name         TEXT,
    kills        INTEGER,
    damage       INTEGER,
    PRIMARY KEY (series_id, character_id)
);

CREATE TABLE IF NOT EXISTS series_character_compositions (
    series_id    TEXT,
    comp_idx     INTEGER,
    slot_idx     INTEGER,
    character_id TEXT,
    PRIMARY KEY (series_id, comp_idx, slot_idx)
);

CREATE TABLE IF NOT EXISTS series_player_agg (
    series_id           TEXT,
    player_id           TEXT,
    team_id             TEXT,
    matches_played      INTEGER,
    match_series_played INTEGER,
    kills               INTEGER,
    assists             INTEGER,
    average_kills       REAL,
    average_assists     REAL,
    raw_json            TEXT,
    PRIMARY KEY (series_id, player_id)
);

CREATE TABLE IF NOT EXISTS series_banned_legends_agg (
    series_id           TEXT,
    character_id        TEXT,
    latest_match_number INTEGER,
    PRIMARY KEY (series_id, character_id)
);

CREATE TABLE IF NOT EXISTS series_poi_stats (
    series_id           TEXT,
    spawn_location_id   TEXT,
    map_id_ulid         TEXT,
    avg_pick            REAL,
    total_picks         INTEGER,
    avg_survival_time   REAL,
    avg_damage          REAL,
    avg_kills           REAL,
    avg_points          REAL,
    avg_ring_damage     REAL,
    avg_placement       REAL,
    raw_json            TEXT,
    PRIMARY KEY (series_id, spawn_location_id)
);

CREATE TABLE IF NOT EXISTS event_teams (
    event_id      TEXT,
    team_id       TEXT,
    version_id    TEXT,
    raw_json      TEXT,
    PRIMARY KEY (event_id, team_id)
);

CREATE TABLE IF NOT EXISTS event_standings (
    event_id      TEXT,
    team_id       TEXT,
    version_id    TEXT,
    position      INTEGER,
    points        INTEGER,
    prize_money   TEXT,
    raw_json      TEXT,
    PRIMARY KEY (event_id, team_id)
);

CREATE TABLE IF NOT EXISTS event_schedule (
    event_id      TEXT,
    phase_id      TEXT,
    team_name     TEXT,
    group_name    TEXT,
    logo          TEXT,
    PRIMARY KEY (event_id, phase_id, team_name)
);

CREATE TABLE IF NOT EXISTS phase_teams (
    phase_id      TEXT,
    team_id       TEXT,
    version_id    TEXT,
    group_name    TEXT,
    raw_json      TEXT,
    PRIMARY KEY (phase_id, team_id)
);

CREATE TABLE IF NOT EXISTS phase_standings (
    phase_id            TEXT,
    team_id             TEXT,
    position            INTEGER,
    points              INTEGER,
    group_name          TEXT,
    qualified           INTEGER,
    in_live_series      INTEGER,
    series_wins         INTEGER,
    match_wins          INTEGER,
    match_series_played INTEGER,
    avg_survival_time   REAL,
    raw_json            TEXT,
    PRIMARY KEY (phase_id, team_id)
);

CREATE TABLE IF NOT EXISTS season_standings_teams (
    season_id     TEXT,
    team_id       TEXT,
    version_id    TEXT,
    region        TEXT,
    total_points  INTEGER,
    raw_json      TEXT,
    PRIMARY KEY (season_id, team_id)
);

CREATE TABLE IF NOT EXISTS season_standings_players (
    season_id     TEXT,
    player_id     TEXT,
    team_id       TEXT,
    total_points  INTEGER,
    raw_json      TEXT,
    PRIMARY KEY (season_id, player_id)
);

CREATE TABLE IF NOT EXISTS cc_leaderboard_teams (
    season_id     TEXT,
    event_id      TEXT,
    team_id       TEXT,
    position      INTEGER,
    points        INTEGER,
    region        TEXT,
    raw_json      TEXT,
    PRIMARY KEY (season_id, event_id, team_id)
);

CREATE TABLE IF NOT EXISTS cc_leaderboard_players (
    season_id     TEXT,
    event_id      TEXT,
    player_id     TEXT,
    position      INTEGER,
    points        INTEGER,
    region        TEXT,
    raw_json      TEXT,
    PRIMARY KEY (season_id, event_id, player_id)
);

CREATE TABLE IF NOT EXISTS live_streams (
    series_id     TEXT,
    stream_id     TEXT,
    name          TEXT,
    channel_name  TEXT,
    provider      TEXT,
    raw_json      TEXT,
    PRIMARY KEY (series_id, stream_id)
);

-- Tracks "have we fetched this resource and when". Used as a freshness
-- gate by sync.py so completed series/events don't get re-hit.
CREATE TABLE IF NOT EXISTS sync_state (
    kind          TEXT,
    ident         TEXT,
    fetched_at    INTEGER,   -- epoch seconds
    status        TEXT,      -- ok | not_available
    PRIMARY KEY (kind, ident)
);

CREATE INDEX IF NOT EXISTS idx_matches_series   ON matches(series_id);
CREATE INDEX IF NOT EXISTS idx_matches_event    ON matches(event_id);
CREATE INDEX IF NOT EXISTS idx_series_event     ON series(event_id);
CREATE INDEX IF NOT EXISTS idx_phases_event     ON phases(event_id);
CREATE INDEX IF NOT EXISTS idx_events_region    ON events(region_id);
CREATE INDEX IF NOT EXISTS idx_picks_draft      ON poi_picks(draft_id);
CREATE INDEX IF NOT EXISTS idx_picks_team       ON poi_picks(team_id);
CREATE INDEX IF NOT EXISTS idx_spawn_map        ON spawn_locations(map_id_ulid);
CREATE INDEX IF NOT EXISTS idx_evstand_event    ON event_standings(event_id);
CREATE INDEX IF NOT EXISTS idx_phstand_phase    ON phase_standings(phase_id);
"""


@contextmanager
def connect(db_path: Path | None = None):
    p = Path(db_path) if db_path else DEFAULT_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        conn.executescript(SCHEMA)
        # additive ALTERs - safe on old DBs
        for col_sql in (
            "ALTER TABLE matches ADD COLUMN winner_team_id TEXT",
            "ALTER TABLE matches ADD COLUMN winner_damage INTEGER",
            "ALTER TABLE matches ADD COLUMN winner_kills INTEGER",
            "ALTER TABLE match_player_stats ADD COLUMN character_id TEXT",
        ):
            try:
                conn.execute(col_sql)
            except sqlite3.OperationalError:
                pass
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------- upsert helpers ---------------------------------

def _upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any],
            pk: Iterable[str]) -> None:
    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    set_clause = ", ".join(
        f"{c}=excluded.{c}" for c in cols if c not in set(pk)
    ) or f"{next(iter(pk))}={next(iter(pk))}"
    pk_cols = ", ".join(pk)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk_cols}) DO UPDATE SET {set_clause}"
    )
    conn.execute(sql, [row[c] for c in cols])


# ---- sync_state ----------------------------------------------------------

def mark_synced(conn, kind: str, ident: str, status: str = "ok") -> None:
    _upsert(conn, "sync_state", {
        "kind": kind, "ident": ident,
        "fetched_at": int(time.time()),
        "status": status,
    }, pk=("kind", "ident"))


def last_synced(conn, kind: str, ident: str) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT fetched_at, status FROM sync_state WHERE kind=? AND ident=?",
        (kind, ident),
    ).fetchone()
    if not row:
        return None
    return int(row["fetched_at"]), row["status"]


# ---- reference / hierarchy ------------------------------------------------

def upsert_map(conn, m: dict[str, Any]) -> None:
    ulid = m["id"]
    _upsert(conn, "maps", {
        "id_ulid": ulid,
        "name": m.get("name"),
        "in_game_name": m.get("inGameName"),
        "canonical_id": canonical_map_id(ulid),
        "active": int(bool(m.get("active"))),
    }, pk=("id_ulid",))


def upsert_character(conn, c: dict[str, Any]) -> None:
    _upsert(conn, "characters", {
        "id": c["id"],
        "name": c.get("name"),
        "image": c.get("image"),
        "character_type": c.get("characterType"),
        "internal_name": c.get("internalName"),
    }, pk=("id",))


def upsert_season(conn, s: dict[str, Any]) -> None:
    _upsert(conn, "seasons", {
        "id": s["id"],
        "name": s.get("name"),
        "is_main": int(bool(s.get("isMainSeason"))),
        "start_date": s.get("startDate"),
        "end_date": s.get("endDate"),
    }, pk=("id",))


def upsert_tournament(conn, t: dict[str, Any]) -> None:
    _upsert(conn, "tournaments", {
        "id": t["id"],
        "season_id": t.get("seasonId"),
        "vendor_id": t.get("vendorId"),
        "name": t.get("name"),
        "start_date": t.get("startDate"),
        "end_date": t.get("endDate"),
    }, pk=("id",))


def upsert_region(conn, r: dict[str, Any], tournament_id: str | None) -> None:
    _upsert(conn, "regions", {
        "id": r["id"],
        "tournament_id": tournament_id,
        "name": r.get("name"),
    }, pk=("id",))


def upsert_event(conn, e: dict[str, Any], *, tournament_id: str | None,
                 region_id: str | None) -> None:
    _upsert(conn, "events", {
        "id": e["id"],
        "tournament_id": tournament_id,
        "region_id": region_id or e.get("regionId"),
        "name": e.get("name"),
        "start_date": e.get("startDate"),
        "end_date": e.get("endDate"),
        "has_standings": int(bool(e.get("hasStandings"))),
    }, pk=("id",))


def upsert_phase(conn, p: dict[str, Any]) -> None:
    _upsert(conn, "phases", {
        "id": p["id"],
        "event_id": p.get("eventId"),
        "name": p.get("name"),
        "phase_number": p.get("phaseNumber"),
        "format": p.get("format"),
        "starts_at": p.get("startsAt"),
        "completed_at": p.get("completedAt"),
        "has_standings": int(bool(p.get("hasStandings"))),
    }, pk=("id",))


def upsert_series(conn, s: dict[str, Any]) -> None:
    _upsert(conn, "series", {
        "id": s["id"],
        "name": s.get("name"),
        "status": s.get("status"),
        "series_number": s.get("seriesNumber"),
        "phase_id": s.get("phaseId") or (s.get("phase") or {}).get("id"),
        "event_id": s.get("eventId") or (s.get("event") or {}).get("id"),
        "region_id": s.get("regionId") or (s.get("region") or {}).get("id"),
        "tournament_id": s.get("tournamentId") or (s.get("tournament") or {}).get("id"),
        "season_id": s.get("seasonId") or (s.get("season") or {}).get("id"),
        "poi_draft_id": s.get("poiDraftId"),
        "starts_at": s.get("startsAt"),
        "completed_at": s.get("completedAt"),
        "is_match_point": int(bool(s.get("isMatchPointFormat"))),
        "match_point_threshold": s.get("matchPointThreshold"),
        "vod_url": s.get("vodUrl"),
    }, pk=("id",))


def upsert_match(conn, m: dict[str, Any]) -> None:
    mp = m.get("map") or {}
    winner = m.get("winner") or {}
    _upsert(conn, "matches", {
        "id": m["id"],
        "match_number": m.get("matchNumber"),
        "series_id": m.get("seriesId"),
        "phase_id": m.get("phaseId"),
        "event_id": m.get("eventId"),
        "region_id": m.get("regionId"),
        "tournament_id": m.get("tournamentId"),
        "season_id": m.get("seasonId"),
        "map_id_ulid": mp.get("id"),
        "status": m.get("status"),
        "in_game_status": m.get("inGameStatus"),
        "winner_determined": int(bool(m.get("winnerDetermined"))),
        "winner_team_id": winner.get("id") or winner.get("teamId"),
        "winner_damage": winner.get("damage"),
        "winner_kills": winner.get("kills"),
        "started_at": m.get("startedAt"),
        "play_started_at": m.get("playStartedAt"),
        "completed_at": m.get("completedAt"),
        "raw_json": json.dumps(m, ensure_ascii=False),
    }, pk=("id",))
    for b in m.get("bannedCharacters") or []:
        ch = (b.get("character") or {})
        if ch.get("id"):
            conn.execute(
                "INSERT OR IGNORE INTO match_banned_legends VALUES (?, ?)",
                (m["id"], ch["id"]),
            )


def upsert_team(conn, t: dict[str, Any]) -> None:
    tid = t.get("teamId") or t.get("id")
    if not tid:
        return
    _upsert(conn, "teams", {
        "id": tid,
        "name": t.get("name") or t.get("teamName"),
        "short_name": t.get("shortName"),
        "region": t.get("region"),
        "disbanded": int(bool(t.get("disbanded"))),
    }, pk=("id",))
    vid = t.get("teamVersionId") or t.get("versionId")
    if vid:
        _upsert(conn, "team_versions", {
            "version_id": vid,
            "team_id": tid,
            "logo_light": t.get("logoLight"),
            "logo_dark": t.get("logoDark"),
        }, pk=("version_id",))


def upsert_player(conn, p: dict[str, Any]) -> None:
    pid = p.get("id")
    if not pid:
        return
    _upsert(conn, "players", {
        "id": pid,
        "name": p.get("name"),
        "front_image": p.get("frontImage"),
        "personality_image": p.get("personalityImage"),
    }, pk=("id",))


def upsert_spawn_location(conn, s: dict[str, Any]) -> None:
    mp = s.get("map") or {}
    map_ulid = mp.get("id") or s.get("mapId")
    try:
        x = float(s.get("x")) / 100.0
        y = float(s.get("y")) / 100.0
    except (TypeError, ValueError):
        x = y = None
    _upsert(conn, "spawn_locations", {
        "id": s["id"],
        "map_id_ulid": map_ulid,
        "name": s.get("name"),
        "x_norm": x,
        "y_norm": y,
        "in_game_drop_id": s.get("inGameDropId"),
    }, pk=("id",))


def upsert_poi_draft(conn, d: dict[str, Any]) -> None:
    _upsert(conn, "poi_drafts", {
        "id": d["id"],
        "series_id": (d.get("series") or {}).get("id"),
        "event_id":  (d.get("event")  or {}).get("id"),
        "region_id": (d.get("region") or {}).get("id"),
        "completed": int(bool(d.get("completed"))),
        "completed_at": d.get("completedAt"),
        "date": d.get("date"),
        "time_to_pick": d.get("timeToPick"),
        "raw_json": json.dumps(d, ensure_ascii=False),
    }, pk=("id",))


def upsert_poi_pick(conn, draft_id: str, p: dict[str, Any]) -> None:
    mp = p.get("map") or {}
    sl = p.get("spawnLocation") or {}
    tm = p.get("team") or {}
    pl = p.get("player") or {}
    if sl.get("id"):
        upsert_spawn_location(conn, {**sl, "map": mp})
    if tm.get("teamId") or tm.get("id"):
        upsert_team(conn, tm)
    if pl.get("id"):
        upsert_player(conn, pl)
    _upsert(conn, "poi_picks", {
        "id": p["id"],
        "draft_id": draft_id,
        "pick_number": p.get("pickNumber"),
        "actual_pick_number": p.get("actualPickNumber"),
        "timed_out": int(bool(p.get("timedOut"))),
        "pick_by_time": p.get("pickByTime"),
        "picked_at": p.get("pickedAt"),
        "map_id_ulid": mp.get("id"),
        "spawn_location_id": sl.get("id"),
        "team_id": tm.get("teamId") or tm.get("id"),
        "team_version_id": tm.get("teamVersionId"),
        "player_id": pl.get("id"),
    }, pk=("id",))


def upsert_series_team_stats(conn, series_id: str, t: dict[str, Any]) -> None:
    tid = t.get("id")
    if not tid:
        return
    upsert_team(conn, t)
    _upsert(conn, "series_team_stats", {
        "series_id": series_id,
        "team_id": tid,
        "version_id": t.get("versionId"),
        "position": t.get("position"),
        "points": t.get("points"),
        "placement_points": t.get("placementPoints"),
        "kills": t.get("kills"),
        "match_point_eligible": int(bool(t.get("matchPointEligible"))),
        "won_match_point": int(bool(t.get("wonMatchPoint"))),
        "eliminated": int(bool(t.get("eliminated"))),
        "raw_json": json.dumps(t, ensure_ascii=False),
    }, pk=("series_id", "team_id"))


def upsert_match_team_stats(conn, match_id: str, t: dict[str, Any]) -> None:
    tid = t.get("id")
    if not tid:
        return
    upsert_team(conn, t)
    _upsert(conn, "match_team_stats", {
        "match_id": match_id,
        "team_id": tid,
        "version_id": t.get("versionId"),
        "placement": t.get("placement"),
        "placement_points": t.get("placementPoints"),
        "points": t.get("points"),
        "kills": t.get("kills"),
        "eliminated": int(bool(t.get("eliminated"))),
        "match_point_eligible": int(bool(t.get("matchPointEligible"))),
        "raw_json": json.dumps(t, ensure_ascii=False),
    }, pk=("match_id", "team_id"))
    for pl in t.get("players") or []:
        pid = pl.get("id")
        if not pid:
            continue
        upsert_player(conn, pl)
        ch = (pl.get("character") or {})
        _upsert(conn, "match_player_stats", {
            "match_id": match_id,
            "player_id": pid,
            "team_id": tid,
            "kills": pl.get("kills"),
            "killed": int(bool(pl.get("killed"))),
            "knocked_down": int(bool(pl.get("knockedDown"))),
            "character_id": ch.get("id"),
            "raw_json": json.dumps(pl, ensure_ascii=False),
        }, pk=("match_id", "player_id"))


# ---- new aggregate upserts -------------------------------------------------

def upsert_weapon_stat(conn, series_id: str, w: dict[str, Any]) -> None:
    _upsert(conn, "series_weapon_stats", {
        "series_id": series_id,
        "weapon":    w.get("weapon"),
        "ammo_type": w.get("ammoType"),
        "gun_type":  w.get("gunType"),
        "kills":     w.get("kills"),
    }, pk=("series_id", "weapon"))


def upsert_character_stat(conn, series_id: str, c: dict[str, Any]) -> None:
    _upsert(conn, "series_character_stats", {
        "series_id": series_id,
        "character_id": c.get("characterId"),
        "name":      c.get("characterName"),
        "kills":     c.get("kills"),
        "damage":    c.get("damage"),
    }, pk=("series_id", "character_id"))


def upsert_character_composition(conn, series_id: str, idx: int,
                                  comp: list[dict[str, Any]]) -> None:
    for slot, c in enumerate(comp):
        _upsert(conn, "series_character_compositions", {
            "series_id": series_id,
            "comp_idx": idx,
            "slot_idx": slot,
            "character_id": c.get("characterId"),
        }, pk=("series_id", "comp_idx", "slot_idx"))


def upsert_player_agg(conn, series_id: str, p: dict[str, Any]) -> None:
    pid = p.get("playerId") or p.get("id")
    if not pid:
        return
    _upsert(conn, "series_player_agg", {
        "series_id": series_id,
        "player_id": pid,
        "team_id":   (p.get("team") or {}).get("id"),
        "matches_played":      p.get("matchesPlayed"),
        "match_series_played": p.get("matchSeriesPlayed"),
        "kills":               p.get("kills"),
        "assists":             p.get("assists"),
        "average_kills":       p.get("averageKills"),
        "average_assists":     p.get("averageAssists"),
        "raw_json": json.dumps(p, ensure_ascii=False),
    }, pk=("series_id", "player_id"))


def upsert_banned_legend_agg(conn, series_id: str, b: dict[str, Any]) -> None:
    if not b.get("id"):
        return
    _upsert(conn, "series_banned_legends_agg", {
        "series_id": series_id,
        "character_id": b["id"],
        "latest_match_number": b.get("latestMatchNumber"),
    }, pk=("series_id", "character_id"))


def upsert_poi_stat(conn, series_id: str, p: dict[str, Any]) -> None:
    sl = p.get("spawnLocation") or {}
    if not sl.get("id"):
        return
    upsert_spawn_location(conn, {**sl, "map": p.get("map") or {}})
    _upsert(conn, "series_poi_stats", {
        "series_id": series_id,
        "spawn_location_id": sl["id"],
        "map_id_ulid": (p.get("map") or {}).get("id"),
        "avg_pick":          p.get("avgPick"),
        "total_picks":       p.get("totalPicks"),
        "avg_survival_time": p.get("avgSurvivalTime"),
        "avg_damage":        p.get("avgDamage"),
        "avg_kills":         p.get("avgKills"),
        "avg_points":        p.get("avgPoints"),
        "avg_ring_damage":   p.get("avgRingDamage"),
        "avg_placement":     p.get("avgPlacement"),
        "raw_json": json.dumps(p, ensure_ascii=False),
    }, pk=("series_id", "spawn_location_id"))


def upsert_event_team(conn, event_id: str, t: dict[str, Any]) -> None:
    upsert_team(conn, t)
    for pl in t.get("players") or []:
        upsert_player(conn, pl)
    _upsert(conn, "event_teams", {
        "event_id": event_id,
        "team_id": t.get("teamId") or t.get("id"),
        "version_id": t.get("teamVersionId"),
        "raw_json": json.dumps(t, ensure_ascii=False),
    }, pk=("event_id", "team_id"))


def upsert_event_standing(conn, event_id: str, s: dict[str, Any]) -> None:
    _upsert(conn, "event_standings", {
        "event_id": event_id,
        "team_id": s.get("teamId"),
        "version_id": s.get("teamVersionId"),
        "position": s.get("position"),
        "points": s.get("points"),
        "prize_money": s.get("prizeMoney"),
        "raw_json": json.dumps(s, ensure_ascii=False),
    }, pk=("event_id", "team_id"))


def upsert_event_schedule_team(conn, event_id: str, phase_id: str,
                                t: dict[str, Any]) -> None:
    name = t.get("name")
    if not name:
        return
    _upsert(conn, "event_schedule", {
        "event_id": event_id,
        "phase_id": phase_id,
        "team_name": name,
        "group_name": t.get("group"),
        "logo": t.get("logo"),
    }, pk=("event_id", "phase_id", "team_name"))


def upsert_phase_team(conn, phase_id: str, t: dict[str, Any]) -> None:
    upsert_team(conn, t)
    for pl in t.get("players") or []:
        upsert_player(conn, pl)
    tid = t.get("teamId") or t.get("id")
    if not tid:
        return
    _upsert(conn, "phase_teams", {
        "phase_id": phase_id,
        "team_id": tid,
        "version_id": t.get("teamVersionId"),
        "group_name": t.get("group"),
        "raw_json": json.dumps(t, ensure_ascii=False),
    }, pk=("phase_id", "team_id"))


def upsert_phase_standing(conn, phase_id: str, s: dict[str, Any]) -> None:
    _upsert(conn, "phase_standings", {
        "phase_id": phase_id,
        "team_id": s.get("teamId"),
        "position": s.get("position"),
        "points": s.get("points"),
        "group_name": s.get("group"),
        "qualified": int(bool(s.get("qualified"))),
        "in_live_series": int(bool(s.get("inLiveSeries"))),
        "series_wins": s.get("seriesWins"),
        "match_wins": s.get("matchWins"),
        "match_series_played": s.get("matchSeriesPlayed"),
        "avg_survival_time": s.get("averageSurvivalTime"),
        "raw_json": json.dumps(s, ensure_ascii=False),
    }, pk=("phase_id", "team_id"))


def upsert_season_standing_team(conn, season_id: str, s: dict[str, Any]) -> None:
    _upsert(conn, "season_standings_teams", {
        "season_id": season_id,
        "team_id":    s.get("teamId"),
        "version_id": s.get("teamVersionId"),
        "region":     s.get("region"),
        "total_points": s.get("totalPoints"),
        "raw_json": json.dumps(s, ensure_ascii=False),
    }, pk=("season_id", "team_id"))


def upsert_season_standing_player(conn, season_id: str, p: dict[str, Any]) -> None:
    pid = p.get("playerId") or p.get("id")
    if not pid:
        return
    _upsert(conn, "season_standings_players", {
        "season_id": season_id,
        "player_id": pid,
        "team_id":   (p.get("team") or {}).get("id"),
        "total_points": p.get("totalPoints") or p.get("points"),
        "raw_json": json.dumps(p, ensure_ascii=False),
    }, pk=("season_id", "player_id"))


def upsert_cc_team(conn, season_id: str, event_id: str,
                    r: dict[str, Any]) -> None:
    tid = r.get("teamId") or r.get("id")
    if not tid:
        return
    _upsert(conn, "cc_leaderboard_teams", {
        "season_id": season_id,
        "event_id":  event_id,
        "team_id":   tid,
        "position":  r.get("position"),
        "points":    r.get("points") or r.get("totalPoints"),
        "region":    r.get("region"),
        "raw_json":  json.dumps(r, ensure_ascii=False),
    }, pk=("season_id", "event_id", "team_id"))


def upsert_cc_player(conn, season_id: str, event_id: str,
                      r: dict[str, Any]) -> None:
    pid = r.get("playerId") or r.get("id")
    if not pid:
        return
    _upsert(conn, "cc_leaderboard_players", {
        "season_id": season_id,
        "event_id":  event_id,
        "player_id": pid,
        "position":  r.get("position"),
        "points":    r.get("points") or r.get("totalPoints"),
        "region":    r.get("region"),
        "raw_json":  json.dumps(r, ensure_ascii=False),
    }, pk=("season_id", "event_id", "player_id"))


def upsert_live_stream(conn, series_id: str, s: dict[str, Any]) -> None:
    sid = s.get("id")
    if not sid:
        return
    _upsert(conn, "live_streams", {
        "series_id": series_id,
        "stream_id": sid,
        "name": s.get("name"),
        "channel_name": s.get("channelName"),
        "provider": s.get("streamProvider"),
        "raw_json": json.dumps(s, ensure_ascii=False),
    }, pk=("series_id", "stream_id"))
