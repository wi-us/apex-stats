#!/usr/bin/env python3
"""
eval_id_switches.py — простая метрика качества трекинга команд.

Принимает tracks.json (выход track_teams) и assets/gt_anchors.json
(ручные опорные точки {t, slot_id, world_xy}). Для каждой GT-точки
находит ближайший трек на ближайшем по времени кадре. Считает:

  - id_switches per slot: сколько раз меняется идентичность ближайшего
    трека между соседними GT одного и того же slot_id;
  - cross_team / same_team switches: распределение свитчей по типу
    (команды отличаются — критическая ошибка; внутри команды — мягкая);
  - coverage: % GT-точек, где slot реально имеет alive/low_conf трек;
  - px_error_med: медианное расстояние ближайшего трека до GT;
  - phase breakdown: DROP / EARLY / MID / LATE (по времени матча).

Запуск:
    python eval_id_switches.py --tracks reports/tracks.json \\
        --gt assets/gt_anchors.json --out reports/eval_id_switches.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


# Phase thresholds (seconds since match start).
PHASES = [
    ("DROP",  0.0,   30.0),
    ("EARLY", 30.0,  180.0),
    ("MID",   180.0, 420.0),
    ("LATE",  420.0, float("inf")),
]


def phase_for(t: float) -> str:
    for name, lo, hi in PHASES:
        if lo <= t < hi:
            return name
    return "LATE"


def build_slot_to_team(meta: dict) -> dict[str, str]:
    """slot_id -> team_color_group_key. Группируем по hex-цвету, т.к.
    4 слота одной команды разделяют один color preset."""
    out: dict[str, str] = {}
    for s in (meta.get("slots") or []):
        sid = s.get("slot_id") or s.get("team_id")
        # group by color hex (5 групп по 4 слота). Если color отсутствует —
        # fallback на team_id.
        key = s.get("color") or s.get("team_id") or sid
        out[sid] = key
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", required=True, type=Path)
    ap.add_argument("--gt",     required=True, type=Path)
    ap.add_argument("--out",    required=True, type=Path)
    args = ap.parse_args()

    tracks = json.loads(args.tracks.read_text(encoding="utf-8"))
    gt = json.loads(args.gt.read_text(encoding="utf-8"))
    frames = tracks["frames"]
    meta = tracks.get("meta") or {}
    slot_to_team = build_slot_to_team(meta)
    if not frames:
        print("[err] empty frames"); return
    times = [f["t"] for f in frames]

    def nearest_frame(t: float) -> dict:
        idx = min(range(len(times)), key=lambda i: abs(times[i] - t))
        return frames[idx]

    per_slot: dict[str, list[dict]] = defaultdict(list)
    px_errors: list[float] = []
    coverage_hits = 0
    coverage_total = 0
    # phase-level coverage / errors
    phase_total: dict[str, int] = defaultdict(int)
    phase_hits: dict[str, int] = defaultdict(int)
    phase_px: dict[str, list[float]] = defaultdict(list)

    for p in gt.get("points", []):
        t_p = float(p["t"])
        ph = phase_for(t_p)
        f = nearest_frame(t_p)
        gx, gy = p["world_xy"]
        best = None; best_d = float("inf")
        for tr in f["tracks"]:
            xy = tr.get("canonical_px") or tr.get("world")
            if not xy: continue
            d = math.hypot(xy[0] - gx, xy[1] - gy)
            if d < best_d:
                best_d, best = d, tr
        coverage_total += 1
        phase_total[ph] += 1
        if best is None or best_d > 200:
            per_slot[p["slot_id"]].append({
                "t": t_p, "phase": ph, "slot_id": None, "team_key": None, "d": None,
            })
            continue
        coverage_hits += 1
        phase_hits[ph] += 1
        px_errors.append(best_d)
        phase_px[ph].append(best_d)
        match_sid = best.get("slot_id")
        match_tid = best.get("team_id")
        match_team_key = slot_to_team.get(match_sid or "", match_tid)
        per_slot[p["slot_id"]].append({
            "t": t_p, "phase": ph,
            "team_id": match_tid,
            "slot_id": match_sid,
            "team_key": match_team_key,
            "d": round(best_d, 1),
        })

    switches: dict[str, int] = {}
    cross_team_switches: dict[str, int] = {}
    same_team_switches: dict[str, int] = {}
    phase_switches: dict[str, dict[str, int]] = {
        ph: {"total": 0, "cross_team": 0, "same_team": 0} for ph, _, _ in PHASES
    }
    switch_events: list[dict] = []
    for slot_id, rows in per_slot.items():
        rows.sort(key=lambda r: r["t"])
        expected_team_key = slot_to_team.get(slot_id)
        n = 0; n_cross = 0; n_same = 0
        prev = None; prev_team_key = None
        for r in rows:
            tid = r.get("slot_id") or r.get("team_id")
            if tid is None: continue
            team_key = r.get("team_key")
            if prev is not None and tid != prev:
                n += 1
                kind = "same_team" if (
                    prev_team_key is not None
                    and team_key is not None
                    and prev_team_key == team_key
                ) else "cross_team"
                if kind == "cross_team":
                    n_cross += 1
                else:
                    n_same += 1
                ph = r.get("phase", "?")
                if ph in phase_switches:
                    phase_switches[ph]["total"] += 1
                    phase_switches[ph][kind] += 1
                switch_events.append({
                    "slot_id": slot_id, "t": r["t"], "phase": ph,
                    "from": prev, "to": tid,
                    "kind": kind,
                    "expected_team_key": expected_team_key,
                })
            prev = tid
            prev_team_key = team_key
        switches[slot_id] = n
        cross_team_switches[slot_id] = n_cross
        same_team_switches[slot_id] = n_same

    # phase-level summary lines
    phase_summary = {}
    for ph, _, _ in PHASES:
        tot = phase_total.get(ph, 0)
        hit = phase_hits.get(ph, 0)
        pxs = phase_px.get(ph, [])
        phase_summary[ph] = {
            "gt_points": tot,
            "coverage_pct": round(100.0 * hit / max(1, tot), 1) if tot else 0.0,
            "px_error_med": round(statistics.median(pxs), 1) if pxs else None,
            "switches": phase_switches[ph],
        }

    summary = {
        "tracks_file": str(args.tracks),
        "gt_file": str(args.gt),
        "gt_points": coverage_total,
        "coverage_pct": round(100.0 * coverage_hits / max(1, coverage_total), 1),
        "px_error_med": round(statistics.median(px_errors), 1) if px_errors else None,
        "px_error_p95": round(statistics.quantiles(px_errors, n=20)[-1], 1) if len(px_errors) >= 20 else None,
        "id_switches_total": sum(switches.values()),
        "id_switches_cross_team": sum(cross_team_switches.values()),
        "id_switches_same_team": sum(same_team_switches.values()),
        "id_switches_per_slot": switches,
        "id_switches_per_slot_cross_team": cross_team_switches,
        "id_switches_per_slot_same_team": same_team_switches,
        "phase_breakdown": phase_summary,
        "switch_events": switch_events,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path = args.out.with_suffix(".txt")
    with txt_path.open("w", encoding="utf-8") as f:
        f.write(f"GT points:        {summary['gt_points']}\n")
        f.write(f"Coverage:         {summary['coverage_pct']}%\n")
        f.write(f"px error median:  {summary['px_error_med']}\n")
        f.write(f"px error p95:     {summary['px_error_p95']}\n")
        f.write(f"ID-switches total:    {summary['id_switches_total']}\n")
        f.write(f"  cross-team (CRITICAL): {summary['id_switches_cross_team']}\n")
        f.write(f"  same-team  (soft):     {summary['id_switches_same_team']}\n\n")
        f.write("Per-slot (sorted by total switches):\n")
        for s, n in sorted(switches.items(), key=lambda kv: -kv[1]):
            f.write(f"  {s:>10}  total={n:>2}  "
                    f"cross={cross_team_switches.get(s, 0):>2}  "
                    f"same={same_team_switches.get(s, 0):>2}\n")
        f.write("\nPhase breakdown (DROP<30s, EARLY<180s, MID<420s, LATE>=420s):\n")
        for ph, _, _ in PHASES:
            ps = phase_summary[ph]
            sw = ps["switches"]
            f.write(f"  {ph:<6} gt={ps['gt_points']:>3}  "
                    f"cov={ps['coverage_pct']:>5.1f}%  "
                    f"px_med={ps['px_error_med']}  "
                    f"switches: total={sw['total']} cross={sw['cross_team']} same={sw['same_team']}\n")
        if switch_events:
            f.write("\nSwitch events:\n")
            for e in switch_events:
                f.write(f"  t={e['t']:>7.1f}  [{e['phase']:<5}] "
                        f"{e['slot_id']:>10}  {e['from']} -> {e['to']}  "
                        f"({e['kind']})\n")
    print(f"[ok] {summary['gt_points']} GT pts | coverage={summary['coverage_pct']}% | "
          f"id_switches={summary['id_switches_total']} "
          f"(cross={summary['id_switches_cross_team']}, "
          f"same={summary['id_switches_same_team']}) | "
          f"px_med={summary['px_error_med']}")
    print(f"[ok] -> {args.out}")
    print(f"[ok] -> {txt_path}")


if __name__ == "__main__":
    main()