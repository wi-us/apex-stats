#!/usr/bin/env python3
"""Сводная таблица по всем tracks_<tag>.json в reports/matrix/.
Для каждого слота показывает % tracked-кадров (state in {tracked,low_conf})
и retired? (post_hoc_fantom) по каждому варианту прогона."""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def summarize(tracks_path: Path) -> dict[str, dict]:
    """slot_id -> {alive_pct, retired, tracked, total}."""
    doc = json.loads(tracks_path.read_text(encoding="utf-8"))
    per: dict[str, dict] = defaultdict(lambda: {"tracked": 0, "alive": 0,
                                                "retired": False, "total": 0})
    for fr in doc.get("frames", []):
        for snap in fr.get("tracks", []):
            sid = snap.get("slot_id") or snap.get("team_id")
            st = snap.get("state", "")
            d = per[sid]
            d["total"] += 1
            if st == "tracked":
                d["tracked"] += 1
                d["alive"] += 1
            elif st == "low_conf":
                d["alive"] += 1
            elif st == "inactive" and snap.get("state_reason") == "post_hoc_fantom":
                d["retired"] = True
    for sid, d in per.items():
        alive_denom = max(1, d["total"] - 0)
        d["alive_pct"] = 100.0 * d["alive"] / alive_denom
    return dict(per)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    args = ap.parse_args()
    files = sorted(p for p in args.dir.glob("tracks_*.json")
                   if not p.name.endswith(".slots.json"))
    if not files:
        print(f"[compare] нет файлов tracks_*.json в {args.dir}")
        return 2
    tag_order = [f.stem.replace("tracks_", "") for f in files]
    per_tag = {tag: summarize(f) for tag, f in zip(tag_order, files)}
    all_slots = sorted({s for d in per_tag.values() for s in d},
                       key=lambda s: int(s.split("_")[-1]) if s.split("_")[-1].isdigit() else 999)

    print(f"\nMatrix comparison — {args.dir}")
    header = "slot       " + "".join(f"{t:>16}" for t in tag_order)
    print(header)
    print("-" * len(header))
    for sid in all_slots:
        row = f"{sid:<11}"
        for tag in tag_order:
            d = per_tag[tag].get(sid)
            if d is None:
                cell = "      -"
            elif d["retired"]:
                cell = "       retired"
            else:
                cell = f"  {d['alive_pct']:>5.1f}% ({d['tracked']:>3}t)"
            row += f"{cell:>16}"
        print(row)
    print()
    # Aggregate: сколько слотов retired в каждом прогоне.
    print("[totals] retired per run:")
    for tag in tag_order:
        r = sum(1 for d in per_tag[tag].values() if d["retired"])
        t = sum(d["tracked"] for d in per_tag[tag].values())
        print(f"  {tag:<20} retired={r:>2}  total_tracked_frames={t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())