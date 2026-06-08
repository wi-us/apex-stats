#!/usr/bin/env python3
"""
find_cuts.py — поиск точных кадров «телепортаций» камеры обсервера.

Алгоритм:
  1. Идём по видео грубым шагом (--coarse, по умолчанию 600 кадров),
     регистрируем кадр против канонической карты, считаем pan_canonical
     (центр кадра на карте).
  2. Если |pan_curr - pan_prev| > --threshold — между prev и curr был cut.
  3. Откатываемся от curr назад с шагом --fine (по умолчанию 10),
     регистрируя каждый промежуточный кадр, пока pan не «вернётся» к prev
     (т.е. найдём последний кадр СТАРОЙ позиции).
  4. Cut = первый кадр после этого. Записываем событие.
  5. Продолжаем грубое сканирование с curr (откуда начали откат).

Вывод:
  cuts.json  — {"events": [{"frame": N, "t": sec, "from_pan":[...], "to_pan":[...], "delta": px}, ...]}
  cuts.txt   — человекочитаемая сводка
  overlay_cut_<N>.png — карта со стрелкой «откуда -> куда» для каждого cut'а

Запуск:
  python find_cuts.py --video game.mp4 --config config.example.yaml --out cuts_out \
      --coarse 600 --fine 10 --threshold 150
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# Windows PowerShell can default to cp1251/cp866; argparse help contains
# symbols outside that codepage.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Registration helpers still live in the archived legacy tracker.
_TT_DIR = Path(__file__).resolve().parent.parent / "_archived" / "track_teams"
if str(_TT_DIR) not in sys.path:
    sys.path.insert(0, str(_TT_DIR))

from track_teams import load_canonical_map, load_config, FrameRegistrar, map_point


def register_frame(cap, reg: FrameRegistrar, idx: int):
    """Прыгает на кадр idx, регистрирует, возвращает (pan_xy, inliers) или (None, inliers)."""
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    if not ok:
        return None, 0, None
    H, inliers = reg.register(frame)
    # сохраняем последний прочитанный кадр для pixel-diff на coarse-шаге
    register_frame.last_frame = frame
    register_frame.last_idx = idx
    if H is None or inliers < max(8, reg.min_inliers // 3):
        return None, inliers, frame
    fh, fw = frame.shape[:2]
    pan = map_point(H, (fw / 2, fh / 2))
    return pan, inliers, frame


def roi_gray_256(frame, roi):
    """ROI-кроп, grayscale, ресайз 256x256 — для быстрого pixel-diff."""
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = int(roi[0] * w), int(roi[1] * h), int(roi[2] * w), int(roi[3] * h)
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (256, 256))


def dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--coarse", type=int, default=300, help="грубый шаг (кадров)")
    ap.add_argument("--fine", type=int, default=10, help="шаг отката для уточнения")
    ap.add_argument("--threshold", type=float, default=90.0,
                    help="Δpan на канонической карте, выше которого считаем cut'ом (px)")
    ap.add_argument("--coarse-diff", type=float, default=20.0, dest="coarse_diff",
                    help="доп. триггер на coarse: pixel-diff в ROI между coarse-кадрами")
    ap.add_argument("--start", type=float, default=0.0, help="старт в секундах")
    ap.add_argument("--end", type=float, default=-1.0, help="конец в секундах (-1 = до конца)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    cfg = load_config(args.config)
    canonical_dir = (args.config.parent / "canonical_maps").resolve()
    if not canonical_dir.exists():
        canonical_dir = (Path(__file__).resolve().parents[2] / "shared" / "canonical_maps").resolve()
    cmap = load_canonical_map(cfg.get("canonical_map", "storm_point"), canonical_dir)
    reg = FrameRegistrar(cmap, cfg.get("registration", {}))

    cap = cv2.VideoCapture(str(args.video), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        print(f"[err] не открыл видео: {args.video}", file=sys.stderr); sys.exit(2)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    start_frame = int(args.start * fps)
    end_frame = total if args.end < 0 else min(total, int(args.end * fps))
    print(f"[info] видео: {total} кадров, {fps:.2f} fps. Сканируем [{start_frame}, {end_frame}) шагом {args.coarse}.")

    events: list[dict] = []
    hud_events: list[dict] = []   # большой pixel_diff, но Δpan ≈ 0 → killcam/zoom/HUD
    gray_zone: list[dict] = []    # средний pixel_diff, неоднозначно
    overlay_base = cv2.cvtColor(reg.map_small, cv2.COLOR_GRAY2BGR)

    prev_idx = start_frame
    prev_pan, prev_inl, _ = register_frame(cap, reg, prev_idx)
    prev_gray = roi_gray_256(register_frame.last_frame, reg.roi) \
        if getattr(register_frame, "last_frame", None) is not None else None
    if prev_pan is None:
        print(f"[warn] стартовый кадр {prev_idx} не регистрируется (inliers={prev_inl}). Иду дальше.")
    t0 = time.time()

    curr_idx = prev_idx + args.coarse
    while curr_idx < end_frame:
        curr_pan, curr_inl, _ = register_frame(cap, reg, curr_idx)
        curr_gray = roi_gray_256(register_frame.last_frame, reg.roi) \
            if getattr(register_frame, "last_frame", None) is not None else None

        # триггеры: SIFT Δpan ИЛИ pixel-diff ROI
        trig_pan = (prev_pan is not None and curr_pan is not None
                    and dist(prev_pan, curr_pan) > args.threshold)
        coarse_diff = (float(np.mean(cv2.absdiff(prev_gray, curr_gray)))
                       if prev_gray is not None and curr_gray is not None else 0.0)
        trig_diff = coarse_diff > args.coarse_diff

        d_pan_str = f"{dist(prev_pan, curr_pan):>6.1f}" if (prev_pan is not None and curr_pan is not None) else "  n/a"
        mark = "  CUT?" if (trig_pan or trig_diff) else "  ok"
        print(f"  [coarse] {prev_idx:>7} -> {curr_idx:>7}: Δpan={d_pan_str}px "
              f"diff={coarse_diff:>5.2f} (inl {prev_inl}->{curr_inl}){mark}")

        if trig_pan or trig_diff:
            # для refine нужны pan'ы; если один из них None — берём центр окна как приближение
            approx_cut = (prev_idx + curr_idx) // 2
            if trig_pan:
                # уточняем линейным откатом с шагом fine
                exact = refine_cut(cap, reg, prev_idx, prev_pan, curr_idx, curr_pan,
                                   args.fine, args.threshold)
                if exact is not None:
                    approx_cut = exact[0]
            # второй проход: шаг 1 в окне ±fine, ищем настоящий межкадровый скачок (по pixel-diff)
            pinned = pinpoint_cut(cap, reg, approx_cut, args.fine, args.threshold)
            if pinned is None:
                print(f"    -> отброшено: pixel-diff < 5 (шум регистрации)")
            else:
                cut_frame, from_pan, to_pan, pixel_diff, status = pinned
                delta = dist(from_pan, to_pan) if (from_pan != (0.0, 0.0) and to_pan != (0.0, 0.0)) else None
                pans_ok = (from_pan != (0.0, 0.0) and to_pan != (0.0, 0.0))
                ev = {
                    "frame": int(cut_frame),
                    "t": round(cut_frame / fps, 3),
                    "from_pan": [round(from_pan[0], 1), round(from_pan[1], 1)],
                    "to_pan": [round(to_pan[0], 1), round(to_pan[1], 1)],
                    "delta": round(delta, 1) if delta is not None else None,
                    "pixel_diff": round(pixel_diff, 2),
                }
                # классификация: настоящий cut = камера перепрыгнула
                #   - оба pan'а валидны: требуем Δpan ≥ 100 + pixel_diff ≥ 10
                #   - регистрация упала на одной стороне: доверяем только pixel_diff ≥ 15
                #   - маленький Δpan + большой diff → HUD/zoom/killcam (не cut)
                #   - средний diff (5..10) → серая зона
                if pans_ok:
                    if delta >= 100 and pixel_diff >= 10:
                        classify = "cut"
                    elif pixel_diff >= 10:
                        classify = "hud"      # картинка изменилась, камера на месте
                    else:
                        classify = "gray"
                else:
                    classify = "cut" if pixel_diff >= 15 else "gray"

                ev["classify"] = classify
                if classify == "cut":
                    events.append(ev)
                    print(f"    -> CUT at frame {cut_frame} (t={ev['t']}s, "
                          f"Δpan={ev['delta']}px, pixel_diff={ev['pixel_diff']})")
                    draw_cut_overlay(overlay_base.copy(), from_pan, to_pan, reg.scale,
                                     args.out / f"overlay_cut_{cut_frame}.png", ev)
                    dump_context_frames(cap, cut_frame, args.out)
                elif classify == "hud":
                    hud_events.append(ev)
                    print(f"    -> HUD at frame {cut_frame} (Δpan={ev['delta']}px, "
                          f"diff={ev['pixel_diff']}) — не cut, картинка поменялась без движения")
                else:
                    gray_zone.append(ev)
                    print(f"    -> GRAY at frame {cut_frame} (Δpan={ev['delta']}, "
                          f"diff={ev['pixel_diff']}) — под подозрением")
                    dump_context_frames(cap, cut_frame, args.out)

        prev_idx = curr_idx
        prev_pan = curr_pan
        prev_inl = curr_inl
        prev_gray = curr_gray
        curr_idx += args.coarse

    cap.release()

    # Сохраняем результат
    out_json = args.out / "cuts.json"
    out_json.write_text(json.dumps({
        "video": args.video.name,
        "fps": fps,
        "coarse": args.coarse,
        "fine": args.fine,
        "threshold": args.threshold,
        "events": events,
        "hud_events": hud_events,
        "gray_zone": gray_zone,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"[ok] cut'ов: {len(events)}, HUD-событий: {len(hud_events)}, "
             f"серая зона: {len(gray_zone)}  (за {time.time() - t0:.1f}s)"]
    for ev in events:
        d_str = f"{ev['delta']:>6.1f}" if ev['delta'] is not None else "   n/a"
        lines.append(f"  frame {ev['frame']:>7} t={ev['t']:>7.2f}s  Δ={d_str}px  "
                     f"diff={ev['pixel_diff']:>5.2f}  "
                     f"{ev['from_pan']} -> {ev['to_pan']}")
    if hud_events:
        lines.append("")
        lines.append(f"[hud] картинка менялась без движения камеры (zoom/killcam/overlay):")
        for ev in hud_events:
            d_str = f"{ev['delta']:>5.1f}" if ev['delta'] is not None else "  n/a"
            lines.append(f"  frame {ev['frame']:>7} t={ev['t']:>7.2f}s  Δ={d_str}px  "
                         f"diff={ev['pixel_diff']:>5.2f}")
    if gray_zone:
        lines.append("")
        lines.append(f"[gray] подозрительные, глянь видеокадры:")
        for ev in gray_zone:
            lines.append(f"  frame {ev['frame']:>7} t={ev['t']:>7.2f}s  diff={ev['pixel_diff']:>5.2f}")
    summary = "\n".join(lines)
    (args.out / "cuts.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    print(f"\n[ok] см. {args.out}/cuts.json")


def refine_cut(cap, reg, prev_idx: int, prev_pan, curr_idx: int, curr_pan,
               step: int, threshold: float):
    """
    Откатываемся от curr_idx к prev_idx с шагом step.
    Ищем последний кадр, который ещё «принадлежит» curr_pan-стороне.
    Cut = этот кадр (первый кадр НОВОЙ позиции после прошлого «старого»).

    Возвращает (cut_frame, from_pan, to_pan) или None.
    """
    # Идём с curr_idx - step, curr_idx - 2*step, ... пока не окажемся ближе к prev_pan чем к curr_pan
    last_new = curr_idx          # последний кадр, который точно «новой» стороны
    last_new_pan = curr_pan
    idx = curr_idx - step
    first_old = None
    first_old_pan = None
    while idx > prev_idx:
        pan, inl, _ = register_frame(cap, reg, idx)
        if pan is None:
            # регистрация провалена — пропускаем
            print(f"    [fine]   frame {idx}: skip (inliers={inl})")
            idx -= step
            continue
        d_to_new = dist(pan, curr_pan)
        d_to_old = dist(pan, prev_pan)
        side = "NEW" if d_to_new < d_to_old else "OLD"
        print(f"    [fine]   frame {idx}: pan={[round(pan[0],1), round(pan[1],1)]} "
              f"dNew={d_to_new:.1f} dOld={d_to_old:.1f} -> {side}")
        if side == "NEW":
            last_new = idx
            last_new_pan = pan
        else:
            first_old = idx
            first_old_pan = pan
            break
        idx -= step

    if first_old is None:
        # дошли до prev_idx и всё было «NEW» — значит cut произошёл между prev_idx и last_new
        first_old = prev_idx
        first_old_pan = prev_pan

    # cut = last_new (первый кадр новой позиции). Проверим, что Δ всё ещё > threshold
    if dist(first_old_pan, last_new_pan) < threshold:
        return None
    return last_new, first_old_pan, last_new_pan


def draw_cut_overlay(canvas, from_pan, to_pan, scale, out_path: Path, ev: dict):
    p0 = (int(from_pan[0] * scale), int(from_pan[1] * scale))
    p1 = (int(to_pan[0] * scale), int(to_pan[1] * scale))
    cv2.circle(canvas, p0, 8, (0, 200, 255), 2)   # старая позиция — оранжевая
    cv2.circle(canvas, p1, 8, (0, 255, 0), 2)     # новая — зелёная
    cv2.arrowedLine(canvas, p0, p1, (0, 255, 255), 2, tipLength=0.05)
    label = f"f={ev['frame']} t={ev['t']}s d={ev['delta']}px"
    cv2.putText(canvas, label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.imwrite(str(out_path), canvas)


def pinpoint_cut(cap, reg, approx_cut_frame: int, window: int, threshold: float):
    """
    Шаг 1 в окне [approx_cut_frame - window, approx_cut_frame + window].
    Камера физически не может телепортироваться, поэтому решение принимает
    ПОПИКСЕЛЬНАЯ разница между соседними кадрами в ROI карты, а не Δpan
    (Δpan может скакать из-за нестабильности SIFT на UI-кадрах).

    Возвращает (cut_frame, pan_from, pan_to, best_diff, status):
      status="accepted"  если best_diff >= PIXEL_DIFF_THR
      status="gray"      если GRAY_LO <= best_diff < PIXEL_DIFF_THR  (для ручной верификации)
      status="rejected"  если best_diff < GRAY_LO
    """
    PIXEL_DIFF_THR = 10.0   # mean abs diff (grayscale 0..255) — настоящий cut
    GRAY_LO = 5.0           # серая зона: подозрительные кандидаты для ручной проверки
    start = max(0, approx_cut_frame - window)
    end = approx_cut_frame + window

    # читаем подряд кадры [start..end], считаем ROI mean-abs-diff между соседями
    roi = reg.roi  # (x0, y0, x1, y1) нормализованный
    frames_gray: dict[int, np.ndarray] = {}
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    for idx in range(start, end + 1):
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = int(roi[0] * w), int(roi[1] * h), int(roi[2] * w), int(roi[3] * h)
        gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        # уменьшаем для скорости и шумоподавления
        gray = cv2.resize(gray, (256, 256))
        frames_gray[idx] = gray

    best_diff = 0.0
    best_i = None
    for idx in sorted(frames_gray.keys()):
        nxt = idx + 1
        if nxt not in frames_gray:
            continue
        diff = float(np.mean(cv2.absdiff(frames_gray[idx], frames_gray[nxt])))
        if diff > best_diff:
            best_diff = diff
            best_i = idx
    if best_i is None:
        return None
    print(f"    [pin]    max pixel-diff: f{best_i}->f{best_i+1}, diff={best_diff:.2f} "
          f"(thr={PIXEL_DIFF_THR})")
    if best_diff < GRAY_LO:
        return None
    status = "accepted" if best_diff >= PIXEL_DIFF_THR else "gray"

    # для overlay нужны pan'ы; регистрируем только эти два кадра
    cut_frame = best_i + 1
    pan_from, _, _ = register_frame(cap, reg, best_i)
    pan_to, _, _ = register_frame(cap, reg, cut_frame)
    if pan_from is None or pan_to is None:
        # регистрация фейлится, но cut точно был (diff большой) — пишем без pan'ов
        pan_from = pan_from or (0.0, 0.0)
        pan_to = pan_to or (0.0, 0.0)
    return cut_frame, pan_from, pan_to, best_diff, status


def dump_context_frames(cap, cut_frame: int, out_dir: Path):
    """Сохраняет 4 кадра видео: cut-1, cut, cut+1, cut+10."""
    for offset, tag in [(-1, "before"), (0, "at"), (1, "after"), (10, "after10")]:
        idx = max(0, cut_frame + offset)
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        # уменьшаем до ширины 960 чтобы не раздувать debug_out
        h, w = frame.shape[:2]
        if w > 960:
            scale = 960 / w
            frame = cv2.resize(frame, (960, int(h * scale)))
        out_path = out_dir / f"frame_cut_{cut_frame}_{tag}_f{idx}.jpg"
        cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])


if __name__ == "__main__":
    main()
