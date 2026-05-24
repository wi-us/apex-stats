"""Write src/data/maps/<map_id>/poi_zones.json from ALGS spawn_locations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db


REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO_ROOT / "src" / "data" / "maps"
DEFAULT_RADIUS = 0.030


def export_map(conn, canonical_id: str, *, default_radius: float) -> int:
    rows = conn.execute(
        """
        SELECT sl.id, sl.name, sl.x_norm, sl.y_norm, sl.in_game_drop_id
          FROM spawn_locations sl
          JOIN maps m ON m.id_ulid = sl.map_id_ulid
         WHERE m.canonical_id = ?
           AND sl.x_norm IS NOT NULL
           AND sl.y_norm IS NOT NULL
         ORDER BY sl.name
        """,
        (canonical_id,),
    ).fetchall()

    out_path = OUT_ROOT / canonical_id / "poi_zones.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing_by_id: dict[str, dict] = {}
    if out_path.exists():
        try:
            cur = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(cur, list):
                for z in cur:
                    if isinstance(z, dict) and z.get("id"):
                        existing_by_id[str(z["id"])] = z
        except Exception as e:  # noqa: BLE001
            print(f"[export-poi] {out_path}: existing JSON unreadable: {e}",
                  file=sys.stderr)

    out: list[dict] = []
    for r in rows:
        prev = existing_by_id.get(r["id"]) or {}
        zone = {
            "id": r["id"],
            "name": r["name"],
            "cx": round(float(r["x_norm"]), 4),
            "cy": round(float(r["y_norm"]), 4),
            "r":  float(prev.get("r") or default_radius),
        }
        if r["in_game_drop_id"] is not None:
            zone["inGameDropId"] = int(r["in_game_drop_id"])
        if prev.get("aliases"):
            zone["aliases"] = list(prev["aliases"])
        out.append(zone)

    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"[export-poi] {canonical_id}: wrote {len(out)} zones -> {out_path}",
          file=sys.stderr)
    return len(out)


def main() -> None:
    ap = argparse.ArgumentParser(prog="scripts.algs_api.export_poi_zones")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--map", dest="map_id",
                    help="Canonical map id (e.g. storm_point). Omit with --all.")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--radius", type=float, default=DEFAULT_RADIUS)
    args = ap.parse_args()

    with db.connect(args.db) as conn:
        if args.all:
            ids = [row["canonical_id"] for row in conn.execute(
                "SELECT DISTINCT canonical_id FROM maps "
                "WHERE canonical_id IS NOT NULL"
            ).fetchall()]
            for cid in ids:
                export_map(conn, cid, default_radius=args.radius)
        elif args.map_id:
            export_map(conn, args.map_id, default_radius=args.radius)
        else:
            ap.error("provide --map <id> or --all")


if __name__ == "__main__":
    main()