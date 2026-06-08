import argparse
import ast
import csv
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_tuple(value):
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return tuple(ast.literal_eval(str(value)))


def center_from_bbox(bbox: Tuple[float, float, float, float], anchor_y_ratio: float = 0.5) -> Tuple[float, float]:
    """
    Anchor point for a team plate.
    x is always the horizontal center. y can be shifted down toward the player arrow/marker.
    anchor_y_ratio=0.5 is bbox center; 0.72 is slightly below center.
    """
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2.0, y1 + (y2 - y1) * float(anchor_y_ratio)


def distance(a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]) -> float:
    if a is None or b is None:
        return float("inf")
    return math.hypot(a[0] - b[0], a[1] - b[1])


def safe_tag(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum() or ch == "_")


def get_profile_list(data: dict) -> list:
    for key in ["team_color_profiles", "profiles", "teams", "colors"]:
        if isinstance(data.get(key), list):
            return data[key]
    return []


def load_profiles(config_path: Optional[str], color_profiles_path: Optional[str]) -> List[dict]:
    config = load_json(Path(config_path)) if config_path else {}
    color_data = load_json(Path(color_profiles_path)) if color_profiles_path else {}

    profiles_raw = get_profile_list(color_data)
    if not profiles_raw:
        profiles_raw = get_profile_list(config)

    teams_by_tag = {}
    for t in config.get("teams", []):
        tag = safe_tag(t.get("tag") or t.get("broadcast_tag") or t.get("short_name") or t.get("name") or "")
        if tag:
            teams_by_tag[tag] = t

    profiles = []
    for idx, p in enumerate(profiles_raw, start=1):
        slot = int(p.get("hud_index") or p.get("slot") or p.get("team_index") or idx)
        broadcast_tag = safe_tag(p.get("broadcast_tag") or p.get("tag") or p.get("team_tag") or f"slot_{slot}")
        team = teams_by_tag.get(broadcast_tag, {})
        profiles.append({
            "slot_id": f"slot_{slot}",
            "slot": slot,
            "team_id": f"slot_{slot}",
            "team_db_id": p.get("team_id") or team.get("team_id") or team.get("id"),
            "team_tag": p.get("team_tag") or team.get("tag") or broadcast_tag,
            "broadcast_tag": broadcast_tag,
            "name": f"{broadcast_tag} · {p.get('team_name') or p.get('name') or team.get('name') or broadcast_tag}",
            "color": p.get("color_hex") or p.get("hex") or p.get("color"),
        })

    # Fallback to config teams if no color profiles are available.
    if not profiles and config.get("teams"):
        for idx, t in enumerate(config.get("teams", []), start=1):
            tag = safe_tag(t.get("tag") or t.get("broadcast_tag") or t.get("name") or f"team_{idx}")
            profiles.append({
                "slot_id": f"slot_{idx}",
                "slot": idx,
                "team_id": f"slot_{idx}",
                "team_db_id": t.get("team_id") or t.get("id"),
                "team_tag": t.get("tag") or tag,
                "broadcast_tag": tag,
                "name": f"{tag} · {t.get('name') or tag}",
                "color": t.get("color") or t.get("hex"),
            })

    return sorted(profiles, key=lambda x: int(x["slot"]))



def parse_json_field(value, default=None):
    if default is None:
        default = []
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        try:
            return ast.literal_eval(str(value))
        except Exception:
            return default


def load_poi_priors(path: Optional[str], left_ignore: int, roi_size: int) -> Dict[str, dict]:
    if not path:
        return {}
    data = load_json(Path(path))
    teams = data.get("teams") or {}
    out: Dict[str, dict] = {}
    for tag, item in teams.items():
        tag_n = safe_tag(tag or item.get("broadcast_tag") or item.get("team_tag") or "")
        if not tag_n:
            continue
        fp = item.get("frame_px")
        if not fp and item.get("x_norm") is not None and item.get("y_norm") is not None:
            fp = [left_ignore + float(item["x_norm"]) * roi_size, float(item["y_norm"]) * roi_size]
        if fp:
            item = dict(item)
            item["frame_px"] = [float(fp[0]), float(fp[1])]
            out[tag_n] = item
    return out


def candidate_tags_from_row(row: dict, best_tag: str, best_score: float, use_candidates: bool, candidate_margin: float, min_candidate_score: float) -> List[Tuple[str, float, int]]:
    """Return [(tag, candidate_score, rank)]. Always includes best_tag when present."""
    result: List[Tuple[str, float, int]] = []
    if best_tag:
        result.append((best_tag, float(best_score), 0))

    if not use_candidates:
        return result

    candidates = parse_json_field(row.get("color_candidates"), [])
    if not isinstance(candidates, list):
        return result

    for rank, c in enumerate(candidates, start=1):
        if not isinstance(c, dict):
            continue
        tag = safe_tag(c.get("tag") or c.get("broadcast_tag") or c.get("team_tag") or "")
        if not tag:
            continue
        try:
            score = float(c.get("score", 0.0) or 0.0)
        except Exception:
            score = 0.0
        if score < min_candidate_score:
            continue
        if best_score > 0 and (best_score - score) > candidate_margin:
            continue
        if not any(t == tag for t, _, _ in result):
            result.append((tag, score, rank))
    return result

def load_detections(path: Path, min_conf: float, min_color_score: float, keep_unknown: bool, anchor_y_ratio: float, use_candidates: bool, candidate_margin: float, min_candidate_score: float) -> Dict[int, Dict[str, List[dict]]]:
    by_frame: Dict[int, Dict[str, List[dict]]] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            best_tag = row.get("matched_broadcast_tag") or row.get("matched_team_tag") or ""
            best_tag = safe_tag(best_tag)
            if not best_tag and not keep_unknown:
                continue
            if not best_tag:
                best_tag = "UNKNOWN"

            try:
                det_conf = float(row.get("det_conf", 0.0) or 0.0)
                best_color_score = float(row.get("color_score", 0.0) or 0.0)
                frame_idx = int(float(row["frame_idx"]))
                time_sec = float(row.get("video_time_sec", 0.0) or 0.0)
            except Exception:
                continue

            if det_conf < min_conf or best_color_score < min_color_score:
                continue

            bbox_original = parse_tuple(row.get("bbox_original"))
            bbox_roi = parse_tuple(row.get("bbox_roi"))
            if not bbox_original:
                continue

            bbox_original = tuple(float(x) for x in bbox_original)
            center = center_from_bbox(bbox_original, anchor_y_ratio=anchor_y_ratio)
            candidates = candidate_tags_from_row(row, best_tag, best_color_score, use_candidates, candidate_margin, min_candidate_score)

            for tag, cand_score, rank in candidates:
                if tag == "UNKNOWN" and not keep_unknown:
                    continue
                if cand_score < min_color_score:
                    continue
                final_tag_bonus = 1.0 if tag == best_tag else 0.92
                item = {
                    "frame_idx": frame_idx,
                    "time_sec": time_sec,
                    "tag": tag,
                    "matched_tag": best_tag,
                    "candidate_rank": rank,
                    "bbox_original": bbox_original,
                    "bbox_roi": tuple(float(x) for x in bbox_roi) if bbox_roi else None,
                    "center": center,
                    "det_conf": det_conf,
                    "color_score": cand_score,
                    "score": det_conf * max(cand_score, 0.001) * final_tag_bonus,
                    "identity_source": row.get("identity_source", ""),
                    "text_best_tag": row.get("text_best_tag", ""),
                    "text_score": float(row.get("text_score", 0.0) or 0.0),
                    "override_reason": row.get("override_reason", ""),
                    "source_row_tag": best_tag,
                }
                by_frame.setdefault(frame_idx, {}).setdefault(tag, []).append(item)
    return by_frame

def aggregate_points(items: List[dict], mode: str) -> Tuple[Optional[Tuple[float, float]], float, float, List[dict]]:
    if not items:
        return None, 0.0, 0.0, []
    if mode == "best":
        best = max(items, key=lambda x: x["score"])
        return best["center"], best["det_conf"], best["color_score"], [best]
    if mode == "weighted":
        weights = [max(1e-6, it["score"]) for it in items]
        sw = sum(weights)
        x = sum(it["center"][0] * w for it, w in zip(items, weights)) / sw
        y = sum(it["center"][1] * w for it, w in zip(items, weights)) / sw
        conf = sum(it["det_conf"] * w for it, w in zip(items, weights)) / sw
        cscore = sum(it["color_score"] * w for it, w in zip(items, weights)) / sw
        return (x, y), conf, cscore, items
    # median
    xs = [it["center"][0] for it in items]
    ys = [it["center"][1] for it in items]
    return (float(median(xs)), float(median(ys))), float(median([it["det_conf"] for it in items])), float(median([it["color_score"] for it in items])), items


def densest_cluster(items: List[dict], radius: float, max_items: int = 3) -> List[dict]:
    if len(items) <= max_items:
        return items
    best_center = None
    best_group: List[dict] = []
    for it in items:
        group = [other for other in items if distance(it["center"], other["center"]) <= radius]
        if len(group) > len(best_group) or (len(group) == len(best_group) and sum(g["score"] for g in group) > sum(g["score"] for g in best_group)):
            best_center = it["center"]
            best_group = group
    best_group.sort(key=lambda x: x["score"], reverse=True)
    return best_group[:max_items]


def selection_cost(it: dict, prev_point: Optional[Tuple[float, float]], drop_point: Optional[Tuple[float, float]], time_sec: float, drop_prior_seconds: float, prev_scale: float, drop_scale: float) -> float:
    # Lower is better. Color score and det confidence reduce cost. Distances increase cost.
    color_cost = 1.0 - max(0.0, min(1.0, float(it.get("color_score", 0.0))))
    det_cost = 1.0 - max(0.0, min(1.0, float(it.get("det_conf", 0.0))))
    prev_cost = 0.0 if prev_point is None else min(2.5, distance(it["center"], prev_point) / max(prev_scale, 1.0))
    drop_cost = 0.0 if drop_point is None else min(2.5, distance(it["center"], drop_point) / max(drop_scale, 1.0))

    if drop_point is not None and (prev_point is None or time_sec <= drop_prior_seconds):
        return 0.35 * color_cost + 0.15 * det_cost + 0.20 * prev_cost + 0.50 * drop_cost
    return 0.45 * color_cost + 0.10 * det_cost + 0.50 * prev_cost + 0.05 * drop_cost


def select_candidates(items: List[dict], prev_point: Optional[Tuple[float, float]], drop_point: Optional[Tuple[float, float]], time_sec: float, spatial_gate: float, drop_gate: float, drop_prior_seconds: float, cluster_radius: float, max_cluster_items: int) -> Tuple[List[dict], str]:
    if not items:
        return [], "no_candidates"

    filtered = items
    if prev_point is not None and spatial_gate > 0:
        near_prev = [it for it in filtered if distance(it["center"], prev_point) <= spatial_gate]
        if near_prev:
            filtered = near_prev
        elif time_sec > drop_prior_seconds:
            return [], "all_candidates_too_far_from_previous"

    if drop_point is not None and drop_gate > 0 and time_sec <= drop_prior_seconds:
        near_drop = [it for it in filtered if distance(it["center"], drop_point) <= drop_gate]
        if near_drop:
            filtered = near_drop
        elif prev_point is None:
            return [], "all_candidates_too_far_from_drop"

    if not filtered:
        return [], "no_candidates_after_gates"

    filtered.sort(key=lambda it: selection_cost(it, prev_point, drop_point, time_sec, drop_prior_seconds, spatial_gate, drop_gate))
    # After cost sort, use nearest plausible items; this avoids one bad candidate dragging median.
    picked = filtered[:max_cluster_items]
    return picked, f"cost_gate:{len(filtered)}"


def build_tracks(args: argparse.Namespace) -> None:
    detections_path = Path(args.detections)
    profiles = load_profiles(args.config, args.color_profiles)
    poi_priors = load_poi_priors(args.poi_priors, args.left_ignore, args.roi_size)
    det_by_frame = load_detections(detections_path, args.min_conf, args.min_color_score, args.keep_unknown, args.anchor_y_ratio, args.use_candidates, args.candidate_margin, args.min_candidate_score)

    all_frames = sorted(det_by_frame.keys())
    if not all_frames:
        raise RuntimeError("No detections after filters. Lower --min-conf / --min-color-score or check detections.csv.")

    tag_to_profile = {p["broadcast_tag"]: p for p in profiles}
    tags_in_detections = sorted({tag for frame in det_by_frame.values() for tag in frame.keys() if tag != "UNKNOWN"})
    for tag in tags_in_detections:
        if tag not in tag_to_profile:
            slot = len(profiles) + 1
            p = {
                "slot_id": f"slot_{slot}",
                "slot": slot,
                "team_id": f"slot_{slot}",
                "team_db_id": None,
                "team_tag": tag,
                "broadcast_tag": tag,
                "name": tag,
                "color": None,
            }
            profiles.append(p)
            tag_to_profile[tag] = p

    last_seen: Dict[str, dict] = {}
    frames_out = []

    for step_idx, frame_idx in enumerate(all_frames):
        frame_dets = det_by_frame.get(frame_idx, {})
        time_sec = next((items[0]["time_sec"] for items in frame_dets.values() if items), 0.0)
        tracks = []

        for p in profiles:
            tag = p["broadcast_tag"]
            raw_items = frame_dets.get(tag, [])
            prev = last_seen.get(tag)
            prev_point = prev["point"] if prev else None

            drop = poi_priors.get(tag)
            drop_point = tuple(drop["frame_px"]) if drop and drop.get("frame_px") else None
            selected, select_reason = select_candidates(raw_items, prev_point, drop_point, time_sec, args.spatial_gate, args.drop_gate, args.drop_prior_seconds, args.cluster_radius, args.max_cluster_items)
            point, conf, color_score, used_items = aggregate_points(selected, args.aggregation)

            state = "low_conf"
            state_reason = "no_assignment"
            accepted = False

            if point is not None and len(selected) >= args.min_detections:
                jump = distance(prev_point, point) if prev_point is not None else 0.0
                if prev_point is not None and args.reject_jumps and jump > args.max_jump:
                    state = "rejected_jump"
                    state_reason = f"candidate_too_far:{jump:.1f}:{select_reason}"
                    point = None
                    conf = 0.0
                    color_score = 0.0
                else:
                    state = "tracked"
                    state_reason = f"from_detections:{len(selected)}:{select_reason}"
                    accepted = True
            elif prev is not None and args.carry > 0 and (step_idx - prev["step_idx"]) <= args.carry:
                point = prev["point"]
                conf = prev["conf"] * (args.carry_decay ** (step_idx - prev["step_idx"]))
                color_score = prev["color_score"]
                state = "carried"
                state_reason = f"carried_from_step:{prev['step_idx']}"

            if accepted:
                last_seen[tag] = {
                    "point": point,
                    "conf": conf,
                    "color_score": color_score,
                    "frame": frame_idx,
                    "step_idx": step_idx,
                    "time_sec": time_sec,
                }

            tracks.append({
                "team_id": p["team_id"],
                "slot_id": p["slot_id"],
                "broadcast_tag": p["broadcast_tag"],
                "team_tag": p.get("team_tag"),
                "team_db_id": p.get("team_db_id"),
                "canonical_px": None,
                "frame_px": [round(point[0], 1), round(point[1], 1)] if point is not None else None,
                "state": state,
                "state_reason": state_reason,
                "mask_mode": "detections_csv_stable",
                "confidence": round(float(conf), 3),
                "score": round(float(color_score), 3),
                "world": None,
                "drop_poi": (poi_priors.get(tag) or {}).get("poi_name"),
                "drop_frame_px": (poi_priors.get(tag) or {}).get("frame_px"),
                "raw_count": len(raw_items),
                "used_count": len(used_items),
            })

        frames_out.append({
            "t": round(float(time_sec), 3),
            "frame": int(frame_idx),
            "camera": {
                "registration": "not_used",
                "ransac_inliers": None,
                "zoom": None,
                "rotation_deg": None,
                "pan_canonical": None,
                "from_detections_rejected": {},
            },
            "tracks": tracks,
            "actual_frame": int(frame_idx),
        })

    meta = {
        "video": args.video_name,
        "fps_source": args.fps_source,
        "fps_processed": None,
        "frame_count": None,
        "canonical_map": None,
        "canonical_size": [2048, 2048],
        "world_bounds": {"x": [0, 2048], "y": [0, 2048]},
        "teams": [
            {
                "id": p["slot_id"],
                "name": p["name"],
                "color": p.get("color"),
                "team_id": p.get("team_db_id"),
                "team_tag": p.get("team_tag"),
                "broadcast_tag": p.get("broadcast_tag"),
            }
            for p in profiles
        ],
        "slots": [
            {
                "slot_id": p["slot_id"],
                "slot": p["slot"],
                "team_id": p["team_id"],
                "name": p["name"],
                "color": p.get("color"),
                "team_db_id": p.get("team_db_id"),
                "team_tag": p.get("team_tag"),
                "broadcast_tag": p.get("broadcast_tag"),
                "anchor_conf": "UNKNOWN",
                "anchor_world": None,
                "wiped_at_t": None,
            }
            for p in profiles
        ],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": 3,
        "da_strategy": "from_detections_csv_stable_spatial_gate",
        "source_files": {
            "detections_csv": str(detections_path),
            "detections_sha256": sha256_file(detections_path),
            "config": args.config,
            "color_profiles": args.color_profiles,
            "poi_priors": args.poi_priors,
        },
        "tracking_params": {
            "aggregation": args.aggregation,
            "min_conf": args.min_conf,
            "min_color_score": args.min_color_score,
            "min_detections": args.min_detections,
            "spatial_gate": args.spatial_gate,
            "max_jump": args.max_jump,
            "reject_jumps": args.reject_jumps,
            "cluster_radius": args.cluster_radius,
            "max_cluster_items": args.max_cluster_items,
            "carry": args.carry,
            "carry_decay": args.carry_decay,
            "anchor_y_ratio": args.anchor_y_ratio,
            "use_candidates": args.use_candidates,
            "candidate_margin": args.candidate_margin,
            "min_candidate_score": args.min_candidate_score,
            "drop_gate": args.drop_gate,
            "drop_prior_seconds": args.drop_prior_seconds,
            "poi_priors_loaded": len(poi_priors),
        },
    }

    save_json(Path(args.out), {"meta": meta, "frames": frames_out})
    print(f"Tracks saved: {Path(args.out).resolve()}")
    print(f"Frames: {len(frames_out)}")
    print(f"Teams: {len(profiles)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build stable tracks.json from detections.csv using spatial gating and carry")
    parser.add_argument("--detections", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--color-profiles", default=None)
    parser.add_argument("--video-name", default=None)
    parser.add_argument("--fps-source", type=float, default=None)
    parser.add_argument("--aggregation", choices=["median", "weighted", "best"], default="median")
    parser.add_argument("--min-conf", type=float, default=0.20)
    parser.add_argument("--min-color-score", type=float, default=0.25)
    parser.add_argument("--min-detections", type=int, default=1)
    parser.add_argument("--spatial-gate", type=float, default=180.0, help="Accept candidates near previous point within this many full-frame pixels")
    parser.add_argument("--max-jump", type=float, default=240.0, help="Reject accepted candidate if jump from previous point is larger")
    parser.add_argument("--reject-jumps", action="store_true")
    parser.add_argument("--cluster-radius", type=float, default=140.0)
    parser.add_argument("--max-cluster-items", type=int, default=3)
    parser.add_argument("--carry", type=int, default=2, help="Carry previous point for N processed frames")
    parser.add_argument("--carry-decay", type=float, default=0.72)
    parser.add_argument("--anchor-y-ratio", type=float, default=0.72, help="Anchor y inside bbox: 0.5=center, 0.72=below center")
    parser.add_argument("--use-candidates", action="store_true", help="Use color_candidates from detections.csv/jsonl for ambiguous colors")
    parser.add_argument("--candidate-margin", type=float, default=0.14, help="Keep candidate if best_score - candidate_score <= margin")
    parser.add_argument("--min-candidate-score", type=float, default=0.25)
    parser.add_argument("--poi-priors", default=None, help="JSON from export_poi_priors.py")
    parser.add_argument("--drop-gate", type=float, default=260.0, help="Early-match gate around team POI/drop point in full-frame px")
    parser.add_argument("--drop-prior-seconds", type=float, default=90.0, help="How long to use POI prior strongly")
    parser.add_argument("--left-ignore", type=int, default=420)
    parser.add_argument("--roi-size", type=int, default=1080)
    parser.add_argument("--keep-unknown", action="store_true")
    build_tracks(parser.parse_args())
