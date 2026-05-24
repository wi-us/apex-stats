#!/usr/bin/env python3
"""
motion_detect.py — отделяет ДВИЖУЩИЕСЯ плашки команд от статичных
HSV-ложных срабатываний (объекты карты, иконки HUD и т.п.).

Три независимых детектора прогоняются на одном окне кадров:
  M1 hsv_strict  — текущий HSV-проход (build_mask + жёсткие фильтры)
  M2 motion_diff — frame-difference в зоне minimap, blob'ы привязываются
                   к команде по медианному HSV-цвету под ними
  M3 hsv_loose   — расширенный HSV (±H, пониженные S/V, мягкие фильтры,
                   мини static-thresh)

Результаты складываются в общий moving[] с полем source. Дубликаты
не схлопываются. В отчёте строится таблица согласия методов:
  3/3 HIGH | 2/3 MED | 1/3 LOW | 0/3 MISS.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm


# ---------- утилиты ----------

def is_clean_frame(frame_idx: int, cuts: dict, guard: int = 30) -> bool:
    for ev in cuts.get("events", []) + cuts.get("hud_events", []) + cuts.get("gray_zone", []):
        if abs(frame_idx - ev["frame"]) < guard:
            return False
    return True


def _band(team_key, key2_key, default_lo=0, default_hi=255):
    return team_key, key2_key


def build_mask(hsv_img: np.ndarray, team: dict, h_pad: int = 0,
               sv_drop: int = 0) -> np.ndarray:
    """Если h_pad/sv_drop > 0 — это loose-режим."""
    def _range(h, s, v):
        h_lo = max(0, h[0] - h_pad)
        h_hi = min(179, h[1] + h_pad)
        s_lo = max(0, s[0] - sv_drop)
        v_lo = max(0, v[0] - sv_drop)
        return (np.array([h_lo, s_lo, v_lo], dtype=np.uint8),
                np.array([h_hi, s[1], v[1]], dtype=np.uint8))

    lo, hi = _range(team["h"], team["s"], team["v"])
    mask = cv2.inRange(hsv_img, lo, hi)
    if team.get("h2"):
        s2 = team.get("s2", team["s"]); v2 = team.get("v2", team["v"])
        lo2, hi2 = _range(team["h2"], s2, v2)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv_img, lo2, hi2))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return mask


def find_blobs(mask, min_area, max_area, min_solidity):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area or a > max_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if w < 3 or h < 3:
            continue
        if a / (w * h) < min_solidity:
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        cx = M["m10"] / M["m00"]; cy = M["m01"] / M["m00"]
        out.append({"cx": cx, "cy": cy, "w": w, "h": h, "area": float(a),
                    "contour": c})
    return out


def scale_zone(z, base_w, base_h, fw, fh):
    sx, sy = fw / base_w, fh / base_h
    x1 = max(0, min(fw, int(round(z["x"] * sx))))
    y1 = max(0, min(fh, int(round(z["y"] * sy))))
    x2 = max(0, min(fw, int(round((z["x"] + z["w"]) * sx))))
    y2 = max(0, min(fh, int(round((z["y"] + z["h"]) * sy))))
    return x1, y1, x2, y2


# ---------- цветовой матчинг для motion_diff ----------

def _team_h_center(team) -> list[int]:
    centers = [int((team["h"][0] + team["h"][1]) / 2)]
    if team.get("h2"):
        centers.append(int((team["h2"][0] + team["h2"][1]) / 2))
    return centers


def _hue_dist(a: int, b: int) -> int:
    d = abs(int(a) - int(b))
    return min(d, 180 - d)


def match_blob_to_team(hsv_patch: np.ndarray, mask_patch: np.ndarray,
                       teams: list[dict], color_tol: int) -> int | None:
    """Возвращает slot команды или None."""
    if mask_patch.sum() == 0:
        # fallback на все пиксели patch
        pixels = hsv_patch.reshape(-1, 3)
    else:
        pixels = hsv_patch[mask_patch > 0]
    if pixels.size == 0:
        return None
    # фильтруем тёмные/блёклые
    pix = pixels[(pixels[:, 1] >= 30) & (pixels[:, 2] >= 30)]
    if pix.shape[0] < 3:
        return None
    med_h = int(np.median(pix[:, 0]))
    best_slot, best_d = None, color_tol + 1
    for t in teams:
        for hc in _team_h_center(t):
            d = _hue_dist(med_h, hc)
            if d < best_d:
                best_d, best_slot = d, t["slot"]
    return best_slot


# ---------- трекинг blob'ов ----------

class Trajectory:
    __slots__ = ("points", "last_frame", "ws", "hs", "source")

    def __init__(self, frame_idx, blob, source: str):
        self.points = [(frame_idx, blob["cx"], blob["cy"])]
        self.ws = [blob["w"]]
        self.hs = [blob["h"]]
        self.last_frame = frame_idx
        self.source = source

    def add(self, frame_idx, blob):
        self.points.append((frame_idx, blob["cx"], blob["cy"]))
        self.ws.append(blob["w"])
        self.hs.append(blob["h"])
        self.last_frame = frame_idx

    @property
    def last_xy(self):
        return self.points[-1][1], self.points[-1][2]

    def displacement(self) -> float:
        if len(self.points) < 2:
            return 0.0
        xs = np.array([p[1] for p in self.points])
        ys = np.array([p[2] for p in self.points])
        mx, my = xs.mean(), ys.mean()
        d = np.hypot(xs - mx, ys - my)
        return float(2 * d.max())

    def path_length(self) -> float:
        if len(self.points) < 2:
            return 0.0
        s = 0.0
        for i in range(1, len(self.points)):
            s += math.hypot(self.points[i][1] - self.points[i - 1][1],
                            self.points[i][2] - self.points[i - 1][2])
        return s

    def median_wh(self):
        return float(np.median(self.ws)), float(np.median(self.hs))

    def median_xy(self):
        xs = np.array([p[1] for p in self.points])
        ys = np.array([p[2] for p in self.points])
        return float(np.median(xs)), float(np.median(ys))


def link_blobs(per_frame, link_dist, max_gap, source: str):
    open_tr: list[Trajectory] = []
    closed: list[Trajectory] = []
    for frame_idx, blobs in per_frame:
        used = set()
        for tr in open_tr:
            lx, ly = tr.last_xy
            best, best_d = None, link_dist
            for i, b in enumerate(blobs):
                if i in used:
                    continue
                d = math.hypot(b["cx"] - lx, b["cy"] - ly)
                if d < best_d:
                    best_d, best = d, i
            if best is not None:
                tr.add(frame_idx, blobs[best])
                used.add(best)
        still_open = []
        for tr in open_tr:
            if frame_idx - tr.last_frame > max_gap:
                closed.append(tr)
            else:
                still_open.append(tr)
        open_tr = still_open
        for i, b in enumerate(blobs):
            if i in used:
                continue
            open_tr.append(Trajectory(frame_idx, b, source))
    closed.extend(open_tr)
    return closed


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--cuts", required=True, type=Path)
    ap.add_argument("--hsv-presets", required=True, type=Path)
    ap.add_argument("--zones", required=True, type=Path)
    ap.add_argument("--zone-tag", default="minimap")
    ap.add_argument("--start-sec", type=float, default=0.0)
    ap.add_argument("--window", type=int, default=300)
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--min-area", type=int, default=15)
    ap.add_argument("--max-area", type=int, default=4000)
    ap.add_argument("--min-solidity", type=float, default=0.55)
    ap.add_argument("--link-dist", type=float, default=80.0)
    ap.add_argument("--max-gap", type=int, default=3)
    ap.add_argument("--static-thresh", type=float, default=3.0)
    ap.add_argument("--min-points", type=int, default=4)
    # M2 motion_diff
    ap.add_argument("--diff-thresh", type=int, default=12,
                    help="порог бинаризации absdiff между соседними выборками")
    ap.add_argument("--color-tol", type=int, default=12,
                    help="макс. дистанция по H для привязки blob'а к команде")
    # M3 hsv_loose
    ap.add_argument("--loose-h", type=int, default=5,
                    help="расширение H-диапазона ±N в loose-режиме")
    ap.add_argument("--loose-sv-drop", type=int, default=30,
                    help="понижение S/V min в loose-режиме")
    ap.add_argument("--loose-static-thresh", type=float, default=1.0)
    # consensus
    ap.add_argument("--agree-radius", type=float, default=0.0,
                    help="px для согласия методов; 0 = 1.5 * link_dist")
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args()

    if not args.video.exists():
        print(f"[err] нет видео {args.video}", file=sys.stderr); sys.exit(2)
    cuts = json.loads(args.cuts.read_text(encoding="utf-8"))
    hsv_cfg = json.loads(args.hsv_presets.read_text(encoding="utf-8"))
    zones_cfg = json.loads(args.zones.read_text(encoding="utf-8"))
    teams = hsv_cfg["teams"]
    base_w, base_h = zones_cfg.get("base", [1920, 1080])
    zones = [z for z in zones_cfg["zones"] if z["tag"] == args.zone_tag]
    if not zones:
        print(f"[err] в {args.zones} нет зон с тегом {args.zone_tag}", file=sys.stderr); sys.exit(2)

    agree_radius = args.agree_radius or (args.link_dist * 1.5)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "overlays").mkdir(exist_ok=True)

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        print("[err] cv2 не открыл видео", file=sys.stderr); sys.exit(2)
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scaled_zones = [(z, scale_zone(z, base_w, base_h, fw, fh)) for z in zones]

    start_frame = int(args.start_sec * fps)
    frames_to_grab = []
    f = start_frame
    while len(frames_to_grab) < args.window and f < total:
        if is_clean_frame(f, cuts):
            frames_to_grab.append(f)
        f += args.step
    if len(frames_to_grab) < args.min_points:
        print(f"[err] слишком мало чистых кадров: {len(frames_to_grab)}", file=sys.stderr); sys.exit(2)

    # (slot, zone_name, method) -> list[(frame_idx, [blob,...])]
    per_key: dict[tuple[int, str, str], list[tuple[int, list[dict]]]] = defaultdict(list)
    overlay_keep = sorted(set([frames_to_grab[0],
                               frames_to_grab[len(frames_to_grab) // 2],
                               frames_to_grab[-1]]))
    overlay_cache: dict[int, np.ndarray] = {}

    # prev gray per zone — для M2
    prev_gray_per_zone: dict[str, np.ndarray] = {}

    pbar = tqdm(total=len(frames_to_grab), unit="f", desc="motion-scan")
    for fi in frames_to_grab:
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.read()
        if not ok:
            pbar.update(1); continue
        if fi in overlay_keep:
            overlay_cache[fi] = frame.copy()
        for z, (x1, y1, x2, y2) in scaled_zones:
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            zname = z.get("name", z["tag"])
            roi = frame[y1:y2, x1:x2]
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

            # --- M1 hsv_strict ---
            for team in teams:
                mask = build_mask(hsv, team)
                blobs = find_blobs(mask, args.min_area, args.max_area,
                                   args.min_solidity)
                per_key[(team["slot"], zname, "hsv_strict")].append((fi, blobs))

            # --- M3 hsv_loose ---
            for team in teams:
                mask = build_mask(hsv, team, h_pad=args.loose_h,
                                  sv_drop=args.loose_sv_drop)
                blobs = find_blobs(mask, max(5, args.min_area // 3),
                                   args.max_area, 0.40)
                per_key[(team["slot"], zname, "hsv_loose")].append((fi, blobs))

            # --- M2 motion_diff ---
            prev = prev_gray_per_zone.get(zname)
            prev_gray_per_zone[zname] = gray
            if prev is not None and prev.shape == gray.shape:
                diff = cv2.absdiff(prev, gray)
                _, dmask = cv2.threshold(diff, args.diff_thresh, 255,
                                          cv2.THRESH_BINARY)
                k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
                dmask = cv2.morphologyEx(dmask, cv2.MORPH_OPEN, k)
                dmask = cv2.morphologyEx(dmask, cv2.MORPH_CLOSE, k)
                d_blobs = find_blobs(dmask, 5, args.max_area, 0.30)
                # привязка каждого blob'а к команде по цвету
                per_team_blobs: dict[int, list[dict]] = defaultdict(list)
                for b in d_blobs:
                    x, y, w, h = cv2.boundingRect(b["contour"])
                    pad = 1
                    xs0 = max(0, x - pad); ys0 = max(0, y - pad)
                    xs1 = min(hsv.shape[1], x + w + pad)
                    ys1 = min(hsv.shape[0], y + h + pad)
                    hsv_patch = hsv[ys0:ys1, xs0:xs1]
                    # маска только этого контура внутри patch
                    sub_mask = np.zeros(hsv_patch.shape[:2], dtype=np.uint8)
                    shifted = b["contour"] - np.array([[xs0, ys0]])
                    cv2.drawContours(sub_mask, [shifted], -1, 255, -1)
                    slot = match_blob_to_team(hsv_patch, sub_mask, teams,
                                               args.color_tol)
                    if slot is not None:
                        per_team_blobs[slot].append(b)
                for team in teams:
                    per_key[(team["slot"], zname, "motion_diff")].append(
                        (fi, per_team_blobs.get(team["slot"], [])))
        pbar.update(1)
    pbar.close()
    cap.release()

    # ---- линкуем blob'ы в траектории ----
    team_by_slot = {t["slot"]: t for t in teams}
    # results[(slot, zone)] = {"moving": [...], "static_rejected": int}
    grouped: dict[tuple[int, str], dict] = {}
    for (slot, zone_name, method), per_frame in per_key.items():
        per_frame.sort(key=lambda x: x[0])
        trs = link_blobs(per_frame, args.link_dist, args.max_gap, method)
        static_thresh = (args.loose_static_thresh
                         if method != "hsv_strict" else args.static_thresh)
        bucket = grouped.setdefault((slot, zone_name),
                                    {"moving": [], "static_rejected": 0,
                                     "by_method": defaultdict(list)})
        for tr in trs:
            if len(tr.points) < args.min_points:
                continue
            disp = tr.displacement()
            entry = {
                "source": method,
                "points": [(int(p[0]), round(p[1], 1), round(p[2], 1))
                           for p in tr.points],
                "n": len(tr.points),
                "displacement_px": round(disp, 1),
                "path_px": round(tr.path_length(), 1),
                "w_med": round(tr.median_wh()[0], 1),
                "h_med": round(tr.median_wh()[1], 1),
                "med_xy": [round(v, 1) for v in tr.median_xy()],
            }
            if disp < static_thresh:
                bucket["static_rejected"] += 1
                continue
            bucket["moving"].append(entry)
            bucket["by_method"][method].append(entry)

    # ---- собираем consensus ----
    results = []
    for (slot, zone_name), bucket in grouped.items():
        bucket["moving"].sort(key=lambda e: -e["path_px"])
        # лучшая траектория каждого метода (по path_px)
        bests = {}
        for m, lst in bucket["by_method"].items():
            if lst:
                bests[m] = max(lst, key=lambda e: e["path_px"])
        methods_present = [m for m in ("hsv_strict", "motion_diff", "hsv_loose")
                            if m in bests]
        # попарные расстояния
        agree_pairs = []
        for i, mi in enumerate(methods_present):
            for mj in methods_present[i + 1:]:
                xi, yi = bests[mi]["med_xy"]
                xj, yj = bests[mj]["med_xy"]
                d = math.hypot(xi - xj, yi - yj)
                if d <= agree_radius:
                    agree_pairs.append((mi, mj, round(d, 1)))
        # категория: считаем размер максимальной клики методов,
        # которые попарно согласились по координате.
        n = len(methods_present)
        agreed_set: set[str] = set()
        if len(agree_pairs) == 3:
            agreed_set = set(methods_present)  # все три попарно сошлись
        elif agree_pairs:
            # 1-2 пары: берём наибольшую связную компоненту
            from collections import defaultdict as _dd
            adj: dict[str, set[str]] = _dd(set)
            for a, b, _d in agree_pairs:
                adj[a].add(b); adj[b].add(a)
            seen: set[str] = set()
            best: set[str] = set()
            for node in adj:
                if node in seen: continue
                stack = [node]; comp: set[str] = set()
                while stack:
                    v = stack.pop()
                    if v in comp: continue
                    comp.add(v); seen.add(v)
                    stack.extend(adj[v] - comp)
                if len(comp) > len(best):
                    best = comp
            agreed_set = best
        agreed_n = len(agreed_set)
        if agreed_n >= 3:
            conf = "HIGH"
        elif agreed_n == 2:
            conf = "MED"
        elif n >= 1:
            conf = "LOW"
        else:
            conf = "MISS"
        agree = f"{agreed_n}/{n}" if n else "0/0"
        # консенсус-координата
        if agreed_set:
            xs = [bests[m]["med_xy"][0] for m in agreed_set]
            ys = [bests[m]["med_xy"][1] for m in agreed_set]
            consensus = [round(sum(xs) / len(xs), 1),
                         round(sum(ys) / len(ys), 1)]
        elif methods_present:
            # один метод — берём его координату
            m = methods_present[0]
            consensus = bests[m]["med_xy"]
        else:
            consensus = None

        results.append({
            "slot": slot,
            "team_name": team_by_slot[slot].get("name"),
            "hex": team_by_slot[slot].get("hex"),
            "zone": zone_name,
            "moving": bucket["moving"],
            "static_rejected": bucket["static_rejected"],
            "counts": {
                "hsv_strict": len(bucket["by_method"].get("hsv_strict", [])),
                "motion_diff": len(bucket["by_method"].get("motion_diff", [])),
                "hsv_loose": len(bucket["by_method"].get("hsv_loose", [])),
            },
            "confidence": conf,
            "agree": agree,
            "agree_pairs": agree_pairs,
            "consensus_xy": consensus,
        })

    # ---- overlay ----
    style_by_source = {
        "hsv_strict": ("solid", 2),
        "motion_diff": ("dashed", 2),
        "hsv_loose": ("dotted", 2),
    }

    def _draw_line(img, a, b, color, style):
        if style == "solid":
            cv2.line(img, a, b, color, 2)
        elif style == "dashed":
            n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 6))
            for i in range(n):
                if i % 2 == 0:
                    t1 = i / n; t2 = (i + 1) / n
                    p1 = (int(a[0] + (b[0] - a[0]) * t1),
                          int(a[1] + (b[1] - a[1]) * t1))
                    p2 = (int(a[0] + (b[0] - a[0]) * t2),
                          int(a[1] + (b[1] - a[1]) * t2))
                    cv2.line(img, p1, p2, color, 2)
        else:  # dotted
            n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 4))
            for i in range(n + 1):
                t = i / max(n, 1)
                p = (int(a[0] + (b[0] - a[0]) * t),
                     int(a[1] + (b[1] - a[1]) * t))
                cv2.circle(img, p, 1, color, -1)

    for fi, frame in overlay_cache.items():
        ov = frame.copy()
        for z, (x1, y1, x2, y2) in scaled_zones:
            cv2.rectangle(ov, (x1, y1), (x2, y2), (255, 200, 0), 1)
            for r in results:
                if r["zone"] != z.get("name", z["tag"]):
                    continue
                hexc = (r["hex"] or "#ffffff").lstrip("#")
                bgr = (int(hexc[4:6], 16), int(hexc[2:4], 16), int(hexc[0:2], 16))
                for tr in r["moving"]:
                    style, _ = style_by_source.get(tr["source"], ("solid", 2))
                    pts = [(int(x1 + p[1]), int(y1 + p[2])) for p in tr["points"]]
                    for a, b in zip(pts[:-1], pts[1:]):
                        _draw_line(ov, a, b, bgr, style)
                    if pts:
                        cv2.circle(ov, pts[-1], 4, bgr, 2)
                if r["consensus_xy"]:
                    cx, cy = r["consensus_xy"]
                    p = (int(x1 + cx), int(y1 + cy))
                    cv2.drawMarker(ov, p, bgr, cv2.MARKER_CROSS, 12, 2)
                    cv2.putText(ov, f"{r['slot']}:{r['confidence']}",
                                (p[0] + 6, p[1] - 6),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, bgr, 1)
        cv2.imwrite(str(args.out_dir / "overlays" / f"motion_{fi}.jpg"),
                    ov, [cv2.IMWRITE_JPEG_QUALITY, 85])

    # ---- сохраняем JSON ----
    (args.out_dir / "motion_tracks.json").write_text(json.dumps({
        "video": args.video.name, "fps": fps,
        "start_sec": args.start_sec, "window": args.window, "step": args.step,
        "static_thresh_px": args.static_thresh,
        "loose_static_thresh_px": args.loose_static_thresh,
        "link_dist_px": args.link_dist,
        "diff_thresh": args.diff_thresh,
        "color_tol": args.color_tol,
        "loose_h": args.loose_h, "loose_sv_drop": args.loose_sv_drop,
        "agree_radius_px": agree_radius,
        "zone_tag": args.zone_tag,
        "frames_used": frames_to_grab,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- start_anchors.json (derived view, consumed by track_teams) ----
    # Стартовые координаты команд + базовый радиус r0 по уверенности.
    # Используется track_teams для жёсткого захвата в первые N секунд матча
    # (см. slot_tracker.anchor_lock_sec / anchor_grow_sec в конфиге).
    r0_by_conf = {"HIGH": 25.0, "MED": 40.0, "LOW": 70.0}
    # Approx t последнего семпла окна (для справки; track_teams использует
    # свой t_now первого обработанного кадра как anchor_t0).
    frame_t_approx = float(args.start_sec) + float(args.window * args.step) / max(fps, 1e-6)
    start_anchors = {
        "video": args.video.name,
        "fps": fps,
        "frame_t_approx": round(frame_t_approx, 3),
        "zone_tag": args.zone_tag,
        "r0_by_conf": r0_by_conf,
        "anchors": {},
    }
    for r in results:
        if not r.get("consensus_xy"):
            continue
        slot_key = f"slot_{r['slot']}"
        conf = r.get("confidence", "MISS")
        start_anchors["anchors"][slot_key] = {
            "slot": r["slot"],
            "x": float(r["consensus_xy"][0]),
            "y": float(r["consensus_xy"][1]),
            "conf": conf,
            "r0_minimap_px": float(r0_by_conf.get(conf, 70.0)),
            "zone": r.get("zone"),
            "hex": r.get("hex"),
        }
    (args.out_dir / "start_anchors.json").write_text(
        json.dumps(start_anchors, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- отчёт ----
    conf_order = {"HIGH": 0, "MED": 1, "LOW": 2, "MISS": 3}
    lines = [
        f"motion_detect: {len(frames_to_grab)} frames, "
        f"window={args.window}, step={args.step}\n",
        f"methods: M1=hsv_strict (static<{args.static_thresh}px) "
        f"M2=motion_diff (diff>{args.diff_thresh}, hue<{args.color_tol}) "
        f"M3=hsv_loose (h±{args.loose_h}, sv-{args.loose_sv_drop}, "
        f"static<{args.loose_static_thresh}px)\n",
        f"agree_radius={agree_radius:.0f}px, link_dist={args.link_dist:.0f}px\n\n",
        f"{'slot':>4} {'hex':>9} {'M1':>3} {'M2':>3} {'M3':>3} "
        f"{'agree':>6} {'conf':>5}  {'cx,cy':>11}  name\n",
        "-" * 70 + "\n",
    ]
    for r in sorted(results, key=lambda x: (conf_order[x["confidence"]], x["slot"])):
        c = r["counts"]
        xy = (f"{r['consensus_xy'][0]:>5.0f},{r['consensus_xy'][1]:<5.0f}"
              if r["consensus_xy"] else "    -      ")
        lines.append(f"{r['slot']:>4} {(r['hex'] or '-'):>9} "
                     f"{c['hsv_strict']:>3} {c['motion_diff']:>3} "
                     f"{c['hsv_loose']:>3} "
                     f"{r['agree']:>6} {r['confidence']:>5}  {xy}  "
                     f"{r['team_name'] or ''}\n")
    # сводка
    n_high = sum(1 for r in results if r["confidence"] == "HIGH")
    n_med  = sum(1 for r in results if r["confidence"] == "MED")
    n_low  = sum(1 for r in results if r["confidence"] == "LOW")
    n_miss = sum(1 for r in results if r["confidence"] == "MISS")
    lines.append("\n")
    lines.append(f"summary: HIGH={n_high} MED={n_med} LOW={n_low} MISS={n_miss} "
                 f"(total {len(results)})\n")
    (args.out_dir / "report.txt").write_text("".join(lines), encoding="utf-8")

    print(f"[ok] motion tracks -> {args.out_dir / 'motion_tracks.json'}")
    print(f"[ok] overlays      -> {args.out_dir / 'overlays'}/motion_*.jpg")
    print(f"[ok] report        -> {args.out_dir / 'report.txt'}")
    print(f"[ok] HIGH={n_high} MED={n_med} LOW={n_low} MISS={n_miss}")


if __name__ == "__main__":
    main()
