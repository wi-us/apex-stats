import argparse
import csv
import json
import random
import shutil
from collections import Counter
from pathlib import Path

import cv2

from slot_utils import crop_with_padding, detect_color_distortion, median_hsv_for_crop


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def find_images(images_dir: Path) -> dict[str, Path]:
    return {path.stem: path for path in images_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTS}


def parse_yolo_label(path: Path) -> list[tuple[int, float, float, float, float]]:
    rows = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            rows.append((int(float(parts[0])), float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])))
        except ValueError:
            continue
    return rows


def yolo_to_xyxy(row: tuple[int, float, float, float, float], width: int, height: int) -> tuple[float, float, float, float]:
    _, x, y, w, h = row
    return (
        (x - w / 2.0) * width,
        (y - h / 2.0) * height,
        (x + w / 2.0) * width,
        (y + h / 2.0) * height,
    )


def class_name(class_id: int) -> str:
    return f"SLOT_{class_id + 1:02d}"


def clear_output(out: Path) -> None:
    for rel in ["train", "val", "metadata"]:
        path = out / rel
        if path.exists():
            shutil.rmtree(path)
    (out / "metadata").mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an Ultralytics classification dataset from slot-labeled plate boxes.")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("datasets/slot_crop_v1"))
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=37)
    parser.add_argument("--padding", type=float, default=0.18)
    parser.add_argument("--min-width", type=int, default=10)
    parser.add_argument("--min-height", type=int, default=8)
    args = parser.parse_args()

    if not args.images.exists():
        raise FileNotFoundError(f"Images directory not found: {args.images}")
    if not args.labels.exists():
        raise FileNotFoundError(f"Labels directory not found: {args.labels}")

    images = find_images(args.images)
    label_paths = sorted(path for path in args.labels.rglob("*.txt") if path.name.lower() != "classes.txt")
    pairs = [(images[label.stem], label) for label in label_paths if label.stem in images]
    if not pairs:
        raise RuntimeError("No image/label pairs found")

    random.Random(args.seed).shuffle(pairs)
    val_count = max(1, int(round(len(pairs) * args.val_ratio))) if len(pairs) > 1 else 0
    val_stems = {label.stem for _, label in pairs[:val_count]}

    args.out.mkdir(parents=True, exist_ok=True)
    clear_output(args.out)

    metadata_path = args.out / "metadata" / "crops.csv"
    class_counts: Counter = Counter()
    split_counts: Counter = Counter()
    crop_count = 0
    skipped = 0

    with metadata_path.open("w", encoding="utf-8", newline="") as meta_file:
        writer = csv.DictWriter(
            meta_file,
            fieldnames=[
                "crop_path",
                "split",
                "slot_id",
                "class_id",
                "source_image",
                "source_label",
                "bbox_original",
                "bbox_crop",
                "median_hsv",
                "color_distortion",
            ],
        )
        writer.writeheader()

        for image_path, label_path in pairs:
            image = cv2.imread(str(image_path))
            if image is None:
                skipped += 1
                continue
            height, width = image.shape[:2]
            split = "val" if label_path.stem in val_stems else "train"
            labels = parse_yolo_label(label_path)

            for idx, row in enumerate(labels):
                class_id = row[0]
                if class_id < 0 or class_id > 19:
                    skipped += 1
                    continue
                xyxy = yolo_to_xyxy(row, width, height)
                crop, crop_box = crop_with_padding(image, xyxy, args.padding)
                if crop.size == 0 or crop.shape[1] < args.min_width or crop.shape[0] < args.min_height:
                    skipped += 1
                    continue

                slot_id = class_name(class_id)
                out_dir = args.out / split / slot_id
                out_dir.mkdir(parents=True, exist_ok=True)
                crop_name = f"{image_path.stem}_{idx:03d}_{slot_id}.jpg"
                crop_path = out_dir / crop_name
                cv2.imwrite(str(crop_path), crop)

                median_hsv = median_hsv_for_crop(crop)
                writer.writerow(
                    {
                        "crop_path": str(crop_path),
                        "split": split,
                        "slot_id": slot_id,
                        "class_id": class_id,
                        "source_image": str(image_path),
                        "source_label": str(label_path),
                        "bbox_original": " ".join(f"{v:.2f}" for v in xyxy),
                        "bbox_crop": " ".join(str(v) for v in crop_box),
                        "median_hsv": " ".join(f"{v:.2f}" for v in median_hsv),
                        "color_distortion": detect_color_distortion(crop),
                    }
                )
                class_counts[slot_id] += 1
                split_counts[split] += 1
                crop_count += 1

    stats = {
        "source_images": str(args.images.resolve()),
        "source_labels": str(args.labels.resolve()),
        "output": str(args.out.resolve()),
        "crops": crop_count,
        "skipped": skipped,
        "splits": dict(split_counts),
        "class_counts": dict(sorted(class_counts.items())),
        "format": "ultralytics_classify",
    }
    (args.out / "dataset_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Prepared crop dataset: {args.out.resolve()}")
    print(f"Crops: {crop_count}")
    print(f"Splits: train={split_counts['train']} val={split_counts['val']}")
    print(f"Skipped: {skipped}")
    print(f"Metadata: {metadata_path.resolve()}")


if __name__ == "__main__":
    main()
