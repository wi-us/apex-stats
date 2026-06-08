#!/usr/bin/env python3
"""
debug_masks.py — точечный визуальный дебаг масок для track_teams.

Читает tracks.json (тот, что произвёл track_teams.py), находит «проблемные»
моменты у указанных слотов и для каждого сохраняет PNG-панель:

   [ROI кадра] | [HSV+LAB mask] | [overlay + контуры] | подпись

Логика загрузки HSV-пресета и построения маски ровно такая же, как в
track_teams.py (SlotTracker._color_mask), поэтому ты видишь именно то,
что видит трекер, а не /admin/hsv с его независимым конвейером.

Usage (из корня репо):
    python scripts/tracking/modules/track_teams/debug_masks.py \
        --video scripts/tracking/game.mp4 \
        --tracks scripts/tracking/modules/track_teams/reports/tracks.json \
        --config scripts/tracking/modules/track_teams/config.example.yaml \
        --anchors scripts/tracking/modules/motion_detect/reports/motion_tracks.json \
        --slots 2,4,7,10,11,16,17 \
        --per-slot 6

По умолчанию (без --slots) слоты выбираются автоматически: те, у кого
доля кадров со state != 'tracked' до wipe выше 30%.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parent))
from track_teams import (  # noqa: E402
    TeamCfg, load_config, teams_from_anchors, build_lab_range_from_hsv,
)


BAD_REASONS = {"shape_reject", "mask_too_sparse", "roi_empty", "out_of_frame",
               "low_conf", "no_anchor", "H_singular"}
BAD_STATES = {"lost", "low_conf"}


def _load_hsv_preset(config_path: Path, canonical_map: str) -> Optional[dict]:
    """Replicate the preset search in track_teams.main()."""
    basename = f"hsv_presets.{canonical_map.replace('_', '-')}.json"
    candidates = [
        config_path.parent / "configs" / basename,
        THIS.parents[2] / "configs" / basename,
        THIS.parents[1] / "motion_detect" / "configs" / basename,
    ]
    for cand in candidates:
        if cand.exists():
            raw = json.loads(cand.read_text(encoding="utf-8"))
            print(f"[debug_masks] hsv preset: {cand}")
            return {int(t["slot"]): {"h": t["h"], "s": t["s"], "v": t["v"]}
                    for t in raw.get("teams", []) if t.get("slot") is not None}
    print(f"[debug_masks] WARN: hsv preset not found for {canonical_map}")
    return None


def build_teams(config_path: Path, anchors_path: Optional[Path]) -> list[TeamCfg]:
    cfg = load_config(config_path)
    canonical = cfg.get("canonical_map", "storm_point")
    preset = _load_hsv_preset(config_path, canonical)
    if anchors_path and anchors_path.exists():
        teams = teams_from_anchors(anchors_path, hsv_preset=preset)
    else:
        raise SystemExit(f"[err] anchors not found: {anchors_path}")
    # build LAB range now
    for t in teams:
        if t.lab_lower is None:
            t.lab_lower, t.lab_upper = build_lab_range_from_hsv(t.hsv_lower, t.hsv_upper)
    return teams


def color_mask(roi_bgr: np.ndarray, team: TeamCfg, morph: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    m_hsv = cv2.inRange(hsv, team.hsv_lower, team.hsv_upper)
    if team.hsv_lower2 is not None and team.hsv_upper2 is not None:
        m_hsv |= cv2.inRange(hsv, team.hsv_lower2, team.hsv_upper2)
    lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)
    m_lab = cv2.inRange(lab, team.lab_lower, team.lab_upper)
    mask = cv2.bitwise_and(m_hsv, m_lab)
    mode = "hsv+lab"
    if cv2.countNonZero(mask) < 8:
        mask = m_hsv
        mode = "hsv_only_fallback"
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph, morph))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return m_hsv, m_lab, mask, mode


def find_contours(mask: np.ndarray, min_a: float, max_a: float
                  ) -> tuple[list[tuple], list[tuple]]:
    """Return (passed, rejected) lists of (x,y,w,h, area, reason)."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    passed, rejected = [], []
    for c in cnts:
        area = cv2.contourArea(c)
        x, y, w, h = cv2.boundingRect(c)
        reason = None
        if area < min_a:
            reason = f"a<{min_a:.0f}"
        elif area > max_a:
            reason = f"a>{max_a:.0f}"
        elif w < 3 or h < 3:
            reason = "tiny"
        else:
            aspect = w / max(1.0, h)
            fill = area / max(1.0, float(w * h))
            if not (0.4 <= aspect <= 12.0):
                reason = f"asp={aspect:.2f}"
            elif fill < 0.18:
                reason = f"fill={fill:.2f}"
        rec = (x, y, w, h, float(area), reason)
        (rejected if reason else passed).append(rec)
    return passed, rejected


def render_panel(roi_bgr: np.ndarray, m_hsv, m_lab, mask, passed, rejected,
                 team: TeamCfg, t_sec: float, frame_idx: int, state: str,
                 reason: str, mode: str, target_local: tuple[float, float]) -> np.ndarray:
    h, w = roi_bgr.shape[:2]
    overlay = roi_bgr.copy()
    # tint mask in team color
    bgr_hex = team.color_hex.lstrip("#")
    tint = (int(bgr_hex[4:6], 16), int(bgr_hex[2:4], 16), int(bgr_hex[0:2], 16))
    overlay[mask > 0] = (0.4 * np.array(overlay[mask > 0]) + 0.6 * np.array(tint)).astype(np.uint8)
    for x, y, ww, hh, area, _r in passed:
        cv2.rectangle(overlay, (x, y), (x + ww, y + hh), (0, 255, 0), 2)
        cv2.putText(overlay, f"{int(area)}", (x, max(10, y - 3)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
    for x, y, ww, hh, area, r in rejected:
        cv2.rectangle(overlay, (x, y), (x + ww, y + hh), (0, 0, 255), 1)
        cv2.putText(overlay, f"{int(area)} {r}", (x, max(10, y - 3)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    # target_local crosshair
    tx, ty = int(target_local[0]), int(target_local[1])
    cv2.drawMarker(overlay, (tx, ty), (255, 255, 0), cv2.MARKER_CROSS, 16, 2)

    def to3(g):
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)

    # row: roi | m_hsv | m_lab | combined | overlay
    parts = [roi_bgr, to3(m_hsv), to3(m_lab), to3(mask), overlay]
    labels = ["ROI", "HSV mask", "LAB mask", f"AND ({mode})", "overlay"]
    pad = 28
    out_h = h + pad
    out_w = (w + 4) * len(parts)
    canvas = np.full((out_h, out_w, 3), 30, dtype=np.uint8)
    for i, (p, lab) in enumerate(zip(parts, labels)):
        x0 = i * (w + 4)
        canvas[pad:pad + h, x0:x0 + w] = p
        cv2.putText(canvas, lab, (x0 + 4, 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (220, 220, 220), 1)
    # caption bar
    cap_h = 56
    cap = np.full((cap_h, out_w, 3), 18, dtype=np.uint8)
    n_hsv = int(cv2.countNonZero(m_hsv)); n_lab = int(cv2.countNonZero(m_lab)); n = int(cv2.countNonZero(mask))
    line1 = f"slot_{team.slot}  t={t_sec:.1f}s  frame={frame_idx}  state={state}  reason={reason}"
    line2 = f"px_hsv={n_hsv}  px_lab={n_lab}  px_and={n}  passed={len(passed)}  rejected={len(rejected)}  hex={team.color_hex}"
    cv2.putText(cap, line1, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    cv2.putText(cap, line2, (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
    return np.vstack([cap, canvas])


def render_fullframe(frame_bgr: np.ndarray, team: TeamCfg, morph: int,
                     min_a: float, max_a: float,
                     roi_box: tuple[int, int, int, int],
                     pred_xy: tuple[float, float],
                     t_sec: float, frame_idx: int, state: str, reason: str,
                     scale: float = 0.5) -> np.ndarray:
    """Полный кадр (даунскейл) | full-frame HSV-маска | overlay с ROI-боксом
    и всеми блобами цвета команды (зелёные / красные по shape-фильтру)."""
    fh, fw = frame_bgr.shape[:2]
    # full-frame HSV+LAB mask (без LAB — слишком широко, оставим только HSV для наглядности)
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, team.hsv_lower, team.hsv_upper)
    if team.hsv_lower2 is not None and team.hsv_upper2 is not None:
        m |= cv2.inRange(hsv, team.hsv_lower2, team.hsv_upper2)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph, morph))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    kclose = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                       (max(7, morph + 4),) * 2)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kclose)
    passed_full, rejected_full = find_contours(m, min_a, max_a)

    overlay = frame_bgr.copy()
    bgr_hex = team.color_hex.lstrip("#")
    tint = (int(bgr_hex[4:6], 16), int(bgr_hex[2:4], 16), int(bgr_hex[0:2], 16))
    overlay[m > 0] = (0.5 * overlay[m > 0] + 0.5 * np.array(tint)).astype(np.uint8)
    for x, y, w, h, area, _r in passed_full:
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(overlay, f"{int(area)}", (x, max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    for x, y, w, h, area, r in rejected_full:
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 0, 255), 1)
    # ROI box (yellow) + prediction crosshair
    x0, y0, x1, y1 = roi_box
    cv2.rectangle(overlay, (x0, y0), (x1, y1), (0, 255, 255), 3)
    px, py = int(pred_xy[0]), int(pred_xy[1])
    cv2.drawMarker(overlay, (px, py), (255, 255, 0), cv2.MARKER_CROSS, 28, 3)

    def _scale(img):
        return cv2.resize(img, (int(fw * scale), int(fh * scale)),
                          interpolation=cv2.INTER_AREA)
    panels = [_scale(frame_bgr),
              _scale(cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)),
              _scale(overlay)]
    labels = ["FULL frame", "FULL HSV mask", "overlay + ROI(yellow)"]
    h, w = panels[0].shape[:2]
    pad = 28
    out_w = (w + 4) * len(panels)
    canvas = np.full((h + pad, out_w, 3), 30, dtype=np.uint8)
    for i, (p, lab) in enumerate(zip(panels, labels)):
        xs = i * (w + 4)
        canvas[pad:pad + h, xs:xs + w] = p
        cv2.putText(canvas, lab, (xs + 4, 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (220, 220, 220), 1)
    cap_h = 38
    cap = np.full((cap_h, out_w, 3), 18, dtype=np.uint8)
    line = (f"FULL  slot_{team.slot}  t={t_sec:.1f}s  frame={frame_idx}  "
            f"state={state}  reason={reason}  full_blobs: passed={len(passed_full)} "
            f"rejected={len(rejected_full)}")
    cv2.putText(cap, line, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return np.vstack([cap, canvas])


def pick_events(frames: list[dict], slot_id: str, per_slot: int) -> list[dict]:
    """Pick up to `per_slot` representative events for a slot."""
    bad = []
    good = []
    for f in frames:
        for tr in f.get("tracks", []):
            if tr.get("slot_id") != slot_id and tr.get("team_id") != slot_id:
                continue
            rec = {"t": f["t"], "frame": f["frame"], **tr}
            state = tr.get("state", "")
            reason = tr.get("state_reason", "")
            if state in BAD_STATES or any(r in reason for r in BAD_REASONS):
                bad.append(rec)
            elif state == "tracked":
                good.append(rec)
            break
    # evenly sample bad first, then top-up with good
    def sample(lst, n):
        if len(lst) <= n: return lst
        step = len(lst) / n
        return [lst[int(i * step)] for i in range(n)]
    out = sample(bad, max(1, per_slot - 1))
    if len(out) < per_slot and good:
        out.append(good[len(good) // 2])
    return out


def auto_warn_slots(frames: list[dict], all_slot_ids: list[str], threshold: float = 0.30) -> list[str]:
    """A slot is 'warn' if >= threshold of its pre-wipe frames were not 'tracked'."""
    bad_count = {s: 0 for s in all_slot_ids}
    total = {s: 0 for s in all_slot_ids}
    for f in frames:
        for tr in f.get("tracks", []):
            sid = tr.get("slot_id") or tr.get("team_id")
            if sid not in bad_count: continue
            state = tr.get("state", "")
            if state in ("wiped",): continue
            total[sid] += 1
            if state != "tracked":
                bad_count[sid] += 1
    warn = []
    for s in all_slot_ids:
        if total[s] >= 5 and bad_count[s] / total[s] >= threshold:
            warn.append(s)
    return warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--tracks", required=True, type=Path,
                    help="reports/tracks.json от track_teams.py")
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--anchors", type=Path, default=None)
    ap.add_argument("--slots", type=str, default="",
                    help="через запятую: 2,4,11. Пусто = автовыбор WARN.")
    ap.add_argument("--per-slot", type=int, default=6)
    ap.add_argument("--roi-size", type=int, default=220)
    ap.add_argument("--full-frame", action="store_true",
                    help="К каждому событию добавить FULL-кадр + HSV-маска по всему кадру + ROI bbox.")
    ap.add_argument("--anchors-preview", action="store_true",
                    help="Только нарисовать кадр 0 со всеми anchors (slot+цвет+label) и выйти.")
    ap.add_argument("--out", type=Path,
                    default=THIS.parent / "reports" / "debug_masks")
    args = ap.parse_args()

    if not args.video.exists():
        sys.exit(f"[err] video not found: {args.video}")
    if not args.tracks.exists():
        sys.exit(f"[err] tracks not found: {args.tracks}")

    tdata = json.loads(args.tracks.read_text(encoding="utf-8"))
    frames = tdata.get("frames", [])
    if not frames:
        sys.exit("[err] tracks.json has no frames")

    anchors_path = args.anchors
    if anchors_path is None:
        guess = THIS.parents[1] / "motion_detect" / "reports" / "motion_tracks.json"
        if guess.exists(): anchors_path = guess
    teams = build_teams(args.config, anchors_path)
    teams_by_slot_id = {t.slot_id: t for t in teams}

    args.out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        sys.exit(f"[err] cannot open video: {args.video}")

    # --- Anchors preview ---------------------------------------------------
    if args.anchors_preview:
        first_frame_idx = int(frames[0]["frame"])
        cap.set(cv2.CAP_PROP_POS_FRAMES, first_frame_idx)
        ok, frame = cap.read()
        if not ok:
            sys.exit("[err] cannot read first frame")
        for tr in frames[0].get("tracks", []):
            sid = tr.get("slot_id") or tr.get("team_id")
            fp = tr.get("frame_px")
            if fp is None: continue
            team = teams_by_slot_id.get(sid)
            hex_s = (team.color_hex if team else "#ffffff").lstrip("#")
            color = (int(hex_s[4:6], 16), int(hex_s[2:4], 16), int(hex_s[0:2], 16))
            x, y = int(fp[0]), int(fp[1])
            cv2.circle(frame, (x, y), 14, color, 3)
            cv2.circle(frame, (x, y), 4, (255, 255, 255), -1)
            cv2.putText(frame, sid.replace("slot_", "#"), (x + 16, y + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        out = args.out / "anchors_frame0.png"
        cv2.imwrite(str(out), frame)
        print(f"[debug_masks] anchors preview -> {out}")
        cap.release()
        return

    all_slot_ids = sorted({tr.get("slot_id") or tr.get("team_id")
                           for f in frames for tr in f.get("tracks", [])
                           if (tr.get("slot_id") or tr.get("team_id"))})
    if args.slots.strip():
        wanted = [f"slot_{int(s)}" for s in args.slots.split(",") if s.strip()]
    else:
        wanted = auto_warn_slots(frames, all_slot_ids)
        print(f"[debug_masks] auto WARN slots: {wanted}")
    if not wanted:
        print("[debug_masks] nothing to do — no WARN slots and no --slots")
        cap.release()
        return

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"[debug_masks] video frames={total_frames}, will process {len(wanted)} slots")

    det_cfg = (load_config(args.config) or {}).get("detection", {}) or {}
    morph_default = int(det_cfg.get("morph_kernel", 5))
    min_a_default = float(det_cfg.get("min_area_px", 40))
    max_a_default = float(det_cfg.get("max_area_px", 2400))

    for sid in wanted:
        team = teams_by_slot_id.get(sid)
        if team is None:
            print(f"[warn] no team for {sid}")
            continue
        events = pick_events(frames, sid, args.per_slot)
        if not events:
            print(f"[info] {sid}: no events")
            continue
        sdir = args.out / sid
        sdir.mkdir(parents=True, exist_ok=True)
        morph = int(team.morph_kernel if team.morph_kernel is not None else morph_default)
        min_a = float(team.min_area if team.min_area is not None else min_a_default)
        max_a = float(team.max_area if team.max_area is not None else max_a_default)
        thumbs = []
        for i, ev in enumerate(events):
            fidx = int(ev["frame"])
            cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
            ok, frame = cap.read()
            if not ok or frame is None:
                print(f"[warn] {sid}: cannot read frame {fidx}")
                continue
            fh, fw = frame.shape[:2]
            fp = ev.get("frame_px") or ev.get("canonical_px")
            if fp is None: continue
            fx, fy = float(fp[0]), float(fp[1])
            # clip frame_px inside image
            fx = min(max(0.0, fx), fw - 1.0)
            fy = min(max(0.0, fy), fh - 1.0)
            rs = args.roi_size
            x0 = max(0, int(fx - rs // 2)); y0 = max(0, int(fy - rs // 2))
            x1 = min(fw, x0 + rs); y1 = min(fh, y0 + rs)
            roi = frame[y0:y1, x0:x1]
            if roi.size == 0: continue
            m_hsv, m_lab, mask, mode = color_mask(roi, team, morph)
            passed, rejected = find_contours(mask, min_a, max_a)
            panel = render_panel(roi, m_hsv, m_lab, mask, passed, rejected, team,
                                 float(ev["t"]), fidx, ev.get("state", "?"),
                                 ev.get("state_reason", "?"), mode,
                                 (fx - x0, fy - y0))
            if args.full_frame:
                full_panel = render_fullframe(frame, team, morph, min_a, max_a,
                                              (x0, y0, x1, y1), (fx, fy),
                                              float(ev["t"]), fidx,
                                              ev.get("state", "?"),
                                              ev.get("state_reason", "?"))
                # paste full_panel under ROI panel (after padding widths)
                w_ff = full_panel.shape[1]
                w_roi = panel.shape[1]
                W = max(w_ff, w_roi)
                def _pad(img, W):
                    if img.shape[1] >= W: return img
                    pad = np.full((img.shape[0], W - img.shape[1], 3), 30, dtype=np.uint8)
                    return np.hstack([img, pad])
                sep = np.full((6, W, 3), 60, dtype=np.uint8)
                panel = np.vstack([_pad(panel, W), sep, _pad(full_panel, W)])
            reason_tag = (ev.get("state_reason") or "x").replace("/", "_").replace(" ", "_")[:24]
            out_name = f"{i:02d}_t{float(ev['t']):07.1f}_{ev.get('state','?')}_{reason_tag}.png"
            cv2.imwrite(str(sdir / out_name), panel)
            thumbs.append(panel)
        if thumbs:
            # ROI у края кадра может быть уже -> паддим до общей ширины.
            max_w = max(t.shape[1] for t in thumbs)
            padded = []
            for t in thumbs:
                if t.shape[1] < max_w:
                    pad = np.full((t.shape[0], max_w - t.shape[1], 3), 30, dtype=np.uint8)
                    t = np.hstack([t, pad])
                padded.append(t)
            sep = np.full((4, max_w, 3), 60, dtype=np.uint8)
            stack = []
            for t in padded: stack += [t, sep]
            summary = np.vstack(stack[:-1])
            cv2.imwrite(str(sdir / "summary.png"), summary)
            print(f"[debug_masks] {sid}: {len(thumbs)} panels  -> {sdir}")
    cap.release()
    print(f"[debug_masks] done -> {args.out}")


if __name__ == "__main__":
    main()