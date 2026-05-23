#!/usr/bin/env python3
"""
debug_register.py — диагностика регистрации одного кадра против канонической карты.

Берёт N кадров из видео (равномерно по таймлайну), пробует зарегистрировать
каждый против canonical map и складывает картинки в папку:

    debug_out/
      frame_<idx>.png           — сам кадр (после CLAHE/ROI, в оттенках серого)
      canonical.png             — каноническая карта (то, что видит детектор)
      matches_<idx>.png         — топ-N inlier матчей линиями
      overlay_<idx>.png         — каноническая карта + проекция углов кадра (квадрат)
      report.txt                — сводка: сколько keypoints/matches/inliers

Запуск:
  python debug_register.py --video game.mp4 --config config.example.yaml --out debug_out --n 6
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from track_teams import load_canonical_map, load_config, FrameRegistrar


def grab_frames(video: Path, n: int):
    video = video.resolve()
    if not video.exists():
        raise RuntimeError(f"файл не найден: {video}")
    # пробуем несколько бэкендов: FFMPEG (по умолчанию), затем MSMF (Windows)
    cap = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap = cv2.VideoCapture(str(video), cv2.CAP_MSMF)
    if not cap.isOpened():
        cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(
            f"OpenCV не смог открыть {video}.\n"
            "Скорее всего нет FFmpeg-бэкенда. Попробуй:\n"
            "  1) pip uninstall opencv-python-headless opencv-python -y && pip install opencv-python\n"
            "  2) поставить ffmpeg в PATH (winget install Gyan.FFmpeg)\n"
            "  3) перекодировать видео: ffmpeg -i in.mp4 -c:v libx264 -an out.mp4"
        )
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        # некоторые контейнеры не отдают frame_count — читаем подряд
        cap.release()
        cap = cv2.VideoCapture(str(video))
        frames = []
        while True:
            ok, fr = cap.read()
            if not ok: break
            frames.append(fr)
        cap.release()
        if not frames:
            raise RuntimeError("у видео 0 кадров (контейнер пустой или кодек не поддерживается)")
        step = max(1, len(frames) // (n + 1))
        return [(i * step, frames[i * step]) for i in range(1, n + 1) if i * step < len(frames)]
    idxs = [int(total * (i + 1) / (n + 1)) for i in range(n)]
    out = []
    for idx in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            out.append((idx, frame))
    cap.release()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--n", type=int, default=6)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    cfg = load_config(args.config)
    canonical_dir = (args.config.parent / "canonical_maps").resolve()
    if not canonical_dir.exists():
        canonical_dir = (Path(__file__).resolve().parents[2] / "shared" / "canonical_maps").resolve()
    cmap = load_canonical_map(cfg.get("canonical_map", "storm_point"), canonical_dir)
    reg = FrameRegistrar(cmap, cfg.get("registration", {}))

    # сохраним каноническую (то, что реально видит детектор — после downscale+clahe)
    cv2.imwrite(str(args.out / "canonical.png"), reg.map_small)

    lines = [f"canonical: size={cmap.size}, features={0 if reg.des_map is None else len(reg.des_map)}"]
    for idx, frame in grab_frames(args.video, args.n):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        x0 = int(reg.roi[0] * w); y0 = int(reg.roi[1] * h)
        x1 = int(reg.roi[2] * w); y1 = int(reg.roi[3] * h)
        roi = gray[y0:y1, x0:x1]
        if reg.clahe is not None:
            roi = reg.clahe.apply(roi)
        kp_f, des_f = reg.detector.detectAndCompute(roi, None)
        nk = 0 if kp_f is None else len(kp_f)
        if des_f is None or reg.des_map is None or nk < 8:
            lines.append(f"frame {idx}: keypoints={nk} — слишком мало, регистрация невозможна")
            cv2.imwrite(str(args.out / f"frame_{idx}.png"), roi)
            continue
        knn = reg.bf.knnMatch(des_f, reg.des_map, k=2)
        good = [m for pair in knn if len(pair) == 2 for m, n in [pair] if m.distance < reg.ratio * n.distance]
        if len(good) < 8:
            lines.append(f"frame {idx}: keypoints={nk}, good_matches={len(good)} — Lowe-фильтр всё съел")
            cv2.imwrite(str(args.out / f"frame_{idx}.png"), roi)
            continue
        src = np.float32([(kp_f[m.queryIdx].pt[0], kp_f[m.queryIdx].pt[1]) for m in good]).reshape(-1, 1, 2)
        dst = np.float32([reg.kp_map[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, reg.reproj)
        inliers = int(mask.sum()) if mask is not None else 0
        lines.append(f"frame {idx}: keypoints={nk}, good_matches={len(good)}, inliers={inliers}, H={'ok' if H is not None else 'None'}")
        # картинка матчей (только inliers)
        match_img = cv2.drawMatches(
            roi, kp_f, reg.map_small, reg.kp_map,
            [m for i, m in enumerate(good) if mask is not None and mask[i]],
            None, matchColor=(0, 255, 0), singlePointColor=None, flags=2,
        )
        cv2.imwrite(str(args.out / f"matches_{idx}.png"), match_img)
        cv2.imwrite(str(args.out / f"frame_{idx}.png"), roi)
        # overlay: спроецировать углы кадра на каноническую
        if H is not None:
            corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
            proj = cv2.perspectiveTransform(corners, H)
            overlay = cv2.cvtColor(reg.map_small, cv2.COLOR_GRAY2BGR)
            proj_small = (proj * reg.scale).astype(np.int32)
            cv2.polylines(overlay, [proj_small], True, (0, 255, 0), 3)
            cv2.imwrite(str(args.out / f"overlay_{idx}.png"), overlay)

    (args.out / "report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[ok] см. {args.out}/")


if __name__ == "__main__":
    main()