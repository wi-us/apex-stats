#!/usr/bin/env python3
"""
detect_plates.py — детектор плашек команд на миникарте Apex.

Это адаптация build_dataset_opencv.py под наш проект:
  * ROI берётся из scripts/tracking/configs/zones.vod.json (зона tag=minimap, "camera roi"),
    а не central_roi(left_ignore=420, roi_size=1080);
  * читается наш формат HSV-пресетов (scripts/tracking/configs/hsv_presets.<map>.json);
  * добавлен temporal-слой RecoveryTracker — на пропусках слота
    расширяются HSV-допуски и локальный поисковый ROI вокруг last_box
    ("стоять на месте и увеличивать радиус").

Пример (соответствует протестированной конфигурации):
  python scripts/tracking/modules/detect_plates/detect_plates.py \
      --video scripts/tracking/game_sp.mp4 \
      --hsv-presets scripts/tracking/configs/hsv_presets.storm-point.json \
      --zones scripts/tracking/configs/zones.vod.json \
      --out scripts/tracking/modules/detect_plates/reports \
      --sample-fps 1 --h-tol 1 --s-tol 6 --v-tol 14 \
      --loose-h-extra 1 --loose-s-extra 12 --loose-v-extra 20 \
      --ignore-bottom-px 105 --target-plate-height 30 \
      --max-expand-x 22 --max-width 220 --recovery
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from tqdm import tqdm

# Импортируем рабочий детектор как есть.
from build_dataset_opencv import (  # type: ignore
    Box,
    RejectedBox,
    load_team_hsv,
    detect_colored_plates_opencv,
    draw_boxes,
    ensure_dir,
)
from tracker_light import MultiSlotTracker
import aggregate_slots


# ---------------------------------------------------------------------------
# ROI из zones.vod.json
# ---------------------------------------------------------------------------
def _scale(z: dict, base_w: int, base_h: int, fw: int, fh: int) -> Tuple[int, int, int, int]:
    sx, sy = fw / base_w, fh / base_h
    x = int(round(z["x"] * sx)); y = int(round(z["y"] * sy))
    w = int(round(z["w"] * sx)); h = int(round(z["h"] * sy))
    x = max(0, min(fw - 1, x)); y = max(0, min(fh - 1, y))
    w = max(1, min(fw - x, w)); h = max(1, min(fh - y, h))
    return x, y, w, h


def pick_minimap_zone(zones_cfg: dict, fw: int, fh: int,
                      zone_name: Optional[str] = None) -> Tuple[int, int, int, int]:
    base_w, base_h = zones_cfg.get("base", [1920, 1080])
    cands = zones_cfg["zones"]
    if zone_name:
        match = [z for z in cands if z.get("name") == zone_name or z.get("id") == zone_name]
    else:
        # дефолт: tag=minimap, иначе самая большая зона.
        match = [z for z in cands if z.get("tag") == "minimap"]
        if not match:
            match = sorted(cands, key=lambda z: z["w"] * z["h"], reverse=True)[:1]
    if not match:
        raise RuntimeError("zones.vod.json: не нашёл подходящей зоны для миникарты")
    z = match[0]
    return _scale(z, base_w, base_h, fw, fh)


# ---------------------------------------------------------------------------
# Recovery: per-slot temporal layer ("стоять и расширять радиус")
# ---------------------------------------------------------------------------
class SlotState:
    __slots__ = ("last_box", "miss", "level")

    def __init__(self) -> None:
        self.last_box: Optional[Tuple[int, int, int, int]] = None
        self.miss: int = 0
        self.level: int = 0  # 0=strict baseline, растёт с каждым промахом


class RecoveryTracker:
    """
    Запускает базовый детектор; для слотов, которых в кадре нет,
    делает повторный проход с расширенными HSV и ограниченным локальным ROI
    вокруг последнего известного bbox. Радиус и расширение HSV растут с каждым
    подряд пропущенным кадром.
    """

    def __init__(self, team_hsv: dict, *,
                 max_level: int = 4,
                 h_step: int = 1, s_step: int = 8, v_step: int = 10,
                 radius_base_px: int = 60, radius_step_px: int = 40,
                 radius_cap_px: int = 320,
                 max_lost_frames: int = 30,
                 base_params: Optional[dict] = None) -> None:
        self.team_hsv = team_hsv
        self.max_level = max_level
        self.h_step = h_step
        self.s_step = s_step
        self.v_step = v_step
        self.radius_base = radius_base_px
        self.radius_step = radius_step_px
        self.radius_cap = radius_cap_px
        self.max_lost_frames = max_lost_frames
        self.base = base_params or {}
        self.state: Dict[str, SlotState] = defaultdict(SlotState)

    def _slot_key(self, box: Box) -> str:
        feat = box[5]
        return str(feat.get("team_key") or feat.get("slot") or feat.get("dominant_team_id"))

    def step(self, roi: np.ndarray,
             accepted: List[Box]) -> Tuple[List[Box], List[dict]]:
        # 1. зафиксировать факт нахождения для каждого слота
        seen: Dict[str, Box] = {}
        for b in accepted:
            k = self._slot_key(b)
            if k == "None":
                continue
            # лучшее по score
            if k not in seen or b[4] > seen[k][4]:
                seen[k] = b

        recoveries: List[dict] = []
        out: List[Box] = list(accepted)

        # 2. для каждой команды из пресета, не найденной в кадре, — recovery-проход
        for team_key, color in self.team_hsv.items():
            if team_key in seen:
                st = self.state[team_key]
                st.last_box = (seen[team_key][0], seen[team_key][1],
                               seen[team_key][2], seen[team_key][3])
                st.miss = 0
                st.level = 0
                continue

            st = self.state[team_key]
            st.miss += 1
            if st.last_box is None or st.miss > self.max_lost_frames:
                continue
            st.level = min(self.max_level, st.level + 1)

            # локальный ROI вокруг last_box, радиус растёт с уровнем
            radius = min(self.radius_cap,
                         self.radius_base + self.radius_step * st.level)
            lx, ly, lw, lh = st.last_box
            cx, cy = lx + lw // 2, ly + lh // 2
            x1 = max(0, cx - radius)
            y1 = max(0, cy - radius)
            x2 = min(roi.shape[1], cx + radius)
            y2 = min(roi.shape[0], cy + radius)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            sub_roi = roi[y1:y2, x1:x2]

            # расширяем h/s/v относительно baseline
            p = dict(self.base)
            p["h_tol"] = int(p.get("h_tol", 1)) + self.h_step * st.level
            p["s_tol"] = int(p.get("s_tol", 6)) + self.s_step * st.level
            p["v_tol"] = int(p.get("v_tol", 14)) + self.v_step * st.level
            # на recovery нам нужен только этот слот
            single = {team_key: color}

            try:
                rec_boxes, _rej, _mask = detect_colored_plates_opencv(
                    sub_roi, team_hsv=single, **p,
                )
            except TypeError:
                # safety: если base содержит лишний ключ — отбросим
                p2 = {k: v for k, v in p.items()
                      if k in detect_colored_plates_opencv.__code__.co_varnames}
                rec_boxes, _rej, _mask = detect_colored_plates_opencv(
                    sub_roi, team_hsv=single, **p2,
                )

            if not rec_boxes:
                continue
            best = max(rec_boxes, key=lambda b: b[4])
            gx = x1 + best[0]; gy = y1 + best[1]
            feat = dict(best[5])
            feat["recovered_level"] = st.level
            feat["recovered_miss"] = st.miss
            box: Box = (gx, gy, best[2], best[3], best[4] * 0.9, feat)
            out.append(box)

            st.last_box = (gx, gy, best[2], best[3])
            st.miss = 0  # нашли — сбрасываем
            st.level = max(0, st.level - 1)
            recoveries.append({
                "team_key": team_key,
                "level": feat["recovered_level"],
                "bbox": [gx, gy, best[2], best[3]],
            })

        return out, recoveries


# ---------------------------------------------------------------------------
# Video reader: seek vs sequential
# ---------------------------------------------------------------------------
def _read_at(cap: cv2.VideoCapture, frame_idx: int, *, seek: bool,
             _state: dict) -> Optional[np.ndarray]:
    """Возвращает BGR-кадр на позиции frame_idx.
    seek=True — CAP_PROP_POS_FRAMES (для H.264 на практике медленнее sequential:
      каждый set() заново декодирует от ближайшего keyframe).
    seek=False (default) — один проход вперёд: grab() для пропуска,
      retrieve() только на нужном индексе. Декодируется только то, что нужно.
    """
    if seek:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if ok and frame is not None:
            _state["pos"] = frame_idx + 1
            return frame
        # fallback: переключаемся на sequential до конца
        _state["seek"] = False
    # sequential: домотать grab() до нужного индекса
    cur = _state.get("pos", 0)
    if cur > frame_idx:
        # назад идти не умеем без seek; включим seek один раз
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        cur = frame_idx
    while cur < frame_idx:
        if not cap.grab():
            return None
        cur += 1
    ok, frame = cap.retrieve()
    _state["pos"] = frame_idx + 1
    if not ok:
        return None
    return frame


def _box_to_dict(b: Box, *, source: str) -> dict:
    feat = dict(b[5]) if b[5] else {}
    return {
        "bbox": [int(b[0]), int(b[1]), int(b[2]), int(b[3])],
        "score": float(b[4]),
        "feat": feat,
        "source": source,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--hsv-presets", required=True, type=Path)
    ap.add_argument("--zones", required=True, type=Path)
    ap.add_argument("--zone-name", default=None,
                    help="name/id зоны (по умолчанию: tag=minimap)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--sample-fps", type=float, default=1.0)
    ap.add_argument("--max-frames", type=int, default=0,
                    help="0 = весь файл")

    # baseline params (под уже протестированную конфигурацию пользователя)
    ap.add_argument("--h-tol", type=int, default=1)
    ap.add_argument("--s-tol", type=int, default=6)
    ap.add_argument("--v-tol", type=int, default=14)
    ap.add_argument("--loose-h-extra", type=int, default=1)
    ap.add_argument("--loose-s-extra", type=int, default=12)
    ap.add_argument("--loose-v-extra", type=int, default=20)
    ap.add_argument("--ignore-bottom-px", type=int, default=105)
    ap.add_argument("--target-plate-height", type=int, default=30)
    ap.add_argument("--max-expand-x", type=int, default=22)
    ap.add_argument("--max-width", type=int, default=220)
    ap.add_argument("--min-height", type=int, default=12)
    ap.add_argument("--max-height", type=int, default=42)
    ap.add_argument("--min-width", type=int, default=24)
    ap.add_argument("--min-color-fill", type=float, default=0.28)
    ap.add_argument("--min-white-ratio", type=float, default=0.012)
    ap.add_argument("--nms-iou", type=float, default=0.22)
    ap.add_argument("--search-pad-x", type=int, default=36)
    ap.add_argument("--search-pad-y", type=int, default=14)
    ap.add_argument("--plate-height-tolerance", type=int, default=9)
    ap.add_argument("--hsv-min-s", type=int, default=30)
    ap.add_argument("--hsv-min-v", type=int, default=30)

    # recovery
    ap.add_argument("--recovery", action="store_true",
                    help="включить temporal recovery (стоять + расширять)")
    ap.add_argument("--rec-max-level", type=int, default=4)
    ap.add_argument("--rec-h-step", type=int, default=1)
    ap.add_argument("--rec-s-step", type=int, default=8)
    ap.add_argument("--rec-v-step", type=int, default=10)
    ap.add_argument("--rec-radius-base", type=int, default=60)
    ap.add_argument("--rec-radius-step", type=int, default=40)
    ap.add_argument("--rec-radius-cap", type=int, default=320)
    ap.add_argument("--rec-max-lost-frames", type=int, default=30)

    # debug
    ap.add_argument("--save-debug", action="store_true", default=False,
                    help="писать debug-jpg на keyframe (тяжёлое, off по умолчанию)")
    ap.add_argument("--debug-every", type=int, default=1,
                    help="N: писать debug-jpg каждый N-й keyframe")

    # speed
    ap.add_argument("--seek", dest="seek", action="store_true", default=False,
                    help="CAP_PROP_POS_FRAMES (для H.264 обычно МЕДЛЕННЕЕ, default off)")
    ap.add_argument("--no-seek", dest="seek", action="store_false")
    ap.add_argument("--hwaccel", default="auto",
                    help="ffmpeg hwaccel: auto|cuda|d3d11va|dxva2|qsv|videotoolbox|none "
                         "(default auto). 'none' отключает.")

    # sub-frame tracking + adaptive up-sample
    ap.add_argument("--track-fps", type=float, default=0.0,
                    help=">0 — между keyframe протягивать слоты KCF/OF на этой fps")
    ap.add_argument("--adaptive-fps", type=float, default=0.0,
                    help=">0 — для recovered/missed слотов up-sample на этой fps")

    # slots aggregator
    ap.add_argument("--emit-slots", action="store_true",
                    help="по окончании посчитать slots/<team>.json + trajectories.json")
    args = ap.parse_args()

    team_hsv = load_team_hsv(str(args.hsv_presets)) or {}
    if not team_hsv:
        raise SystemExit(f"[err] пустой пресет: {args.hsv_presets}")
    zones_cfg = json.loads(args.zones.read_text(encoding="utf-8"))

    # D — hardware-accelerated H.264 decode через ffmpeg backend.
    if args.hwaccel and args.hwaccel.lower() != "none":
        # OpenCV пробрасывает эти опции в libavcodec.
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            f"hwaccel;{args.hwaccel}",
        )
    cap = cv2.VideoCapture(str(args.video), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        # fallback: без hwaccel и без явного backend
        os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
        cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"[err] cv2 не открыл {args.video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    rx, ry, rw, rh = pick_minimap_zone(zones_cfg, fw, fh, args.zone_name)
    step = max(1, int(round(src_fps / args.sample_fps)))
    track_step = max(1, int(round(src_fps / args.track_fps))) if args.track_fps > 0 else 0
    adaptive_step = max(1, int(round(src_fps / args.adaptive_fps))) if args.adaptive_fps > 0 else 0

    out_dir = args.out
    debug_dir = out_dir / "debug"
    ensure_dir(out_dir); ensure_dir(debug_dir)

    base_params = dict(
        min_width=args.min_width, max_width=args.max_width,
        min_height=args.min_height, max_height=args.max_height,
        min_color_fill=args.min_color_fill,
        min_white_ratio=args.min_white_ratio,
        nms_iou=args.nms_iou,
        h_tol=args.h_tol, s_tol=args.s_tol, v_tol=args.v_tol,
        hsv_min_s=args.hsv_min_s, hsv_min_v=args.hsv_min_v,
        loose_h_extra=args.loose_h_extra,
        loose_s_extra=args.loose_s_extra,
        loose_v_extra=args.loose_v_extra,
        search_pad_x=args.search_pad_x, search_pad_y=args.search_pad_y,
        max_expand_x=args.max_expand_x,
        target_plate_height=args.target_plate_height,
        plate_height_tolerance=args.plate_height_tolerance,
        ignore_bottom_px=args.ignore_bottom_px,
    )

    tracker: Optional[RecoveryTracker] = None
    if args.recovery:
        tracker = RecoveryTracker(
            team_hsv,
            max_level=args.rec_max_level,
            h_step=args.rec_h_step, s_step=args.rec_s_step, v_step=args.rec_v_step,
            radius_base_px=args.rec_radius_base,
            radius_step_px=args.rec_radius_step,
            radius_cap_px=args.rec_radius_cap,
            max_lost_frames=args.rec_max_lost_frames,
            base_params=base_params,
        )

    multi = MultiSlotTracker() if track_step > 0 else None

    log: list = []
    keyframes = list(range(0, total, step)) if total > 0 else []
    if not keyframes:
        # fallback на стриминговое чтение, если total неизвестен
        keyframes = [i * step for i in range(10**9)]
    if args.max_frames:
        keyframes = keyframes[: args.max_frames]

    read_state = {"seek": args.seek, "pos": 0}
    sampled = 0
    t0 = time.time()
    pbar = tqdm(total=len(keyframes), desc="detect_plates")
    prev_keyframe: Optional[int] = None
    prev_roi: Optional[np.ndarray] = None
    for kf_i, frame_idx in enumerate(keyframes):
        if frame_idx >= total and total > 0:
            break
        frame = _read_at(cap, frame_idx, seek=read_state["seek"], _state=read_state)
        if frame is None:
            break
        roi = frame[ry:ry + rh, rx:rx + rw]

        # sub-frame трекинг + adaptive up-sample в промежутке [prev_keyframe+1, frame_idx-1]
        tracked_records: List[dict] = []
        if prev_keyframe is not None and (track_step > 0 or adaptive_step > 0):
            inter_step = min(
                track_step if track_step > 0 else step,
                adaptive_step if adaptive_step > 0 else step,
            )
            for inter_idx in range(prev_keyframe + inter_step, frame_idx, inter_step):
                inter_frame = _read_at(cap, inter_idx, seek=read_state["seek"], _state=read_state)
                if inter_frame is None:
                    continue
                inter_roi = inter_frame[ry:ry + rh, rx:rx + rw]
                if multi is not None:
                    upd = multi.update_all(inter_roi)
                    for slot, bb in upd.items():
                        tracked_records.append({
                            "frame": inter_idx, "t": inter_idx / src_fps,
                            "slot": slot, "bbox": list(bb),
                        })

        accepted, rejected, _mask = detect_colored_plates_opencv(
            roi, team_hsv=team_hsv, **base_params,
        )
        recoveries: list = []
        if tracker is not None:
            accepted, recoveries = tracker.step(roi, accepted)

        # re-init трекеров от свежих детекций
        if multi is not None:
            seen_keys = set()
            for b in accepted:
                feat = b[5] or {}
                k = str(feat.get("team_key") or feat.get("slot") or feat.get("dominant_team_id"))
                if k == "None":
                    continue
                seen_keys.add(k)
                multi.init_slot(k, roi, (b[0], b[1], b[2], b[3]))
            for k in list(multi.slots.keys()):
                if k not in seen_keys:
                    multi.reset(k)

        if args.save_debug and (kf_i % max(1, args.debug_every) == 0):
            dbg = draw_boxes(roi, accepted, rejected=rejected, draw_rejected=False)
            cv2.imwrite(str(debug_dir / f"f{frame_idx:07d}.jpg"),
                        dbg, [cv2.IMWRITE_JPEG_QUALITY, 88])

        boxes_dump = [_box_to_dict(b, source=("recover" if (b[5] or {}).get("recovered_level") else "detect"))
                      for b in accepted]

        # отдельные tracked-точки в промежутке, плюс tracked на самом keyframe пропускаем
        # (на keyframe есть detect/recover)
        if tracked_records:
            # отсечь те, что относятся к этому keyframe (их нет — мы шли inter_step)
            tracked_for_this = [r for r in tracked_records if r["frame"] < frame_idx]
        else:
            tracked_for_this = []

        log.append({
            "frame": frame_idx,
            "t": frame_idx / src_fps,
            "accepted": len(accepted),
            "rejected": len(rejected),
            "boxes": boxes_dump,
            "recoveries": recoveries,
            "tracked": tracked_for_this,
            "by_slot": sorted({
                str((b[5] or {}).get("team_key") or (b[5] or {}).get("slot")) for b in accepted
            }),
        })

        prev_keyframe = frame_idx
        prev_roi = roi
        sampled += 1
        pbar.update(1)
    pbar.close(); cap.release()
    elapsed = time.time() - t0

    (out_dir / "detections.json").write_text(json.dumps({
        "video": str(args.video), "fps": src_fps,
        "roi": [rx, ry, rw, rh],
        "sampled_frames": sampled,
        "elapsed_sec": round(elapsed, 2),
        "seek_used": read_state["seek"],
        "params": base_params,
        "recovery": bool(tracker),
        "track_fps": args.track_fps,
        "adaptive_fps": args.adaptive_fps,
        "frames": log,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    n_rec = sum(len(f["recoveries"]) for f in log)
    n_tracked = sum(len(f["tracked"]) for f in log)
    print(f"[ok] frames={sampled}, recoveries={n_rec}, tracked={n_tracked}, "
          f"elapsed={elapsed:.1f}s")
    if args.save_debug:
        print(f"[ok] debug -> {debug_dir}")
    print(f"[ok] log   -> {out_dir/'detections.json'}")

    if args.emit_slots:
        det = json.loads((out_dir / "detections.json").read_text(encoding="utf-8"))
        by_slot = aggregate_slots.aggregate(det)
        aggregate_slots.write_outputs(out_dir, by_slot)
        print(f"[ok] slots -> {out_dir/'slots'} ({len(by_slot)} files), "
              f"trajectories -> {out_dir/'trajectories.json'}")


if __name__ == "__main__":
    main()