#!/usr/bin/env python3
"""
detect_teams.py — поиск 20 команд по HSV-маскам внутри указанных зон.

Замысел (по совету оператора):
  1. НЕ ищем по всему кадру. Берём JSON зон (export из /admin/zones) — там
     прямоугольники {tag, x, y, w, h} в базовых координатах 1920x1080.
  2. Для каждой команды используем HSV-диапазон (export из /admin/hsv).
     Поддерживается дополнительный диапазон h2/s2/v2 для red-wrap.
  3. В кадре масштабируем зоны до реального размера, делаем cv2.inRange,
     морфологию, ищем контуры, фильтруем по площади / соотношению сторон.
  4. Сохраняем debug: исходный кадр, кроп зоны, маску, найденные bbox.

Параллельно копит per-team медианные размеры плашек -> team_profiles.json
(аналог bootstrap, но уже на проверенных HSV-цветах).

Запуск:
  python detect_teams.py \
      --video game.mp4 \
      --cuts cuts_out/cuts.json \
      --hsv-presets hsv_presets.worlds-edge.json \
      --zones zones.vod.json \
      --zone-tags team,minimap \
      --out-dir detect_out \
      --frames 40

JSON-форматы:
  zones.vod.json   { "base": [1920, 1080], "mode": "vod",
                     "zones": [{"id","name","tag","x","y","w","h"}, ...] }
  hsv_presets.json { "frame": "worlds-edge",
                     "teams": [{"slot":1,"name":...,"hex":"#078396",
                                "h":[lo,hi],"s":[lo,hi],"v":[lo,hi],
                                "h2"?:[lo,hi]}, ...] }
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm


def is_clean_frame(frame_idx: int, cuts: dict, guard: int = 60) -> bool:
    for ev in cuts.get("events", []) + cuts.get("hud_events", []) + cuts.get("gray_zone", []):
        if abs(frame_idx - ev["frame"]) < guard:
            return False
    return True


def build_mask(hsv_img: np.ndarray, team: dict) -> np.ndarray:
    lo = np.array([team["h"][0], team["s"][0], team["v"][0]], dtype=np.uint8)
    hi = np.array([team["h"][1], team["s"][1], team["v"][1]], dtype=np.uint8)
    mask = cv2.inRange(hsv_img, lo, hi)
    # red-wrap: второй диапазон
    if "h2" in team and team["h2"]:
        s2 = team.get("s2", team["s"]); v2 = team.get("v2", team["v"])
        lo2 = np.array([team["h2"][0], s2[0], v2[0]], dtype=np.uint8)
        hi2 = np.array([team["h2"][1], s2[1], v2[1]], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv_img, lo2, hi2))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return mask


def find_blobs(mask: np.ndarray, min_area: int, max_area: int, min_solidity: float):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area or a > max_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if w < 3 or h < 3:
            continue
        rect = w * h
        if rect == 0 or a / rect < min_solidity:
            continue
        out.append({"bbox": (x, y, w, h), "area": float(a)})
    return out


def scale_zone(z: dict, base_w: int, base_h: int, fw: int, fh: int):
    sx, sy = fw / base_w, fh / base_h
    x1 = int(round(z["x"] * sx)); y1 = int(round(z["y"] * sy))
    x2 = int(round((z["x"] + z["w"]) * sx)); y2 = int(round((z["y"] + z["h"]) * sy))
    x1 = max(0, min(fw, x1)); y1 = max(0, min(fh, y1))
    x2 = max(0, min(fw, x2)); y2 = max(0, min(fh, y2))
    return x1, y1, x2, y2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--cuts", required=True, type=Path)
    ap.add_argument("--hsv-presets", required=True, type=Path)
    ap.add_argument("--zones", required=True, type=Path)
    ap.add_argument("--zone-tags", default="team,minimap",
                    help="запятая-список тэгов зон, в которых ищем (team, minimap, camera, ...)")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--step", type=int, default=600)
    ap.add_argument("--min-area", type=int, default=40)
    ap.add_argument("--max-area", type=int, default=4000)
    ap.add_argument("--min-solidity", type=float, default=0.6)
    ap.add_argument("--debug-frames", type=int, default=8,
                    help="сколько кадров сохранить с overlay-визуализацией")
    args = ap.parse_args()

    if not args.video.exists():
        print(f"[err] нет видео {args.video}", file=sys.stderr); sys.exit(2)
    cuts = json.loads(args.cuts.read_text(encoding="utf-8"))
    hsv_cfg = json.loads(args.hsv_presets.read_text(encoding="utf-8"))
    zones_cfg = json.loads(args.zones.read_text(encoding="utf-8"))
    teams = hsv_cfg["teams"]
    base_w, base_h = zones_cfg.get("base", [1920, 1080])
    wanted_tags = {t.strip() for t in args.zone_tags.split(",") if t.strip()}
    zones = [z for z in zones_cfg["zones"] if z["tag"] in wanted_tags]
    if not zones:
        print(f"[err] в {args.zones} нет зон с тегами {wanted_tags}", file=sys.stderr); sys.exit(2)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "slots").mkdir(exist_ok=True)
    (args.out_dir / "frames").mkdir(exist_ok=True)
    (args.out_dir / "masks").mkdir(exist_ok=True)

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        print("[err] cv2 не открыл видео", file=sys.stderr); sys.exit(2)
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    scaled_zones = [(z, scale_zone(z, base_w, base_h, fw, fh)) for z in zones]

    slot_samples: dict[int, list[dict]] = defaultdict(list)
    detections: list[dict] = []

    collected = 0
    debug_saved = 0
    frame_idx = 0
    pbar = tqdm(total=args.frames, unit="f", desc="detect")
    while collected < args.frames and frame_idx < total:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            break
        if not is_clean_frame(frame_idx, cuts):
            frame_idx += args.step
            continue

        overlay = frame.copy() if debug_saved < args.debug_frames else None
        any_hit = False

        for z, (x1, y1, x2, y2) in scaled_zones:
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue
            roi = frame[y1:y2, x1:x2]
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

            if overlay is not None:
                cv2.rectangle(overlay, (x1, y1), (x2, y2), (255, 200, 0), 1)
                cv2.putText(overlay, f"{z['tag']}:{z.get('name','')}",
                            (x1 + 2, max(12, y1 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 200, 0), 1)

            for team in teams:
                mask = build_mask(hsv, team)
                blobs = find_blobs(mask, args.min_area, args.max_area, args.min_solidity)
                for b in blobs:
                    bx, by, bw, bh = b["bbox"]
                    crop = roi[by:by + bh, bx:bx + bw].copy()
                    rec = {
                        "frame": frame_idx, "t": frame_idx / fps,
                        "slot": team["slot"], "team_name": team.get("name", ""),
                        "zone": z.get("name", z["tag"]),
                        "bbox_global": (int(x1 + bx), int(y1 + by), int(bw), int(bh)),
                        "w": int(bw), "h": int(bh), "area": b["area"],
                    }
                    detections.append(rec)
                    slot_samples[team["slot"]].append(rec)
                    any_hit = True

                    slot_dir = args.out_dir / "slots" / f"{team['slot']:02d}"
                    slot_dir.mkdir(exist_ok=True)
                    if len(list(slot_dir.glob("*.png"))) < 16:
                        cv2.imwrite(str(slot_dir /
                                        f"f{frame_idx}_{z['tag']}_{bx}_{by}.png"), crop)

                    if overlay is not None:
                        gx, gy = int(x1 + bx), int(y1 + by)
                        hex_color = team.get("hex", "#ffffff").lstrip("#")
                        r = int(hex_color[0:2], 16); g = int(hex_color[2:4], 16); bcol = int(hex_color[4:6], 16)
                        cv2.rectangle(overlay, (gx, gy), (gx + bw, gy + bh), (bcol, g, r), 2)
                        cv2.putText(overlay, str(team["slot"]),
                                    (gx, max(10, gy - 2)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (bcol, g, r), 1)

        if overlay is not None and any_hit:
            cv2.imwrite(str(args.out_dir / "frames" / f"overlay_{frame_idx}.jpg"),
                        overlay, [cv2.IMWRITE_JPEG_QUALITY, 80])
            debug_saved += 1

        collected += 1
        pbar.update(1)
        frame_idx += args.step
    pbar.close()
    cap.release()

    # агрегируем профили
    profiles = []
    for team in teams:
        slot = team["slot"]
        samples = slot_samples.get(slot, [])
        if not samples:
            profiles.append({"slot": slot, "hex": team.get("hex"),
                             "name": team.get("name"), "found": False, "samples": 0})
            continue
        ws = np.array([s["w"] for s in samples])
        hs = np.array([s["h"] for s in samples])
        areas = np.array([s["area"] for s in samples])
        profiles.append({
            "slot": slot, "hex": team.get("hex"), "name": team.get("name"),
            "found": True, "samples": int(len(samples)),
            "w_median": int(np.median(ws)), "h_median": int(np.median(hs)),
            "w_std": float(np.std(ws)), "h_std": float(np.std(hs)),
            "area_median": float(np.median(areas)),
        })

    (args.out_dir / "team_profiles.json").write_text(json.dumps({
        "video": args.video.name, "fps": fps,
        "frames_scanned": collected,
        "zones_used": [z["name"] for z in zones],
        "profiles": profiles,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    (args.out_dir / "detections.json").write_text(json.dumps({
        "video": args.video.name, "fps": fps,
        "count": len(detections),
        "detections": detections,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # отчёт
    found = sum(1 for p in profiles if p["found"])
    lines = [f"detect_teams: {collected} clean frames, {len(detections)} detections\n"]
    lines.append(f"slots found: {found}/{len(teams)}\n\n")
    lines.append(f"{'slot':>4} {'hex':>9} {'n':>5} {'w':>4} {'h':>4} {'area':>7}  name\n")
    for p in profiles:
        if not p["found"]:
            lines.append(f"{p['slot']:>4} {p['hex'] or '-':>9}     -    -    -       -   {p.get('name','')}  NOT FOUND\n")
        else:
            lines.append(f"{p['slot']:>4} {p['hex']:>9} {p['samples']:>5} "
                         f"{p['w_median']:>4} {p['h_median']:>4} "
                         f"{p['area_median']:>7.0f}   {p.get('name','')}\n")
    (args.out_dir / "report.txt").write_text("".join(lines), encoding="utf-8")

    print(f"[ok] profiles    -> {args.out_dir / 'team_profiles.json'}")
    print(f"[ok] detections  -> {args.out_dir / 'detections.json'}")
    print(f"[ok] crops       -> {args.out_dir / 'slots'}/<slot>/*.png")
    print(f"[ok] overlays    -> {args.out_dir / 'frames'}/overlay_*.jpg")
    print(f"[ok] report      -> {args.out_dir / 'report.txt'}")
    print(f"     {found}/{len(teams)} slots found, {len(detections)} blobs total")


if __name__ == "__main__":
    main()