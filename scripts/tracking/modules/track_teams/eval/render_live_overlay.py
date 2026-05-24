"""Live overlay video: 20 команд на canonical-карте.

На каждый кадр (по умолчанию шаг 1 сек):
  - фон: storm_point.png (canonical-карта)
  - ALGS POI-пики из start_coords.json — пустые жёлтые круги (план)
  - motion drop-точки из start_coords.json — белый крестик (факт DROP)
  - актуальные позиции 20 слотов из tracks.json — закрашенные кружки
    цветом slot (палитра HUD VOD), с подписью slot_N
  - timestamp + ring overlay (если есть rings.json)

Запуск:
    python -m scripts.tracking.modules.track_teams.eval.render_live_overlay \\
        --tracks src/data/m-test-g1/tracks.json \\
        --start-coords scripts/tracking/modules/track_teams/eval/reports/start_coords.json \\
        --map scripts/tracking/shared/canonical_maps/storm_point.png \\
        --rings src/data/m-test-g1/ring_geometry_v2.json \\
        --eliminations src/data/m-test-g1/eliminations.json \\
        --out /mnt/documents/tracking_overlay.mp4 \\
        --fps 10 --step-sec 1.0
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2  # type: ignore
import numpy as np

# Палитра — HUD VOD (см. src/lib/team-colors.ts)
SLOT_HEX = [
    "#078396", "#1B486A", "#1F55CD", "#452A60", "#6E2C70", "#AD2D78",
    "#AE1C51", "#BF000B", "#C34221", "#791F14", "#9F3A0D", "#764B01",
    "#CE7A12", "#967E01", "#84930A", "#495903", "#719844", "#398935",
    "#2F5B19", "#017557",
]


def hex_to_bgr(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", type=Path, required=True)
    ap.add_argument("--start-coords", type=Path, required=True)
    ap.add_argument("--map", type=Path, required=True, help="canonical .png")
    ap.add_argument("--rings", type=Path, default=None)
    ap.add_argument("--eliminations", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--step-sec", type=float, default=1.0)
    ap.add_argument("--render-w", type=int, default=1280,
                    help="итоговая ширина видео (карта пропорционально)")
    args = ap.parse_args()

    bg_full = cv2.imread(str(args.map), cv2.IMREAD_COLOR)
    if bg_full is None:
        raise SystemExit(f"cannot read map: {args.map}")
    H0, W0 = bg_full.shape[:2]
    scale = args.render_w / W0
    W = args.render_w
    H = int(round(H0 * scale))
    bg = cv2.resize(bg_full, (W, H), interpolation=cv2.INTER_AREA)
    bg = (bg * 0.55).astype(np.uint8)  # затемняем для контраста точек

    tracks_data = json.loads(args.tracks.read_text(encoding="utf-8"))
    meta = tracks_data.get("meta", {})
    cW, cH = meta.get("canonical_size", [W0, H0])
    frames = tracks_data.get("frames", [])
    if not frames:
        raise SystemExit("tracks.json: no frames")
    t_max = max(fr["t"] for fr in frames)

    # Индексируем по времени для быстрой выборки ближайшего фрейма.
    frames_sorted = sorted(frames, key=lambda f: f["t"])
    ts = np.array([f["t"] for f in frames_sorted], dtype=np.float64)

    start = json.loads(args.start_coords.read_text(encoding="utf-8"))
    rings = json.loads(args.rings.read_text(encoding="utf-8")) if args.rings else None
    elim = json.loads(args.eliminations.read_text(encoding="utf-8")) if args.eliminations else None
    dead_at: dict[str, float] = {}
    if elim:
        for slot, t in (elim.get("teams") or {}).items():
            if t.get("t_first_dead") is not None:
                dead_at[f"slot_{slot}"] = float(t["t_first_dead"])

    def to_px(cx_norm: float, cy_norm: float) -> tuple[int, int]:
        return int(round(cx_norm * W)), int(round(cy_norm * H))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(args.out), fourcc, args.fps, (W, H))
    if not vw.isOpened():
        raise SystemExit(f"cannot open writer: {args.out}")

    n_steps = int(math.floor(t_max / args.step_sec)) + 1
    for k in range(n_steps):
        t = k * args.step_sec
        frame = bg.copy()

        # ALGS POI (план) — пустые жёлтые круги
        for slot, e in start.get("slots", {}).items():
            a = e.get("algs")
            if not a:
                continue
            px, py = to_px(a["cx_norm"], a["cy_norm"])
            cv2.circle(frame, (px, py), 14, (0, 220, 240), 1, cv2.LINE_AA)

        # motion drop (факт) — белый крестик
        for slot, e in start.get("slots", {}).items():
            m = e.get("motion")
            if not m:
                continue
            px, py = to_px(m["cx_norm"], m["cy_norm"])
            cv2.drawMarker(frame, (px, py), (255, 255, 255),
                           cv2.MARKER_CROSS, 10, 1, cv2.LINE_AA)

        # ring overlay (берём последнее реальное кольцо к моменту t)
        if rings and rings.get("phases"):
            active = None
            for p in rings["phases"]:
                if (p.get("measured_at_t") or 0) <= t:
                    active = p
            if active and active.get("cx_canon_norm") is not None:
                cx = int(active["cx_canon_norm"] * W)
                cy = int(active["cy_canon_norm"] * H)
                rr = int(active["r_canon_norm"] * W)
                cv2.circle(frame, (cx, cy), rr, (80, 180, 255), 2, cv2.LINE_AA)

        # live tracks — ближайший фрейм
        idx = int(np.searchsorted(ts, t))
        idx = max(0, min(idx, len(ts) - 1))
        fr = frames_sorted[idx]
        for tr in fr.get("tracks", []):
            if not tr.get("world"):
                continue
            if tr.get("state") in ("lost", "wiped"):
                continue
            slot_id = tr["team_id"]
            dead = dead_at.get(slot_id)
            if dead is not None and t > dead:
                continue
            slot_n = int(slot_id.replace("slot_", ""))
            wx, wy = tr["world"]
            px = int(round(wx / cW * W))
            py = int(round(wy / cH * H))
            color = hex_to_bgr(SLOT_HEX[(slot_n - 1) % 20])
            cv2.circle(frame, (px, py), 6, color, -1, cv2.LINE_AA)
            cv2.circle(frame, (px, py), 7, (0, 0, 0), 1, cv2.LINE_AA)
            cv2.putText(frame, f"{slot_n}", (px + 8, py - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (255, 255, 255), 1, cv2.LINE_AA)

        # HUD
        bar_h = 26
        cv2.rectangle(frame, (0, 0), (W, bar_h), (0, 0, 0), -1)
        mm = int(t // 60); ss = int(t - mm * 60)
        alive = sum(1 for s in (elim.get("teams") if elim else {}).values()
                    if s.get("t_first_dead") is None or s["t_first_dead"] > t) if elim else len(start.get("slots", {}))
        txt = f"t = {mm:02d}:{ss:02d}   alive={alive}   plan(yellow)=ALGS POI   actual(white x)=motion drop"
        cv2.putText(frame, txt, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (220, 220, 220), 1, cv2.LINE_AA)

        vw.write(frame)

    vw.release()
    print(f"[overlay] {n_steps} frames @ {args.fps}fps -> {args.out}")


if __name__ == "__main__":
    main()