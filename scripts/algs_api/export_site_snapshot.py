"""Export the local ALGS SQLite cache into the lightweight site data shape."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "scripts" / "algs_api" / "data" / "algs.sqlite"
OUT_PATH = ROOT / "src" / "data" / "algs-bundle.snapshot.json"
ROSTERS_OUT_PATH = ROOT / "src" / "data" / "algs-team-rosters.snapshot.json"
WEAPONS_OUT_PATH = ROOT / "src" / "data" / "algs-team-weapons.snapshot.json"


MAP_IMAGE_BY_CANONICAL = {
    "worlds_edge": "worlds-edge",
    "kings_canyon": "kings-canyon",
    "storm_point": "storm-point",
    "broken_moon": "broken-moon",
    "olympus": "olympus",
    "e_district": "e-district",
}


def rows(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def to_iso_date(value: str | None) -> str:
    return value[:10] if value else ""


def to_ui_region(name: str | None) -> str:
    n = (name or "").lower()
    if "america" in n and "south" in n:
        return "South America"
    if "america" in n:
        return "North America"
    if "europe" in n or "emea" in n:
        return "EMEA"
    if "pacific" in n or "apac" in n or "asia" in n:
        return "APAC"
    return "EMEA"


def derive_type(name: str | None) -> str:
    n = (name or "").lower()
    if "qualifier" in n or "scrim" in n:
        return "Qualifier"
    if "playoff" in n or "final" in n or "championship" in n:
        return "LAN"
    return "Online"


def derive_status(start: str, end: str) -> str:
    today = "2026-05-30"
    if not start or not end:
        return "draft"
    if today < start:
        return "upcoming"
    if today > end:
        return "finished"
    return "active"


def to_ui_map_id(canonical: str | None) -> str | None:
    return canonical.replace("_", "-") if canonical else None


def main() -> int:
    con = sqlite3.connect(DB_PATH)

    events = rows(con, "select id, name, start_date, end_date, tournament_id, region_id from events order by start_date desc")
    regions = {r["id"]: r["name"] for r in rows(con, "select id, name from regions")}
    tournaments_meta = {t["id"]: t["name"] for t in rows(con, "select id, name from tournaments")}
    teams_rows = rows(con, "select id, name, short_name, region, disbanded from teams order by name")
    versions = rows(con, "select version_id, team_id, logo_light, logo_dark from team_versions order by version_id asc")
    series_rows = rows(con, "select id, name, status, starts_at, completed_at, event_id from series where event_id is not null")
    match_rows = rows(con, "select id, series_id, match_number, map_id_ulid, started_at, completed_at from matches")
    maps_rows = rows(con, "select id_ulid, name, canonical_id, active from maps")
    match_team_rows = rows(con, "select match_id, team_id from match_team_stats")
    event_team_rows = rows(con, "select event_id, team_id from event_teams")
    series_team_rows = rows(con, "select series_id, team_id from series_team_stats")
    series_weapon_rows = rows(con, "select series_id, weapon, ammo_type, gun_type, kills from series_weapon_stats")
    event_team_detail_rows = rows(
        con,
        """
        select et.event_id, et.team_id, et.raw_json, e.start_date
        from event_teams et
        left join events e on e.id = et.event_id
        where et.raw_json is not null
        order by et.team_id asc, e.start_date desc
        """,
    )

    event_tournaments = []
    for ev in events:
        start = to_iso_date(ev["start_date"])
        end = to_iso_date(ev["end_date"])
        parent = tournaments_meta.get(ev["tournament_id"])
        reg = regions.get(ev["region_id"])
        full_name = f"{parent} — {ev['name'] or 'Event'}" if parent else ev["name"] or "Event"
        year = max(1, min(6, int(start[:4]) - 2020)) if start else 6
        event_tournaments.append({
            "id": ev["id"],
            "name": full_name,
            "startDate": start,
            "endDate": end,
            "year": year,
            "type": derive_type(full_name),
            "region": to_ui_region(reg),
            "status": derive_status(start, end),
        })

    logo_by_team: dict[str, dict[str, str]] = {}
    for v in versions:
        if not v["team_id"]:
            continue
        prev = logo_by_team.get(v["team_id"], {})
        logo_by_team[v["team_id"]] = {
            "light": v["logo_light"] or prev.get("light", ""),
            "dark": v["logo_dark"] or prev.get("dark", ""),
        }

    latest_rosters: dict[str, list[dict[str, Any]]] = {}
    for row in event_team_detail_rows:
        team_id = row["team_id"]
        if not team_id or team_id in latest_rosters:
            continue
        try:
            raw = json.loads(row["raw_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        players = raw.get("players") if isinstance(raw, dict) else None
        if not isinstance(players, list):
            continue
        roster: list[dict[str, Any]] = []
        for p in players:
            if not isinstance(p, dict) or p.get("role") != "player":
                continue
            player_id = str(p.get("id") or p.get("name") or "")
            name = str(p.get("name") or "").strip()
            if not player_id or not name:
                continue
            roster.append({
                "id": player_id,
                "name": name,
                "image": p.get("frontImage"),
                "role": p.get("role") or "player",
                "teamVersionId": p.get("teamVersionId"),
            })
        if roster:
            latest_rosters[team_id] = roster

    teams = []
    for t in teams_rows:
        logos = logo_by_team.get(t["id"], {})
        logo = logos.get("dark") or logos.get("light") or None
        teams.append({
            "id": t["id"],
            "tag": t["short_name"] or (t["name"] or "")[:4].upper(),
            "name": t["name"] or "",
            "color": "#888888",
            "logo": logo,
            "logoLight": logos.get("light") or None,
            "logoDark": logos.get("dark") or None,
            "players": [p["name"] for p in latest_rosters.get(t["id"], [])],
            "placement": 0,
            "kills": 0,
            "alive": True,
            "status": "archived" if t["disbanded"] else "active",
        })

    map_by_id = {m["id_ulid"]: m for m in maps_rows}
    site_maps = []
    for m in maps_rows:
        image_key = MAP_IMAGE_BY_CANONICAL.get(m["canonical_id"])
        site_maps.append({"id": m["id_ulid"], "name": m["name"] or "", "imageKey": image_key})
        ui_id = to_ui_map_id(m["canonical_id"])
        if ui_id:
            site_maps.append({"id": ui_id, "name": m["name"] or "", "imageKey": image_key})

    teams_by_match: dict[str, list[str]] = defaultdict(list)
    for r in match_team_rows:
        if r["team_id"] and r["team_id"] not in teams_by_match[r["match_id"]]:
            teams_by_match[r["match_id"]].append(r["team_id"])

    teams_by_event: dict[str, list[str]] = defaultdict(list)
    for r in event_team_rows:
        if r["team_id"] and r["team_id"] not in teams_by_event[r["event_id"]]:
            teams_by_event[r["event_id"]].append(r["team_id"])

    teams_by_series: dict[str, list[str]] = defaultdict(list)
    for r in series_team_rows:
        if r["series_id"] and r["team_id"] and r["team_id"] not in teams_by_series[r["series_id"]]:
            teams_by_series[r["series_id"]].append(r["team_id"])

    def add_weapon(agg: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
        weapon = row.get("weapon")
        if not weapon:
            return
        cur = agg.setdefault(weapon, {
            "weapon": weapon,
            "gunType": row.get("gun_type"),
            "ammoType": row.get("ammo_type"),
            "kills": 0,
            "_series": set(),
        })
        cur["kills"] += row.get("kills") or 0
        if row.get("series_id"):
            cur["_series"].add(row["series_id"])

    def finish_weapons(agg: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for item in agg.values():
            out.append({
                "weapon": item["weapon"],
                "gunType": item.get("gunType"),
                "ammoType": item.get("ammoType"),
                "kills": item["kills"],
                "series": len(item["_series"]),
            })
        return sorted(out, key=lambda x: x["kills"], reverse=True)

    global_weapon_agg: dict[str, dict[str, Any]] = {}
    team_weapon_aggs: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in series_weapon_rows:
        add_weapon(global_weapon_agg, row)
        for team_id in teams_by_series.get(row["series_id"], []):
            add_weapon(team_weapon_aggs[team_id], row)

    team_weapons = {team_id: finish_weapons(agg) for team_id, agg in team_weapon_aggs.items()}
    weapon_payload = {
        "teams": team_weapons,
        "global": finish_weapons(global_weapon_agg),
    }

    matches_by_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in match_rows:
        if m["series_id"]:
            matches_by_series[m["series_id"]].append(m)

    matches = []
    for s in series_rows:
        games = sorted(matches_by_series.get(s["id"], []), key=lambda g: g["match_number"] or 0)
        map_ids = []
        durations = []
        match_team_ids: list[str] = []
        for g in games:
            mp = map_by_id.get(g["map_id_ulid"])
            map_id = to_ui_map_id(mp["canonical_id"]) if mp else g["map_id_ulid"]
            if map_id:
                map_ids.append(map_id)
            if g["started_at"] and g["completed_at"]:
                seconds = int((__import__("datetime").datetime.fromisoformat(g["completed_at"].replace("Z", "+00:00")) - __import__("datetime").datetime.fromisoformat(g["started_at"].replace("Z", "+00:00"))).total_seconds())
                durations.append(max(60, seconds))
            else:
                durations.append(1200)
            for team_id in teams_by_match.get(g["id"], []):
                if team_id not in match_team_ids:
                    match_team_ids.append(team_id)
        team_ids = match_team_ids or teams_by_event.get(s["event_id"], [])
        matches.append({
            "id": s["id"],
            "name": s["name"] or "Series",
            "tournamentId": s["event_id"],
            "mapId": map_ids[0] if map_ids else "storm-point",
            "durationSec": durations[0] if durations else 1200,
            "mapIds": map_ids or None,
            "gameDurations": durations or None,
            "teamIds": team_ids,
            "teamVods": {},
            "vodLink": "",
            "startedAt": s["starts_at"],
            "completedAt": s["completed_at"],
            "seriesStatus": s["status"],
        })

    payload = {
        "tournaments": event_tournaments,
        "teams": teams,
        "matches": matches,
        "maps": site_maps,
        "fetchedAt": int(__import__("time").time() * 1000),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)
    roster_tmp = ROSTERS_OUT_PATH.with_suffix(".tmp")
    roster_tmp.write_text(json.dumps(latest_rosters, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    roster_tmp.replace(ROSTERS_OUT_PATH)
    weapons_tmp = WEAPONS_OUT_PATH.with_suffix(".tmp")
    weapons_tmp.write_text(json.dumps(weapon_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    weapons_tmp.replace(WEAPONS_OUT_PATH)
    print(f"Exported {len(teams)} teams, {len(event_tournaments)} tournaments, {len(matches)} matches to {OUT_PATH}")
    print(f"Exported rosters for {len(latest_rosters)} teams to {ROSTERS_OUT_PATH}")
    print(f"Exported weapon stats for {len(team_weapons)} teams to {WEAPONS_OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
