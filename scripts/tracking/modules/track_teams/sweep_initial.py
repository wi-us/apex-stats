#!/usr/bin/env python3
"""sweep_initial.py — массивный параметрический поиск для первых 30 секунд.

Идея: вместо ручных 10-15 named-конфигов генерируем большую сетку (десятки
вариантов), гоняем track_teams.py параллельно (до --jobs воркеров) с
`--end 30`, затем оцениваем каждый выход по GT-якорям из assets/gt_anchors.json
с t<=30. Для каждого слота сравниваем позицию трека с тем же slot_id с GT —
это прямой ответ на вопрос «корректно ли определена команда на 0:00».

На выходе ровно три файла (никакого мусора per-variant):
  reports/sweep_initial/sweep_report.json   — машинный
  reports/sweep_initial/sweep_report.txt    — человекочитаемый
  reports/sweep_initial/winner_tracks.json  — выход варианта-победителя
  reports/sweep_initial/winner_tracks.slots.json
  reports/sweep_initial/winner.config.yaml  — какой именно конфиг победил

Все промежуточные YAML/tracks_*.json удаляются.

Запуск:
  python sweep_initial.py --video D:\\path\\game.mp4 \\
      --anchors scripts/tracking/modules/motion_detect/reports/motion_tracks.json \\
      --eliminations scripts/tracking/modules/hud_read/reports/eliminations.json \\
      --jobs 8 --max-variants 60
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import itertools
import json
import math
import shutil
import statistics
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

from anchor_diagnostics import group_gt_points, slot_sort_key, summarize_anchor_coverage

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3].parent  # repo root
MOD = Path(__file__).resolve().parent

# ───────────────────────── базовый шаблон ─────────────────────────
# Минимальный валидный конфиг; sweep будет точечно патчить ключи.
BASE_CFG = {
    "canonical_map": "storm_point",
    "frame_step": 30,
    "anchors_file": "../../motion_detect/reports/motion_tracks.json",
    "eliminations_file": "../../hud_read/reports/eliminations.json",
    "zones_file": "../../../configs/zones.vod.json",
    "da_strategy": "detect_first",
    "da_weights": {
        "beta_world": 1.0,
        "gamma_shape": 0.3,
        "delta_color_mismatch": 5.0,
        "eps_hysteresis": 0.3,
        "gate_radius_mult": 1.2,
        "fallback_gate_canonical_px": 250.0,
        "frame_sanity_jump_px": 150.0,
        "frame_sanity_max_jumps": 6,
    },
    "registration": {
        "detector": "sift", "max_features": 4000, "clahe": True,
        "canonical_target_w": 1600, "match_ratio": 0.75,
        "ransac_reproj_px": 5.0, "min_inliers": 15,
        "roi": [0.21875, 0.0, 0.78125, 1.0],
    },
    "detection": {"min_area_px": 40, "max_area_px": 2400, "morph_kernel": 3},
    "slot_tracker": {
        "roi_size": 220,
        "min_tracked_for_active": 10,
        "min_tracked_ratio_for_active": 0.20,
        "near_anchor_radius_canonical_px": 120.0,
    },
    "tracking": {
        "max_gap_frames": 30,
        "gating_world_dist": 50.0,
        "process_noise": 1.0,
        "measurement_noise": 4.0,
        "init_warmup_sec": 5.0,
        "init_reject_world_margin": 30.0,
        "init_min_score": 0.3,
    },
    "output": {"include_frame_px": True, "include_canonical_px": True},
}

# ─────────────────────── оси параметрического поиска ───────────────────────
# Каждая ось = (key_path, [(label, value), ...]).
# Декартово произведение -> множество вариантов. Урезается --max-variants.
AXES = [
    # Цветовое и DA-разрешение похожих команд.
    ("da_strategy",                                  [("ds=detect", "detect_first"),
                                                       ("ds=color",  "color_first"),
                                                       ("ds=hybrid", "hybrid")]),
    ("da_weights.delta_color_mismatch",              [("dc=2", 2.0), ("dc=5", 5.0), ("dc=10", 10.0)]),
    # Motion-гейт (ширина зоны поиска) — главный регулятор «слипания» с похожим цветом.
    ("slot_tracker.motion.gate_cap_px",              [("gc=120", 120.0), ("gc=200", 200.0), ("gc=350", 350.0)]),
    ("slot_tracker.motion.v_max_px_s",               [("vm=40", 40.0), ("vm=60", 60.0), ("vm=90", 90.0)]),
    # Защита от перепрыгивания на чужой трек.
    ("slot_tracker.jump_switch_threshold_px",        [("js=40", 40.0), ("js=80", 80.0), ("js=150", 150.0)]),
    ("slot_tracker.switch_confirm_frames",           [("sc=3", 3), ("sc=8", 8)]),
    # Анкер-старт: насколько жёстко прибиваем слот к POI в первые секунды.
    ("slot_tracker.anchor_lock_sec",                 [("al=0", 0.0), ("al=10", 10.0), ("al=30", 30.0)]),
    ("slot_tracker.near_anchor_radius_canonical_px", [("na=80", 80.0), ("na=120", 120.0), ("na=200", 200.0)]),
    # Темп обработки.
    ("frame_step",                                   [("fs=30", 30), ("fs=60", 60)]),
]


def set_path(d: dict, path: str, value):
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def gen_variants(max_n: int) -> list[dict]:
    """Генерируем сетку. Стратегия: берём первые max_n из cartesian (детерминированно)."""
    combos = list(itertools.product(*[ax[1] for ax in AXES]))
    # Перемешиваем «равномерно» — берём через равный шаг чтобы покрыть разные оси.
    step = max(1, len(combos) // max_n)
    picked = combos[::step][:max_n]
    out = []
    for combo in picked:
        cfg = deepcopy(BASE_CFG)
        label_parts = []
        for (key_path, _), (lab, val) in zip(AXES, combo):
            set_path(cfg, key_path, val)
            label_parts.append(lab)
        out.append({"tag": "_".join(label_parts), "cfg": cfg})
    # Уникализируем по тегу (на всякий).
    seen, uniq = set(), []
    for v in out:
        if v["tag"] in seen: continue
        seen.add(v["tag"]); uniq.append(v)
    return uniq


def dump_yaml(cfg: dict, path: Path):
    """Простой YAML-дамп через json (track_teams.py читает обоими — на самом деле через yaml.safe_load,
    но JSON — валидный YAML)."""
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def run_one(args, variant, out_dir: Path) -> dict:
    tag = variant["tag"]
    cfg_path = out_dir / "_configs" / f"{tag}.yaml"
    tracks_path = out_dir / "_tracks" / f"{tag}.json"
    log_path = out_dir / "_logs" / f"{tag}.log"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tracks_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(variant["cfg"], cfg_path)

    cmd = [
        sys.executable,
        str(MOD / "track_teams.py"),
        "--video", args.video,
        "--config", str(cfg_path),
        "--out", str(tracks_path),
        "--start", "0",
        "--end", str(args.end),
        "--anchors", args.anchors,
        "--eliminations", args.eliminations,
    ]
    t0 = time.time()
    with log_path.open("w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    return {"tag": tag, "cfg_path": str(cfg_path), "tracks_path": str(tracks_path),
            "log_path": str(log_path), "exit_code": proc.returncode, "duration_s": round(dt, 1)}


def evaluate(tracks_file: Path, gt_points: list[dict], match_px: float) -> dict:
    """Для каждой GT-группы находим трек с тем же slot_id в ближайшем по времени кадре,
    считаем d_px. Также находим АБСОЛЮТНО ближайший трек — это «реально кого распознали»."""
    if not tracks_file.exists():
        return {"ok": False, "reason": "no_tracks_file"}
    try:
        doc = json.loads(tracks_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"parse:{e}"}
    frames = doc.get("frames", [])
    if not frames:
        return {"ok": False, "reason": "no_frames"}
    times = [f["t"] for f in frames]
    def nearest(t):
        return frames[min(range(len(times)), key=lambda i: abs(times[i] - t))]
    per_slot = {}
    correct = 0
    d_list = []
    for gp in gt_points:
        sid = gp["slot_id"]
        label = gp.get("label", sid)
        gt_xy = gp.get("points") or [gp["world_xy"]]
        f = nearest(float(gp["t"]))
        own = None; own_d = None
        nearest_tr = None; nearest_d = float("inf")
        for tr in f.get("tracks", []):
            xy = tr.get("canonical_px") or tr.get("world")
            if not xy: continue
            d = min(math.hypot(xy[0] - gx, xy[1] - gy) for gx, gy in gt_xy)
            if d < nearest_d:
                nearest_d, nearest_tr = d, tr
            if (tr.get("slot_id") or tr.get("team_id")) == sid:
                if own_d is None or d < own_d:
                    own_d, own = d, tr
        ok = (own_d is not None and own_d <= match_px)
        if ok:
            correct += 1
            d_list.append(own_d)
        per_slot[label] = {
            "own_d": round(own_d, 1) if own_d is not None else None,
            "ok": ok,
            "nearest_slot": (nearest_tr.get("slot_id") or nearest_tr.get("team_id")) if nearest_tr else None,
            "nearest_d": round(nearest_d, 1) if nearest_tr else None,
        }
    return {
        "ok": True,
        "correct": correct,
        "total": len(gt_points),
        "score_pct": round(100.0 * correct / max(1, len(gt_points)), 1),
        "d_med": round(statistics.median(d_list), 1) if d_list else None,
        "d_mean": round(statistics.mean(d_list), 1) if d_list else None,
        "per_slot": per_slot,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--anchors", default=str(MOD.parent / "motion_detect" / "reports" / "motion_tracks.json"))
    ap.add_argument("--eliminations", default=str(MOD.parent / "hud_read" / "reports" / "eliminations.json"))
    ap.add_argument("--gt", default=str(MOD / "assets" / "gt_anchors.json"))
    ap.add_argument("--end", type=float, default=300.0, help="секунд видео анализировать (по умолчанию 5 мин)")
    ap.add_argument("--gt-cutoff", type=float, default=30.5,
                    help="окно GT-якорей для оценки идентификации на старте")
    ap.add_argument("--match-px", type=float, default=100.0, help="d_px <= этого = «корректно»")
    ap.add_argument("--jobs", type=int, default=8, help="параллельные процессы (1..15)")
    ap.add_argument("--max-variants", type=int, default=40)
    ap.add_argument("--out-dir", default=str(MOD / "reports" / "sweep_initial"))
    ap.add_argument("--keep-intermediate", action="store_true",
                    help="не удалять _tracks/_configs/_logs после прогона")
    args = ap.parse_args()

    args.jobs = max(1, min(15, args.jobs))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Sanity-check входов.
    for label, p in [("video", args.video), ("anchors", args.anchors),
                     ("eliminations", args.eliminations), ("gt", args.gt)]:
        if not Path(p).exists():
            print(f"[err] {label} не найден: {p}", file=sys.stderr); return 2

    gt_all = json.loads(Path(args.gt).read_text(encoding="utf-8"))["points"]
    gt_raw = [p for p in gt_all if float(p["t"]) <= args.gt_cutoff]
    gt_pts = group_gt_points(gt_raw)
    print(f"[sweep] GT в окне [0..{args.gt_cutoff}s]: {len(gt_raw)} точек, "
          f"{len(gt_pts)} групп для оценки")

    # ── INPUT SANITY: проверяем, что anchors покрывают окно оценки ──
    anchors_diag = summarize_anchor_coverage(Path(args.anchors), gt_raw, args.end)
    print(f"[sweep] anchors окно: t=[{anchors_diag['t_min']:.1f}..{anchors_diag['t_max']:.1f}]s, "
          f"всего точек={anchors_diag['total_pts']}")
    if anchors_diag["t_max"] < args.end or anchors_diag["t_min"] > 0.5:
        print(f"[WARN] anchors НЕ покрывают [0..{args.end}]s — пересобери motion_tracks "
              f"с -StartSec 0 -Window {anchors_diag.get('suggested_window_step5', 390)} -Step 5!", file=sys.stderr)

    variants = gen_variants(args.max_variants)
    print(f"[sweep] вариантов: {len(variants)}, jobs={args.jobs}")

    # Чистим прошлые промежутки.
    for sub in ("_tracks", "_configs", "_logs"):
        shutil.rmtree(out_dir / sub, ignore_errors=True)

    t_start = time.time()
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_one, args, v, out_dir): v for v in variants}
        done_n = 0
        for fut in cf.as_completed(futs):
            res = fut.result()
            done_n += 1
            ev = evaluate(Path(res["tracks_path"]), gt_pts, args.match_px)
            res["eval"] = ev
            results.append(res)
            status = f"{ev['correct']}/{ev['total']}" if ev.get("ok") else "FAIL"
            print(f"[sweep] {done_n}/{len(variants)} {res['tag']:<55} ({res['duration_s']}s) -> {status}")

    total_dt = round(time.time() - t_start, 1)

    # Сортировка: больше correct, меньше d_med.
    def sort_key(r):
        e = r.get("eval") or {}
        return (-(e.get("correct") or 0), e.get("d_med") if e.get("d_med") is not None else 1e9)
    results.sort(key=sort_key)
    winner = results[0] if results and results[0].get("eval", {}).get("ok") else None

    # Per-slot: для каждого слота — лучший вариант (минимальный own_d среди ok=True).
    slot_ids = [gp["label"] for gp in gt_pts]
    per_slot_best = {}
    for sid in slot_ids:
        best = None
        for r in results:
            ps = (r.get("eval") or {}).get("per_slot", {}).get(sid)
            if not ps or not ps.get("ok"): continue
            if best is None or ps["own_d"] < best["d_px"]:
                best = {"variant": r["tag"], "d_px": ps["own_d"]}
        per_slot_best[sid] = best  # None если ни один не справился

    report = {
        "video": args.video,
        "end_sec": args.end,
        "match_px": args.match_px,
        "gt_points_raw": len(gt_raw),
        "gt_points": len(gt_pts),
        "variants_total": len(variants),
        "total_duration_s": total_dt,
        "winner": winner["tag"] if winner else None,
        "top10": [
            {"tag": r["tag"], **{k: r["eval"].get(k) for k in ("correct", "total", "score_pct", "d_med", "d_mean")}}
            for r in results[:10] if r.get("eval", {}).get("ok")
        ],
        "per_slot_best": per_slot_best,
        "all_results": [
            {
                "tag": r["tag"], "exit_code": r["exit_code"], "duration_s": r["duration_s"],
                "eval": r.get("eval"),
            } for r in results
        ],
    }

    (out_dir / "sweep_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # TXT-сводка.
    lines = []
    lines.append(f"sweep_initial: {len(variants)} variants, end={args.end}s, "
                 f"match_px={args.match_px}, jobs={args.jobs}, total={total_dt}s")
    lines.append(f"GT points: {len(gt_raw)} raw, {len(gt_pts)} grouped\n")
    lines.append("TOP-10 by (correct desc, d_med asc):")
    lines.append(f"  {'rank':>4} {'correct':>8} {'d_med':>7} {'d_mean':>7}  tag")
    for i, r in enumerate(report["top10"], 1):
        lines.append(f"  {i:>4} {r['correct']:>3}/{r['total']:<3} "
                     f"{str(r['d_med']):>7} {str(r['d_mean']):>7}  {r['tag']}")
    lines.append("")
    lines.append("PER-SLOT BEST (минимальный d_px среди вариантов где slot правильный):")
    lines.append(f"  {'slot':<10} {'d_px':>7}  best_variant")
    for sid in sorted(slot_ids, key=slot_sort_key):
        b = per_slot_best.get(sid)
        if b is None:
            lines.append(f"  {sid:<10} {'—':>7}  (никто не справился)")
        else:
            lines.append(f"  {sid:<10} {b['d_px']:>7}  {b['variant']}")
    if winner:
        lines.append("")
        lines.append(f"WINNER (overall): {winner['tag']}")
        ps = winner["eval"]["per_slot"]
        lines.append("  per-slot breakdown:")
        for sid in sorted(slot_ids, key=slot_sort_key):
            d = ps.get(sid, {})
            mark = "OK " if d.get("ok") else "BAD"
            extra = ""
            if not d.get("ok"):
                extra = f"  (nearest={d.get('nearest_slot')} @ {d.get('nearest_d')}px)"
            lines.append(f"    {sid:<10} {mark}  own_d={d.get('own_d')}{extra}")
    # ── ANCHORS IN WINDOW ──
    lines.append("")
    lines.append(f"ANCHORS IN WINDOW [0..{args.end}s] (motion-points в радиусе 200px от GT):")
    lines.append(f"  anchors file t=[{anchors_diag['t_min']:.1f}..{anchors_diag['t_max']:.1f}]s, "
                 f"total_pts={anchors_diag['total_pts']}")
    lines.append(f"  {'slot':<10} {'pts<=200px':>11}  {'nearest_pt_px':>14}")
    for sid in sorted(slot_ids, key=slot_sort_key):
        base_sid = sid.split("@")[0]
        d = anchors_diag["per_slot"].get(base_sid, {"n_near": 0, "nearest_px": None})
        nx = "—" if d["nearest_px"] is None else f"{d['nearest_px']:.1f}"
        lines.append(f"  {sid:<10} {d['n_near']:>11}  {nx:>14}")
    if anchors_diag["t_max"] < args.end:
        lines.append(f"  [WARN] anchors заканчиваются на {anchors_diag['t_max']:.1f}s — "
                     f"пересобери motion_tracks с -StartSec 0 -Window {anchors_diag.get('suggested_window_step5', 390)} -Step 5")

    # ── CONFUSION (по победителю) ──
    if winner:
        lines.append("")
        lines.append("CONFUSION (по winner): ближайший ЛЮБОЙ трек к GT каждого слота")
        lines.append(f"  {'gt_slot':<10} {'nearest_slot':<14} {'d_px':>8}")
        for sid in sorted(slot_ids, key=slot_sort_key):
            d = ps.get(sid, {})
            lines.append(f"  {sid:<10} {str(d.get('nearest_slot')):<14} {str(d.get('nearest_d')):>8}")

    (out_dir / "sweep_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))

    # Сохраняем выход победителя.
    if winner:
        win_src = Path(winner["tracks_path"])
        win_slots = win_src.with_suffix(".slots.json")
        if win_src.exists():
            shutil.copy2(win_src, out_dir / "winner_tracks.json")
        if win_slots.exists():
            shutil.copy2(win_slots, out_dir / "winner_tracks.slots.json")
        shutil.copy2(Path(winner["cfg_path"]), out_dir / "winner.config.yaml")

    # Чистим промежутки.
    if not args.keep_intermediate:
        for sub in ("_tracks", "_configs", "_logs"):
            shutil.rmtree(out_dir / sub, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())