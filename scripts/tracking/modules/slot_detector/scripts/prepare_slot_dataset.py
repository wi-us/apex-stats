import argparse
import json
import random
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def read_class_names(path: Path | None, class_ids: list[int]) -> dict[int, str]:
    if path is None:
        return {class_id: f"slot_{class_id:02d}" for class_id in class_ids}

    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    names: dict[int, str] = {}
    for idx, line in enumerate(lines):
        if ":" in line:
            left, right = line.split(":", 1)
            if left.strip().isdigit():
                names[int(left.strip())] = right.strip()
                continue
        names[idx] = line

    for class_id in class_ids:
        names.setdefault(class_id, f"slot_{class_id:02d}")
    return names


def collect_label_classes(labels: list[Path]) -> tuple[list[int], Counter]:
    counts: Counter = Counter()
    for label_path in labels:
        for raw_line in label_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                counts[int(float(parts[0]))] += 1
            except ValueError:
                continue
    return sorted(counts), counts


def find_images(root: Path) -> dict[str, Path]:
    images: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            images.setdefault(path.stem, path)
    return images


def find_labels(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.txt") if path.is_file() and path.name.lower() != "classes.txt")


def clear_dataset_dirs(out: Path) -> None:
    for rel in ["images/train", "images/val", "labels/train", "labels/val"]:
        path = out / rel
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_data_yaml(out: Path, names: dict[int, str]) -> None:
    name_lines = "\n".join(f"  {class_id}: {yaml_quote(names[class_id])}" for class_id in sorted(names))
    (out / "data.yaml").write_text(
        "\n".join(
            [
                f"path: {out.resolve().as_posix()}",
                "train: images/train",
                "val: images/val",
                "",
                "names:",
                name_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )


def prepare(source: Path, out: Path, val_ratio: float, seed: int, class_names: Path | None) -> None:
    if source.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="slot_detector_") as tmp:
            tmp_path = Path(tmp)
            with zipfile.ZipFile(source, "r") as zf:
                zf.extractall(tmp_path)
            prepare_from_dir(tmp_path, out, val_ratio, seed, class_names, source)
    else:
        prepare_from_dir(source, out, val_ratio, seed, class_names, source)


def prepare_from_dir(root: Path, out: Path, val_ratio: float, seed: int, class_names: Path | None, source: Path) -> None:
    labels = find_labels(root)
    if not labels:
        raise FileNotFoundError(f"No YOLO label files found in {root}")

    images_by_stem = find_images(root)
    class_ids, class_counts = collect_label_classes(labels)
    if not class_ids:
        raise ValueError("Labels were found, but no class ids were detected")

    names = read_class_names(class_names, class_ids)

    pairs: list[tuple[Path, Path]] = []
    missing_images: list[str] = []
    for label_path in labels:
        image_path = images_by_stem.get(label_path.stem)
        if image_path is None:
            missing_images.append(label_path.name)
            continue
        pairs.append((image_path, label_path))

    if not pairs:
        raise FileNotFoundError("No image/label pairs found")

    random.Random(seed).shuffle(pairs)
    val_count = max(1, int(round(len(pairs) * val_ratio))) if len(pairs) > 1 else 0
    val_stems = {label.stem for _, label in pairs[:val_count]}

    out.mkdir(parents=True, exist_ok=True)
    clear_dataset_dirs(out)

    split_counts: Counter = Counter()
    for image_path, label_path in pairs:
        split = "val" if label_path.stem in val_stems else "train"
        shutil.copy2(image_path, out / "images" / split / image_path.name)
        shutil.copy2(label_path, out / "labels" / split / label_path.name)
        split_counts[split] += 1

    write_data_yaml(out, names)

    stats = {
        "source": str(source),
        "output": str(out.resolve()),
        "pairs": len(pairs),
        "missing_images": missing_images,
        "splits": dict(split_counts),
        "classes": {str(class_id): names[class_id] for class_id in sorted(names)},
        "class_counts": {str(class_id): class_counts[class_id] for class_id in sorted(class_counts)},
    }
    (out / "dataset_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Prepared dataset: {out.resolve()}")
    print(f"Pairs: {len(pairs)}")
    print(f"Splits: train={split_counts['train']} val={split_counts['val']}")
    print(f"Classes: {', '.join(f'{cid}:{names[cid]}' for cid in sorted(names))}")
    if missing_images:
        print(f"Missing images for labels: {len(missing_images)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a YOLO slot dataset from old slot-labeled plates.")
    parser.add_argument("--source-zip", type=Path, help="Path to old YOLO dataset zip")
    parser.add_argument("--source-dir", type=Path, help="Path to extracted old YOLO dataset")
    parser.add_argument("--out", type=Path, default=Path("datasets/slot_plates_v1"))
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=37)
    parser.add_argument("--class-names", type=Path, help="Optional class names file")
    args = parser.parse_args()

    sources = [value for value in [args.source_zip, args.source_dir] if value is not None]
    if len(sources) != 1:
        raise SystemExit("Pass exactly one of --source-zip or --source-dir")

    prepare(sources[0], args.out, args.val_ratio, args.seed, args.class_names)


if __name__ == "__main__":
    main()
