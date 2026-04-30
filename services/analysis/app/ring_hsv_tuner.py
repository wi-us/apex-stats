"""
Interactive HSV tuner for static white ring detection on Apex minimap.

Usage:
  python services/analysis/app/ring_hsv_tuner.py --video ffmpeg_downloader/my_match.mp4 --map mp_storm_point
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# Ensure project root is importable when script is run directly.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from team_tracking import config
from team_tracking.tracking_settings import get_round_windows


WINDOW_VIZ = "Ring HSV Tuner"
WINDOW_MASK = "Ring HSV Mask"
CONTROL = "controls"


def _odd(value: int) -> int:
    return value if value % 2 == 1 else value + 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune HSV ring detection on sampled map frames")
    parser.add_argument("--video", required=True, help="Video path")
    parser.add_argument("--map", default="mp_storm_point", help="Map key for round split")
    parser.add_argument("--start-seconds", type=float, default=0.0, help="Start timestamp (sec)")
    parser.add_argument("--end-seconds", type=float, help="End timestamp (sec)")
    parser.add_argument("--sample-step", type=int, default=1000, help="Sample every N frames")
    parser.add_argument(
        "--output",
        default="output/ring_hsv_tuner.json",
        help="Path to save tuned values with key S",
    )
    return parser.parse_args()


def collect_samples(video_path: str, start_seconds: float, end_seconds: float | None, sample_step: int) -> tuple[list[dict[str, Any]], float]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    start_frame = max(0, int(start_seconds * fps))
    end_frame = int(end_seconds * fps) if end_seconds is not None else max(start_frame, frame_count - 1)
    if frame_count > 0:
        end_frame = min(end_frame, frame_count - 1)
    if end_frame < start_frame:
        end_frame = start_frame

    map_x, map_y, map_w, map_h = config.MAP_ROI
    samples: list[dict[str, Any]] = []
    step = max(1, int(sample_step))
    frame_num = start_frame

    while frame_num <= end_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        if not ret:
            frame_num += step
            continue
        timestamp = frame_num / fps
        map_frame = frame[map_y : map_y + map_h, map_x : map_x + map_w]
        if map_frame.size == 0:
            frame_num += step
            continue
        samples.append(
            {
                "frameNum": frame_num,
                "timestampSec": timestamp,
                "image": map_frame.copy(),
            }
        )
        frame_num += step

    cap.release()
    if not samples:
        raise RuntimeError("No samples collected. Decrease --sample-step or adjust start/end seconds.")
    return samples, fps


def _settings_from_trackbars() -> dict[str, int]:
    return {
        "h_min": cv2.getTrackbarPos("H min", CONTROL),
        "h_max": cv2.getTrackbarPos("H max", CONTROL),
        "s_min": cv2.getTrackbarPos("S min", CONTROL),
        "s_max": cv2.getTrackbarPos("S max", CONTROL),
        "v_min": cv2.getTrackbarPos("V min", CONTROL),
        "v_max": cv2.getTrackbarPos("V max", CONTROL),
        "gray_min": cv2.getTrackbarPos("Gray min", CONTROL),
        "gray_max": cv2.getTrackbarPos("Gray max", CONTROL),
        "morph_k": _odd(max(1, cv2.getTrackbarPos("Morph k", CONTROL))),
        "blur_k": _odd(max(1, cv2.getTrackbarPos("Blur k", CONTROL))),
        "hough_p2": max(5, cv2.getTrackbarPos("Hough p2", CONTROL)),
        "min_r_pct": max(1, cv2.getTrackbarPos("Min R %", CONTROL)),
        "max_r_pct": max(1, cv2.getTrackbarPos("Max R %", CONTROL)),
    }


def _apply_mask(img: np.ndarray, st: dict[str, int]) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lower = np.array([st["h_min"], st["s_min"], st["v_min"]], dtype=np.uint8)
    upper = np.array([st["h_max"], st["s_max"], st["v_max"]], dtype=np.uint8)
    mask_hsv = cv2.inRange(hsv, lower, upper)
    mask_gray = cv2.inRange(gray, st["gray_min"], st["gray_max"])
    mask = cv2.bitwise_and(mask_hsv, mask_gray)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (st["morph_k"], st["morph_k"]))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return mask


def _detect_circle(mask: np.ndarray, st: dict[str, int], width: int) -> tuple[float, float, float] | None:
    blur = cv2.GaussianBlur(mask, (st["blur_k"], st["blur_k"]), 1.6)
    min_r = max(5, int(width * st["min_r_pct"] / 100.0))
    max_r = max(min_r + 1, int(width * st["max_r_pct"] / 100.0))
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(20, width // 6),
        param1=90,
        param2=st["hough_p2"],
        minRadius=min_r,
        maxRadius=max_r,
    )
    if circles is None:
        return None

    best = None
    best_score = -1e9
    cx0 = width / 2.0
    cy0 = mask.shape[0] / 2.0
    for c in circles[0]:
        cx, cy, r = float(c[0]), float(c[1]), float(c[2])
        score = r - 0.15 * np.hypot(cx - cx0, cy - cy0)
        if score > best_score:
            best_score = score
            best = (cx, cy, r)
    return best


def _dump_preset(st: dict[str, int], output_path: Path) -> None:
    payload = {
        "ring_hsv": {
            "lower": [st["h_min"], st["s_min"], st["v_min"]],
            "upper": [st["h_max"], st["s_max"], st["v_max"]],
        },
        "ring_gray": {
            "min": st["gray_min"],
            "max": st["gray_max"],
        },
        "ring_cv": {
            "morph_k": st["morph_k"],
            "blur_k": st["blur_k"],
            "hough_p2": st["hough_p2"],
            "min_r_pct": st["min_r_pct"],
            "max_r_pct": st["max_r_pct"],
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Saved: {output_path}")


def main() -> None:
    args = parse_args()
    samples, fps = collect_samples(args.video, args.start_seconds, args.end_seconds, args.sample_step)
    round_windows = get_round_windows(args.map)
    round2_start = float(round_windows["round2"]["start_sec"])

    cv2.namedWindow(WINDOW_VIZ, cv2.WINDOW_NORMAL)
    cv2.namedWindow(WINDOW_MASK, cv2.WINDOW_NORMAL)
    cv2.namedWindow(CONTROL, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(CONTROL, 620, 420)

    cv2.createTrackbar("H min", CONTROL, 0, 180, lambda _v: None)
    cv2.createTrackbar("H max", CONTROL, 180, 180, lambda _v: None)
    cv2.createTrackbar("S min", CONTROL, 0, 255, lambda _v: None)
    cv2.createTrackbar("S max", CONTROL, 70, 255, lambda _v: None)
    cv2.createTrackbar("V min", CONTROL, 150, 255, lambda _v: None)
    cv2.createTrackbar("V max", CONTROL, 255, 255, lambda _v: None)
    cv2.createTrackbar("Gray min", CONTROL, 150, 255, lambda _v: None)
    cv2.createTrackbar("Gray max", CONTROL, 255, 255, lambda _v: None)
    cv2.createTrackbar("Morph k", CONTROL, 3, 21, lambda _v: None)
    cv2.createTrackbar("Blur k", CONTROL, 9, 21, lambda _v: None)
    cv2.createTrackbar("Hough p2", CONTROL, 24, 100, lambda _v: None)
    cv2.createTrackbar("Min R %", CONTROL, 10, 60, lambda _v: None)
    cv2.createTrackbar("Max R %", CONTROL, 49, 95, lambda _v: None)

    idx = 0
    output_path = Path(args.output)

    print("Keys: [A]/[D] prev/next sample, [P] print JSON, [S] save JSON, [Q]/Esc exit")
    while True:
        sample = samples[idx]
        st = _settings_from_trackbars()
        img = sample["image"]
        mask = _apply_mask(img, st)
        circle = _detect_circle(mask, st, img.shape[1])

        viz = img.copy()
        if circle is not None:
            cx, cy, radius = circle
            cv2.circle(viz, (int(round(cx)), int(round(cy))), int(round(radius)), (255, 255, 255), 2)
            cv2.circle(viz, (int(round(cx)), int(round(cy))), 3, (0, 255, 255), -1)

        ts = float(sample["timestampSec"])
        segment = 1 if ts < round2_start else 2
        line1 = f"sample={idx+1}/{len(samples)} frame={sample['frameNum']} t={ts:.1f}s seg={segment} fps={fps:.2f}"
        line2 = f"HSV[{st['h_min']},{st['s_min']},{st['v_min']}]..[{st['h_max']},{st['s_max']},{st['v_max']}] p2={st['hough_p2']}"
        cv2.putText(viz, line1, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
        cv2.putText(viz, line2, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)

        cv2.imshow(WINDOW_VIZ, viz)
        cv2.imshow(WINDOW_MASK, mask)

        key = cv2.waitKeyEx(15)
        if key in (27, ord("q"), ord("Q")):
            break
        if key in (ord("a"), ord("A"), 2424832):  # left arrow
            idx = (idx - 1) % len(samples)
        elif key in (ord("d"), ord("D"), 2555904):  # right arrow
            idx = (idx + 1) % len(samples)
        elif key in (ord("p"), ord("P")):
            _dump_preset(st, output_path)
        elif key in (ord("s"), ord("S")):
            _dump_preset(st, output_path)

    cv2.destroyWindow(WINDOW_VIZ)
    cv2.destroyWindow(WINDOW_MASK)
    cv2.destroyWindow(CONTROL)


if __name__ == "__main__":
    main()

