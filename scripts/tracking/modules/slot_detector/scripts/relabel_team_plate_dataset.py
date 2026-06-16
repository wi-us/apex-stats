import argparse
import csv
from pathlib import Path

import cv2
from ultralytics import YOLO

from slot_utils import load_slot_styles, readable_text_color, resolve_device, slot_style


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def yolo_to_xyxy(parts: list[str], width: int, height: int) -> tuple[float, float, float, float]:
    x, y, w, h = [float(value) for value in parts[1:5]]
    x1 = (x - w / 2.0) * width
    y1 = (y - h / 2.0) * height
    x2 = (x + w / 2.0) * width
    y2 = (y + h / 2.0) * height
    return x1, y1, x2, y2


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def find_images(images_dir: Path) -> dict[str, Path]:
    return {path.stem: path for path in images_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTS}


def infer_output_root(out_labels: Path) -> Path:
    if out_labels.name in {"train", "val"} and out_labels.parent.name == "labels":
        return out_labels.parent.parent
    if out_labels.name == "labels":
        return out_labels.parent
    return out_labels.parent


def draw_labels(image, boxes: list[dict], styles: dict[int, dict], draw_rejected: bool):
    out = image.copy()
    for box in boxes:
        if not box["save"] and not draw_rejected:
            continue
        x1, y1, x2, y2 = [int(round(value)) for value in box["xyxy"]]
        class_id = int(box["class_id"])
        is_kept_match = box["status"] == "matched"
        style = slot_style(class_id, styles) if class_id >= 0 else {"hud_index": 0, "label": "UNMATCHED", "color": (160, 160, 160)}
        color = style["color"] if is_kept_match else (160, 160, 160)
        text_color = readable_text_color(color)
        if class_id >= 0:
            label = f"S{style['hud_index']:02d} {style['label']} {box['status']} {box['slot_conf']:.2f}"
        else:
            label = f"UNMATCHED {box['status']}"
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
        y0 = max(0, y1 - th - 8)
        cv2.rectangle(out, (x1, y0), (min(out.shape[1] - 1, x1 + tw + 6), y0 + th + 6), color, -1)
        cv2.putText(out, label, (x1 + 3, y0 + th + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.48, text_color, 1, cv2.LINE_AA)
    return out


def save_review_crop(image, xyxy: tuple[float, float, float, float], out_path: Path) -> bool:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = [int(round(value)) for value in xyxy]
    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(0, min(width, x2))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(out_path), image[y1:y2, x1:x2]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Replace one-class team_plate YOLO labels with slot classes predicted by slot model.")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out-labels", type=Path, required=True)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--match-iou", type=float, default=0.35)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--unmatched-action", choices=["drop", "keep-old", "keep-class"], default="drop")
    parser.add_argument("--keep-unmatched-class", type=int, default=0)
    parser.add_argument("--max-per-slot", type=int, default=3, help="Maximum saved boxes per slot per image; 0 disables the limit")
    parser.add_argument("--out-visuals", type=Path, help="Annotated images output directory")
    parser.add_argument("--color-profile", type=Path, help="Optional project color profile JSON")
    parser.add_argument("--draw-rejected", action="store_true", help="Draw unmatched and dropped boxes on visual debug images")
    parser.add_argument("--save-unmatched-crops", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    if not args.weights.exists():
        raise FileNotFoundError(f"Weights not found: {args.weights}")
    if not args.images.exists():
        raise FileNotFoundError(f"Images directory not found: {args.images}")
    if not args.labels.exists():
        raise FileNotFoundError(f"Labels directory not found: {args.labels}")

    args.out_labels.mkdir(parents=True, exist_ok=True)
    output_root = infer_output_root(args.out_labels)
    default_visuals = output_root / "visuals" / args.out_labels.name if args.out_labels.name in {"train", "val"} else output_root / "visuals"
    out_visuals = args.out_visuals or default_visuals
    out_visuals.mkdir(parents=True, exist_ok=True)
    images = find_images(args.images)
    label_paths = sorted(path for path in args.labels.rglob("*.txt") if path.name.lower() != "classes.txt")
    device = resolve_device(args.device)
    print(f"Using device: {device}")
    styles = load_slot_styles(args.color_profile)
    model = YOLO(str(args.weights))

    report_path = output_root / "relabel_report.csv"
    unmatched_path = output_root / "unmatched.txt"
    review_unmatched_dir = output_root / "review" / "unmatched"

    total_boxes = 0
    matched_boxes = 0
    saved_boxes = 0
    dropped_unmatched = 0
    dropped_duplicates = 0
    missing_images: list[str] = []
    unmatched_rows: list[str] = []

    with report_path.open("w", encoding="utf-8", newline="") as report_file:
        writer = csv.DictWriter(
            report_file,
            fieldnames=["image", "label_line", "old_class", "new_class", "slot", "match_iou", "slot_conf", "status", "saved"],
        )
        writer.writeheader()

        for label_path in label_paths:
            image_path = images.get(label_path.stem)
            if image_path is None:
                missing_images.append(str(label_path))
                continue

            image = cv2.imread(str(image_path))
            if image is None:
                missing_images.append(str(image_path))
                continue
            height, width = image.shape[:2]

            result = model.predict(
                source=str(image_path),
                imgsz=args.imgsz,
                conf=args.conf,
                iou=args.iou,
                device=device,
                verbose=False,
            )[0]

            pred_boxes: list[dict] = []
            if result.boxes is not None:
                xyxy = result.boxes.xyxy.cpu().numpy()
                confs = result.boxes.conf.cpu().numpy()
                classes = result.boxes.cls.cpu().numpy().astype(int)
                for pred_box, conf, class_id in zip(xyxy, confs, classes):
                    pred_boxes.append(
                        {
                            "xyxy": tuple(float(value) for value in pred_box),
                            "conf": float(conf),
                            "class_id": int(class_id),
                            "slot": str(model.names.get(int(class_id), f"slot_{int(class_id):02d}")),
                        }
                    )

            records: list[dict] = []
            for line_no, raw_line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
                line = raw_line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue

                total_boxes += 1
                old_class = parts[0]
                old_box = yolo_to_xyxy(parts, width, height)
                best = None
                best_iou = 0.0
                for pred in pred_boxes:
                    score = iou(old_box, pred["xyxy"])
                    if score > best_iou:
                        best = pred
                        best_iou = score

                if best is not None and best_iou >= args.match_iou:
                    new_class = best["class_id"]
                    parts[0] = str(new_class)
                    matched_boxes += 1
                    status = "matched"
                    slot = best["slot"]
                    slot_conf = best["conf"]
                    save = True
                else:
                    if args.unmatched_action == "keep-old":
                        parts[0] = old_class
                        save = True
                    elif args.unmatched_action == "keep-class":
                        parts[0] = str(args.keep_unmatched_class)
                        save = True
                    else:
                        save = False
                    status = "unmatched"
                    slot = ""
                    slot_conf = 0.0
                    unmatched_rows.append(f"{image_path.name}:{line_no}:{line}")
                    dropped_unmatched += 1

                records.append(
                    {
                        "line": " ".join(parts),
                        "line_no": line_no,
                        "old_class": old_class,
                        "new_class": parts[0] if save or status == "matched" else "",
                        "slot": slot,
                        "match_iou": best_iou,
                        "slot_conf": float(slot_conf),
                        "status": status,
                        "save": save,
                        "xyxy": old_box,
                        "class_id": int(parts[0]) if save or status == "matched" else -1,
                    }
                )

            if args.max_per_slot > 0:
                by_class: dict[int, list[dict]] = {}
                for record in records:
                    if record["status"] == "matched" and record["save"]:
                        by_class.setdefault(int(record["new_class"]), []).append(record)
                for class_id, class_records in by_class.items():
                    class_records.sort(key=lambda item: (item["slot_conf"], item["match_iou"]), reverse=True)
                    for record in class_records[args.max_per_slot:]:
                        record["save"] = False
                        record["status"] = "dropped_duplicate"
                        dropped_duplicates += 1
                        unmatched_rows.append(f"{image_path.name}:{record['line_no']}:slot_{class_id:02d}:max_per_slot")

            new_lines = [record["line"] for record in records if record["save"]]
            saved_boxes += len(new_lines)

            for record in records:
                if args.save_unmatched_crops and record["status"] in {"unmatched", "dropped_duplicate"}:
                    reason_dir = review_unmatched_dir / record["status"]
                    crop_name = f"{image_path.stem}_line{record['line_no']:03d}_{record['status']}.jpg"
                    save_review_crop(image, record["xyxy"], reason_dir / crop_name)
                writer.writerow(
                    {
                        "image": str(image_path),
                        "label_line": record["line_no"],
                        "old_class": record["old_class"],
                        "new_class": record["new_class"],
                        "slot": record["slot"],
                        "match_iou": f"{record['match_iou']:.4f}",
                        "slot_conf": f"{record['slot_conf']:.4f}",
                        "status": record["status"],
                        "saved": "yes" if record["save"] else "no",
                    }
                )

            out_path = args.out_labels / label_path.name
            out_path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
            visual = draw_labels(image, records, styles, args.draw_rejected)
            cv2.imwrite(str(out_visuals / f"{image_path.stem}_slots.jpg"), visual)

    unmatched_text = []
    if missing_images:
        unmatched_text.append("[missing_images]")
        unmatched_text.extend(missing_images)
    if unmatched_rows:
        unmatched_text.append("[unmatched_boxes]")
        unmatched_text.extend(unmatched_rows)
    unmatched_path.write_text("\n".join(unmatched_text), encoding="utf-8")

    print(f"Relabeled boxes: {matched_boxes}/{total_boxes}")
    print(f"Saved boxes: {saved_boxes}/{total_boxes}")
    print(f"Dropped unmatched: {dropped_unmatched}")
    print(f"Dropped duplicates: {dropped_duplicates}")
    print(f"Labels: {args.out_labels.resolve()}")
    print(f"Visuals: {out_visuals.resolve()}")
    print(f"Report: {report_path.resolve()}")
    print(f"Unmatched: {unmatched_path.resolve()}")
    if args.save_unmatched_crops:
        print(f"Review unmatched crops: {review_unmatched_dir.resolve()}")


if __name__ == "__main__":
    main()
