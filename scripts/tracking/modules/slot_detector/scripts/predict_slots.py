import argparse
import csv
import json
from pathlib import Path

import cv2
from ultralytics import YOLO

from slot_utils import load_slot_styles, readable_text_color, resolve_device, slot_style


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def draw_result(image, rows: list[dict], styles: dict[int, dict]) -> object:
    out = image.copy()
    for row in rows:
        x1, y1, x2, y2 = [int(round(float(value))) for value in row["xyxy"].split()]
        class_id = int(row["class_id"])
        style = slot_style(class_id, styles)
        color = style["color"]
        label = f"S{style['hud_index']:02d} {style['label']} {float(row['conf']):.2f}"
        text_color = readable_text_color(color)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
        y0 = max(0, y1 - th - 8)
        cv2.rectangle(out, (x1, y0), (x1 + tw + 6, y0 + th + 6), color, -1)
        cv2.putText(out, label, (x1 + 3, y0 + th + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.48, text_color, 1, cv2.LINE_AA)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict team plate slots with a trained YOLO model.")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--source", required=True, help="Image, video, folder, or glob accepted by Ultralytics")
    parser.add_argument("--out", type=Path, default=Path("outputs/slot_predictions"))
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--color-profile", type=Path, help="Optional project color profile JSON")
    parser.add_argument("--save-visuals", action="store_true", default=True)
    args = parser.parse_args()

    if not args.weights.exists():
        raise FileNotFoundError(f"Weights not found: {args.weights}")

    ensure_dir(args.out)
    visuals_dir = args.out / "visuals"
    ensure_dir(visuals_dir)

    device = resolve_device(args.device)
    print(f"Using device: {device}")
    styles = load_slot_styles(args.color_profile)

    model = YOLO(str(args.weights))
    names = model.names

    fieldnames = ["source", "class_id", "slot", "conf", "xyxy", "xywhn"]
    csv_path = args.out / "predictions.csv"
    jsonl_path = args.out / "predictions.jsonl"

    rows_written = 0
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file, jsonl_path.open("w", encoding="utf-8") as jsonl_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        results = model.predict(
            source=args.source,
            imgsz=args.imgsz,
            conf=args.conf,
            iou=args.iou,
            device=device,
            verbose=False,
            stream=True,
        )

        for result in results:
            result_rows: list[dict] = []
            if result.boxes is not None:
                boxes_xyxy = result.boxes.xyxy.cpu().numpy()
                boxes_xywhn = result.boxes.xywhn.cpu().numpy()
                confs = result.boxes.conf.cpu().numpy()
                classes = result.boxes.cls.cpu().numpy().astype(int)

                for class_id, conf, xyxy, xywhn in zip(classes, confs, boxes_xyxy, boxes_xywhn):
                    row = {
                        "source": str(result.path),
                        "class_id": int(class_id),
                        "slot": str(names.get(int(class_id), f"slot_{int(class_id):02d}")),
                        "conf": float(conf),
                        "xyxy": " ".join(f"{float(value):.3f}" for value in xyxy),
                        "xywhn": " ".join(f"{float(value):.6f}" for value in xywhn),
                    }
                    writer.writerow(row)
                    jsonl_file.write(json.dumps(row, ensure_ascii=False) + "\n")
                    result_rows.append(row)
                    rows_written += 1

            if args.save_visuals and result.orig_img is not None:
                image = draw_result(result.orig_img, result_rows, styles)
                out_name = Path(str(result.path)).stem + "_slots.jpg"
                cv2.imwrite(str(visuals_dir / out_name), image)

    print(f"Wrote {rows_written} detections")
    print(f"CSV: {csv_path.resolve()}")
    print(f"JSONL: {jsonl_path.resolve()}")
    print(f"Visuals: {visuals_dir.resolve()}")


if __name__ == "__main__":
    main()
