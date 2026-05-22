"""
aggregate_slots.py — постпроцессинг detections.json -> trajectories + slots/.

На входе: detections.json (формат detect_plates.py) — на каждый sample-кадр
содержит accepted/recoveries/tracked points в координатах ROI миникарты.

На выходе:
  reports/trajectories.json     — единый плотный лог [{t, frame, slot, source, ...}]
  reports/slots/<team_key>.json — отдельный файл на слот, отсортирован по t.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _slot_key(feat: dict) -> str:
    return str(
        feat.get("team_key")
        or feat.get("slot")
        or feat.get("dominant_team_id")
        or "unknown"
    )


def aggregate(detections: dict) -> Dict[str, List[dict]]:
    by_slot: Dict[str, List[dict]] = defaultdict(list)
    frames = detections.get("frames", [])

    for f in frames:
        t = f.get("t")
        fr = f.get("frame")

        # accepted: список Box-tuple, но в detections.json уже сериализован
        # как [{bbox:[x,y,w,h], score, feat, source}, ...] или legacy {accepted:int}.
        for b in f.get("boxes", []):
            slot = _slot_key(b.get("feat") or {})
            if slot == "unknown":
                continue
            x, y, w, h = b["bbox"]
            by_slot[slot].append({
                "t": t, "frame": fr,
                "x": x, "y": y, "w": w, "h": h,
                "cx": x + w / 2.0, "cy": y + h / 2.0,
                "score": b.get("score"),
                "source": b.get("source", "detect"),
            })

        for rec in f.get("recoveries", []):
            slot = rec.get("team_key")
            x, y, w, h = rec.get("bbox", [0, 0, 0, 0])
            by_slot[slot].append({
                "t": t, "frame": fr,
                "x": x, "y": y, "w": w, "h": h,
                "cx": x + w / 2.0, "cy": y + h / 2.0,
                "score": None,
                "source": f"recover:L{rec.get('level', 0)}",
            })

        for tr in f.get("tracked", []):
            slot = tr.get("slot")
            x, y, w, h = tr.get("bbox", [0, 0, 0, 0])
            by_slot[slot].append({
                "t": t, "frame": fr,
                "x": x, "y": y, "w": w, "h": h,
                "cx": x + w / 2.0, "cy": y + h / 2.0,
                "score": None,
                "source": "track",
            })

    for slot in by_slot:
        by_slot[slot].sort(key=lambda r: (r["frame"], r["t"]))
    return by_slot


def write_outputs(out_dir: Path, by_slot: Dict[str, List[dict]]) -> None:
    slots_dir = out_dir / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)

    flat: List[dict] = []
    summary: Dict[str, Any] = {}
    for slot, pts in by_slot.items():
        (slots_dir / f"{slot}.json").write_text(
            json.dumps(pts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary[slot] = {
            "points": len(pts),
            "sources": _count_sources(pts),
            "t_first": pts[0]["t"] if pts else None,
            "t_last": pts[-1]["t"] if pts else None,
        }
        for p in pts:
            flat.append({**p, "slot": slot})

    flat.sort(key=lambda r: (r["frame"], r["slot"]))
    (out_dir / "trajectories.json").write_text(
        json.dumps({"slots": list(by_slot.keys()),
                    "summary": summary,
                    "points": flat},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _count_sources(pts: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = defaultdict(int)
    for p in pts:
        out[str(p.get("source", "detect"))] += 1
    return dict(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detections", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path,
                    help="директория, куда писать slots/ и trajectories.json")
    args = ap.parse_args()

    d = json.loads(args.detections.read_text(encoding="utf-8"))
    by_slot = aggregate(d)
    args.out.mkdir(parents=True, exist_ok=True)
    write_outputs(args.out, by_slot)
    print(f"[ok] slots={len(by_slot)} -> {args.out}")


if __name__ == "__main__":
    main()