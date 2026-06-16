import argparse
import json
import random
import shutil
from collections import Counter
from pathlib import Path


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def find_images(images_dir: Path) -> dict[str, Path]:
    return {path.stem: path for path in images_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTS}


def label_has_boxes(path: Path) -> bool:
    return any(line.strip() for line in path.read_text(encoding="utf-8").splitlines())


def label_classes(path: Path) -> list[int]:
    classes: list[int] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        try:
            classes.append(int(float(parts[0])))
        except (ValueError, IndexError):
            continue
    return classes


def clear_dataset_dirs(out: Path) -> None:
    for rel in ["images/train", "images/val", "labels/train", "labels/val"]:
        path = out / rel
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


def write_data_yaml(out: Path, class_count: int) -> None:
    names = "\n".join(f'  {idx}: "slot_{idx:02d}"' for idx in range(class_count))
    (out / "data.yaml").write_text(
        "\n".join(
            [
                f"path: {out.resolve().as_posix()}",
                "train: images/train",
                "val: images/val",
                "",
                "names:",
                names,
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build YOLO dataset from relabeled slot labels.")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("datasets/slot_plates_relabel_v1"))
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=37)
    parser.add_argument("--class-count", type=int, default=20)
    parser.add_argument("--include-empty", action="store_true", help="Include images with empty label files as backgrounds")
    args = parser.parse_args()

    if not args.images.exists():
        raise FileNotFoundError(f"Images directory not found: {args.images}")
    if not args.labels.exists():
        raise FileNotFoundError(f"Labels directory not found: {args.labels}")

    images = find_images(args.images)
    labels = sorted(path for path in args.labels.rglob("*.txt") if path.name.lower() != "classes.txt")

    pairs: list[tuple[Path, Path]] = []
    skipped_empty: list[str] = []
    missing_images: list[str] = []
    class_counts: Counter = Counter()

    for label_path in labels:
        image_path = images.get(label_path.stem)
        if image_path is None:
            missing_images.append(str(label_path))
            continue
        if not args.include_empty and not label_has_boxes(label_path):
            skipped_empty.append(str(label_path))
            continue
        pairs.append((image_path, label_path))
        class_counts.update(label_classes(label_path))

    if not pairs:
        raise RuntimeError("No image/label pairs found for the relabeled dataset")

    random.Random(args.seed).shuffle(pairs)
    val_count = max(1, int(round(len(pairs) * args.val_ratio))) if len(pairs) > 1 else 0
    val_stems = {label.stem for _, label in pairs[:val_count]}

    args.out.mkdir(parents=True, exist_ok=True)
    clear_dataset_dirs(args.out)

    split_counts: Counter = Counter()
    for image_path, label_path in pairs:
        split = "val" if label_path.stem in val_stems else "train"
        shutil.copy2(image_path, args.out / "images" / split / image_path.name)
        shutil.copy2(label_path, args.out / "labels" / split / label_path.name)
        split_counts[split] += 1

    write_data_yaml(args.out, args.class_count)

    stats = {
        "source_images": str(args.images.resolve()),
        "source_labels": str(args.labels.resolve()),
        "output": str(args.out.resolve()),
        "pairs": len(pairs),
        "splits": dict(split_counts),
        "class_counts": {str(key): class_counts[key] for key in sorted(class_counts)},
        "skipped_empty": len(skipped_empty),
        "missing_images": missing_images,
        "include_empty": args.include_empty,
    }
    (args.out / "dataset_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Prepared relabel dataset: {args.out.resolve()}")
    print(f"Pairs: {len(pairs)}")
    print(f"Splits: train={split_counts['train']} val={split_counts['val']}")
    print(f"Skipped empty labels: {len(skipped_empty)}")
    print(f"Missing images: {len(missing_images)}")


if __name__ == "__main__":
    main()
