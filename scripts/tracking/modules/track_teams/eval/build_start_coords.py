"""Стартовые координаты команд — два независимых источника.

1) **ALGS API (план):** POI-пики команд из SQLite-кэша
   (`scripts/algs_api/data/algs.sqlite`). Это то, куда команда
   *собиралась* высадиться — semantic ground truth.
2) **motion_detect (факт):** первый стабильный кластер по slot из
   `motion_detect/reports/motion_tracks.json` (~t=30s) в canonical-px.

Скрипт выравнивает оба источника в нормализованные координаты карты
(0..1 от canonical_size) и пишет per-slot файл с обоими + дельтой.

Запуск:
    python -m scripts.tracking.modules.track_teams.eval.build_start_coords \\
        --series <SERIES_ULID> --map storm_point \\
        --motion scripts/tracking/modules/motion_detect/reports/motion_tracks.json \\
        --canonical scripts/tracking/shared/canonical_maps/storm_point.json \\
        --out scripts/tracking/modules/track_teams/eval/reports/start_coords.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO_ROOT))

from scripts.algs_api import db as algs_db  # noqa: E402
from scripts.algs_api.build_poi_hints import build as build_algs_hints  # noqa: E402


def _load_canonical_size(canonical_json: Path) -> tuple[int, int]:
    data = json.loads(canonical_json.read_text(encoding="utf-8"))
    size = data.get("canonical_size") or data.get("size") or [2048, 2048]
    return int(size[0]), int(size[1])


def _motion_drop_xy(motion_json: Path, *, warmup_sec: float = 30.0,
                    min_pts: int = 8) -> tuple[dict[str, tuple[float, float, int]], str | None]:
    """Per slot: median (x,y) of first stable cluster after warmup_sec.

    Возвращает координаты blob'ов в **локальных px ROI-зоны minimap**
    (как записано motion_detect: cx,cy относительно (x1,y1) zone-кропа)
    + число точек, + zone_tag из файла.
    """
    data = json.loads(motion_json.read_text(encoding="utf-8"))
    fps = float(data.get("fps") or 60.0)
    zone_tag = data.get("zone_tag")
    out: dict[str, tuple[float, float, int]] = {}
    for r in data.get("results", []):
        slot = int(r["slot"])
        pts: list[tuple[float, float]] = []
        for src in r.get("moving", []) or []:
            for p in src.get("points", []) or []:
                if not p or len(p) < 3:
                    continue
                f_idx, x, y = p[0], p[1], p[2]
                # points записаны как [frame_offset_within_window, x, y]
                t_sec = float(f_idx) / fps
                if t_sec < warmup_sec:
                    continue
                pts.append((float(x), float(y)))
        if len(pts) < min_pts:
            continue
        xs = sorted(p[0] for p in pts)
        ys = sorted(p[1] for p in pts)
        mid = len(xs) // 2
        mx = (xs[mid] + xs[~mid]) / 2.0
        my = (ys[mid] + ys[~mid]) / 2.0
        out[f"slot_{slot}"] = (mx, my, len(pts))
    return out, zone_tag


def _load_zone_size(zones_json: Path, zone_tag: str) -> tuple[int, int]:
    """Возвращает (w, h) первой zone с tag==zone_tag (в base-координатах)."""
    cfg = json.loads(zones_json.read_text(encoding="utf-8"))
    for z in cfg.get("zones", []):
        if z.get("tag") == zone_tag:
            return int(z["w"]), int(z["h"])
    raise SystemExit(f"[build_start_coords] zone tag={zone_tag} not found in {zones_json}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True, help="ALGS series ULID")
    ap.add_argument("--map", dest="map_id", default="storm_point")
    ap.add_argument("--motion", type=Path, required=True)
    ap.add_argument("--canonical", type=Path, required=True,
                    help="canonical_maps/<map>.json (для canonical_size)")
    ap.add_argument("--zones", type=Path, required=True,
                    help="motion_detect/configs/zones.vod.json — нужен размер minimap-зоны")
    ap.add_argument("--zone-tag", default=None,
                    help="по умолчанию берётся zone_tag из motion_tracks.json")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--warmup-sec", type=float, default=30.0)
    args = ap.parse_args()

    W, H = _load_canonical_size(args.canonical)

    # 1) ALGS POI picks → нормализованные xy
    with algs_db.connect() as conn:
        algs = build_algs_hints(conn, series_id=args.series,
                                canonical_map=args.map_id,
                                default_radius=0.03)
    algs_by_slot = {k: v for k, v in algs.items() if k.startswith("slot_")}

    # 2) motion_detect → minimap-zone px → нормализованные xy (0..1 от minimap)
    motion_px, tag_in_file = _motion_drop_xy(args.motion,
                                             warmup_sec=args.warmup_sec)
    zone_tag = args.zone_tag or tag_in_file or "minimap"
    zw, zh = _load_zone_size(args.zones, zone_tag)

    out: dict = {
        "meta": {
            "series_id": args.series,
            "map": args.map_id,
            "canonical_size": [W, H],
            "minimap_zone_tag": zone_tag,
            "minimap_zone_size": [zw, zh],
            "warmup_sec": args.warmup_sec,
        },
        "slots": {},
    }
    all_slots = sorted(set(algs_by_slot) | set(motion_px),
                       key=lambda s: int(s.split("_")[1]))
    for slot in all_slots:
        a = algs_by_slot.get(slot)
        m = motion_px.get(slot)
        entry: dict = {}
        if a:
            entry["algs"] = {"cx_norm": a["cx"], "cy_norm": a["cy"], "r_norm": a["r"]}
        if m:
            mx, my, n = m
            entry["motion"] = {
                "cx_norm": round(mx / zw, 4),
                "cy_norm": round(my / zh, 4),
                "cx_px": round(mx, 1), "cy_px": round(my, 1),
                "n_points": n,
            }
        if a and m:
            dx = (mx / zw) - a["cx"]
            dy = (my / zh) - a["cy"]
            entry["delta_norm"] = round(math.hypot(dx, dy), 4)
        out["slots"][slot] = entry

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    deltas = [e["delta_norm"] for e in out["slots"].values() if "delta_norm" in e]
    if deltas:
        deltas.sort()
        med = deltas[len(deltas) // 2]
        print(f"[start_coords] {len(out['slots'])} slots, "
              f"both-source={len(deltas)}, median delta_norm={med:.3f} "
              f"-> {args.out}", file=sys.stderr)
    else:
        print(f"[start_coords] {len(out['slots'])} slots -> {args.out}",
              file=sys.stderr)


if __name__ == "__main__":
    main()