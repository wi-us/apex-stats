#!/usr/bin/env python3
"""
Build a POI hints JSON for track_teams.py from:
  - a tournament cache file (scripts/scrape_liquipedia/data/tournaments/<slug>.json)
  - a canonical POI zones file (src/data/maps/<map_id>/poi_zones.json)
  - a stage + map selector (e.g. --stage finals --map storm_point)

Output schema (consumed by track_teams.py --poi-hints):
  {
    "slot_1": { "cx": 0.61, "cy": 0.41, "r": 0.035 },
    "TSM":    { "cx": 0.18, "cy": 0.22, "r": 0.030 },
    ...
  }

Both `slot_<N>` (1-based, matching the tournament's `teams[]` order on the
chosen map) and team tag keys are emitted, so the tracker can look up by
whichever it has on hand.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _match_zone(zones: list[dict[str, Any]], spot: str) -> dict[str, Any] | None:
    key = _norm(spot)
    if not key:
        return None
    for z in zones:
        if _norm(z.get("name", "")) == key:
            return z
        for a in z.get("aliases") or []:
            if _norm(a) == key:
                return z
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tournament", required=True, type=Path,
                    help="Path to scripts/scrape_liquipedia/data/tournaments/<slug>.json")
    ap.add_argument("--zones", required=True, type=Path,
                    help="Path to src/data/maps/<map_id>/poi_zones.json")
    ap.add_argument("--stage", required=True,
                    help="Stage key as stored in poi_drafts (e.g. finals, regular)")
    ap.add_argument("--map", dest="map_id", required=True,
                    help="Map id as stored in poi_drafts (e.g. storm_point)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    tdata = json.loads(args.tournament.read_text(encoding="utf-8"))
    zones = json.loads(args.zones.read_text(encoding="utf-8"))

    drafts = (tdata.get("poi_drafts") or {}).get(args.stage, {}).get(args.map_id, [])
    if not drafts:
        print(f"[err] no picks for stage={args.stage} map={args.map_id} in "
              f"{args.tournament}", file=sys.stderr)
        sys.exit(2)

    # slot index = position in the tournament's teams[] list.
    teams = tdata.get("teams") or []
    slot_by_slug = {t["slug"]: i + 1 for i, t in enumerate(teams)}
    tag_by_slug = {t["slug"]: (t.get("tag") or t.get("name") or t["slug"]) for t in teams}

    out: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    for pick in drafts:
        spot = pick.get("spot")
        zone = _match_zone(zones, spot or "")
        if zone is None:
            missing.append(f"{pick.get('team_name')} -> {spot!r}")
            continue
        entry = {"cx": float(zone["cx"]), "cy": float(zone["cy"]), "r": float(zone["r"])}
        slot = slot_by_slug.get(pick.get("team_slug", ""))
        if slot is not None:
            out[f"slot_{slot}"] = entry
        tag = tag_by_slug.get(pick.get("team_slug", ""))
        if tag:
            out[tag] = entry

    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] wrote {len(out)} hint entries to {args.out}", file=sys.stderr)
    if missing:
        print(f"[warn] {len(missing)} picks had no zone match:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)


if __name__ == "__main__":
    main()