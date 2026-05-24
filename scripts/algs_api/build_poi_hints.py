"""Build a POI hints JSON for track_teams.py --poi-hints from SQLite."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db


REPO_ROOT = Path(__file__).resolve().parents[2]


def _radius_for(canonical_id: str, spawn_id: str, default_r: float) -> float:
    p = REPO_ROOT / "src" / "data" / "maps" / canonical_id / "poi_zones.json"
    if not p.exists():
        return default_r
    try:
        arr = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default_r
    for z in arr or []:
        if isinstance(z, dict) and z.get("id") == spawn_id:
            try:
                return float(z.get("r") or default_r)
            except (TypeError, ValueError):
                return default_r
    return default_r


def build(conn, *, series_id: str, canonical_map: str,
          default_radius: float) -> dict[str, dict[str, float]]:
    rows = conn.execute(
        """
        SELECT pp.spawn_location_id,
               sl.x_norm, sl.y_norm,
               pp.team_id,
               t.short_name, t.name,
               st.position
          FROM poi_picks pp
          JOIN poi_drafts pd      ON pd.id = pp.draft_id
          JOIN spawn_locations sl ON sl.id = pp.spawn_location_id
          JOIN maps m             ON m.id_ulid = pp.map_id_ulid
          LEFT JOIN teams t       ON t.id = pp.team_id
          LEFT JOIN series_team_stats st
                                  ON st.series_id = pd.series_id
                                 AND st.team_id  = pp.team_id
         WHERE pd.series_id = ?
           AND m.canonical_id = ?
           AND sl.x_norm IS NOT NULL
           AND sl.y_norm IS NOT NULL
        """,
        (series_id, canonical_map),
    ).fetchall()
    if not rows:
        return {}

    by_team: dict[str, dict] = {}
    for r in rows:
        tid = r["team_id"] or ""
        if tid in by_team:
            continue
        by_team[tid] = dict(r)
    ordered = sorted(
        by_team.values(),
        key=lambda r: (r["position"] if r["position"] is not None else 999,
                       (r["name"] or "")),
    )
    slot_by_tid = {r["team_id"]: i + 1 for i, r in enumerate(ordered)}

    out: dict[str, dict[str, float]] = {}
    for r in rows:
        entry = {
            "cx": round(float(r["x_norm"]), 4),
            "cy": round(float(r["y_norm"]), 4),
            "r":  _radius_for(canonical_map, r["spawn_location_id"], default_radius),
        }
        slot = slot_by_tid.get(r["team_id"])
        if slot is not None:
            out[f"slot_{slot}"] = entry
        tag = (r["short_name"] or "").strip()
        if tag:
            out[tag] = entry
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="scripts.algs_api.build_poi_hints")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--series", required=True)
    ap.add_argument("--map", dest="map_id", required=True)
    ap.add_argument("--radius", type=float, default=0.03)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    with db.connect(args.db) as conn:
        data = build(conn, series_id=args.series,
                     canonical_map=args.map_id,
                     default_radius=args.radius)
    if not data:
        print(f"[hints] no picks for series={args.series} map={args.map_id}",
              file=sys.stderr)
        sys.exit(2)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[hints] {len(data)} entries -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()