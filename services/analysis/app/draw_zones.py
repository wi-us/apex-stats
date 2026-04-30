"""
Interactive zone drawing tool for map reliability filtering.

Use this tool to draw polygons on a map image and save them as JSON.
You can later share the JSON file for integration into analysis filters.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WINDOW_NAME = "Zone Drawer"

ZONE_COLORS = {
    "forbidden": (60, 60, 230),   # red-ish (BGR)
    "transient": (20, 160, 240),  # orange-ish
    "trusted": (70, 190, 70),     # green-ish
}

ZONE_ORDER = ["forbidden", "transient", "trusted"]


def normalize_map_name(map_name: str) -> str:
    return map_name if map_name.startswith("mp_") else f"mp_{map_name}"


def resolve_map_image(map_name: str, explicit_path: str | None) -> Path:
    if explicit_path:
        image_path = Path(explicit_path)
        if not image_path.is_absolute():
            image_path = PROJECT_ROOT / image_path
        if image_path.exists():
            return image_path
        raise FileNotFoundError(f"Map image not found: {image_path}")

    normalized = normalize_map_name(map_name)
    short_name = normalized.removeprefix("mp_")
    maps_dir = PROJECT_ROOT / "maps"
    output_dir = PROJECT_ROOT / "output"

    candidates = [
        maps_dir / f"{normalized}.png",
        maps_dir / f"{normalized}.webp",
        maps_dir / f"{normalized}.jpg",
        maps_dir / f"{normalized}.jpeg",
        maps_dir / f"{short_name}.png",
        maps_dir / f"{short_name}.webp",
        output_dir / f"map_background_{normalized}.png",
        output_dir / f"map_background_{short_name}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Map image not found. Provide --map-image or add one of: "
        f"{', '.join(str(c) for c in candidates)}"
    )


def load_image_safe(image_path: Path) -> np.ndarray:
    """
    Load image robustly on Windows paths with non-ASCII characters.

    cv2.imread() may fail on Cyrillic/Unicode paths, so we use
    np.fromfile + cv2.imdecode as the primary path.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Map image not found: {image_path}")

    try:
        data = np.fromfile(str(image_path), dtype=np.uint8)
        if data.size > 0:
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if image is not None:
                return image
    except Exception:
        pass

    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(
            "Cannot open image: "
            f"{image_path}. If this is a Unicode-path issue, pass --map-image with a short ASCII path."
        )
    return image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Draw zone polygons on map image")
    parser.add_argument("--map", default="mp_storm_point", help="Map key, for example mp_storm_point")
    parser.add_argument("--map-image", help="Optional explicit path to map image")
    parser.add_argument(
        "--output",
        help="Output path for zones JSON (default: output/zones/<map>.zones.json)",
    )
    parser.add_argument(
        "--transient-max-dwell",
        type=float,
        default=8.0,
        help="Default max dwell time (seconds) for transient zones",
    )
    return parser


def draw_scene(
    base_image: np.ndarray,
    zones: list[dict[str, Any]],
    current_points: list[tuple[int, int]],
    current_type: str,
    transient_max_dwell: float,
) -> np.ndarray:
    frame = base_image.copy()
    overlay = frame.copy()

    # Render saved zones.
    for zone in zones:
        polygon = np.array(zone["polygon"], dtype=np.int32)
        color = ZONE_COLORS.get(zone["type"], (180, 180, 180))
        if len(polygon) >= 3:
            cv2.fillPoly(overlay, [polygon], color)
        cv2.polylines(frame, [polygon], True, color, 2)
        label = zone["id"]
        if zone["type"] == "transient":
            label += f" ({zone.get('max_dwell_sec', transient_max_dwell):.1f}s)"
        tx, ty = polygon[0]
        cv2.putText(frame, label, (tx + 5, ty - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1)

    # Render in-progress polygon.
    if current_points:
        points = np.array(current_points, dtype=np.int32)
        for x, y in current_points:
            cv2.circle(frame, (x, y), 4, (255, 255, 255), -1)
        if len(points) > 1:
            cv2.polylines(frame, [points], False, ZONE_COLORS[current_type], 1)

    frame = cv2.addWeighted(overlay, 0.22, frame, 0.78, 0.0)

    # Toolbar text.
    help_lines = [
        f"Mode: {current_type}",
        "LMB: add point | Enter: finish polygon | U/Z: undo point | C/X: clear current",
        "1: forbidden | 2: transient | 3: trusted | D/Delete: delete last zone",
        "S: save JSON | Q or ESC: exit",
    ]
    for idx, line in enumerate(help_lines):
        cv2.putText(frame, line, (10, 20 + idx * 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

    return frame


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    map_name = normalize_map_name(args.map)
    map_image_path = resolve_map_image(map_name, args.map_image)
    image = load_image_safe(map_image_path)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
    else:
        output_path = PROJECT_ROOT / "output" / "zones" / f"{map_name}.zones.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    zones: list[dict[str, Any]] = []
    current_points: list[tuple[int, int]] = []
    current_type_idx = 0

    def on_mouse(event: int, x: int, y: int, _flags: int, _userdata: Any) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            current_points.append((int(x), int(y)))

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW_NAME, on_mouse)

    while True:
        current_type = ZONE_ORDER[current_type_idx]
        frame = draw_scene(image, zones, current_points, current_type, args.transient_max_dwell)
        cv2.imshow(WINDOW_NAME, frame)

        key = cv2.waitKeyEx(20)
        key_ascii = key & 0xFF
        key_char = chr(key_ascii).lower() if 32 <= key_ascii <= 126 else ""

        if key in (27,) or key_char == "q":  # ESC / Q
            break
        if key in (13, 10):  # Enter
            if len(current_points) >= 3:
                zone_id = f"{current_type}_{len(zones) + 1}"
                zone: dict[str, Any] = {
                    "id": zone_id,
                    "type": current_type,
                    "polygon": [[x, y] for x, y in current_points],
                }
                if current_type == "transient":
                    zone["max_dwell_sec"] = float(args.transient_max_dwell)
                zones.append(zone)
                current_points.clear()
        elif key_ascii in (8,):  # Backspace
            if current_points:
                current_points.pop()
        elif key_char in ("u", "z"):
            if current_points:
                current_points.pop()
        elif key_char in ("c", "x"):
            current_points.clear()
        elif key_char == "d" or key in (3014656,):  # Delete key in waitKeyEx (Windows)
            if zones:
                zones.pop()
        elif key_char == "1":
            current_type_idx = 0
        elif key_char == "2":
            current_type_idx = 1
        elif key_char == "3":
            current_type_idx = 2
        elif key_char == "s":
            payload = {
                "map": map_name,
                "image_path": str(map_image_path.relative_to(PROJECT_ROOT)) if map_image_path.is_relative_to(PROJECT_ROOT) else str(map_image_path),
                "image_size": {"width": int(image.shape[1]), "height": int(image.shape[0])},
                "zones": zones,
            }
            with output_path.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
            print(f"Saved zones: {output_path}")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
