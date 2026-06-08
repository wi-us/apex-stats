import argparse
import random
import re
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import cv2
import numpy as np
from tqdm import tqdm


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def safe_stem(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "video"


def iter_files(root: Path, exts: set[str]) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def central_roi(frame: np.ndarray, left_ignore: int = 420, roi_size: int = 1080) -> np.ndarray:
    h, w = frame.shape[:2]
    if w < roi_size or h < roi_size:
        raise ValueError(f"Frame is too small: {w}x{h}, need at least {roi_size}x{roi_size}")

    if w >= left_ignore * 2 + roi_size:
        x1 = left_ignore
    else:
        x1 = max(0, (w - roi_size) // 2)

    y1 = max(0, (h - roi_size) // 2)
    return frame[y1:y1 + roi_size, x1:x1 + roi_size]


def yolo_line_to_xyxy(line: str, img_w: int, img_h: int) -> Optional[Tuple[int, int, int, int, float]]:
    parts = line.strip().split()
    if len(parts) not in {5, 6}:
        return None

    try:
        cls = int(float(parts[0]))
        xc = float(parts[1])
        yc = float(parts[2])
        bw = float(parts[3])
        bh = float(parts[4])
        conf = float(parts[5]) if len(parts) == 6 else 1.0
    except ValueError:
        return None

    x1 = int(round((xc - bw / 2) * img_w))
    y1 = int(round((yc - bh / 2) * img_h))
    x2 = int(round((xc + bw / 2) * img_w))
    y2 = int(round((yc + bh / 2) * img_h))

    x1 = max(0, min(img_w - 1, x1))
    y1 = max(0, min(img_h - 1, y1))
    x2 = max(0, min(img_w, x2))
    y2 = max(0, min(img_h, y2))

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2, y2, conf


def xyxy_to_yolo_line(x1: int, y1: int, x2: int, y2: int, img_w: int, img_h: int, cls: int = 0) -> str:
    bw = (x2 - x1) / img_w
    bh = (y2 - y1) / img_h
    xc = (x1 + x2) / 2 / img_w
    yc = (y1 + y2) / 2 / img_h
    return f"{cls} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}"


def crop_quality(image: np.ndarray, box: Tuple[int, int, int, int]) -> dict:
    x1, y1, x2, y2 = box
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return {"white_ratio": 0.0, "color_ratio": 0.0, "mean_sat": 0.0, "mean_val": 0.0}

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    white_mask = (gray > 135) & (hsv[:, :, 1] < 175)
    color_mask = (hsv[:, :, 1] > 45) & (hsv[:, :, 2] > 45)

    return {
        "white_ratio": float(np.mean(white_mask)),
        "color_ratio": float(np.mean(color_mask)),
        "mean_sat": float(np.mean(hsv[:, :, 1])),
        "mean_val": float(np.mean(hsv[:, :, 2])),
    }


def should_keep_box(
    image: np.ndarray,
    box: Tuple[int, int, int, int],
    conf: float,
    min_conf: float,
    min_width: int,
    max_width: int,
    min_height: int,
    max_height: int,
    ignore_bottom_px: int,
    strict_quality: bool,
) -> Tuple[bool, str]:
    img_h, img_w = image.shape[:2]
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    aspect = w / max(h, 1)

    if conf < min_conf:
        return False, "low_conf"
    if w < min_width:
        return False, "too_narrow"
    if w > max_width:
        return False, "too_wide"
    if h < min_height:
        return False, "too_low"
    if h > max_height:
        return False, "too_tall"
    if aspect < 0.65:
        return False, "bad_aspect_low"
    if aspect > 12.0:
        return False, "bad_aspect_high"
    if ignore_bottom_px > 0 and y2 > img_h - ignore_bottom_px:
        return False, "bottom_ui"

    q = crop_quality(image, box)
    white_ratio = q["white_ratio"]
    color_ratio = q["color_ratio"]

    # Мягкие правила против карты. Настоящая длинная плашка обычно имеет заметный белый текст.
    if aspect >= 4.5 and white_ratio < 0.012:
        return False, "long_low_text"
    if aspect >= 3.0 and white_ratio < 0.009:
        return False, "wide_low_text"
    if aspect >= 2.0 and color_ratio < 0.12:
        return False, "wide_low_color"

    if strict_quality:
        if aspect >= 2.0 and white_ratio < 0.012:
            return False, "strict_low_text"
        if color_ratio < 0.16:
            return False, "strict_low_color"

    return True, "ok"


def draw_debug(image: np.ndarray, kept: List[Tuple[int, int, int, int, float]], rejected: List[Tuple[int, int, int, int, float, str]]) -> np.ndarray:
    out = image.copy()

    for x1, y1, x2, y2, conf in kept:
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(out, f"{conf:.2f}", (x1, max(0, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

    for x1, y1, x2, y2, conf, reason in rejected:
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 0, 255), 1)
        cv2.putText(out, reason[:14], (x1, min(out.shape[0] - 2, y2 + 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1, cv2.LINE_AA)

    cv2.rectangle(out, (10, 10), (330, 50), (0, 0, 0), -1)
    cv2.putText(out, f"kept: {len(kept)} | rejected: {len(rejected)}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
    return out


def cmd_sample(args: argparse.Namespace) -> None:
    videos_dir = Path(args.videos_dir)
    out_pool = Path(args.out_pool)
    images_dir = out_pool / "images"
    ensure_dir(images_dir)

    videos = list(iter_files(videos_dir, VIDEO_EXTS))
    if not videos:
        raise RuntimeError(f"No videos found in {videos_dir}")

    saved = 0
    for video in videos:
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            print(f"SKIP cannot open: {video}")
            continue

        fps = cap.get(cv2.CAP_PROP_FPS)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0:
            fps = 30.0

        frame_step = max(1, int(round(fps / args.sample_fps)))
        max_frame = total
        if args.max_seconds > 0:
            max_frame = min(max_frame, int(args.max_seconds * fps))

        video_key = safe_stem(video.stem)
        frame_idx = 0

        pbar = tqdm(total=max_frame, desc=f"sample {video.name}")
        while frame_idx < max_frame:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_step == 0:
                try:
                    roi = central_roi(frame, left_ignore=args.left_ignore, roi_size=args.roi_size)
                except ValueError as e:
                    print(f"SKIP frame {frame_idx}: {e}")
                    frame_idx += 1
                    pbar.update(1)
                    continue

                out_name = f"{video_key}_frame_{frame_idx:07d}.jpg"
                out_path = images_dir / out_name
                cv2.imwrite(str(out_path), roi, [cv2.IMWRITE_JPEG_QUALITY, args.jpg_quality])
                saved += 1

            frame_idx += 1
            pbar.update(1)

        pbar.close()
        cap.release()

    print(f"Saved sampled ROI images: {saved}")
    print(f"Images dir: {images_dir.resolve()}")


def cmd_autolabel(args: argparse.Namespace) -> None:
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError("Install ultralytics first: pip install ultralytics") from e

    pool = Path(args.pool)
    images_dir = pool / "images"
    labels_raw_dir = pool / "labels_raw"
    labels_auto_dir = pool / "labels_auto"
    debug_dir = pool / "debug_auto"
    rejected_dir = pool / "rejected_preview"

    ensure_dir(labels_raw_dir)
    ensure_dir(labels_auto_dir)
    if args.save_debug:
        ensure_dir(debug_dir)
        ensure_dir(rejected_dir)

    images = list(iter_files(images_dir, IMAGE_EXTS))
    if not images:
        raise RuntimeError(f"No images found in {images_dir}")

    model = YOLO(args.weights)

    total_kept = 0
    total_rejected = 0
    total_images = 0

    for img_path in tqdm(images, desc="autolabel"):
        image = cv2.imread(str(img_path))
        if image is None:
            print(f"SKIP unreadable image: {img_path}")
            continue

        img_h, img_w = image.shape[:2]
        results = model.predict(
            source=image,
            imgsz=args.imgsz,
            conf=args.predict_conf,
            iou=args.iou,
            device=args.device,
            verbose=False,
            max_det=args.max_det,
        )

        kept: List[Tuple[int, int, int, int, float]] = []
        rejected: List[Tuple[int, int, int, int, float, str]] = []
        raw_lines: List[str] = []
        clean_lines: List[str] = []

        if results and results[0].boxes is not None:
            xyxy = results[0].boxes.xyxy.cpu().numpy()
            confs = results[0].boxes.conf.cpu().numpy()

            for box_arr, conf in zip(xyxy, confs):
                x1, y1, x2, y2 = [int(round(v)) for v in box_arr.tolist()]
                x1 = max(0, min(img_w - 1, x1))
                y1 = max(0, min(img_h - 1, y1))
                x2 = max(0, min(img_w, x2))
                y2 = max(0, min(img_h, y2))
                if x2 <= x1 or y2 <= y1:
                    continue

                raw_lines.append(xyxy_to_yolo_line(x1, y1, x2, y2, img_w, img_h, cls=0) + f" {float(conf):.6f}")

                keep, reason = should_keep_box(
                    image=image,
                    box=(x1, y1, x2, y2),
                    conf=float(conf),
                    min_conf=args.keep_conf,
                    min_width=args.min_width,
                    max_width=args.max_width,
                    min_height=args.min_height,
                    max_height=args.max_height,
                    ignore_bottom_px=args.ignore_bottom_px,
                    strict_quality=args.strict_quality,
                )

                if keep:
                    kept.append((x1, y1, x2, y2, float(conf)))
                    clean_lines.append(xyxy_to_yolo_line(x1, y1, x2, y2, img_w, img_h, cls=0))
                else:
                    rejected.append((x1, y1, x2, y2, float(conf), reason))

        (labels_raw_dir / f"{img_path.stem}.txt").write_text("\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8")
        (labels_auto_dir / f"{img_path.stem}.txt").write_text("\n".join(clean_lines) + ("\n" if clean_lines else ""), encoding="utf-8")

        if args.save_debug:
            dbg = draw_debug(image, kept, rejected)
            cv2.imwrite(str(debug_dir / img_path.name), dbg, [cv2.IMWRITE_JPEG_QUALITY, 92])

            # Быстрый preview проблемных кадров: мало плашек или слишком много reject.
            if len(kept) < args.review_min_boxes or len(rejected) > args.review_max_rejected:
                cv2.imwrite(str(rejected_dir / img_path.name), dbg, [cv2.IMWRITE_JPEG_QUALITY, 92])

        total_kept += len(kept)
        total_rejected += len(rejected)
        total_images += 1

    print(f"Images processed: {total_images}")
    print(f"Total kept boxes: {total_kept}")
    print(f"Total rejected boxes: {total_rejected}")
    print(f"Auto labels: {labels_auto_dir.resolve()}")
    print(f"Raw labels with confidence: {labels_raw_dir.resolve()}")
    if args.save_debug:
        print(f"Debug: {debug_dir.resolve()}")
        print(f"Needs review preview: {rejected_dir.resolve()}")


def count_label_boxes(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if len(line.strip().split()) == 5:
            count += 1
    return count


def cmd_build_dataset(args: argparse.Namespace) -> None:
    pool = Path(args.pool)
    dataset = Path(args.dataset)

    pool_images = pool / "images"
    pool_labels = pool / "labels_auto"

    train_img = dataset / "images" / "train"
    val_img = dataset / "images" / "val"
    train_lbl = dataset / "labels" / "train"
    val_lbl = dataset / "labels" / "val"

    for p in [train_img, val_img, train_lbl, val_lbl]:
        ensure_dir(p)

    pairs = []
    for img in iter_files(pool_images, IMAGE_EXTS):
        lbl = pool_labels / f"{img.stem}.txt"
        boxes = count_label_boxes(lbl)
        if boxes >= args.min_boxes:
            pairs.append((img, lbl, boxes))

    if not pairs:
        raise RuntimeError("No valid image/label pairs found. Check pool labels or lower --min-boxes.")

    rng = random.Random(args.seed)
    rng.shuffle(pairs)

    if args.max_images > 0:
        pairs = pairs[:args.max_images]

    val_count = max(1, int(round(len(pairs) * args.val_ratio))) if len(pairs) > 1 else 0
    val_set = set(img for img, _, _ in pairs[:val_count])

    copied_train = 0
    copied_val = 0

    for img, lbl, boxes in pairs:
        is_val = img in val_set
        dst_img_dir = val_img if is_val else train_img
        dst_lbl_dir = val_lbl if is_val else train_lbl

        shutil.copy2(img, dst_img_dir / img.name)
        shutil.copy2(lbl, dst_lbl_dir / f"{img.stem}.txt")

        if is_val:
            copied_val += 1
        else:
            copied_train += 1

    data_yaml = dataset / "data.yaml"
    data_yaml.write_text(
        f"path: {dataset.resolve().as_posix()}\ntrain: images/train\nval: images/val\n\nnames:\n  0: team_plate\n",
        encoding="utf-8",
    )

    print(f"Dataset: {dataset.resolve()}")
    print(f"Train images: {copied_train}")
    print(f"Val images: {copied_val}")
    print(f"data.yaml: {data_yaml.resolve()}")


def cmd_fix_classes(args: argparse.Namespace) -> None:
    root = Path(args.dataset)
    labels_dirs = [root / "labels" / "train", root / "labels" / "val"]
    files = 0
    changed = 0
    skipped = 0

    for labels_dir in labels_dirs:
        if not labels_dir.exists():
            continue
        for p in labels_dir.glob("*.txt"):
            if p.name == "classes.txt":
                continue
            out_lines = []
            for line in p.read_text(encoding="utf-8").splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                if len(parts) < 5:
                    skipped += 1
                    continue
                if parts[0] != "0":
                    changed += 1
                out_lines.append(" ".join(["0"] + parts[1:5]))
            p.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
            files += 1

    for cache in [root / "labels" / "train.cache", root / "labels" / "val.cache"]:
        if cache.exists():
            cache.unlink()

    print(f"Label files fixed: {files}")
    print(f"Changed lines: {changed}")
    print(f"Skipped bad lines: {skipped}")


def cmd_report(args: argparse.Namespace) -> None:
    root = Path(args.dataset)
    for split in ["train", "val"]:
        img_dir = root / "images" / split
        lbl_dir = root / "labels" / split
        imgs = list(iter_files(img_dir, IMAGE_EXTS)) if img_dir.exists() else []
        labels = list(lbl_dir.glob("*.txt")) if lbl_dir.exists() else []
        label_names = {p.stem for p in labels if p.name != "classes.txt"}
        missing = [img.name for img in imgs if img.stem not in label_names]
        total_boxes = sum(count_label_boxes(lbl_dir / f"{img.stem}.txt") for img in imgs)
        empty_labels = sum(1 for img in imgs if (lbl_dir / f"{img.stem}.txt").exists() and count_label_boxes(lbl_dir / f"{img.stem}.txt") == 0)
        print(f"{split}: images={len(imgs)} labels={len(label_names)} missing_labels={len(missing)} empty_labels={empty_labels} boxes={total_boxes}")
        if missing[:10]:
            print("  missing examples:", ", ".join(missing[:10]))


def cmd_add_negatives(args: argparse.Namespace) -> None:
    """
    Добавляет hard negative изображения в YOLO dataset.

    Hard negative = кадр/кроп, где есть мусорный элемент, но НЕТ team_plate-разметки.
    Для YOLO это нормальная обучающая картинка с пустым .txt.
    """
    dataset = Path(args.dataset)
    neg_dir = Path(args.negative_dir)
    split = args.split

    dst_img_dir = dataset / "images" / split
    dst_lbl_dir = dataset / "labels" / split
    ensure_dir(dst_img_dir)
    ensure_dir(dst_lbl_dir)

    negatives = list(iter_files(neg_dir, IMAGE_EXTS))
    if args.max_images > 0:
        negatives = negatives[:args.max_images]

    copied = 0
    for idx, src in enumerate(negatives):
        image = cv2.imread(str(src))
        if image is None:
            continue

        if args.as_canvas:
            canvas_size = args.canvas_size
            canvas = np.zeros((canvas_size, canvas_size, 3), dtype=np.uint8)

            h, w = image.shape[:2]
            scale = min((canvas_size * 0.75) / max(w, 1), (canvas_size * 0.75) / max(h, 1), 1.0)
            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))
            resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

            x = (canvas_size - new_w) // 2
            y = (canvas_size - new_h) // 2
            canvas[y:y + new_h, x:x + new_w] = resized
            out_image = canvas
        else:
            out_image = image

        out_name = f"NEG_{safe_stem(src.stem)}_{idx:05d}.jpg"
        out_img = dst_img_dir / out_name
        out_lbl = dst_lbl_dir / f"{Path(out_name).stem}.txt"

        cv2.imwrite(str(out_img), out_image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        out_lbl.write_text("", encoding="utf-8")
        copied += 1

    for cache in [dataset / "labels" / "train.cache", dataset / "labels" / "val.cache"]:
        if cache.exists():
            cache.unlink()

    print(f"Negative images copied: {copied}")
    print(f"Target images: {dst_img_dir.resolve()}")
    print(f"Target empty labels: {dst_lbl_dir.resolve()}")


def cmd_make_negative_composites(args: argparse.Namespace) -> None:
    """
    Создаёт hard-negative composite images:
    берём фон без настоящих team_plate и вставляем туда плохие кропы, которые модель путает с плашками.
    Итоговые label-файлы пустые.

    Это полезнее, чем просто класть маленький crop на чёрный canvas: мусор появляется в более реалистичной сцене.
    """
    dataset = Path(args.dataset)
    bg_dir = Path(args.background_dir)
    crop_dir = Path(args.crop_dir)
    split = args.split

    dst_img_dir = dataset / "images" / split
    dst_lbl_dir = dataset / "labels" / split
    ensure_dir(dst_img_dir)
    ensure_dir(dst_lbl_dir)

    backgrounds = list(iter_files(bg_dir, IMAGE_EXTS))
    crops = list(iter_files(crop_dir, IMAGE_EXTS))

    if not backgrounds:
        raise RuntimeError(f"No background images found in {bg_dir}")
    if not crops:
        raise RuntimeError(f"No crop images found in {crop_dir}")

    rng = random.Random(args.seed)
    made = 0

    for i in range(args.count):
        bg_path = rng.choice(backgrounds)
        bg = cv2.imread(str(bg_path))
        if bg is None:
            continue

        if args.force_canvas_size > 0:
            bg = cv2.resize(bg, (args.force_canvas_size, args.force_canvas_size), interpolation=cv2.INTER_AREA)

        out = bg.copy()
        H, W = out.shape[:2]

        n_crops = rng.randint(args.min_crops, args.max_crops)
        for _ in range(n_crops):
            crop_path = rng.choice(crops)
            crop = cv2.imread(str(crop_path))
            if crop is None:
                continue

            ch, cw = crop.shape[:2]
            if ch <= 0 or cw <= 0:
                continue

            scale = rng.uniform(args.min_scale, args.max_scale)
            nw = max(6, int(round(cw * scale)))
            nh = max(6, int(round(ch * scale)))

            if nw >= W or nh >= H:
                continue

            resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)

            x = rng.randint(0, W - nw)
            y = rng.randint(0, H - nh)

            # Простая вставка без alpha. Это намеренно: модель должна видеть, что такой паттерн не является team_plate.
            out[y:y + nh, x:x + nw] = resized

        out_name = f"NEGCOMP_{i:06d}.jpg"
        cv2.imwrite(str(dst_img_dir / out_name), out, [cv2.IMWRITE_JPEG_QUALITY, 95])
        (dst_lbl_dir / f"{Path(out_name).stem}.txt").write_text("", encoding="utf-8")
        made += 1

    for cache in [dataset / "labels" / "train.cache", dataset / "labels" / "val.cache"]:
        if cache.exists():
            cache.unlink()

    print(f"Negative composites made: {made}")
    print(f"Target images: {dst_img_dir.resolve()}")
    print(f"Target empty labels: {dst_lbl_dir.resolve()}")


def cmd_export_label_crops(args: argparse.Namespace) -> None:
    """
    Экспортирует кропы найденных bbox для быстрого просмотра ложных срабатываний.
    Удобно открыть папку и быстро увидеть: модель ловит плашки или мусор карты.
    """
    images_dir = Path(args.images_dir)
    labels_dir = Path(args.labels_dir)
    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)

    saved = 0
    for img_path in iter_files(images_dir, IMAGE_EXTS):
        lbl_path = labels_dir / f"{img_path.stem}.txt"
        if not lbl_path.exists():
            continue

        image = cv2.imread(str(img_path))
        if image is None:
            continue
        img_h, img_w = image.shape[:2]

        for idx, line in enumerate(lbl_path.read_text(encoding="utf-8").splitlines()):
            parsed = yolo_line_to_xyxy(line, img_w, img_h)
            if parsed is None:
                continue
            x1, y1, x2, y2, conf = parsed
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            out_name = f"{img_path.stem}_box_{idx:03d}_conf_{conf:.2f}.jpg"
            cv2.imwrite(str(out_dir / out_name), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
            saved += 1

    print(f"Crops saved: {saved}")
    print(f"Out: {out_dir.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto-labeling loop tools for Apex team plate detector")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sample", help="Sample central 1080x1080 ROI frames from videos")
    p.add_argument("--videos-dir", default="videos")
    p.add_argument("--out-pool", required=True)
    p.add_argument("--sample-fps", type=float, default=0.25, help="0.25 = 1 frame per 4 seconds")
    p.add_argument("--left-ignore", type=int, default=420)
    p.add_argument("--roi-size", type=int, default=1080)
    p.add_argument("--max-seconds", type=float, default=0.0, help="0 = full video")
    p.add_argument("--jpg-quality", type=int, default=95)
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("autolabel", help="Run YOLO and create cleaned auto labels")
    p.add_argument("--pool", required=True)
    p.add_argument("--weights", required=True)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--predict-conf", type=float, default=0.12, help="YOLO inference conf; low to collect candidates")
    p.add_argument("--keep-conf", type=float, default=0.18, help="Filter conf for final auto labels")
    p.add_argument("--iou", type=float, default=0.55)
    p.add_argument("--device", default="cpu")
    p.add_argument("--max-det", type=int, default=120)
    p.add_argument("--min-width", type=int, default=18)
    p.add_argument("--max-width", type=int, default=340)
    p.add_argument("--min-height", type=int, default=10)
    p.add_argument("--max-height", type=int, default=55)
    p.add_argument("--ignore-bottom-px", type=int, default=95)
    p.add_argument("--strict-quality", action="store_true")
    p.add_argument("--save-debug", action="store_true")
    p.add_argument("--review-min-boxes", type=int, default=18)
    p.add_argument("--review-max-rejected", type=int, default=25)
    p.set_defaults(func=cmd_autolabel)

    p = sub.add_parser("build-dataset", help="Build YOLO dataset from pool/images + pool/labels_auto")
    p.add_argument("--pool", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--val-ratio", type=float, default=0.15)
    p.add_argument("--min-boxes", type=int, default=5)
    p.add_argument("--max-images", type=int, default=0, help="0 = all")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_build_dataset)

    p = sub.add_parser("fix-classes", help="Set all YOLO label class ids to 0 and remove cache")
    p.add_argument("--dataset", required=True)
    p.set_defaults(func=cmd_fix_classes)

    p = sub.add_parser("report", help="Report dataset image/label counts")
    p.add_argument("--dataset", required=True)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("add-negatives", help="Add hard negative images with empty labels to a YOLO dataset")
    p.add_argument("--dataset", required=True)
    p.add_argument("--negative-dir", required=True)
    p.add_argument("--split", default="train", choices=["train", "val"])
    p.add_argument("--max-images", type=int, default=0, help="0 = all")
    p.add_argument("--as-canvas", action="store_true", help="Paste small negative crops into 1080x1080 black canvas")
    p.add_argument("--canvas-size", type=int, default=1080)
    p.set_defaults(func=cmd_add_negatives)

    p = sub.add_parser("make-negative-composites", help="Paste bad crops onto negative backgrounds and add empty labels")
    p.add_argument("--dataset", required=True)
    p.add_argument("--background-dir", required=True)
    p.add_argument("--crop-dir", required=True)
    p.add_argument("--split", default="train", choices=["train", "val"])
    p.add_argument("--count", type=int, default=300)
    p.add_argument("--min-crops", type=int, default=1)
    p.add_argument("--max-crops", type=int, default=4)
    p.add_argument("--min-scale", type=float, default=0.7)
    p.add_argument("--max-scale", type=float, default=1.25)
    p.add_argument("--force-canvas-size", type=int, default=1080, help="0 = keep background size")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_make_negative_composites)

    p = sub.add_parser("export-label-crops", help="Export bbox crops from YOLO labels for quick false-positive review")
    p.add_argument("--images-dir", required=True)
    p.add_argument("--labels-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=cmd_export_label_crops)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
