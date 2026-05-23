#!/usr/bin/env python3
"""
apply_slot_tags.py — применить результат ocr_tags к данным игры в UI.

- читает reports/slot_tags.json (assignments: slot_id -> tag)
- обновляет src/data/<game>/slot-to-tag.json (slot number -> tag), сохраняя
  существующие записи как fallback там, где OCR ничего не дал
- пишет в src/data/<game>/tracks.json meta.trimmed.ocr_tags = {assigned, total, conflicts}

Usage:
  python scripts/postprocess/apply_slot_tags.py \
      --slot-tags scripts/tracking/modules/ocr_tags/reports/slot_tags.json \
      --game      src/data/m-test-g1
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SLOT_RE = re.compile(r"slot[_-]?(\d+)", re.IGNORECASE)


def slot_num(slot_id: str) -> str | None:
    m = SLOT_RE.search(slot_id)
    return m.group(1) if m else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot-tags", required=True, type=Path)
    ap.add_argument("--game", required=True, type=Path,
                    help="Каталог src/data/<game>")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    payload = json.loads(args.slot_tags.read_text(encoding="utf-8"))
    assignments: dict = payload.get("assignments", {})
    per_slot: dict = payload.get("per_slot", {})
    conflicts = [s for s, r in per_slot.items() if r.get("needs_review")]

    map_path = args.game / "slot-to-tag.json"
    cur = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}

    changed = {}
    for slot_id, tag in assignments.items():
        n = slot_num(slot_id)
        if not n:
            continue
        if cur.get(n) != tag:
            changed[n] = {"from": cur.get(n), "to": tag}
            cur[n] = tag

    print(f"[info] OCR назначений: {len(assignments)}")
    print(f"[info] изменено в slot-to-tag.json: {len(changed)}")
    for n, ch in sorted(changed.items(), key=lambda kv: int(kv[0])):
        print(f"  slot {n}: {ch['from']} -> {ch['to']}")
    if conflicts:
        print(f"[warn] коллизии (нужен review): {conflicts}")

    tracks_path = args.game / "tracks.json"
    meta_patch = None
    if tracks_path.exists():
        tr = json.loads(tracks_path.read_text(encoding="utf-8"))
        meta = tr.setdefault("meta", {})
        trimmed = meta.setdefault("trimmed", {})
        trimmed["ocr_tags"] = {
            "assigned": len(assignments),
            "total_slots": len(per_slot),
            "changed": len(changed),
            "conflicts": conflicts,
        }
        meta_patch = trimmed["ocr_tags"]

    if args.dry_run:
        print("[dry-run] файлы не записаны")
        return

    map_path.write_text(json.dumps(cur, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[ok] -> {map_path}")
    if meta_patch is not None:
        tracks_path.write_text(json.dumps(tr, ensure_ascii=False),
                               encoding="utf-8")
        print(f"[ok] -> {tracks_path} (meta.trimmed.ocr_tags={meta_patch})")


if __name__ == "__main__":
    main()