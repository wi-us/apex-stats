#!/usr/bin/env python3
"""
extract_crops.py — формирует датасет кропов плашек из detections.json + видео.

Логика:
  - читает detect_plates/reports/detections.json
  - для каждого slot_id берёт топ-N кадров по score (без recovered_level)
  - вырезает bbox + паддинг (как в ocr_tags), сохраняет в
    dataset/raw/{match_id}/{slot_id}/f{frame}_s{score}.png

После запуска человек вручную раскладывает кропы из raw/ в labeled/{TAG}/
(или в _review/ при сомнениях). Лишние/мусорные — удаляет.

Пример:
  python extract_crops.py \
      --detections ../detect_plates/reports/detections.json \
      --video ../../game_sp.mp4 \
      --zones ../../configs/zones.vod.json \
      --match-id m-test-g1 \
      --out dataset/raw \
      --top-n 80 \
      --pad-frac 0.4 --pad-frac-v 0.25
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import cv2


def _scale(z: dict, base_w: int, base_h: int, fw: int, fh: int):
    sx, sy = fw / base_w, fh / base_h
    x = int(round(z["x"] * sx)); y = int(round(z["y"] * sy))
    w = int(round(z["w"] * sx)); h = int(round(z["h"] * sy))
    x = max(0, min(fw - 1, x)); y = max(0, min(fh - 1, y))
    w = max(1, min(fw - x, w)); h = max(1, min(fh - y, h))
    return x, y, w, h


def pick_minimap_zone(zones_cfg: dict, fw: int, fh: int):
    base_w, base_h = zones_cfg.get("base", [1920, 1080])
    cands = zones_cfg["zones"]
    match = [z for z in cands if z.get("tag") == "minimap"]
    if not match:
        match = sorted(cands, key=lambda z: z["w"] * z["h"], reverse=True)[:1]
    if not match:
        raise RuntimeError("zones.vod.json: minimap zone not found")
    return _scale(match[0], base_w, base_h, fw, fh)


def pick_samples(detections: dict, top_n: int) -> Dict[str, List[dict]]:
    by_slot: Dict[str, List[dict]] = defaultdict(list)
    for f in detections.get("frames", []):
        for b in f.get("boxes", []):
            feat = b.get("feat") or {}
            if feat.get("recovered_level"):
                continue
            if b.get("source") != "detect":
                continue
            slot = str(feat.get("team_key") or feat.get("slot")
                       or feat.get("dominant_team_id") or "")
            if not slot or slot == "None":
                continue
            by_slot[slot].append({
                "frame": f["frame"],
                "bbox": b["bbox"],
                "score": float(b.get("score", 0.0)),
            })
    out: Dict[str, List[dict]] = {}
    for slot, items in by_slot.items():
        items.sort(key=lambda x: x["score"], reverse=True)
        out[slot] = items[:top_n]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detections", required=True, type=Path)
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--zones", required=True, type=Path)
    ap.add_argument("--match-id", required=True,
                    help="ID матча — будет подкаталогом в out/")
    ap.add_argument("--out", required=True, type=Path,
                    help="Корневой каталог для raw-кропов")
    ap.add_argument("--top-n", type=int, default=80)
    ap.add_argument("--pad-frac", type=float, default=0.4)
    ap.add_argument("--pad-frac-v", type=float, default=0.25)
    args = ap.parse_args()

    detections = json.loads(args.detections.read_text(encoding="utf-8"))
    zones_cfg = json.loads(args.zones.read_text(encoding="utf-8"))

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"[err] не открыт {args.video}")
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    rx, ry, rw, rh = pick_minimap_zone(zones_cfg, fw, fh)

    samples = pick_samples(detections, args.top_n)
    print(f"[info] slots: {len(samples)}, top_n={args.top_n}")

    needed: Dict[int, List[Tuple[str, List[int], float]]] = defaultdict(list)
    for slot, items in samples.items():
        for it in items:
            needed[it["frame"]].append((slot, it["bbox"], it["score"]))

    root = args.out / args.match_id
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    for i, frame_idx in enumerate(sorted(needed.keys())):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        roi = frame[ry:ry + rh, rx:rx + rw]
        for slot, bbox, score in needed[frame_idx]:
            x, y, w, h = bbox
            pad_x_l = max(2, int(w * 0.05))
            pad_x_r = max(4, int(w * args.pad_frac))
            pad_y   = max(2, int(h * args.pad_frac_v))
            x1 = max(0, x - pad_x_l); y1 = max(0, y - pad_y)
            x2 = min(roi.shape[1], x + w + pad_x_r)
            y2 = min(roi.shape[0], y + h + pad_y)
            crop = roi[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            safe_slot = slot.replace("/", "_").replace("\\", "_")
            slot_dir = root / safe_slot
            slot_dir.mkdir(parents=True, exist_ok=True)
            fname = f"f{frame_idx:07d}_s{int(score*1000):05d}.png"
            cv2.imwrite(str(slot_dir / fname), crop)
            total += 1
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(needed)} frames, {total} crops")
    cap.release()
    print(f"[ok] wrote {total} crops -> {root}")


if __name__ == "__main__":
    main()