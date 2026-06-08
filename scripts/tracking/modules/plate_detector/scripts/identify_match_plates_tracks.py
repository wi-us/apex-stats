import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from tqdm import tqdm


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def round_list(values, ndigits: int = 1):
    if values is None:
        return None
    return [round(float(v), ndigits) for v in values]


def time_hms(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def central_roi(frame: np.ndarray, left_ignore: int = 420, roi_size: int = 1080) -> Tuple[np.ndarray, int, int]:
    h, w = frame.shape[:2]
    if w < roi_size or h < roi_size:
        raise ValueError(f"Frame is too small: {w}x{h}, need at least {roi_size}x{roi_size}")

    if w >= left_ignore * 2 + roi_size:
        x1 = left_ignore
    else:
        x1 = max(0, (w - roi_size) // 2)

    y1 = max(0, (h - roi_size) // 2)
    return frame[y1:y1 + roi_size, x1:x1 + roi_size], x1, y1


def hsv_to_hex(h: float, s: float, v: float) -> str:
    hsv = np.uint8([[[int(clamp(h, 0, 179)), int(clamp(s, 0, 255)), int(clamp(v, 0, 255))]]])
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    b, g, r = [int(x) for x in bgr]
    return f"#{r:02X}{g:02X}{b:02X}"


def extract_median_hsv_from_profile(profile: dict) -> Optional[Tuple[float, float, float]]:
    candidates = [
        profile.get("median_hsv"),
        profile.get("hsv_median"),
        profile.get("mean_hsv"),
        profile.get("hsv"),
    ]

    for c in candidates:
        if isinstance(c, list) and len(c) >= 3:
            return float(c[0]), float(c[1]), float(c[2])
        if isinstance(c, dict):
            for key in ["median", "mean", "value"]:
                v = c.get(key)
                if isinstance(v, list) and len(v) >= 3:
                    return float(v[0]), float(v[1]), float(v[2])

    # Fallback for range style profiles.
    h = profile.get("h")
    s = profile.get("s")
    v = profile.get("v")
    if isinstance(h, list) and isinstance(s, list) and isinstance(v, list):
        return (float(h[0] + h[1]) / 2, float(s[0] + s[1]) / 2, float(v[0] + v[1]) / 2)

    return None


def load_color_profiles(config_path: Path, color_profiles_path: Optional[Path]) -> List[dict]:
    config = load_json(config_path) if config_path.exists() else {}

    if color_profiles_path:
        data = load_json(color_profiles_path)
    else:
        data = config

    profiles = None
    if isinstance(data, dict):
        profiles = data.get("team_color_profiles") or data.get("profiles") or data.get("teams")
    elif isinstance(data, list):
        profiles = data

    if not profiles:
        raise RuntimeError("No team color profiles found. Run calibrate_hud_colors.py first or pass --color-profiles.")

    config_team_by_tag = {}
    for t in config.get("teams", []):
        for key in [t.get("tag"), t.get("name"), t.get("db_tag"), t.get("db_name")]:
            if key:
                config_team_by_tag[str(key).upper()] = t

    out = []
    for idx, p in enumerate(profiles, start=1):
        hsv = extract_median_hsv_from_profile(p)
        if hsv is None:
            continue

        hud_index = p.get("hud_index") or p.get("team_index") or p.get("slot") or idx
        try:
            hud_index = int(hud_index)
        except Exception:
            hud_index = idx

        tag = p.get("broadcast_tag") or p.get("team_tag") or p.get("tag") or p.get("short_name") or f"slot_{hud_index}"
        tag = str(tag)

        team_from_config = config_team_by_tag.get(tag.upper())
        team_id = p.get("team_id") or (team_from_config or {}).get("team_id") or f"slot_{hud_index}"
        team_name = p.get("team_name") or p.get("name") or (team_from_config or {}).get("name") or tag
        team_tag = p.get("matched_team_tag") or p.get("team_tag") or (team_from_config or {}).get("tag") or tag
        color_hex = p.get("color") or p.get("hex") or p.get("color_hex") or hsv_to_hex(*hsv)

        out.append({
            "slot": hud_index,
            "slot_id": f"slot_{hud_index}",
            "team_id": str(team_id),
            "name": str(team_name),
            "tag": str(team_tag),
            "broadcast_tag": tag,
            "color": color_hex,
            "median_hsv": [float(hsv[0]), float(hsv[1]), float(hsv[2])],
        })

    if not out:
        raise RuntimeError("No valid color profiles with HSV values found.")

    return sorted(out, key=lambda x: x["slot"])


def hue_dist(a: float, b: float) -> float:
    d = abs(a - b)
    return min(d, 180.0 - d)


def hsv_match_score(hsv_a: Tuple[float, float, float], hsv_b: Tuple[float, float, float]) -> float:
    h1, s1, v1 = hsv_a
    h2, s2, v2 = hsv_b

    dh = hue_dist(h1, h2) / 22.0
    ds = abs(s1 - s2) / 95.0
    dv = abs(v1 - v2) / 105.0

    dist = math.sqrt(dh * dh + ds * ds + dv * dv)
    return float(clamp(1.0 - dist, 0.0, 1.0))


def dominant_plate_hsv(crop: np.ndarray) -> Tuple[float, float, float]:
    if crop.size == 0:
        return 0.0, 0.0, 0.0

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Убираем белый текст и тёмные/серые области. Оставляем цветную подложку плашки.
    mask = (hsv[:, :, 1] > 45) & (hsv[:, :, 2] > 45) & ~((gray > 135) & (hsv[:, :, 1] < 180))

    if np.mean(mask) < 0.03:
        mask = (hsv[:, :, 1] > 25) & (hsv[:, :, 2] > 35)

    if np.mean(mask) < 0.01:
        values = hsv.reshape(-1, 3)
    else:
        values = hsv[mask]

    med = np.median(values, axis=0)
    return float(med[0]), float(med[1]), float(med[2])


def match_profile_by_hsv(hsv: Tuple[float, float, float], profiles: List[dict]) -> Tuple[Optional[dict], float]:
    best = None
    best_score = -1.0
    for p in profiles:
        score = hsv_match_score(hsv, tuple(p["median_hsv"]))
        if score > best_score:
            best = p
            best_score = score
    return best, float(best_score)


def should_reject_box(x1: int, y1: int, x2: int, y2: int, image_shape, args) -> Tuple[bool, str]:
    H, W = image_shape[:2]
    w = x2 - x1
    h = y2 - y1
    aspect = w / max(h, 1)

    if args.ignore_bottom_px > 0 and y2 > H - args.ignore_bottom_px:
        return True, "bottom_ui"
    if w < args.min_width:
        return True, "too_narrow"
    if w > args.max_width:
        return True, "too_wide"
    if h < args.min_height:
        return True, "too_low"
    if h > args.max_height:
        return True, "too_tall"
    if aspect < 0.55:
        return True, "aspect_low"
    if aspect > 13.0:
        return True, "aspect_high"
    return False, "ok"


def run_yolo(model, image: np.ndarray, args) -> List[dict]:
    result = model.predict(
        source=image,
        imgsz=args.imgsz,
        conf=args.conf,
        iou=args.iou,
        device=args.device,
        max_det=args.max_det,
        verbose=False,
    )[0]

    if result.boxes is None:
        return []

    xyxy = result.boxes.xyxy.cpu().numpy()
    confs = result.boxes.conf.cpu().numpy()
    H, W = image.shape[:2]

    detections = []
    for b, c in zip(xyxy, confs):
        x1, y1, x2, y2 = [int(round(v)) for v in b.tolist()]
        x1 = max(0, min(W - 1, x1))
        y1 = max(0, min(H - 1, y1))
        x2 = max(0, min(W, x2))
        y2 = max(0, min(H, y2))
        if x2 <= x1 or y2 <= y1:
            continue
        reject, reason = should_reject_box(x1, y1, x2, y2, image.shape, args)
        if reject:
            continue
        detections.append({"bbox": [x1, y1, x2, y2], "conf": float(c)})
    return detections


def aggregate_team_detections(team_dets: List[dict], mode: str) -> Optional[dict]:
    if not team_dets:
        return None

    if mode == "best":
        return max(team_dets, key=lambda d: d["final_score"])

    # mean aggregation: useful when 2-3 player plates of same team are visible.
    xs = []
    ys = []
    confs = []
    color_scores = []
    final_scores = []
    for d in team_dets:
        x1, y1, x2, y2 = d["bbox_original"]
        xs.append((x1 + x2) / 2)
        ys.append((y1 + y2) / 2)
        confs.append(d["det_conf"])
        color_scores.append(d["color_score"])
        final_scores.append(d["final_score"])

    best = max(team_dets, key=lambda d: d["final_score"])
    agg = dict(best)
    agg["frame_px"] = [float(np.mean(xs)), float(np.mean(ys))]
    agg["det_conf"] = float(np.mean(confs))
    agg["color_score"] = float(np.mean(color_scores))
    agg["final_score"] = float(np.mean(final_scores))
    agg["detections_count"] = len(team_dets)
    return agg


def make_meta(video_path: Path, fps: float, fps_processed: float, frame_count: int, profiles: List[dict], args) -> dict:
    teams = []
    slots = []

    for p in profiles:
        team_label = f"{p['broadcast_tag']} · {p['name']}"
        teams.append({
            "id": p["slot_id"],
            "name": team_label,
            "color": p["color"],
            "team_id": p["team_id"],
            "team_tag": p["tag"],
            "broadcast_tag": p["broadcast_tag"],
        })
        slots.append({
            "slot_id": p["slot_id"],
            "slot": p["slot"],
            "team_id": p["slot_id"],
            "name": team_label,
            "color": p["color"],
            "team_db_id": p["team_id"],
            "team_tag": p["tag"],
            "broadcast_tag": p["broadcast_tag"],
            "anchor_conf": "UNKNOWN",
            "anchor_world": None,
            "wiped_at_t": None,
        })

    return {
        "video": video_path.name,
        "fps_source": fps,
        "fps_processed": fps_processed,
        "frame_count": frame_count,
        "canonical_map": args.canonical_map,
        "canonical_size": [args.canonical_size, args.canonical_size],
        "world_bounds": {"x": [0, args.canonical_size], "y": [0, args.canonical_size]},
        "teams": teams,
        "slots": slots,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": 2,
        "da_strategy": "yolo_hud_color_profiles",
    }


def build_frame_record(frame_idx: int, time_sec: float, team_dets_by_slot: Dict[str, List[dict]], profiles: List[dict], args) -> dict:
    tracks = []

    for p in profiles:
        slot_id = p["slot_id"]
        agg = aggregate_team_detections(team_dets_by_slot.get(slot_id, []), args.aggregate)

        if agg is None:
            tracks.append({
                "team_id": slot_id,
                "slot_id": slot_id,
                "canonical_px": None,
                "frame_px": None,
                "state": "low_conf",
                "state_reason": "no_assignment",
                "mask_mode": "yolo+hsv_profile",
                "confidence": 0.0,
                "score": 0.0,
                "world": None,
            })
        else:
            tracks.append({
                "team_id": slot_id,
                "slot_id": slot_id,
                "canonical_px": None,
                "frame_px": round_list(agg["frame_px"], 1),
                "state": "tracked",
                "state_reason": f"yolo_color:{agg['detections_count']}_detections",
                "mask_mode": "yolo+hsv_profile",
                "confidence": round(float(agg["det_conf"]), 3),
                "score": round(float(agg["color_score"]), 3),
                "world": None,
            })

    return {
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
    }


def draw_debug(roi: np.ndarray, frame_dets: List[dict]) -> np.ndarray:
    out = roi.copy()
    for d in frame_dets:
        x1, y1, x2, y2 = d["bbox_roi"]
        tag = d.get("matched_broadcast_tag") or d.get("broadcast_tag") or "UNK"
        det_conf = float(d.get("det_conf", 0.0))
        color_score = float(d.get("color_score", 0.0))
        label = f"{tag} {det_conf:.2f}/{color_score:.2f}"
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(out, label, (x1, max(0, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)
    return out


def analyze_video(args) -> dict:
    from ultralytics import YOLO

    video_path = Path(args.video)
    config_path = Path(args.config)
    color_profiles_path = Path(args.color_profiles) if args.color_profiles else None
    profiles = load_color_profiles(config_path, color_profiles_path)

    model = YOLO(args.weights)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0:
        fps = args.assume_fps

    if args.frame_step > 0:
        frame_step = int(args.frame_step)
        fps_processed = fps / frame_step
    else:
        frame_step = max(1, int(round(fps / args.sample_fps)))
        fps_processed = fps / frame_step

    max_frame = frame_count
    if args.max_seconds > 0:
        max_frame = min(max_frame, int(round(args.max_seconds * fps)))

    # ВАЖНО: раньше цикл читал каждый кадр подряд и только обрабатывал каждый N-й.
    # Это выглядело так, будто --frame-step не работает, и было медленно.
    # Теперь мы явно прыгаем на нужные кадры: 0, 600, 1200...
    target_frames = list(range(0, max_frame, frame_step))

    frames = []
    all_flat_detections = []

    debug_dir = Path(args.out).with_suffix("") / "debug"
    if args.save_debug:
        ensure_dir(debug_dir)
        if args.clear_debug:
            for old in debug_dir.glob("*.jpg"):
                old.unlink()

    pbar = tqdm(total=len(target_frames), desc=f"analyze video step={frame_step}")

    for frame_idx in target_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            pbar.update(1)
            continue

        actual_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        if actual_frame < 0:
            actual_frame = frame_idx

        # Для тайминга используем запрошенный frame_idx, а не actual_frame.
        # actual_frame нужен только для диагностики, если OpenCV прыгнул не идеально.
        roi, roi_x, roi_y = central_roi(frame, args.left_ignore, args.roi_size)
        yolo_dets = run_yolo(model, roi, args)
        time_sec = frame_idx / fps

        team_dets_by_slot: Dict[str, List[dict]] = {p["slot_id"]: [] for p in profiles}
        frame_debug_dets = []

        for yd in yolo_dets:
            x1, y1, x2, y2 = yd["bbox"]
            crop = roi[y1:y2, x1:x2]
            plate_hsv = dominant_plate_hsv(crop)
            profile, color_score = match_profile_by_hsv(plate_hsv, profiles)
            if profile is None or color_score < args.min_color_score:
                if not args.keep_unknown:
                    continue
                profile = {
                    "slot_id": "unknown",
                    "slot": None,
                    "team_id": None,
                    "name": None,
                    "tag": None,
                    "broadcast_tag": "UNK",
                    "color": "#000000",
                }

            bbox_original = [x1 + roi_x, y1 + roi_y, x2 + roi_x, y2 + roi_y]
            frame_px = [(bbox_original[0] + bbox_original[2]) / 2, (bbox_original[1] + bbox_original[3]) / 2]
            final_score = float(yd["conf"] * color_score)

            det = {
                "source": video_path.name,
                "frame_idx": frame_idx,
                "actual_frame_idx": actual_frame,
                "video_time_sec": round(time_sec, 3),
                "video_time_hms": time_hms(time_sec),
                "bbox_roi": [x1, y1, x2, y2],
                "bbox_original": bbox_original,
                "frame_px": frame_px,
                "det_conf": float(yd["conf"]),
                "plate_hsv": round_list(plate_hsv, 1),
                "matched_slot": profile.get("slot"),
                "matched_slot_id": profile.get("slot_id"),
                "matched_broadcast_tag": profile.get("broadcast_tag"),
                "matched_team_id": profile.get("team_id"),
                "matched_team_name": profile.get("name"),
                "matched_team_tag": profile.get("tag"),
                "color_score": float(color_score),
                "final_score": final_score,
                "identity_source": "hud_color" if profile.get("slot_id") != "unknown" else "unknown",
                "detections_count": 1,
            }

            all_flat_detections.append(det)
            frame_debug_dets.append(det)
            if profile.get("slot_id") in team_dets_by_slot:
                team_dets_by_slot[profile["slot_id"]].append(det)

        frame_record = build_frame_record(frame_idx, time_sec, team_dets_by_slot, profiles, args)
        frame_record["actual_frame"] = actual_frame
        frames.append(frame_record)

        if args.save_debug:
            dbg = draw_debug(roi, frame_debug_dets)
            cv2.imwrite(str(debug_dir / f"{video_path.stem}_frame_{frame_idx:07d}.jpg"), dbg, [cv2.IMWRITE_JPEG_QUALITY, 92])

        pbar.update(1)

    pbar.close()
    cap.release()

    meta = make_meta(video_path, fps, fps_processed, frame_count, profiles, args)
    meta["frame_step"] = frame_step
    meta["processed_frame_count"] = len(frames)
    result = {"meta": meta, "frames": frames}

    out_path = Path(args.out)
    save_json(out_path, result)

    if args.save_detections_jsonl:
        jsonl_path = out_path.with_name(out_path.stem + ".detections.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for d in all_flat_detections:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print(f"Flat detections saved: {jsonl_path.resolve()}")

    print(f"Tracks JSON saved: {out_path.resolve()}")
    print(f"Processed frames: {len(frames)}")
    print(f"Frame step: {frame_step}")
    print(f"fps_source: {fps:.6f}")
    print(f"fps_processed: {fps_processed:.6f}")
    return result


def analyze_images(args) -> dict:
    from ultralytics import YOLO

    images_dir = Path(args.images_dir)
    config_path = Path(args.config)
    color_profiles_path = Path(args.color_profiles) if args.color_profiles else None
    profiles = load_color_profiles(config_path, color_profiles_path)
    model = YOLO(args.weights)

    images = [p for p in sorted(images_dir.rglob("*")) if p.suffix.lower() in IMAGE_EXTS]
    if not images:
        raise RuntimeError(f"No images found: {images_dir}")

    frames = []
    all_flat_detections = []
    debug_dir = Path(args.out).with_suffix("") / "debug"
    if args.save_debug:
        ensure_dir(debug_dir)

    for idx, img_path in enumerate(tqdm(images, desc="analyze images")):
        img = cv2.imread(str(img_path))
        if img is None:
            continue

        if img.shape[0] == args.roi_size and img.shape[1] == args.roi_size:
            roi, roi_x, roi_y = img, 0, 0
        else:
            roi, roi_x, roi_y = central_roi(img, args.left_ignore, args.roi_size)

        # frame index is parsed from filename when possible.
        import re
        m = re.search(r"frame_(\d+)", img_path.stem)
        frame_idx = int(m.group(1)) if m else idx
        time_sec = frame_idx / args.assume_fps

        yolo_dets = run_yolo(model, roi, args)
        team_dets_by_slot: Dict[str, List[dict]] = {p["slot_id"]: [] for p in profiles}
        frame_debug_dets = []

        for yd in yolo_dets:
            x1, y1, x2, y2 = yd["bbox"]
            crop = roi[y1:y2, x1:x2]
            plate_hsv = dominant_plate_hsv(crop)
            profile, color_score = match_profile_by_hsv(plate_hsv, profiles)
            if profile is None or color_score < args.min_color_score:
                if not args.keep_unknown:
                    continue
                profile = {"slot_id": "unknown", "slot": None, "team_id": None, "name": None, "tag": None, "broadcast_tag": "UNK", "color": "#000000"}

            bbox_original = [x1 + roi_x, y1 + roi_y, x2 + roi_x, y2 + roi_y]
            frame_px = [(bbox_original[0] + bbox_original[2]) / 2, (bbox_original[1] + bbox_original[3]) / 2]
            final_score = float(yd["conf"] * color_score)
            det = {
                "source": img_path.name,
                "frame_idx": frame_idx,
                "video_time_sec": round(time_sec, 3),
                "video_time_hms": time_hms(time_sec),
                "bbox_roi": [x1, y1, x2, y2],
                "bbox_original": bbox_original,
                "frame_px": frame_px,
                "det_conf": float(yd["conf"]),
                "plate_hsv": round_list(plate_hsv, 1),
                "matched_slot": profile.get("slot"),
                "matched_slot_id": profile.get("slot_id"),
                "matched_broadcast_tag": profile.get("broadcast_tag"),
                "matched_team_id": profile.get("team_id"),
                "matched_team_name": profile.get("name"),
                "matched_team_tag": profile.get("tag"),
                "color_score": float(color_score),
                "final_score": final_score,
                "identity_source": "hud_color" if profile.get("slot_id") != "unknown" else "unknown",
                "detections_count": 1,
            }
            all_flat_detections.append(det)
            frame_debug_dets.append(det)
            if profile.get("slot_id") in team_dets_by_slot:
                team_dets_by_slot[profile["slot_id"]].append(det)

        frames.append(build_frame_record(frame_idx, time_sec, team_dets_by_slot, profiles, args))

        if args.save_debug:
            dbg = draw_debug(roi, frame_debug_dets)
            cv2.imwrite(str(debug_dir / img_path.name), dbg, [cv2.IMWRITE_JPEG_QUALITY, 92])

    meta = make_meta(images_dir, args.assume_fps, 0.0, len(images), profiles, args)
    meta["video"] = str(images_dir)
    result = {"meta": meta, "frames": frames}
    out_path = Path(args.out)
    save_json(out_path, result)

    if args.save_detections_jsonl:
        jsonl_path = out_path.with_name(out_path.stem + ".detections.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for d in all_flat_detections:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print(f"Flat detections saved: {jsonl_path.resolve()}")

    print(f"Tracks JSON saved: {out_path.resolve()}")
    print(f"Processed images: {len(frames)}")
    return result


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--weights", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--color-profiles", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.20)
    p.add_argument("--iou", type=float, default=0.55)
    p.add_argument("--device", default="cpu")
    p.add_argument("--max-det", type=int, default=120)
    p.add_argument("--left-ignore", type=int, default=420)
    p.add_argument("--roi-size", type=int, default=1080)
    p.add_argument("--ignore-bottom-px", type=int, default=95)
    p.add_argument("--min-color-score", type=float, default=0.32)
    p.add_argument("--keep-unknown", action="store_true")
    p.add_argument("--aggregate", choices=["mean", "best"], default="mean")
    p.add_argument("--min-width", type=int, default=18)
    p.add_argument("--max-width", type=int, default=360)
    p.add_argument("--min-height", type=int, default=10)
    p.add_argument("--max-height", type=int, default=58)
    p.add_argument("--canonical-map", default=None)
    p.add_argument("--canonical-size", type=int, default=2048)
    p.add_argument("--save-debug", action="store_true")
    p.add_argument("--clear-debug", action="store_true", help="Delete old debug jpg files before saving new debug frames")
    p.add_argument("--save-detections-jsonl", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YOLO + HUD-color team identification. Outputs tracks.json-style schema.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze-video")
    p.add_argument("--video", required=True)
    p.add_argument("--sample-fps", type=float, default=1.0)
    p.add_argument("--frame-step", type=int, default=0, help="Exact frame step. Example: 600 = analyze every 600th frame")
    p.add_argument("--max-seconds", type=float, default=0.0)
    p.add_argument("--assume-fps", type=float, default=60.0)
    add_common_args(p)
    p.set_defaults(func=analyze_video)

    p = sub.add_parser("analyze-images")
    p.add_argument("--images-dir", required=True)
    p.add_argument("--assume-fps", type=float, default=60.0)
    add_common_args(p)
    p.set_defaults(func=analyze_images)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
