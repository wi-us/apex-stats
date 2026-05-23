"""ring_mask_filter — пост-фильтр tracks.json по геометрии колец.

Идея: пиксели на границе белого кольца (safe-zone) и на границе красной
зоны имеют сдвиг оттенка (белый десатурирует, красный смещает hue в
тёплую сторону) — это ломает HSV-классификацию слотов и порождает
ID-swap, который виден как "телепорт".

Здесь мы не пересчитываем детекцию. Берём готовый tracks.json,
для каждой точки определяем, попадает ли её canonical-позиция в
±BAND_PX от любого "нарисованного" на карте кольца в этот момент
времени, и если да — выкидываем точку.

Что такое "нарисованное" кольцо в момент t:
  - ring N с известной геометрией (ring_geometry_v2.json), у которого
    t_countdown_start[N] <= t (rings.json/phases). Если COUNTDOWN не
    зафиксирован — fallback на t_closing_start[N] - 90s.

Красная зона:
  - её внешняя граница совпадает с тем же белым кольцом текущей
    safe-zone, поэтому отдельная маска не нужна;
  - её внутренняя граница во время CLOSING — это уходящая стенка
    шторма между ring(N-1) и ring(N). У нас нет t_closed, поэтому
    параметризуем линейной интерполяцией от t_closing_start[N] до
    t_countdown_start[N+1] и добавляем второе кольцо в список
    нарисованных.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Optional


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _phase_visible_from(phase: dict) -> Optional[float]:
    t = phase.get("t_countdown_start")
    if t is not None:
        return float(t)
    t = phase.get("t_closing_start")
    if t is not None:
        return float(t) - 90.0
    return None


def _active_rings(t, phases, geo_by_ring):
    out = []
    phases_sorted = sorted(phases, key=lambda p: int(p["ring"]))
    n_phases = len(phases_sorted)
    for idx, ph in enumerate(phases_sorted):
        n = int(ph["ring"])
        g = geo_by_ring.get(n)
        if g is None:
            continue
        vis = _phase_visible_from(ph)
        if vis is None or t < vis:
            continue
        cx0 = float(g["cx_canon_px"]); cy0 = float(g["cy_canon_px"]); r0 = float(g["r_canon_px"])
        out.append((cx0, cy0, r0))
        if idx + 1 < n_phases:
            nxt = phases_sorted[idx + 1]
            t_close = nxt.get("t_closing_start")
            n_next = int(nxt["ring"])
            g_next = geo_by_ring.get(n_next)
            if t_close is not None and g_next is not None and t >= float(t_close):
                t_end = None
                if idx + 2 < n_phases:
                    t_end = phases_sorted[idx + 2].get("t_countdown_start")
                if t_end is None:
                    t_end = float(t_close) + 120.0
                t_end = float(t_end); t_close = float(t_close)
                frac = 0.0 if t_end <= t_close else max(0.0, min(1.0, (t - t_close) / (t_end - t_close)))
                cx1 = float(g_next["cx_canon_px"]); cy1 = float(g_next["cy_canon_px"]); r1 = float(g_next["r_canon_px"])
                cx = (1 - frac) * cx0 + frac * cx1
                cy = (1 - frac) * cy0 + frac * cy1
                r  = (1 - frac) * r0  + frac * r1
                out.append((cx, cy, r))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", type=Path, required=True)
    ap.add_argument("--rings",  type=Path, required=True)
    ap.add_argument("--geo",    type=Path, required=True)
    ap.add_argument("--out",    type=Path, required=True)
    ap.add_argument("--band-px", type=float, default=10.0)
    args = ap.parse_args()

    tracks = _load(args.tracks)
    rings = _load(args.rings)
    geo = _load(args.geo)

    phases_timing = rings.get("phases", [])
    geo_by_ring = {int(p["ring"]): p for p in geo.get("phases", [])}
    band = float(args.band_px)

    dropped = 0; kept = 0
    per_slot_drop = {}

    for fr in tracks.get("frames", []):
        t = float(fr.get("t", 0.0))
        active = _active_rings(t, phases_timing, geo_by_ring)
        if not active:
            kept += sum(1 for tr in fr.get("tracks", []) if tr.get("canonical_px"))
            continue
        new_tracks = []
        for tr in fr.get("tracks", []):
            cp = tr.get("canonical_px")
            if not cp:
                new_tracks.append(tr); continue
            px, py = float(cp[0]), float(cp[1])
            on_ring = False
            for cx, cy, r in active:
                d = math.hypot(px - cx, py - cy)
                if abs(d - r) <= band:
                    on_ring = True; break
            if on_ring:
                dropped += 1
                sid = tr.get("slot_id") or tr.get("team_id") or "?"
                per_slot_drop[sid] = per_slot_drop.get(sid, 0) + 1
            else:
                kept += 1
                new_tracks.append(tr)
        fr["tracks"] = new_tracks

    meta = tracks.setdefault("meta", {})
    trimmed = meta.setdefault("trimmed", {})
    trimmed["ring_mask"] = {
        "band_px": band,
        "rings_used": sorted(geo_by_ring.keys()),
        "dropped": dropped,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(tracks, ensure_ascii=False), encoding="utf-8")

    print(f"[ring_mask] band=+/-{band}px  kept={kept}  dropped={dropped}")
    print("[ring_mask] top slots by drop:")
    for sid, n in sorted(per_slot_drop.items(), key=lambda x: -x[1])[:10]:
        print(f"  {sid}: {n}")


if __name__ == "__main__":
    main()
