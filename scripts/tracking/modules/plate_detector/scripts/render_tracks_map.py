import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


Point = Tuple[float, float]


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def parse_color(value, fallback_key: str = "") -> Tuple[int, int, int]:
    """Return BGR color for OpenCV."""
    if isinstance(value, str) and re.match(r"^#[0-9a-fA-F]{6}$", value):
        r = int(value[1:3], 16)
        g = int(value[3:5], 16)
        b = int(value[5:7], 16)
        return b, g, r

    digest = hashlib.md5(fallback_key.encode("utf-8")).digest()
    # Bright deterministic BGR color.
    b = 80 + digest[0] % 150
    g = 80 + digest[1] % 150
    r = 80 + digest[2] % 150
    return int(b), int(g), int(r)


def scale_map_to_canonical(map_path: Path, canonical_size: int) -> np.ndarray:
    image = cv2.imread(str(map_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"Cannot read map image: {map_path}")
    return cv2.resize(image, (canonical_size, canonical_size), interpolation=cv2.INTER_AREA)


def get_slots_meta(tracks_data: dict) -> Dict[str, dict]:
    result = {}
    meta = tracks_data.get("meta", {})

    for item in meta.get("slots", []):
        slot_id = item.get("slot_id") or item.get("id") or f"slot_{item.get('slot', len(result) + 1)}"
        result[slot_id] = item

    # If slots are missing, fallback to teams.
    for item in meta.get("teams", []):
        slot_id = item.get("id") or item.get("slot_id")
        if slot_id and slot_id not in result:
            result[slot_id] = item

    return result


def track_label(track: dict, slots_meta: Dict[str, dict]) -> str:
    slot_id = track.get("slot_id") or track.get("team_id") or "unknown"
    meta = slots_meta.get(slot_id, {})
    return (
        track.get("broadcast_tag")
        or track.get("team_tag")
        or meta.get("broadcast_tag")
        or meta.get("team_tag")
        or meta.get("name")
        or slot_id
    )


def track_color(track: dict, slots_meta: Dict[str, dict]) -> Tuple[int, int, int]:
    slot_id = track.get("slot_id") or track.get("team_id") or "unknown"
    meta = slots_meta.get(slot_id, {})
    return parse_color(meta.get("color"), fallback_key=track_label(track, slots_meta))


def point_from_track(
    track: dict,
    coord_source: str,
    left_ignore: int,
    roi_size: int,
    canonical_size: int,
    clamp: bool,
) -> Optional[Point]:
    value = None

    if coord_source == "canonical_px":
        value = track.get("canonical_px")
        if value is None:
            return None
        x, y = float(value[0]), float(value[1])
        # If canonical was saved as 2048 or another size, rescale using meta is unavailable here;
        # assume it is already target-sized unless values clearly look like ROI/fullframe.
        return x, y

    if coord_source == "auto":
        value = track.get("canonical_px") or track.get("frame_px")
    else:
        value = track.get("frame_px")

    if value is None:
        return None

    x_full, y_full = float(value[0]), float(value[1])

    # Current tracks store frame_px in full-frame coordinates.
    # The 1080x1080 map ROI starts at x = left_ignore, y = 0.
    x_roi = x_full - left_ignore
    y_roi = y_full

    x = x_roi * canonical_size / roi_size
    y = y_roi * canonical_size / roi_size

    if clamp:
        x = max(0.0, min(float(canonical_size - 1), x))
        y = max(0.0, min(float(canonical_size - 1), y))

    return x, y


def find_previous_point(
    frames: List[dict],
    frame_pos: int,
    slot_id: str,
    coord_source: str,
    left_ignore: int,
    roi_size: int,
    canonical_size: int,
    max_back: int,
    allowed_states: set,
) -> Optional[Tuple[int, Point, dict]]:
    start = max(0, frame_pos - max_back) if max_back > 0 else 0
    for i in range(frame_pos - 1, start - 1, -1):
        frame = frames[i]
        for tr in frame.get("tracks", []):
            if (tr.get("slot_id") or tr.get("team_id")) != slot_id:
                continue
            if allowed_states and tr.get("state") not in allowed_states:
                continue
            p = point_from_track(tr, coord_source, left_ignore, roi_size, canonical_size, clamp=True)
            if p is not None:
                return i, p, tr
    return None


def draw_text_with_outline(img, text: str, org: Tuple[int, int], scale: float, color: Tuple[int, int, int], thickness: int = 1):
    x, y = org
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 3, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)



def text_size(text: str, scale: float, thickness: int) -> Tuple[int, int]:
    (w, h), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    return w, h + baseline


def boxes_intersect(a, b, pad: int = 2) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 + pad < bx1 or bx2 + pad < ax1 or ay2 + pad < by1 or by2 + pad < ay1)


def place_labels(img, label_entries: List[dict], args: argparse.Namespace) -> None:
    placed = []
    offsets = [
        (args.label_dx, args.label_dy),
        (args.label_dx, args.label_dy - 24),
        (args.label_dx, args.label_dy + 24),
        (-90, args.label_dy),
        (-90, args.label_dy - 24),
        (-90, args.label_dy + 24),
        (18, 34),
        (-110, 34),
        (18, -44),
        (-110, -44),
    ]
    for e in label_entries:
        text = e["text"]
        x, y = e["point"]
        color = e["color"]
        w, h = text_size(text, args.font_scale, args.font_thickness)
        chosen = None
        for dx, dy in offsets if args.avoid_label_collisions else [(args.label_dx, args.label_dy)]:
            tx, ty = int(x + dx), int(y + dy)
            tx = max(4, min(args.canonical_size - w - 4, tx))
            ty = max(h + 4, min(args.canonical_size - 4, ty))
            box = (tx, ty - h, tx + w, ty + 4)
            if not any(boxes_intersect(box, b, pad=args.label_pad) for b in placed):
                chosen = (tx, ty, box)
                break
        if chosen is None:
            # fallback: stack downward near point
            tx, ty = int(x + args.label_dx), int(y + args.label_dy)
            for _ in range(20):
                tx = max(4, min(args.canonical_size - w - 4, tx))
                ty = max(h + 4, min(args.canonical_size - 4, ty))
                box = (tx, ty - h, tx + w, ty + 4)
                if not any(boxes_intersect(box, b, pad=args.label_pad) for b in placed):
                    chosen = (tx, ty, box)
                    break
                ty += h + args.label_pad
        if chosen is None:
            tx, ty = max(4, min(args.canonical_size - w - 4, int(x + args.label_dx))), max(h + 4, min(args.canonical_size - 4, int(y + args.label_dy)))
            chosen = (tx, ty, (tx, ty - h, tx + w, ty + 4))
        tx, ty, box = chosen
        placed.append(box)
        if args.leader_lines and math.hypot(tx - x, ty - y) > 30:
            cv2.line(img, (int(x), int(y)), (tx, ty - h // 2), color, max(1, args.line_thickness // 2), cv2.LINE_AA)
        draw_text_with_outline(img, text, (tx, ty), args.font_scale, (255, 255, 255), args.font_thickness)

def render_one_frame(
    base_map: np.ndarray,
    frames: List[dict],
    frame_pos: int,
    slots_meta: Dict[str, dict],
    args: argparse.Namespace,
) -> Tuple[np.ndarray, List[dict]]:
    img = base_map.copy()
    frame = frames[frame_pos]
    allowed_states = set(args.states.split(",")) if args.states else set()
    only_tags = {x.strip().upper() for x in args.only_tags.split(",") if x.strip()}
    suspicious_rows = []
    label_entries = []

    # Dark overlay for readability.
    if args.dim > 0:
        overlay = np.zeros_like(img)
        img = cv2.addWeighted(img, 1.0 - args.dim, overlay, args.dim, 0)

    for tr in frame.get("tracks", []):
        if allowed_states and tr.get("state") not in allowed_states:
            continue

        slot_id = tr.get("slot_id") or tr.get("team_id") or "unknown"
        label = track_label(tr, slots_meta)
        if only_tags and label.upper() not in only_tags:
            continue
        color = track_color(tr, slots_meta)
        point = point_from_track(
            tr,
            args.coord_source,
            args.left_ignore,
            args.roi_size,
            args.canonical_size,
            clamp=True,
        )

        if point is None:
            continue

        x, y = int(round(point[0])), int(round(point[1]))

        prev = find_previous_point(
            frames,
            frame_pos,
            slot_id,
            args.coord_source,
            args.left_ignore,
            args.roi_size,
            args.canonical_size,
            args.max_back,
            allowed_states,
        )

        jump = None
        line_color = color
        if prev is not None:
            prev_pos, prev_point, prev_tr = prev
            px, py = int(round(prev_point[0])), int(round(prev_point[1]))
            jump = math.hypot(x - px, y - py)
            if args.jump_threshold > 0 and jump > args.jump_threshold:
                line_color = (0, 0, 255)
                suspicious_rows.append({
                    "frame_index": frame_pos,
                    "frame": frame.get("frame"),
                    "t": frame.get("t"),
                    "slot_id": slot_id,
                    "tag": label,
                    "prev_frame_index": prev_pos,
                    "prev_frame": frames[prev_pos].get("frame"),
                    "prev_t": frames[prev_pos].get("t"),
                    "x": x,
                    "y": y,
                    "prev_x": px,
                    "prev_y": py,
                    "jump_px": round(jump, 2),
                    "state": tr.get("state"),
                    "state_reason": tr.get("state_reason"),
                })
            cv2.line(img, (px, py), (x, y), line_color, args.line_thickness, cv2.LINE_AA)
            cv2.circle(img, (px, py), max(2, args.point_radius - 2), color, -1, cv2.LINE_AA)

        cv2.circle(img, (x, y), args.point_radius + 3, (0, 0, 0), -1, cv2.LINE_AA)
        cv2.circle(img, (x, y), args.point_radius, color, -1, cv2.LINE_AA)

        label_text = label
        if args.show_state:
            label_text += f" {tr.get('state', '')[:1]}"
        if args.show_conf:
            label_text += f" {float(tr.get('confidence') or 0):.2f}"
        if jump is not None and args.show_jump:
            label_text += f" d={jump:.0f}"

        label_entries.append({"text": label_text, "point": (x, y), "color": color})

    place_labels(img, label_entries, args)

    title = f"frame_idx={frame.get('frame')}  t={frame.get('t')}s  rendered={frame_pos + 1}/{len(frames)}"
    cv2.rectangle(img, (10, 10), (min(args.canonical_size - 10, 760), 58), (0, 0, 0), -1)
    draw_text_with_outline(img, title, (22, 44), 0.9, (255, 255, 255), 2)

    return img, suspicious_rows


def choose_frame_pos(frames: List[dict], args: argparse.Namespace) -> int:
    if args.frame_index is not None:
        return max(0, min(len(frames) - 1, args.frame_index))

    if args.frame is not None:
        best_i = min(range(len(frames)), key=lambda i: abs(int(frames[i].get("frame", 0)) - args.frame))
        return best_i

    if args.time is not None:
        best_i = min(range(len(frames)), key=lambda i: abs(float(frames[i].get("t", 0.0)) - args.time))
        return best_i

    return 0


def write_suspicious_csv(path: Path, rows: List[dict]) -> None:
    if not rows:
        return
    ensure_dir(path.parent)
    fields = [
        "frame_index", "frame", "t", "slot_id", "tag", "prev_frame_index", "prev_frame", "prev_t",
        "x", "y", "prev_x", "prev_y", "jump_px", "state", "state_reason"
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Render tracks.json on a canonical map. Draws line between last two points for each team.")
    parser.add_argument("--tracks", required=True, help="Path to tracks.json")
    parser.add_argument("--map", required=True, help="Canonical map image")
    parser.add_argument("--out", required=True, help="Output PNG path or output directory when --all-frames is used")
    parser.add_argument("--canonical-size", type=int, default=2000, help="Resize map to this square size")
    parser.add_argument("--coord-source", choices=["frame_px", "canonical_px", "auto"], default="frame_px")
    parser.add_argument("--left-ignore", type=int, default=420, help="Full-frame x offset of 1080x1080 ROI")
    parser.add_argument("--roi-size", type=int, default=1080)
    parser.add_argument("--frame-index", type=int, default=None, help="Index inside tracks.frames array")
    parser.add_argument("--frame", type=int, default=None, help="actual/source frame number, nearest will be used")
    parser.add_argument("--time", type=float, default=None, help="time in seconds, nearest will be used")
    parser.add_argument("--all-frames", action="store_true", help="Render every tracks frame into output directory")
    parser.add_argument("--make-video", default=None, help="Optional MP4 path created from rendered frames")
    parser.add_argument("--fps", type=float, default=6.0)
    parser.add_argument("--max-back", type=int, default=1, help="How many previous entries to search for previous point. 1 = strictly last frame")
    parser.add_argument("--states", default="tracked,carried", help="Comma-separated states to draw. Empty string = all")
    parser.add_argument("--jump-threshold", type=float, default=180.0, help="Mark line red if jump is larger than this many canonical pixels. 0 disables")
    parser.add_argument("--only-tags", default="", help="Optional comma-separated broadcast tags to render, for example: BB,THUG")
    parser.add_argument("--suspicious-csv", default=None)
    parser.add_argument("--dim", type=float, default=0.18, help="Darken map for readability, 0..1")
    parser.add_argument("--point-radius", type=int, default=8)
    parser.add_argument("--line-thickness", type=int, default=4)
    parser.add_argument("--font-scale", type=float, default=0.72)
    parser.add_argument("--font-thickness", type=int, default=2)
    parser.add_argument("--label-dx", type=int, default=12)
    parser.add_argument("--label-dy", type=int, default=-10)
    parser.add_argument("--avoid-label-collisions", action="store_true", default=True)
    parser.add_argument("--no-avoid-label-collisions", dest="avoid_label_collisions", action="store_false")
    parser.add_argument("--leader-lines", action="store_true", default=True)
    parser.add_argument("--no-leader-lines", dest="leader_lines", action="store_false")
    parser.add_argument("--label-pad", type=int, default=6)
    parser.add_argument("--show-conf", action="store_true")
    parser.add_argument("--show-state", action="store_true")
    parser.add_argument("--show-jump", action="store_true")
    args = parser.parse_args()

    tracks_path = Path(args.tracks)
    map_path = Path(args.map)
    data = load_json(tracks_path)
    frames = data.get("frames", [])
    if not frames:
        raise RuntimeError("No frames found in tracks.json")

    slots_meta = get_slots_meta(data)
    base_map = scale_map_to_canonical(map_path, args.canonical_size)

    all_suspicious = []

    if args.all_frames:
        out_dir = Path(args.out)
        ensure_dir(out_dir)
        rendered_paths = []
        for i in range(len(frames)):
            img, suspicious = render_one_frame(base_map, frames, i, slots_meta, args)
            all_suspicious.extend(suspicious)
            out_path = out_dir / f"tracks_{i:05d}_frame_{frames[i].get('frame')}.png"
            cv2.imwrite(str(out_path), img)
            rendered_paths.append(out_path)

        if args.make_video:
            video_path = Path(args.make_video)
            ensure_dir(video_path.parent)
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(video_path), fourcc, args.fps, (args.canonical_size, args.canonical_size))
            for p in rendered_paths:
                frame_img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                writer.write(frame_img)
            writer.release()
            print(f"Video saved: {video_path.resolve()}")

        print(f"Rendered frames: {len(rendered_paths)} to {out_dir.resolve()}")
    else:
        frame_pos = choose_frame_pos(frames, args)
        img, suspicious = render_one_frame(base_map, frames, frame_pos, slots_meta, args)
        all_suspicious.extend(suspicious)
        out_path = Path(args.out)
        ensure_dir(out_path.parent)
        cv2.imwrite(str(out_path), img)
        print(f"Rendered: {out_path.resolve()}")
        print(f"Rendered tracks frame index: {frame_pos}, source frame: {frames[frame_pos].get('frame')}, t={frames[frame_pos].get('t')}")

    if args.suspicious_csv:
        write_suspicious_csv(Path(args.suspicious_csv), all_suspicious)
        print(f"Suspicious jumps: {len(all_suspicious)} saved to {Path(args.suspicious_csv).resolve()}")
    elif all_suspicious:
        print(f"Suspicious jumps detected: {len(all_suspicious)}. Use --suspicious-csv to save them.")


if __name__ == "__main__":
    main()
