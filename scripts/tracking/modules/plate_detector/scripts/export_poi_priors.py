import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, Optional


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())


def safe_tag(s: str) -> str:
    return norm(s)


def load_team_alias_index(config: dict) -> Dict[str, str]:
    """
    Returns alias_norm -> broadcast_tag.
    Uses config.teams, broadcast_tag_aliases and hud_team_order.
    """
    alias_to_tag: Dict[str, str] = {}

    # Explicit aliases are strongest.
    for tag, aliases in (config.get("broadcast_tag_aliases") or {}).items():
        tag_n = safe_tag(tag)
        alias_to_tag[tag_n] = tag_n
        for a in aliases or []:
            alias_to_tag[norm(a)] = tag_n

    # HUD order makes broadcast tag official for this VOD/series.
    for _, tag in (config.get("hud_team_order") or {}).items():
        tag_n = safe_tag(tag)
        if tag_n:
            alias_to_tag[tag_n] = tag_n

    for t in config.get("teams", []):
        tag = safe_tag(t.get("tag") or t.get("broadcast_tag") or t.get("short_name") or t.get("name") or "")
        if not tag:
            continue
        candidates = [
            t.get("tag"),
            t.get("broadcast_tag"),
            t.get("short_name"),
            t.get("name"),
            t.get("team_name"),
            t.get("db_name"),
            t.get("db_tag"),
            t.get("team_id"),
            t.get("id"),
        ]
        for a in candidates:
            if a:
                alias_to_tag.setdefault(norm(a), tag)

    return alias_to_tag


def resolve_broadcast_tag(config: dict, alias_to_tag: Dict[str, str], team_id: str, db_short: str, db_name: str, raw_name: str = "", raw_short: str = "") -> str:
    candidates = [team_id, raw_short, raw_name, db_short, db_name]
    for c in candidates:
        n = norm(c)
        if n in alias_to_tag:
            return alias_to_tag[n]

    # Team-id exact lookup in config teams.
    for t in config.get("teams", []):
        if team_id and team_id in {t.get("team_id"), t.get("id")}:
            return safe_tag(t.get("tag") or t.get("broadcast_tag") or db_short or db_name)

    # Fallback to short name.
    return safe_tag(raw_short or db_short or db_name or team_id)


def get_series_match(conn, series_id: str, match_number: int):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM series WHERE id=?", (series_id,))
    series = cur.fetchone()
    if not series:
        raise RuntimeError(f"Series not found: {series_id}")

    cur.execute("SELECT * FROM matches WHERE series_id=? AND match_number=?", (series_id, match_number))
    match = cur.fetchone()
    if not match:
        raise RuntimeError(f"Match not found: series={series_id} match_number={match_number}")

    cur.execute("SELECT * FROM maps WHERE id_ulid=?", (match["map_id_ulid"],))
    m = cur.fetchone()
    return dict(series), dict(match), dict(m) if m else None


def load_poi_picks(conn, draft_id: str, map_id_ulid: str):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pp.pick_number, pp.actual_pick_number, pp.team_id, pp.team_version_id,
               pp.spawn_location_id, pp.map_id_ulid,
               sl.name AS poi_name, sl.x_norm, sl.y_norm,
               t.name AS db_team_name, t.short_name AS db_team_tag,
               sts.raw_json AS series_team_raw
        FROM poi_picks pp
        JOIN spawn_locations sl ON sl.id = pp.spawn_location_id
        LEFT JOIN teams t ON t.id = pp.team_id
        LEFT JOIN series_team_stats sts ON sts.team_id = pp.team_id
        WHERE pp.draft_id=? AND pp.map_id_ulid=?
        ORDER BY pp.pick_number
        """,
        (draft_id, map_id_ulid),
    )
    return [dict(r) for r in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export ALGS POI/drop priors from SQLite for one series match")
    parser.add_argument("--db", required=True, help="SQLite path, e.g. data\\algs.sqlite")
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--match-number", type=int, required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--left-ignore", type=int, default=420)
    parser.add_argument("--roi-size", type=int, default=1080)
    parser.add_argument("--canonical-size", type=int, default=2000)
    args = parser.parse_args()

    config = load_json(Path(args.config))
    alias_to_tag = load_team_alias_index(config)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    series, match, map_row = get_series_match(conn, args.series_id, args.match_number)
    draft_id = series.get("poi_draft_id")
    if not draft_id:
        cur = conn.cursor()
        cur.execute("SELECT id FROM poi_drafts WHERE series_id=? ORDER BY completed_at DESC LIMIT 1", (args.series_id,))
        r = cur.fetchone()
        draft_id = r["id"] if r else None
    if not draft_id:
        raise RuntimeError(f"No poi_draft_id found for series {args.series_id}")

    picks = load_poi_picks(conn, draft_id, match["map_id_ulid"])
    if not picks:
        raise RuntimeError(f"No poi picks found for draft={draft_id} map={match['map_id_ulid']}")

    teams = {}
    rows = []
    for p in picks:
        raw = {}
        try:
            raw = json.loads(p.get("series_team_raw") or "{}")
        except Exception:
            raw = {}
        raw_name = raw.get("name") or ""
        raw_short = raw.get("shortName") or raw.get("short_name") or ""
        tag = resolve_broadcast_tag(
            config,
            alias_to_tag,
            team_id=p.get("team_id"),
            db_short=p.get("db_team_tag") or "",
            db_name=p.get("db_team_name") or "",
            raw_name=raw_name,
            raw_short=raw_short,
        )
        x_norm = float(p["x_norm"])
        y_norm = float(p["y_norm"])
        item = {
            "broadcast_tag": tag,
            "team_id": p.get("team_id"),
            "team_version_id": p.get("team_version_id"),
            "team_name": raw_name or p.get("db_team_name") or tag,
            "team_tag": raw_short or p.get("db_team_tag") or tag,
            "poi_name": p.get("poi_name"),
            "spawn_location_id": p.get("spawn_location_id"),
            "pick_number": p.get("pick_number"),
            "actual_pick_number": p.get("actual_pick_number"),
            "x_norm": x_norm,
            "y_norm": y_norm,
            "frame_px": [round(args.left_ignore + x_norm * args.roi_size, 1), round(y_norm * args.roi_size, 1)],
            "roi_px": [round(x_norm * args.roi_size, 1), round(y_norm * args.roi_size, 1)],
            "canonical_px": [round(x_norm * args.canonical_size, 1), round(y_norm * args.canonical_size, 1)],
        }
        teams[tag] = item
        rows.append(item)

    out = {
        "series_id": args.series_id,
        "match_number": args.match_number,
        "match_id": match.get("id"),
        "draft_id": draft_id,
        "map_id_ulid": match.get("map_id_ulid"),
        "map_name": (map_row or {}).get("name"),
        "map_canonical_id": (map_row or {}).get("canonical_id"),
        "coordinate_system": {
            "x_norm_y_norm": "0..1 ALGS spawn location coordinates",
            "roi_px": f"{args.roi_size}x{args.roi_size} map ROI coordinates",
            "frame_px": f"full-frame coordinates: x = {args.left_ignore} + x_norm*{args.roi_size}, y = y_norm*{args.roi_size}",
            "canonical_px": f"{args.canonical_size}x{args.canonical_size} render coordinates",
        },
        "teams": teams,
        "rows": rows,
    }

    save_json(Path(args.out), out)
    print(f"POI priors saved: {Path(args.out).resolve()}")
    for tag in sorted(teams):
        t = teams[tag]
        print(f"{tag:<6} {t['poi_name']:<20} norm=({t['x_norm']:.2f},{t['y_norm']:.2f}) frame={t['frame_px']}")


if __name__ == "__main__":
    main()
