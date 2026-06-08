import argparse
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())


def hue_circular_diff(a: np.ndarray, center: float) -> np.ndarray:
    d = np.abs(a.astype(np.float32) - float(center))
    return np.minimum(d, 180.0 - d)


def scaled_zone(zone: dict, base_size: Tuple[int, int], image_shape) -> Tuple[int, int, int, int]:
    base_w, base_h = base_size
    img_h, img_w = image_shape[:2]
    sx = img_w / base_w
    sy = img_h / base_h

    x = int(round(zone["x"] * sx))
    y = int(round(zone["y"] * sy))
    w = int(round(zone["w"] * sx))
    h = int(round(zone["h"] * sy))

    x = max(0, min(img_w - 1, x))
    y = max(0, min(img_h - 1, y))
    w = max(1, min(img_w - x, w))
    h = max(1, min(img_h - y, h))
    return x, y, w, h


def get_team_zones(zones_data: dict) -> Dict[int, List[dict]]:
    by_team: Dict[int, List[dict]] = {}
    for z in zones_data.get("zones", []):
        tag = str(z.get("tag", ""))
        m = re.fullmatch(r"team_(\d+)", tag)
        if not m:
            continue
        idx = int(m.group(1))
        by_team.setdefault(idx, []).append(z)
    return by_team


def get_zone_by_name(zones: List[dict], name: str) -> Optional[dict]:
    for z in zones:
        if str(z.get("name", "")) == name:
            return z
    return None


def hud_sample_box(team_index: int, zones: List[dict], base_size: Tuple[int, int], image_shape) -> Tuple[int, int, int, int]:
    """
    Возвращает область, где лучше всего брать цвет команды из бокового HUD.

    Для левой колонки цвет чаще всего лежит в rank-полосе слева от logo/name.
    Для правой колонки цвет чаще всего лежит в rank-полосе слева от name/logo.
    """
    logo = get_zone_by_name(zones, "logo")
    name = get_zone_by_name(zones, "name")
    hero1 = get_zone_by_name(zones, "hero 1")

    z_ref = logo or name or hero1 or zones[0]
    rx, ry, rw, rh = scaled_zone(z_ref, base_size, image_shape)

    img_h, img_w = image_shape[:2]

    # Верх строки обычно совпадает с logo.y, высота строки около 70-80 px.
    row_y = ry
    row_h = max(rh, int(round(70 * (img_h / base_size[1]))))
    row_y = max(0, min(img_h - 1, row_y))
    row_h = max(10, min(img_h - row_y, row_h))

    if team_index <= 10:
        # Левая колонка: цветная rank-полоска находится левее logo/name.
        x1 = 0
        x2 = max(rx, 70)
        x2 = min(img_w, x2)
    else:
        # Правая колонка: цветная rank-полоска находится левее logo/name.
        x2 = rx
        x1 = max(0, rx - int(round(85 * (img_w / base_size[0]))))
        if x2 <= x1 + 10:
            x2 = min(img_w, x1 + 85)

    return x1, row_y, x2 - x1, row_h


def extract_team_color(crop: np.ndarray) -> dict:
    """
    Достаёт доминирующий насыщенный цвет из HUD-полосы.
    Игнорирует белый текст, тёмный фон и лица/портреты насколько возможно.
    """
    if crop.size == 0:
        return {
            "median_hsv": [0.0, 0.0, 0.0],
            "hsv_range": {"h": [0, 0], "s": [0, 0], "v": [0, 0]},
            "median_bgr": [0, 0, 0],
            "pixels": 0,
            "quality": 0.0,
        }

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    bgr = crop

    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    # Насыщенные и достаточно яркие пиксели. Так мы отсекаем белый текст и чёрный фон.
    mask = (s > 55) & (v > 45)

    # Убираем почти белые/серые пиксели и слишком тёмные.
    mask &= ~((s < 75) & (v > 150))
    mask &= ~(v < 35)

    if np.mean(mask) < 0.02:
        # fallback помягче
        mask = (s > 35) & (v > 35)

    if np.mean(mask) < 0.005:
        values_hsv = hsv.reshape(-1, 3)
        values_bgr = bgr.reshape(-1, 3)
    else:
        values_hsv = hsv[mask]
        values_bgr = bgr[mask]

    if len(values_hsv) == 0:
        return {
            "median_hsv": [0.0, 0.0, 0.0],
            "hsv_range": {"h": [0, 0], "s": [0, 0], "v": [0, 0]},
            "median_bgr": [0, 0, 0],
            "pixels": 0,
            "quality": 0.0,
        }

    # Hue берём через histogram mode, потому что median на красном/пурпурном может быть неустойчивым.
    h_vals = values_hsv[:, 0].astype(np.int32)
    hist = np.bincount(h_vals, minlength=180)
    h_mode = int(hist.argmax())

    # Берём пиксели рядом с mode hue.
    dh = hue_circular_diff(h_vals, h_mode)
    cluster = values_hsv[dh <= 8]
    cluster_bgr = values_bgr[dh <= 8]

    if len(cluster) < max(10, len(values_hsv) * 0.10):
        cluster = values_hsv
        cluster_bgr = values_bgr

    med_h = float(np.median(cluster[:, 0]))
    med_s = float(np.median(cluster[:, 1]))
    med_v = float(np.median(cluster[:, 2]))
    med_bgr = np.median(cluster_bgr, axis=0).astype(int).tolist()

    # Диапазоны делаем не слишком узкими, чтобы пережить компрессию и затемнение.
    h_cluster = cluster[:, 0].astype(np.float32)
    dh2 = hue_circular_diff(h_cluster, h_mode)
    h_low = max(0, int(round(h_mode - max(3, np.percentile(dh2, 80)))))
    h_high = min(179, int(round(h_mode + max(3, np.percentile(dh2, 80)))))
    s_low = max(0, int(round(np.percentile(cluster[:, 1], 10) - 20)))
    s_high = min(255, int(round(np.percentile(cluster[:, 1], 90) + 20)))
    v_low = max(0, int(round(np.percentile(cluster[:, 2], 10) - 25)))
    v_high = min(255, int(round(np.percentile(cluster[:, 2], 90) + 25)))

    quality = float(min(1.0, len(cluster) / max(1, crop.shape[0] * crop.shape[1]) * 4.0))

    return {
        "median_hsv": [med_h, med_s, med_v],
        "hsv_range": {"h": [h_low, h_high], "s": [s_low, s_high], "v": [v_low, v_high]},
        "median_bgr": med_bgr,
        "pixels": int(len(cluster)),
        "quality": quality,
    }


def find_team_by_broadcast_tag(config: dict, broadcast_tag: str) -> Optional[dict]:
    tag_norm = norm(broadcast_tag)
    if not tag_norm:
        return None

    # broadcast_tag_aliases: "S2": ["S2", "Team Vision", "VSN"]
    aliases_cfg = config.get("broadcast_tag_aliases") or {}
    candidate_aliases = [broadcast_tag]
    if broadcast_tag in aliases_cfg:
        candidate_aliases.extend(aliases_cfg[broadcast_tag])
    candidate_norms = {norm(x) for x in candidate_aliases if x}

    for team in config.get("teams", []):
        aliases = []
        for key in ["tag", "name", "db_tag", "db_name"]:
            if team.get(key):
                aliases.append(team[key])
        aliases.extend(team.get("aliases") or [])

        team_norms = {norm(x) for x in aliases if x}
        if candidate_norms & team_norms:
            return team

    # fallback: exact tag only
    for team in config.get("teams", []):
        if norm(team.get("tag", "")) == tag_norm or norm(team.get("name", "")) == tag_norm:
            return team

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate team color profiles from VOD HUD side panels")
    parser.add_argument("--image", required=True, help="Full 1920x1080 screenshot with side HUD")
    parser.add_argument("--zones", required=True, help="zones.vod.json")
    parser.add_argument("--config", required=True, help="match config json with teams and hud_team_order")
    parser.add_argument("--out", required=True, help="output color profile json")
    parser.add_argument("--update-config", action="store_true", help="write team_color_profiles into config")
    parser.add_argument("--debug-dir", default=None)
    args = parser.parse_args()

    image_path = Path(args.image)
    zones_path = Path(args.zones)
    config_path = Path(args.config)
    out_path = Path(args.out)

    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"Cannot read image: {image_path}")

    zones_data = load_json(zones_path)
    config = load_json(config_path)

    base_size = tuple(zones_data.get("base", [1920, 1080]))
    by_team_zones = get_team_zones(zones_data)
    hud_order = config.get("hud_team_order") or {}

    if not hud_order:
        raise RuntimeError(
            "config.hud_team_order is empty. Add mapping like {'1':'ELTE','2':'FREE',...} first."
        )

    debug = image.copy()
    profiles = []
    debug_dir = Path(args.debug_dir) if args.debug_dir else out_path.parent / "hud_color_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    for k, broadcast_tag in sorted(hud_order.items(), key=lambda kv: int(kv[0])):
        hud_idx = int(k)
        zones = by_team_zones.get(hud_idx)
        if not zones:
            print(f"WARNING: no zones for team_{hud_idx}")
            continue

        x, y, w, h = hud_sample_box(hud_idx, zones, base_size, image.shape)
        crop = image[y:y + h, x:x + w]
        color = extract_team_color(crop)
        team = find_team_by_broadcast_tag(config, str(broadcast_tag))

        profile = {
            "hud_index": hud_idx,
            "broadcast_tag": str(broadcast_tag),
            "team_id": team.get("team_id") if team else None,
            "team_name": team.get("name") if team else None,
            "team_tag": team.get("tag") if team else str(broadcast_tag),
            "db_name": team.get("db_name") if team else None,
            "db_tag": team.get("db_tag") if team else None,
            "sample_box": {"x": x, "y": y, "w": w, "h": h},
            "median_hsv": color["median_hsv"],
            "hsv_range": color["hsv_range"],
            "median_bgr": color["median_bgr"],
            "pixels": color["pixels"],
            "quality": color["quality"],
        }
        profiles.append(profile)

        crop_name = f"team_{hud_idx:02d}_{broadcast_tag}.jpg"
        cv2.imwrite(str(debug_dir / crop_name), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])

        b, g, r = color["median_bgr"]
        rect_color = (int(b), int(g), int(r))
        cv2.rectangle(debug, (x, y), (x + w, y + h), rect_color, 3)
        cv2.putText(
            debug,
            f"{hud_idx}:{broadcast_tag} HSV={tuple(round(v) for v in color['median_hsv'])}",
            (x, max(0, y - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            rect_color,
            1,
            cv2.LINE_AA,
        )

    result = {
        "source_image": str(image_path),
        "zones": str(zones_path),
        "config": str(config_path),
        "profiles": profiles,
    }

    save_json(out_path, result)
    cv2.imwrite(str(debug_dir / "_hud_color_debug.jpg"), debug, [cv2.IMWRITE_JPEG_QUALITY, 92])

    if args.update_config:
        config["team_color_profiles"] = profiles
        save_json(config_path, config)
        print(f"Updated config: {config_path.resolve()}")

    print(f"Saved color profiles: {out_path.resolve()}")
    print(f"Debug dir: {debug_dir.resolve()}")
    for p in profiles:
        print(
            f"HUD {p['hud_index']:02d} {p['broadcast_tag']:<6} "
            f"{(p['team_name'] or '?'):<24} HSV={tuple(round(v) for v in p['median_hsv'])} "
            f"quality={p['quality']:.2f}"
        )


if __name__ == "__main__":
    main()
