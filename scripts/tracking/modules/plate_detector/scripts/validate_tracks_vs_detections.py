import argparse
import ast
import csv
import json
import math
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple


def parse_tuple(value: str):
    if value is None or value == "":
        return None
    return ast.literal_eval(value)


def center_from_bbox(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def dist(a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]) -> Optional[float]:
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def load_detections(path: Path) -> Dict[int, Dict[str, List[dict]]]:
    by_frame: Dict[int, Dict[str, List[dict]]] = {}

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            frame = int(float(row["frame_idx"]))
            tag = row.get("matched_broadcast_tag") or row.get("matched_team_tag") or "UNKNOWN"
            bbox_original = parse_tuple(row["bbox_original"])
            bbox_roi = parse_tuple(row["bbox_roi"])
            cx, cy = center_from_bbox(bbox_original)

            item = {
                "frame": frame,
                "tag": tag,
                "bbox_original": bbox_original,
                "bbox_roi": bbox_roi,
                "center_original": (cx, cy),
                "det_conf": float(row.get("det_conf", 0.0) or 0.0),
                "color_score": float(row.get("color_score", 0.0) or 0.0),
            }
            by_frame.setdefault(frame, {}).setdefault(tag, []).append(item)

    return by_frame


def aggregate_points(items: List[dict], mode: str = "median") -> Optional[Tuple[float, float]]:
    if not items:
        return None
    xs = [d["center_original"][0] for d in items]
    ys = [d["center_original"][1] for d in items]

    if mode == "mean":
        return sum(xs) / len(xs), sum(ys) / len(ys)

    if mode == "weighted":
        weights = [max(1e-6, d["det_conf"] * max(d["color_score"], 0.01)) for d in items]
        sw = sum(weights)
        return sum(x * w for x, w in zip(xs, weights)) / sw, sum(y * w for y, w in zip(ys, weights)) / sw

    return median(xs), median(ys)


def load_slot_to_tag(tracks: dict) -> Dict[str, str]:
    result = {}
    for slot in tracks.get("meta", {}).get("slots", []):
        slot_id = slot.get("slot_id") or f"slot_{slot.get('slot')}"
        result[slot_id] = slot.get("broadcast_tag") or slot.get("team_tag") or slot_id
    return result


def validate(args: argparse.Namespace) -> None:
    tracks = json.loads(Path(args.tracks).read_text(encoding="utf-8"))
    detections = load_detections(Path(args.detections))
    slot_to_tag = load_slot_to_tag(tracks)

    out_rows = []
    warnings = []

    for frame in tracks.get("frames", []):
        frame_idx = int(frame["frame"])
        det_by_tag = detections.get(frame_idx, {})

        for tr in frame.get("tracks", []):
            slot_id = tr.get("slot_id") or tr.get("team_id")
            tag = slot_to_tag.get(slot_id, slot_id)
            track_px = tr.get("frame_px")
            track_point = tuple(track_px) if isinstance(track_px, list) and len(track_px) == 2 else None
            items = det_by_tag.get(tag, [])

            agg_median = aggregate_points(items, "median")
            agg_mean = aggregate_points(items, "mean")
            agg_weighted = aggregate_points(items, "weighted")

            d_median = dist(track_point, agg_median)
            d_mean = dist(track_point, agg_mean)
            d_weighted = dist(track_point, agg_weighted)

            status = "ok"
            if track_point is not None and not items:
                status = "track_without_raw_detections"
            elif track_point is None and items:
                status = "raw_detections_without_track"
            elif d_median is not None and d_median > args.max_dist:
                status = "track_far_from_raw_detections"

            row = {
                "frame": frame_idx,
                "time_sec": frame.get("t"),
                "slot_id": slot_id,
                "tag": tag,
                "track_state": tr.get("state"),
                "state_reason": tr.get("state_reason"),
                "track_px": track_point,
                "raw_count": len(items),
                "raw_median": agg_median,
                "raw_mean": agg_mean,
                "raw_weighted": agg_weighted,
                "dist_to_median": d_median,
                "dist_to_mean": d_mean,
                "dist_to_weighted": d_weighted,
                "status": status,
            }
            out_rows.append(row)

            if status != "ok":
                warnings.append(row)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "frame", "time_sec", "slot_id", "tag", "track_state", "state_reason",
        "track_px", "raw_count", "raw_median", "raw_mean", "raw_weighted",
        "dist_to_median", "dist_to_mean", "dist_to_weighted", "status",
    ]

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in out_rows:
            writer.writerow(row)

    print(f"Rows checked: {len(out_rows)}")
    print(f"Warnings: {len(warnings)}")
    print(f"Report saved: {out_path.resolve()}")

    print("\nTop warnings:")
    for row in warnings[:args.print_limit]:
        print(
            f"frame={row['frame']} tag={row['tag']} slot={row['slot_id']} "
            f"status={row['status']} raw_count={row['raw_count']} "
            f"track={row['track_px']} raw_median={row['raw_median']} "
            f"dist={row['dist_to_median']} reason={row['state_reason']}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate tracks.json against raw detections.csv")
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--detections", required=True)
    parser.add_argument("--out", default="reports/tracks_vs_detections.csv")
    parser.add_argument("--max-dist", type=float, default=80.0)
    parser.add_argument("--print-limit", type=int, default=40)
    validate(parser.parse_args())
