import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm
from ultralytics import YOLO


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def central_roi(frame, left_ignore=420, roi_size=1080):
    h, w = frame.shape[:2]

    if w < roi_size or h < roi_size:
        raise ValueError(f"Frame is too small: {w}x{h}, need at least {roi_size}x{roi_size}")

    if w >= left_ignore * 2 + roi_size:
        x1 = left_ignore
    else:
        x1 = max(0, (w - roi_size) // 2)

    y1 = max(0, (h - roi_size) // 2)

    crop = frame[y1:y1 + roi_size, x1:x1 + roi_size]
    return crop, x1, y1


def mean_plate_color_bgr(plate_crop):
    """
    Берём средний цвет по насыщенным пикселям,
    чтобы белый текст не ломал цвет команды.
    """
    if plate_crop.size == 0:
        return None

    hsv = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2HSV)

    # Оставляем именно цветную подложку, а не белый текст.
    mask = (hsv[:, :, 1] > 45) & (hsv[:, :, 2] > 45)

    if np.mean(mask) < 0.05:
        mean_bgr = plate_crop.reshape(-1, 3).mean(axis=0)
    else:
        mean_bgr = plate_crop[mask].mean(axis=0)

    return [int(x) for x in mean_bgr.tolist()]


def draw_boxes(roi, detections):
    out = roi.copy()

    for det in detections:
        x1, y1, x2, y2 = det["xyxy_roi"]
        conf = det["confidence"]

        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            out,
            f"{conf:.2f}",
            (x1, max(0, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
            cv2.LINE_AA
        )

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, help="Path to source video")
    parser.add_argument("--weights", required=True, help="Path to trained best.pt")
    parser.add_argument("--out", default="yolo_crops", help="Output directory")
    parser.add_argument("--sample-fps", type=float, default=1.0)
    parser.add_argument("--left-ignore", type=int, default=420)
    parser.add_argument("--roi-size", type=int, default=1080)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()

    video_path = Path(args.video)
    out_dir = Path(args.out)
    crops_dir = out_dir / "crops"
    debug_dir = out_dir / "debug"

    ensure_dir(crops_dir)
    ensure_dir(debug_dir)

    model = YOLO(args.weights)

    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    source_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if source_fps <= 0:
        source_fps = 30

    frame_step = max(1, int(round(source_fps / args.sample_fps)))

    all_results = {
        "video": str(video_path),
        "weights": str(args.weights),
        "source_fps": source_fps,
        "sample_fps": args.sample_fps,
        "frame_step": frame_step,
        "left_ignore": args.left_ignore,
        "roi_size": args.roi_size,
        "frames": []
    }

    frame_idx = 0
    pbar = tqdm(total=total_frames, desc="YOLO cutting")

    while True:
        ret, frame = cap.read()

        if not ret:
            break

        if frame_idx % frame_step != 0:
            frame_idx += 1
            pbar.update(1)
            continue

        roi, roi_x, roi_y = central_roi(
            frame,
            left_ignore=args.left_ignore,
            roi_size=args.roi_size
        )

        results = model.predict(
            source=roi,
            imgsz=args.imgsz,
            conf=args.conf,
            iou=args.iou,
            device=args.device,
            verbose=False
        )

        detections = []

        if results and len(results) > 0:
            result = results[0]

            if result.boxes is not None:
                boxes_xyxy = result.boxes.xyxy.cpu().numpy()
                confs = result.boxes.conf.cpu().numpy()

                for det_idx, (box, conf) in enumerate(zip(boxes_xyxy, confs)):
                    x1, y1, x2, y2 = box

                    x1 = max(0, int(round(x1)))
                    y1 = max(0, int(round(y1)))
                    x2 = min(args.roi_size, int(round(x2)))
                    y2 = min(args.roi_size, int(round(y2)))

                    if x2 <= x1 or y2 <= y1:
                        continue

                    plate_crop = roi[y1:y2, x1:x2]
                    mean_bgr = mean_plate_color_bgr(plate_crop)

                    crop_name = f"{video_path.stem}_frame_{frame_idx:07d}_plate_{det_idx:02d}.png"
                    crop_path = crops_dir / crop_name

                    cv2.imwrite(str(crop_path), plate_crop)

                    detections.append({
                        "frame_idx": frame_idx,
                        "time_sec": frame_idx / source_fps,
                        "confidence": float(conf),
                        "xyxy_roi": [x1, y1, x2, y2],
                        "xyxy_original": [
                            roi_x + x1,
                            roi_y + y1,
                            roi_x + x2,
                            roi_y + y2
                        ],
                        "mean_bgr": mean_bgr,
                        "crop": str(crop_path)
                    })

        debug_img = draw_boxes(roi, detections)
        debug_name = f"{video_path.stem}_frame_{frame_idx:07d}.jpg"
        cv2.putText(debug_img, f"detections: {len(boxes)}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 2, cv2.LINE_AA)
        cv2.imwrite(str(debug_dir / debug_name), debug_img, [cv2.IMWRITE_JPEG_QUALITY, 95])

        all_results["frames"].append({
            "frame_idx": frame_idx,
            "time_sec": frame_idx / source_fps,
            "roi_x": roi_x,
            "roi_y": roi_y,
            "detections_count": len(detections),
            "detections": detections
        })

        frame_idx += 1
        pbar.update(1)

    pbar.close()
    cap.release()

    with open(out_dir / "detections.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\nDone.")
    print(f"Crops saved to: {crops_dir.resolve()}")
    print(f"Debug images saved to: {debug_dir.resolve()}")
    print(f"Detections JSON: {(out_dir / 'detections.json').resolve()}")


if __name__ == "__main__":
    main()