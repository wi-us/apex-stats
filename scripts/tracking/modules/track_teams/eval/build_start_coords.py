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


def _algs_poi_names(conn, *, series_id: str, canonical_map: str) -> dict[str, dict]:
    """slot_N -> {team_tag, team_name, poi_id, poi_name}."""
    rows = conn.execute(
        """
        SELECT pp.spawn_location_id AS poi_id,
               sl.name              AS poi_name,
               pp.team_id,
               t.short_name, t.name AS team_name,
               st.position
          FROM poi_picks pp
          JOIN poi_drafts pd      ON pd.id = pp.draft_id
          JOIN spawn_locations sl ON sl.id = pp.spawn_location_id
          JOIN maps m             ON m.id_ulid = pp.map_id_ulid
          LEFT JOIN teams t       ON t.id = pp.team_id
          LEFT JOIN series_team_stats st
                                  ON st.series_id = pd.series_id
                                 AND st.team_id  = pp.team_id
         WHERE pd.series_id = ?
           AND m.canonical_id = ?
        """,
        (series_id, canonical_map),
    ).fetchall()
    by_team: dict[str, dict] = {}
    for r in rows:
        tid = r["team_id"] or ""
        if tid in by_team:
            continue
        by_team[tid] = dict(r)
    ordered = sorted(
        by_team.values(),
        key=lambda r: (r["position"] if r["position"] is not None else 999,
                       (r["team_name"] or "")),
    )
    out: dict[str, dict] = {}
    for i, r in enumerate(ordered):
        out[f"slot_{i + 1}"] = {
            "team_tag":  (r["short_name"] or "").strip() or None,
            "team_name": r["team_name"],
            "poi_id":    r["poi_id"],
            "poi_name":  r["poi_name"],
        }
    return out


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


def _load_affine(affine_json: Path) -> tuple[list[list[float]], tuple[int, int]]:
    """Загружает 2x3 матрицу minimap_px → canonical_px + canonical_size."""
    data = json.loads(affine_json.read_text(encoding="utf-8"))
    M = data["affine"]["matrix"]
    src = data.get("source", {})
    cw, ch = src.get("canonical_size", [2048, 2048])
    return M, (int(cw), int(ch))


def _apply_affine(M: list[list[float]], x: float, y: float) -> tuple[float, float]:
    a, b, tx = M[0]
    c, d, ty = M[1]
    return a * x + b * y + tx, c * x + d * y + ty


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
    ap.add_argument("--affine", type=Path, default=None,
                    help="canonical_maps/<map>.minimap_affine.json — переводит "
                         "minimap-ROI px в canonical px (рекомендуется)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--warmup-sec", type=float, default=30.0)
    args = ap.parse_args()

    W, H = _load_canonical_size(args.canonical)

    # 1) ALGS POI picks → нормализованные xy
    with algs_db.connect() as conn:
        algs = build_algs_hints(conn, series_id=args.series,
                                canonical_map=args.map_id,
                                default_radius=0.03)
        algs_names = _algs_poi_names(conn, series_id=args.series,
                                     canonical_map=args.map_id)
    algs_by_slot = {k: v for k, v in algs.items() if k.startswith("slot_")}

    # 2) motion_detect → minimap-zone px → нормализованные xy (0..1 от minimap)
    motion_px, tag_in_file = _motion_drop_xy(args.motion,
                                             warmup_sec=args.warmup_sec)
    zone_tag = args.zone_tag or tag_in_file or "minimap"
    zw, zh = _load_zone_size(args.zones, zone_tag)

    affine = None
    if args.affine is not None:
        affine_M, (cw, ch) = _load_affine(args.affine)
        affine = (affine_M, cw, ch)

    out: dict = {
        "meta": {
            "series_id": args.series,
            "map": args.map_id,
            "canonical_size": [W, H],
            "minimap_zone_tag": zone_tag,
            "minimap_zone_size": [zw, zh],
            "affine_applied": bool(affine),
            "warmup_sec": args.warmup_sec,
        },
        "slots": {},
    }
    all_slots = sorted(set(algs_by_slot) | set(motion_px),
                       key=lambda s: int(s.split("_")[1]))
    for slot in all_slots:
        a = algs_by_slot.get(slot)
        m = motion_px.get(slot)
        meta = algs_names.get(slot) or {}
        entry: dict = {}
        if meta:
            entry["team_tag"]  = meta.get("team_tag")
            entry["team_name"] = meta.get("team_name")
            entry["poi"] = {
                "id":   meta.get("poi_id"),
                "name": meta.get("poi_name"),
            }
        if a:
            entry["algs"] = {"cx_norm": a["cx"], "cy_norm": a["cy"], "r_norm": a["r"]}
        if m:
            mx, my, n = m
            if affine is not None:
                M_aff, cw, ch = affine
                cx_c, cy_c = _apply_affine(M_aff, mx, my)
                nx, ny = cx_c / cw, cy_c / ch
            else:
                nx, ny = mx / zw, my / zh
            entry["motion"] = {
                "cx_norm": round(nx, 4),
                "cy_norm": round(ny, 4),
                "cx_px": round(mx, 1), "cy_px": round(my, 1),
                "n_points": n,
            }
        if a and m:
            dx = nx - a["cx"]
            dy = ny - a["cy"]
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