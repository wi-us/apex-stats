#!/usr/bin/env python3
"""
register_camera_pan.py — оценить и убрать pan камеры миникарты
непосредственно из tracks.json (без видео и без template).

Идея:
  1. frame_px (пиксели исходного кадра 1920x1080) → canonical_px
     через ROI миникарты (по умолчанию Olympus 449,23,960,960 → 2048x2048).
  2. Якорь slot'а  = median(canonical_px) по всем кадрам, где slot виден.
  3. На каждом кадре dx,dy = median(anchor - current) по общим slot'ам.
     Это сдвиг камеры. Игнорируем зум/поворот (1-я итерация).
  4. canonical_px_corrected = current + (dx,dy); пишем в треки и
     заодно выставляем camera.pan_canonical / registration='ok'.

Запуск:
  python register_camera_pan.py <in.json> <out.json>
      [--roi x,y,w,h]  (default 449,23,960,960)
      [--canon W,H]    (default 2048,2048)
"""
import argparse, json, statistics, sys
from collections import defaultdict

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out")
    ap.add_argument("--roi",   default="449,23,960,960")
    ap.add_argument("--canon", default="2048,2048")
    a = ap.parse_args()
    rx,ry,rw,rh = map(float, a.roi.split(","))
    cw,ch       = map(float, a.canon.split(","))

    with open(a.inp, "r", encoding="utf-8") as f:
        d = json.load(f)

    # ── 1. frame_px → raw canonical_px ─────────────────────────────
    raw = []   # list[(frame_idx, slot_id, cx, cy)]
    for i, fr in enumerate(d["frames"]):
        for t in fr["tracks"]:
            px = t.get("frame_px")
            if not px: continue
            u = (px[0] - rx) / rw
            v = (px[1] - ry) / rh
            raw.append((i, t["slot_id"], u*cw, v*ch))

    # ── 2. anchor per slot = median по всему матчу ─────────────────
    bys = defaultdict(list)
    for i,s,x,y in raw: bys[s].append((x,y))
    anchor = {s: (statistics.median(p[0] for p in v),
                  statistics.median(p[1] for p in v))
              for s,v in bys.items() if len(v) >= 3}

    # ── 3. per-frame median shift ──────────────────────────────────
    shifts = {}
    byf = defaultdict(list)
    for i,s,x,y in raw: byf[i].append((s,x,y))
    for i, pts in byf.items():
        dxs, dys = [], []
        for s,x,y in pts:
            a_ = anchor.get(s)
            if not a_: continue
            dxs.append(a_[0]-x); dys.append(a_[1]-y)
        if len(dxs) >= 3:
            shifts[i] = (statistics.median(dxs), statistics.median(dys))
        else:
            shifts[i] = (0.0, 0.0)

    # ── 4. write canonical_px + camera.pan_canonical ───────────────
    for i, fr in enumerate(d["frames"]):
        dx,dy = shifts.get(i,(0.0,0.0))
        cam = fr.setdefault("camera", {})
        cam["registration"]   = "ok"
        cam["pan_canonical"]  = [round(dx,2), round(dy,2)]
        cam["zoom"]           = 1.0
        cam["rotation_deg"]   = 0.0
        for t in fr["tracks"]:
            px = t.get("frame_px")
            if not px: continue
            u = (px[0] - rx) / rw
            v = (px[1] - ry) / rh
            cx = u*cw + dx
            cy = v*ch + dy
            t["canonical_px"] = [round(cx,2), round(cy,2)]

    d["meta"].setdefault("canonical_size", [int(cw), int(ch)])
    d["meta"]["postprocess"] = {
        "registration": "median_pan_from_anchors",
        "roi_xywh": [rx,ry,rw,rh],
        "canon_size": [cw,ch],
        "frames_corrected": len(shifts),
        "slots_with_anchor": len(anchor),
    }

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"OK: {a.inp} → {a.out}  (frames={len(d['frames'])}, slots={len(anchor)})")

if __name__ == "__main__":
    sys.exit(main() or 0)