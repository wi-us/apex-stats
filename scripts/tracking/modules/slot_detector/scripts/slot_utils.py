import json
from pathlib import Path

import cv2
import numpy as np


FALLBACK_BGR = [
    (255, 92, 31),
    (31, 198, 255),
    (31, 255, 122),
    (190, 88, 255),
    (255, 210, 31),
    (255, 31, 92),
    (31, 255, 233),
    (151, 255, 31),
    (255, 132, 31),
    (92, 31, 255),
    (31, 117, 255),
    (255, 31, 225),
    (31, 255, 31),
    (255, 31, 31),
    (31, 190, 255),
    (210, 31, 255),
    (255, 255, 31),
    (31, 255, 167),
    (255, 167, 31),
    (167, 31, 255),
]


def resolve_device(device: str) -> str:
    if device.lower() != "auto":
        return device
    try:
        import torch
    except ImportError:
        return "cpu"
    return "0" if torch.cuda.is_available() and torch.cuda.device_count() > 0 else "cpu"


def hex_to_bgr(value: str) -> tuple[int, int, int] | None:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        r = int(text[0:2], 16)
        g = int(text[2:4], 16)
        b = int(text[4:6], 16)
    except ValueError:
        return None
    return b, g, r


def hsv_to_bgr(hsv: list[float]) -> tuple[int, int, int] | None:
    if len(hsv) < 3:
        return None
    hsv_img = [[[int(round(hsv[0])), int(round(hsv[1])), int(round(hsv[2]))]]]
    bgr = cv2.cvtColor(np.array(hsv_img, dtype=np.uint8), cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def default_color_profile_path() -> Path | None:
    root = Path(__file__).resolve().parents[3]
    config_dir = root / "configs" / "plate_detector"
    if not config_dir.exists():
        return None
    candidates = sorted(config_dir.glob("*.preset_colors.json"))
    if candidates:
        return candidates[0]
    candidates = sorted(config_dir.glob("*.colors.json"))
    return candidates[0] if candidates else None


def load_slot_styles(path: Path | None = None) -> dict[int, dict]:
    profile_path = path or default_color_profile_path()
    if profile_path is None or not profile_path.exists():
        return {}

    data = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = data.get("team_color_profiles") or data.get("profiles") or []
    styles: dict[int, dict] = {}
    for profile in profiles:
        hud_index = profile.get("hud_index")
        if not isinstance(hud_index, int):
            continue
        class_id = hud_index - 1

        color = None
        if profile.get("hex"):
            color = hex_to_bgr(str(profile["hex"]))
        if color is None and profile.get("median_bgr"):
            raw = profile["median_bgr"]
            if isinstance(raw, list) and len(raw) >= 3:
                color = (int(raw[0]), int(raw[1]), int(raw[2]))
        if color is None and profile.get("median_hsv"):
            color = hsv_to_bgr(profile["median_hsv"])

        label = (
            profile.get("broadcast_tag")
            or profile.get("team_tag")
            or profile.get("team_name")
            or f"slot_{hud_index:02d}"
        )
        styles[class_id] = {
            "color": color or FALLBACK_BGR[class_id % len(FALLBACK_BGR)],
            "label": str(label),
            "hud_index": hud_index,
            "source": str(profile_path),
        }
    return styles


def load_slot_profiles(path: Path | None = None) -> dict[int, dict]:
    profile_path = path or default_color_profile_path()
    if profile_path is None or not profile_path.exists():
        return {}

    data = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = data.get("team_color_profiles") or data.get("profiles") or []
    out: dict[int, dict] = {}
    for profile in profiles:
        hud_index = profile.get("hud_index")
        if not isinstance(hud_index, int):
            continue
        class_id = hud_index - 1
        hsv_range = profile.get("hsv_range") or {}
        h_range = profile.get("h") or hsv_range.get("h")
        s_range = profile.get("s") or hsv_range.get("s")
        v_range = profile.get("v") or hsv_range.get("v")
        median_hsv = profile.get("median_hsv")
        if median_hsv is None and h_range and s_range and v_range:
            median_hsv = [
                (float(h_range[0]) + float(h_range[1])) / 2.0,
                (float(s_range[0]) + float(s_range[1])) / 2.0,
                (float(v_range[0]) + float(v_range[1])) / 2.0,
            ]
        out[class_id] = {
            "class_id": class_id,
            "hud_index": hud_index,
            "slot_id": f"SLOT_{hud_index:02d}",
            "h": h_range,
            "s": s_range,
            "v": v_range,
            "median_hsv": median_hsv,
            "broadcast_tag": profile.get("broadcast_tag"),
            "team_name": profile.get("team_name"),
        }
    return out


def slot_style(class_id: int, styles: dict[int, dict] | None = None) -> dict:
    if styles and class_id in styles:
        style = dict(styles[class_id])
        style["label"] = f"SLOT_{style['hud_index']:02d}"
        return style
    hud_index = class_id + 1
    return {
        "color": FALLBACK_BGR[class_id % len(FALLBACK_BGR)],
        "label": f"SLOT_{hud_index:02d}",
        "hud_index": hud_index,
        "source": "fallback",
    }


def readable_text_color(bgr: tuple[int, int, int]) -> tuple[int, int, int]:
    b, g, r = bgr
    luminance = 0.114 * b + 0.587 * g + 0.299 * r
    return (0, 0, 0) if luminance > 145 else (255, 255, 255)


def median_hsv_for_crop(bgr_crop: np.ndarray) -> list[float]:
    if bgr_crop.size == 0:
        return [0.0, 0.0, 0.0]
    hsv = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 1] > 35) & (hsv[:, :, 2] > 35)
    if np.mean(mask) < 0.03:
        pixels = hsv.reshape(-1, 3)
    else:
        pixels = hsv[mask]
    med = np.median(pixels, axis=0)
    return [float(med[0]), float(med[1]), float(med[2])]


def hue_distance(a: float, b: float) -> float:
    diff = abs(a - b)
    return min(diff, 180.0 - diff)


def hsv_slot_candidates(median_hsv: list[float], profiles: dict[int, dict], top_k: int = 3) -> list[dict]:
    if not profiles:
        return []
    h, s, v = median_hsv
    candidates: list[dict] = []
    for class_id, profile in profiles.items():
        target = profile.get("median_hsv")
        if not target:
            continue
        dh = hue_distance(float(h), float(target[0])) / 90.0
        ds = abs(float(s) - float(target[1])) / 255.0
        dv = abs(float(v) - float(target[2])) / 255.0
        distance = 0.62 * dh + 0.23 * ds + 0.15 * dv
        score = max(0.0, 1.0 - distance)
        candidates.append(
            {
                "slot_id": f"SLOT_{class_id + 1:02d}",
                "class_id": class_id,
                "score": round(score, 4),
                "distance": round(distance, 4),
            }
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:top_k]


def detect_color_distortion(bgr_crop: np.ndarray, prev_slot_id: str | None = None) -> str:
    if bgr_crop.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    red_or_orange = (((h <= 12) | (h >= 168)) & (s > 80) & (v > 70)) | ((h >= 13) & (h <= 25) & (s > 100) & (v > 80))
    red_ratio = float(np.mean(red_or_orange))
    white_ratio = float(np.mean((s < 45) & (v > 170)))
    sat_mean = float(np.mean(s))

    if red_ratio > 0.42:
        return "damage_flash" if prev_slot_id else "red_zone"
    if white_ratio > 0.45 or (sat_mean < 55 and float(np.mean(v)) > 145):
        return "white_zone"
    return "unknown"


def crop_with_padding(image: np.ndarray, xyxy: tuple[float, float, float, float], padding: float = 0.18) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = xyxy
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    px = bw * padding
    py = bh * padding
    ix1 = max(0, int(round(x1 - px)))
    iy1 = max(0, int(round(y1 - py)))
    ix2 = min(w, int(round(x2 + px)))
    iy2 = min(h, int(round(y2 + py)))
    return image[iy1:iy2, ix1:ix2], (ix1, iy1, ix2, iy2)
