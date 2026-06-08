#!/usr/bin/env python3
"""diagnose_inputs.py — быстрый аудит входов перед свипом.

Проверяет реальные trajectory-точки из motion_detect/reports/motion_tracks.json
в формате results[].moving[].points и сравнивает их с GT в canonical-map px.

Запуск:
  python scripts/tracking/modules/track_teams/diagnose_inputs.py --end 30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from anchor_diagnostics import slot_sort_key, summarize_anchor_coverage


MOD = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchors", default=str(MOD.parent / "motion_detect" / "reports" / "motion_tracks.json"))
    ap.add_argument("--gt", default=str(MOD / "assets" / "gt_anchors.json"))
    ap.add_argument("--end", type=float, default=30.0)
    ap.add_argument("--radius", type=float, default=200.0)
    ap.add_argument("--map", default="storm_point")
    args = ap.parse_args()

    for label, p in [("anchors", args.anchors), ("gt", args.gt)]:
        if not Path(p).exists():
            print(f"[err] {label} не найден: {p}", file=sys.stderr)
            return 2

    raw = json.loads(Path(args.anchors).read_text(encoding="utf-8"))
    gt_all = json.loads(Path(args.gt).read_text(encoding="utf-8"))["points"]
    gt = [g for g in gt_all if float(g["t"]) <= args.end + 0.5]
    diag = summarize_anchor_coverage(Path(args.anchors), gt, args.end, map_name=args.map, radius=args.radius)

    print(f"anchors file: {args.anchors}")
    print(f"  fps={diag.get('fps', raw.get('fps', 60.0)):.2f}  start_sec={raw.get('start_sec')}  "
          f"window={raw.get('window')}  step={raw.get('step')}")
    print(f"  motion-points raw={diag.get('total_raw_pts', 0)}, "
          f"in window [0..{args.end}]s={diag['total_pts']}, "
          f"t=[{diag['t_min']:.1f}..{diag['t_max']:.1f}]s")
    if diag["t_max"] < args.end or diag["t_min"] > 0.5:
        print(f"\n  [!!] anchors не покрывают окно [0..{args.end}]s.")
        print("  [!!] ПЕРЕСОБЕРИ motion_tracks командой:")
        print("       powershell -ExecutionPolicy Bypass -File "
              "scripts\\tracking\\modules\\motion_detect\\push.ps1 `")
        print(f"         -Video scripts\\tracking\\game_sp.mp4 -StartSec 0 "
              f"-Window {diag.get('suggested_window_step5', 390)} -Step 5 -NoPush")

    print(f"\nPER-SLOT (radius={args.radius}px in [0..{args.end}]s):")
    print(f"  {'slot':<10} {'n_near':>7}  {'nearest_px':>11}  verdict")
    n_dead = 0
    for sid in sorted(diag["per_slot"], key=slot_sort_key):
        d = diag["per_slot"][sid]
        n_near = int(d["n_near"])
        nx = d["nearest_px"]
        verdict = "OK" if n_near >= 3 else ("WEAK" if n_near >= 1 else "DEAD")
        if verdict == "DEAD":
            n_dead += 1
        nxs = "—" if nx is None else f"{nx:.1f}"
        print(f"  {sid:<10} {n_near:>7}  {nxs:>11}  {verdict}")
    print(f"\nИТОГ: DEAD слотов = {n_dead}/{len(diag['per_slot'])}")
    if n_dead > len(diag["per_slot"]) // 3:
        print("  → motion_detect/HSV или GT-позиции — основной баг. Свип DA-параметров не поможет.")
    else:
        print("  → anchors норм, можно свипить DA-параметры.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())