#!/usr/bin/env python3
"""Copy plate_detector outputs into src/data/<game>/ for the web app."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[3]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_slots_payload(tracks: dict[str, Any]) -> dict[str, Any]:
    slots = []
    for item in tracks.get("meta", {}).get("slots", []) or []:
        slots.append(
            {
                "slot_id": item.get("slot_id"),
                "slot": item.get("slot"),
                "team_id": item.get("team_id"),
                "name": item.get("name") or item.get("team_tag") or item.get("broadcast_tag"),
                "color": item.get("color"),
                "team_db_id": item.get("team_db_id"),
                "team_tag": item.get("team_tag"),
                "broadcast_tag": item.get("broadcast_tag"),
                "anchor_conf": item.get("anchor_conf", "UNKNOWN"),
                "anchor_world": item.get("anchor_world"),
                "wiped_at_t": item.get("wiped_at_t"),
            }
        )
    return {"slots": slots}


def build_slot_to_tag(tracks: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in tracks.get("meta", {}).get("slots", []) or []:
        slot = item.get("slot")
        tag = item.get("team_tag") or item.get("broadcast_tag") or item.get("name")
        if slot is not None and tag:
            out[str(int(slot))] = str(tag).upper()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", type=Path, required=True, help="plate_detector tracks.json")
    parser.add_argument("--game", default="m-test-g1", help="src/data game directory name")
    parser.add_argument("--out", type=Path, default=None, help="override output directory")
    parser.add_argument("--copy-reports", action="store_true", help="copy detections.csv/jsonl and summary.json into plate_detector/")
    parser.add_argument("--write-slot-tags", action="store_true", help="write slot-to-tag.json from plate detector metadata")
    args = parser.parse_args()

    tracks_path = args.tracks.resolve()
    if not tracks_path.exists():
        raise FileNotFoundError(tracks_path)

    out_dir = args.out or (REPO_ROOT / "src" / "data" / args.game)
    out_dir.mkdir(parents=True, exist_ok=True)

    tracks = load_json(tracks_path)
    shutil.copy2(tracks_path, out_dir / "tracks.json")
    save_json(out_dir / "tracks.slots.json", build_slots_payload(tracks))
    print(f"[plate_detector] tracks.json -> {out_dir / 'tracks.json'}")
    print(f"[plate_detector] tracks.slots.json -> {out_dir / 'tracks.slots.json'}")

    if args.write_slot_tags:
        slot_to_tag = build_slot_to_tag(tracks)
        save_json(out_dir / "slot-to-tag.json", slot_to_tag)
        print(f"[plate_detector] slot-to-tag.json ({len(slot_to_tag)} teams)")

    if args.copy_reports:
        report_dir = out_dir / "plate_detector"
        report_dir.mkdir(parents=True, exist_ok=True)
        source_dir = tracks_path.parent
        for name in ("detections.csv", "detections.jsonl", "summary.json"):
            src = source_dir / name
            if src.exists():
                shutil.copy2(src, report_dir / name)
                print(f"[plate_detector] {name} -> {report_dir / name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
