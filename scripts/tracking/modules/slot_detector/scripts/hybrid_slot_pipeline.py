import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from slot_utils import (
    crop_with_padding,
    detect_color_distortion,
    hsv_slot_candidates,
    load_slot_profiles,
    load_slot_styles,
    median_hsv_for_crop,
    readable_text_color,
    resolve_device,
    slot_style,
)


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def default_detector_weights() -> Path:
    return Path(__file__).resolve().parents[1] / ".." / "plate_detector" / "runs" / "detect" / "runs" / "team_plate_v2_hardneg_cpu" / "weights" / "best.pt"


def slot_id_from_name(name: str, class_id: int) -> str:
    text = str(name).upper().replace(" ", "_")
    if text.startswith("SLOT_"):
        return text
    return f"SLOT_{class_id + 1:02d}"


def classifier_topk(model: YOLO, crop: np.ndarray, device: str, imgsz: int, top_k: int) -> list[dict]:
    if crop.size == 0:
        return []
    result = model.predict(source=crop, imgsz=imgsz, device=device, verbose=False)[0]
    if result.probs is None:
        return []
    probs = result.probs
    top_indices = probs.top5[:top_k]
    top_scores = probs.top5conf.cpu().numpy().tolist()[:top_k]
    out = []
    for class_id, score in zip(top_indices, top_scores):
        out.append(
            {
                "class_id": int(class_id),
                "slot_id": slot_id_from_name(model.names.get(int(class_id), ""), int(class_id)),
                "score": float(score),
            }
        )
    return out


def center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


class SlotHistory:
    def __init__(self, max_distance: float = 48.0, hold_frames: int = 3):
        self.max_distance = max_distance
        self.hold_frames = hold_frames
        self.items: list[dict] = []

    def nearest(self, box: tuple[float, float, float, float]) -> dict | None:
        cx, cy = center(box)
        best = None
        best_dist = self.max_distance
        for item in self.items:
            px, py = item["center"]
            dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
            if dist < best_dist:
                best = item
                best_dist = dist
        return best

    def choose(self, box: tuple[float, float, float, float], proposed_slot: str, proposed_conf: float, color_distortion: str) -> tuple[str, str]:
        prev = self.nearest(box)
        if prev and color_distortion in {"red_zone", "white_zone", "damage_flash"} and proposed_slot != prev["slot_id"]:
            if proposed_conf < 0.78 or prev["seen"] >= self.hold_frames:
                return prev["slot_id"], "history"
        return proposed_slot, "classifier"

    def update(self, box: tuple[float, float, float, float], slot_id: str, confidence: float) -> None:
        cx, cy = center(box)
        prev = self.nearest(box)
        if prev:
            prev["center"] = (cx, cy)
            prev["slot_id"] = slot_id
            prev["confidence"] = confidence
            prev["seen"] += 1
        else:
            self.items.append({"center": (cx, cy), "slot_id": slot_id, "confidence": confidence, "seen": 1})
        self.items = self.items[-80:]


def combine_identity(
    classifier_candidates: list[dict],
    hsv_candidates: list[dict],
    color_distortion: str,
    history: SlotHistory,
    box: tuple[float, float, float, float],
    min_conf: float,
) -> dict:
    if not classifier_candidates:
        return {
            "slot_id": "",
            "slot_conf": 0.0,
            "identity_source": "conflict",
            "top3": [],
        }

    best = classifier_candidates[0]
    proposed_slot = best["slot_id"]
    proposed_conf = float(best["score"])
    source = "classifier"

    hsv_best = hsv_candidates[0] if hsv_candidates else None
    if hsv_best and hsv_best["slot_id"] == proposed_slot and color_distortion == "unknown":
        proposed_conf = min(1.0, proposed_conf * 0.78 + float(hsv_best["score"]) * 0.22 + 0.08)
        source = "classifier+hsv"
    elif hsv_best and hsv_best["score"] > 0.82 and proposed_conf < 0.55 and color_distortion == "unknown":
        source = "conflict"

    final_slot, history_source = history.choose(box, proposed_slot, proposed_conf, color_distortion)
    if history_source == "history":
        source = "history"
        proposed_slot = final_slot

    if proposed_conf < min_conf:
        source = "conflict"

    return {
        "slot_id": proposed_slot,
        "slot_conf": proposed_conf,
        "identity_source": source,
        "top3": classifier_candidates[:3],
    }


def draw_visual(image: np.ndarray, rows: list[dict], styles: dict[int, dict]) -> np.ndarray:
    out = image.copy()
    for row in rows:
        x1, y1, x2, y2 = [int(round(float(v))) for v in row["bbox_original"].split()]
        class_id = max(0, int(row["slot_id"].split("_")[-1]) - 1) if row["slot_id"] else 0
        style = slot_style(class_id, styles)
        color = style["color"] if row["identity_source"] != "conflict" else (128, 128, 128)
        text_color = readable_text_color(color)
        label = f"{row['slot_id']} {float(row['slot_conf']):.2f} {row['identity_source']}"
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        y0 = max(0, y1 - th - 8)
        cv2.rectangle(out, (x1, y0), (min(out.shape[1] - 1, x1 + tw + 6), y0 + th + 6), color, -1)
        cv2.putText(out, label, (x1 + 3, y0 + th + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, text_color, 1, cv2.LINE_AA)
    return out


def iter_sources(source: Path, sample_fps: float):
    if source.is_dir():
        for path in sorted(source.rglob("*")):
            if path.suffix.lower() in IMAGE_EXTS:
                image = cv2.imread(str(path))
                if image is not None:
                    yield str(path), 0, 0.0, image
    elif source.suffix.lower() in IMAGE_EXTS:
        image = cv2.imread(str(source))
        if image is not None:
            yield str(source), 0, 0.0, image
    elif source.suffix.lower() in VIDEO_EXTS:
        cap = cv2.VideoCapture(str(source))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps / sample_fps)))
        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % step == 0:
                yield str(source), frame_idx, frame_idx / fps, frame
            frame_idx += 1
        cap.release()
    else:
        raise ValueError(f"Unsupported source: {source}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hybrid plate detector + crop slot classifier pipeline.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--detector-weights", type=Path, default=default_detector_weights())
    parser.add_argument("--classifier-weights", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("outputs/hybrid_slots"))
    parser.add_argument("--det-imgsz", type=int, default=960)
    parser.add_argument("--cls-imgsz", type=int, default=96)
    parser.add_argument("--det-conf", type=float, default=0.25)
    parser.add_argument("--det-iou", type=float, default=0.45)
    parser.add_argument("--slot-min-conf", type=float, default=0.28)
    parser.add_argument("--padding", type=float, default=0.18)
    parser.add_argument("--sample-fps", type=float, default=1.0)
    parser.add_argument("--max-per-slot", type=int, default=3)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--color-profile", type=Path)
    parser.add_argument("--save-crops", action="store_true")
    args = parser.parse_args()

    if not args.detector_weights.exists():
        raise FileNotFoundError(f"Detector weights not found: {args.detector_weights}")
    if not args.classifier_weights.exists():
        raise FileNotFoundError(f"Classifier weights not found: {args.classifier_weights}")

    ensure_dir(args.out)
    visuals_dir = args.out / "visuals"
    crops_dir = args.out / "crops"
    review_dir = args.out / "review" / "conflicts"
    ensure_dir(visuals_dir)
    ensure_dir(review_dir)
    if args.save_crops:
        ensure_dir(crops_dir)

    device = resolve_device(args.device)
    print(f"Using device: {device}")

    detector = YOLO(str(args.detector_weights))
    classifier = YOLO(str(args.classifier_weights))
    profiles = load_slot_profiles(args.color_profile)
    styles = load_slot_styles(args.color_profile)
    history = SlotHistory()

    csv_path = args.out / "hybrid_predictions.csv"
    jsonl_path = args.out / "hybrid_predictions.jsonl"
    fieldnames = [
        "source",
        "frame_idx",
        "time_sec",
        "bbox_original",
        "det_conf",
        "slot_id",
        "slot_conf",
        "top3",
        "hsv_candidates",
        "median_hsv",
        "identity_source",
        "is_color_distorted",
    ]

    total = 0
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file, jsonl_path.open("w", encoding="utf-8") as jsonl_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        for source_name, frame_idx, time_sec, frame in iter_sources(args.source, args.sample_fps):
            det_result = detector.predict(
                source=frame,
                imgsz=args.det_imgsz,
                conf=args.det_conf,
                iou=args.det_iou,
                device=device,
                verbose=False,
            )[0]

            frame_rows: list[dict] = []
            if det_result.boxes is not None:
                boxes = det_result.boxes.xyxy.cpu().numpy()
                confs = det_result.boxes.conf.cpu().numpy()
                candidates: list[dict] = []

                for det_idx, (box, det_conf) in enumerate(zip(boxes, confs)):
                    xyxy = tuple(float(v) for v in box)
                    crop, crop_box = crop_with_padding(frame, xyxy, args.padding)
                    median_hsv = median_hsv_for_crop(crop)
                    distortion = detect_color_distortion(crop)
                    hsv_candidates = hsv_slot_candidates(median_hsv, profiles)
                    cls_candidates = classifier_topk(classifier, crop, device, args.cls_imgsz, 3)
                    identity = combine_identity(cls_candidates, hsv_candidates, distortion, history, xyxy, args.slot_min_conf)
                    history.update(xyxy, identity["slot_id"], identity["slot_conf"])

                    row = {
                        "source": source_name,
                        "frame_idx": frame_idx,
                        "time_sec": round(time_sec, 3),
                        "bbox_original": " ".join(f"{v:.2f}" for v in xyxy),
                        "det_conf": round(float(det_conf), 4),
                        "slot_id": identity["slot_id"],
                        "slot_conf": round(float(identity["slot_conf"]), 4),
                        "top3": json.dumps(identity["top3"], ensure_ascii=False),
                        "hsv_candidates": json.dumps(hsv_candidates, ensure_ascii=False),
                        "median_hsv": " ".join(f"{v:.2f}" for v in median_hsv),
                        "identity_source": identity["identity_source"],
                        "is_color_distorted": distortion,
                        "_crop": crop,
                        "_det_idx": det_idx,
                    }
                    candidates.append(row)

                if args.max_per_slot > 0:
                    by_slot: dict[str, list[dict]] = {}
                    for row in candidates:
                        by_slot.setdefault(str(row["slot_id"]), []).append(row)
                    filtered: list[dict] = []
                    for slot_id, slot_rows in by_slot.items():
                        slot_rows.sort(key=lambda r: (float(r["slot_conf"]), float(r["det_conf"])), reverse=True)
                        filtered.extend(slot_rows[: args.max_per_slot])
                    candidates = filtered

                for row in candidates:
                    public_row = {k: row[k] for k in fieldnames}
                    writer.writerow(public_row)
                    jsonl_file.write(json.dumps(public_row, ensure_ascii=False) + "\n")
                    frame_rows.append(public_row)
                    if args.save_crops:
                        safe_slot = row["slot_id"] or "CONFLICT"
                        crop_name = f"{Path(source_name).stem}_f{frame_idx:07d}_{row['_det_idx']:02d}_{safe_slot}.jpg"
                        cv2.imwrite(str(crops_dir / crop_name), row["_crop"])
                    if row["identity_source"] == "conflict":
                        conflict_name = f"{Path(source_name).stem}_f{frame_idx:07d}_{row['_det_idx']:02d}_conflict.jpg"
                        cv2.imwrite(str(review_dir / conflict_name), row["_crop"])
                    total += 1

            if frame_rows:
                visual = draw_visual(frame, frame_rows, styles)
                visual_name = f"{Path(source_name).stem}_f{frame_idx:07d}_hybrid.jpg"
                cv2.imwrite(str(visuals_dir / visual_name), visual)

    print(f"Wrote {total} hybrid detections")
    print(f"CSV: {csv_path.resolve()}")
    print(f"JSONL: {jsonl_path.resolve()}")
    print(f"Visuals: {visuals_dir.resolve()}")
    print(f"Conflicts: {review_dir.resolve()}")


if __name__ == "__main__":
    main()
