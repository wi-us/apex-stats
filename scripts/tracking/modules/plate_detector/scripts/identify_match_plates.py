import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np
from tqdm import tqdm


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# -----------------------------
# Basic helpers
# -----------------------------

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def time_hms(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        seconds += 1
        ms = 0
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def safe_tag(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "", (s or "").upper())


def central_roi(frame: np.ndarray, left_ignore: int = 420, roi_size: int = 1080) -> Tuple[np.ndarray, int, int]:
    h, w = frame.shape[:2]
    if w < roi_size or h < roi_size:
        raise ValueError(f"Frame is too small: {w}x{h}, need at least {roi_size}x{roi_size}")

    if w >= left_ignore * 2 + roi_size:
        x1 = left_ignore
    else:
        x1 = max(0, (w - roi_size) // 2)

    y1 = max(0, (h - roi_size) // 2)
    return frame[y1:y1 + roi_size, x1:x1 + roi_size], x1, y1


def iter_images(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            yield p


# -----------------------------
# Team / profile model
# -----------------------------

@dataclass
class ColorProfile:
    hud_index: int
    broadcast_tag: str
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    team_tag: Optional[str] = None
    hex_color: Optional[str] = None
    median_hsv: Optional[Tuple[float, float, float]] = None
    h_range: Optional[Tuple[int, int]] = None
    s_range: Optional[Tuple[int, int]] = None
    v_range: Optional[Tuple[int, int]] = None
    aliases: Optional[List[str]] = None
    source: str = "unknown"

    @property
    def tag(self) -> str:
        return self.broadcast_tag


def normalize_range(value, lo: int, hi: int) -> Tuple[int, int]:
    if value is None:
        return lo, hi
    if isinstance(value, list) and len(value) >= 2:
        a, b = int(value[0]), int(value[1])
    else:
        a = b = int(value)
    a = max(lo, min(hi, a))
    b = max(lo, min(hi, b))
    if a > b:
        a, b = b, a
    return a, b


def get_config_hud_order(config: dict) -> Dict[int, str]:
    order = config.get("hud_team_order") or {}
    out: Dict[int, str] = {}
    for k, v in order.items():
        try:
            out[int(k)] = str(v).upper()
        except Exception:
            continue
    return out


def get_team_meta_from_config(config: dict) -> Dict[str, dict]:
    """Map broadcast tag / aliases to team metadata."""
    meta = {}

    aliases_cfg = config.get("broadcast_tag_aliases") or {}
    alias_to_tag = {}
    for tag, aliases in aliases_cfg.items():
        tag_u = safe_tag(tag)
        alias_to_tag[tag_u] = tag_u
        for a in aliases:
            alias_to_tag[safe_tag(str(a))] = tag_u

    for t in config.get("teams", []):
        possible = [
            t.get("tag"),
            t.get("name"),
            t.get("db_tag"),
            t.get("db_name"),
            t.get("broadcast_tag"),
            t.get("short_name"),
        ]
        aliases = t.get("aliases") or []
        possible += aliases

        broadcast_tag = None
        for p in possible:
            n = safe_tag(str(p or ""))
            if n in alias_to_tag:
                broadcast_tag = alias_to_tag[n]
                break
        if not broadcast_tag:
            broadcast_tag = safe_tag(str(t.get("tag") or t.get("short_name") or t.get("name") or ""))

        if not broadcast_tag:
            continue

        existing_aliases = set()
        for p in possible:
            if p:
                existing_aliases.add(str(p))
                existing_aliases.add(safe_tag(str(p)))
        for a in aliases_cfg.get(broadcast_tag, []):
            existing_aliases.add(str(a))
            existing_aliases.add(safe_tag(str(a)))

        meta[broadcast_tag] = {
            "team_id": t.get("team_id") or t.get("id"),
            "team_name": t.get("name") or t.get("db_name") or broadcast_tag,
            "team_tag": t.get("tag") or t.get("db_tag") or broadcast_tag,
            "aliases": sorted(a for a in existing_aliases if a),
        }

    # Ensure aliases from config exist even if team not in teams.
    for tag, aliases in aliases_cfg.items():
        tag_u = safe_tag(tag)
        if tag_u not in meta:
            meta[tag_u] = {
                "team_id": None,
                "team_name": tag_u,
                "team_tag": tag_u,
                "aliases": [tag_u] + list(aliases),
            }
        else:
            for a in aliases:
                if a not in meta[tag_u]["aliases"]:
                    meta[tag_u]["aliases"].append(a)

    return meta


def load_profiles_from_hsv_presets(path: Path, config: dict) -> List[ColorProfile]:
    data = load_json(path)
    hud_order = get_config_hud_order(config)
    team_meta = get_team_meta_from_config(config)

    profiles = []
    for item in data.get("teams", []):
        slot = int(item["slot"])
        tag = safe_tag(hud_order.get(slot, f"TEAM{slot}"))
        meta = team_meta.get(tag, {})

        profiles.append(ColorProfile(
            hud_index=slot,
            broadcast_tag=tag,
            team_id=meta.get("team_id"),
            team_name=meta.get("team_name") or tag,
            team_tag=meta.get("team_tag") or tag,
            hex_color=item.get("hex"),
            median_hsv=None,
            h_range=normalize_range(item.get("h"), 0, 179),
            s_range=normalize_range(item.get("s"), 0, 255),
            v_range=normalize_range(item.get("v"), 0, 255),
            aliases=meta.get("aliases") or [tag],
            source=f"hsv_preset:{path.name}",
        ))

    return sorted(profiles, key=lambda p: p.hud_index)


def _profile_list_from_json(data: dict) -> list:
    if isinstance(data.get("team_color_profiles"), list):
        return data["team_color_profiles"]
    if isinstance(data.get("profiles"), list):
        return data["profiles"]
    if isinstance(data.get("teams"), list):
        return data["teams"]
    return []


def load_profiles_from_color_file(path: Path, config: dict) -> List[ColorProfile]:
    data = load_json(path)
    team_meta = get_team_meta_from_config(config)
    profiles = []

    for idx, item in enumerate(_profile_list_from_json(data), start=1):
        slot = int(item.get("hud_index") or item.get("slot") or item.get("team_index") or idx)
        tag = safe_tag(item.get("broadcast_tag") or item.get("tag") or item.get("team_tag") or f"TEAM{slot}")
        meta = team_meta.get(tag, {})

        median_hsv = item.get("median_hsv") or item.get("hsv") or item.get("mean_hsv")
        if isinstance(median_hsv, list) and len(median_hsv) >= 3:
            median_hsv = (float(median_hsv[0]), float(median_hsv[1]), float(median_hsv[2]))
        else:
            median_hsv = None

        h_range = normalize_range(item.get("h") or item.get("h_range"), 0, 179) if (item.get("h") or item.get("h_range")) else None
        s_range = normalize_range(item.get("s") or item.get("s_range"), 0, 255) if (item.get("s") or item.get("s_range")) else None
        v_range = normalize_range(item.get("v") or item.get("v_range"), 0, 255) if (item.get("v") or item.get("v_range")) else None

        profiles.append(ColorProfile(
            hud_index=slot,
            broadcast_tag=tag,
            team_id=item.get("team_id") or meta.get("team_id"),
            team_name=item.get("team_name") or item.get("name") or meta.get("team_name") or tag,
            team_tag=item.get("team_tag") or meta.get("team_tag") or tag,
            hex_color=item.get("color_hex") or item.get("hex") or item.get("color"),
            median_hsv=median_hsv,
            h_range=h_range,
            s_range=s_range,
            v_range=v_range,
            aliases=item.get("aliases") or meta.get("aliases") or [tag],
            source=f"color_file:{path.name}",
        ))

    return sorted(profiles, key=lambda p: p.hud_index)


def merge_profiles(primary: List[ColorProfile], secondary: List[ColorProfile]) -> List[ColorProfile]:
    """Primary wins by broadcast_tag. Used to let hsv presets override HUD-calibrated colors."""
    by_tag = {p.broadcast_tag: p for p in secondary}
    for p in primary:
        by_tag[p.broadcast_tag] = p
    return sorted(by_tag.values(), key=lambda p: p.hud_index)


def load_color_profiles(config: dict, color_profiles: Optional[str], hsv_presets: Optional[str], prefer_presets: bool) -> List[ColorProfile]:
    profiles_from_color = []
    profiles_from_presets = []

    if color_profiles:
        profiles_from_color = load_profiles_from_color_file(Path(color_profiles), config)

    if hsv_presets:
        profiles_from_presets = load_profiles_from_hsv_presets(Path(hsv_presets), config)

    if profiles_from_presets and prefer_presets:
        return merge_profiles(profiles_from_presets, profiles_from_color)
    if profiles_from_color:
        return merge_profiles(profiles_from_color, profiles_from_presets)
    if profiles_from_presets:
        return profiles_from_presets

    # Fallback: config embedded team_color_profiles.
    if config.get("team_color_profiles"):
        tmp = {"team_color_profiles": config.get("team_color_profiles")}
        tmp_path = Path("__config_embedded__")
        team_meta = get_team_meta_from_config(config)
        profiles = []
        for idx, item in enumerate(tmp["team_color_profiles"], start=1):
            slot = int(item.get("hud_index") or item.get("slot") or idx)
            tag = safe_tag(item.get("broadcast_tag") or item.get("tag") or f"TEAM{slot}")
            meta = team_meta.get(tag, {})
            profiles.append(ColorProfile(
                hud_index=slot,
                broadcast_tag=tag,
                team_id=item.get("team_id") or meta.get("team_id"),
                team_name=item.get("team_name") or item.get("name") or meta.get("team_name") or tag,
                team_tag=item.get("team_tag") or meta.get("team_tag") or tag,
                median_hsv=tuple(item.get("median_hsv")) if isinstance(item.get("median_hsv"), list) else None,
                h_range=normalize_range(item.get("h"), 0, 179) if item.get("h") else None,
                s_range=normalize_range(item.get("s"), 0, 255) if item.get("s") else None,
                v_range=normalize_range(item.get("v"), 0, 255) if item.get("v") else None,
                aliases=item.get("aliases") or meta.get("aliases") or [tag],
                source="config_embedded",
            ))
        return sorted(profiles, key=lambda p: p.hud_index)

    raise RuntimeError("No color profiles loaded. Provide --color-profiles or --hsv-presets.")


# -----------------------------
# HSV extraction and matching
# -----------------------------

def dominant_plate_hsv(crop: np.ndarray) -> Tuple[float, float, float]:
    if crop.size == 0:
        return 0.0, 0.0, 0.0

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Keep colored backing, suppress white text and almost-black background.
    mask = (hsv[:, :, 1] > 45) & (hsv[:, :, 2] > 45) & ~((gray > 135) & (hsv[:, :, 1] < 180))

    if np.mean(mask) < 0.03:
        mask = (hsv[:, :, 1] > 25) & (hsv[:, :, 2] > 35)

    if np.mean(mask) < 0.01:
        values = hsv.reshape(-1, 3)
    else:
        values = hsv[mask]

    med = np.median(values, axis=0)
    return float(med[0]), float(med[1]), float(med[2])


def hue_distance_to_range(h: float, h_range: Tuple[int, int]) -> float:
    lo, hi = h_range
    # OpenCV hue is 0..179. Some presets may use 180 as upper red bound.
    if hi >= 180:
        hi = 179
    if lo <= h <= hi:
        return 0.0

    # Handle red wrap if needed, e.g. [170, 179] or [0, 3] are not joined here,
    # but cyclic distance to nearest boundary is enough.
    d1 = min(abs(h - lo), 180 - abs(h - lo))
    d2 = min(abs(h - hi), 180 - abs(h - hi))
    return min(d1, d2)


def linear_distance_to_range(x: float, r: Tuple[int, int]) -> float:
    lo, hi = r
    if lo <= x <= hi:
        return 0.0
    return min(abs(x - lo), abs(x - hi))


def hsv_score_to_profile(hsv: Tuple[float, float, float], p: ColorProfile) -> float:
    h, s, v = hsv

    if p.h_range and p.s_range and p.v_range:
        dh = hue_distance_to_range(h, p.h_range) / 12.0
        ds = linear_distance_to_range(s, p.s_range) / 55.0
        dv = linear_distance_to_range(v, p.v_range) / 65.0
        dist = math.sqrt(dh * dh + ds * ds + dv * dv)
        return max(0.0, 1.0 - dist)

    if p.median_hsv is not None:
        ph, ps, pv = p.median_hsv
        dh = min(abs(h - ph), 180 - abs(h - ph)) / 18.0
        ds = abs(s - ps) / 90.0
        dv = abs(v - pv) / 95.0
        dist = math.sqrt(dh * dh + ds * ds + dv * dv)
        return max(0.0, 1.0 - dist)

    return 0.0


def match_color(hsv: Tuple[float, float, float], profiles: List[ColorProfile]) -> List[Tuple[ColorProfile, float]]:
    scored = [(p, hsv_score_to_profile(hsv, p)) for p in profiles]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# -----------------------------
# Targeted text / template verification
# -----------------------------

def normalize_text_alias(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())


class TargetedTextResolver:
    def __init__(self, profiles: List[ColorProfile], font_path: Optional[str], font_size: int = 24):
        self.enabled = False
        self.templates: Dict[str, List[Tuple[str, np.ndarray]]] = {}

        if not font_path:
            return

        try:
            from PIL import Image, ImageDraw, ImageFont
            self.Image = Image
            self.ImageDraw = ImageDraw
            self.font = ImageFont.truetype(str(font_path), font_size)
        except Exception as e:
            print(f"WARNING: text resolver disabled: cannot load font {font_path}: {e}")
            return

        for p in profiles:
            aliases = set(p.aliases or [])
            aliases.add(p.broadcast_tag)
            aliases.add(p.team_tag or p.broadcast_tag)
            aliases.add(p.team_name or p.broadcast_tag)

            # Practical hardcoded aliases for known map labels.
            if p.broadcast_tag == "BB":
                aliases.update(["BB", "BUCKLE BOYS", "BUCKLEBOYS", "Buckle Boys"])
            if p.broadcast_tag == "THUG":
                aliases.update(["THUG", "THUGGETS", "THUGGETS"])

            for a in aliases:
                norm = normalize_text_alias(a)
                if len(norm) < 2:
                    continue
                templ = self._render(norm)
                if templ is not None:
                    self.templates.setdefault(p.broadcast_tag, []).append((a, templ))

        self.enabled = bool(self.templates)

    def _render(self, text: str) -> Optional[np.ndarray]:
        img = self.Image.new("L", (520, 90), 0)
        draw = self.ImageDraw.Draw(img)
        draw.text((5, 8), text, font=self.font, fill=255)
        arr = np.array(img)
        ys, xs = np.where(arr > 10)
        if len(xs) == 0:
            return None
        return arr[max(0, ys.min() - 2):min(arr.shape[0], ys.max() + 3),
                   max(0, xs.min() - 2):min(arr.shape[1], xs.max() + 3)]

    @staticmethod
    def _crop_text_mask(crop: np.ndarray) -> np.ndarray:
        if crop.size == 0:
            return np.zeros((1, 1), dtype=np.uint8)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

        # White/light text on team plate.
        mask = ((gray > 120) & (hsv[:, :, 1] < 210)).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        return mask

    @staticmethod
    def _score_template(mask: np.ndarray, templ: np.ndarray) -> float:
        if mask.size == 0 or templ.size == 0:
            return 0.0

        h, w = mask.shape[:2]
        th, tw = templ.shape[:2]
        if h < 8 or w < 8 or th < 4 or tw < 4:
            return 0.0

        best = 0.0
        a = mask.astype(np.float32) / 255.0

        # Try several template heights and positions via matchTemplate.
        for height_factor in [0.55, 0.65, 0.75, 0.85, 0.95, 1.05]:
            new_h = max(4, int(h * height_factor))
            new_w = max(4, int(tw * (new_h / max(th, 1))))
            if new_w > w * 1.35 or new_h > h * 1.15:
                continue

            t = cv2.resize(templ, (new_w, new_h), interpolation=cv2.INTER_AREA)
            b = t.astype(np.float32) / 255.0

            if b.shape[0] <= a.shape[0] and b.shape[1] <= a.shape[1]:
                res = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
                if res.size:
                    best = max(best, float(np.nanmax(res)))

            # Also IoU-like center score; helps if crop and template are close but matchTemplate is noisy.
            canvas = np.zeros((h, w), dtype=np.float32)
            y = max(0, (h - new_h) // 2)
            x = max(0, (w - new_w) // 2)
            hh = min(new_h, h - y)
            ww = min(new_w, w - x)
            if hh > 0 and ww > 0:
                canvas[y:y+hh, x:x+ww] = b[:hh, :ww]
                inter = float(np.sum(np.minimum(a, canvas)))
                denom = float(np.sum(canvas) + 1e-6)
                best = max(best, inter / denom)

        return max(0.0, min(1.0, best))

    def resolve(self, crop: np.ndarray, candidate_tags: List[str]) -> Tuple[Optional[str], float, Dict[str, float]]:
        if not self.enabled:
            return None, 0.0, {}

        mask = self._crop_text_mask(crop)
        scores: Dict[str, float] = {}

        for tag in candidate_tags:
            tag = safe_tag(tag)
            best = 0.0
            for alias, templ in self.templates.get(tag, []):
                best = max(best, self._score_template(mask, templ))
            scores[tag] = best

        if not scores:
            return None, 0.0, {}

        best_tag, best_score = max(scores.items(), key=lambda x: x[1])
        return best_tag, float(best_score), scores


def parse_conflict_pairs(config: dict, cli_pairs: Optional[str]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []

    # Default known issue.
    pairs.append(("BB", "THUG"))

    cfg = config.get("color_conflicts") or {}
    if isinstance(cfg, dict):
        for a, values in cfg.items():
            for b in values:
                pairs.append((safe_tag(a), safe_tag(str(b))))
    elif isinstance(cfg, list):
        for item in cfg:
            if isinstance(item, list) and len(item) >= 2:
                pairs.append((safe_tag(str(item[0])), safe_tag(str(item[1]))))

    if cli_pairs:
        for pair in cli_pairs.split(","):
            if ":" in pair:
                a, b = pair.split(":", 1)
            elif "-" in pair:
                a, b = pair.split("-", 1)
            else:
                continue
            pairs.append((safe_tag(a), safe_tag(b)))

    # Deduplicate as unordered pairs.
    seen = set()
    out = []
    for a, b in pairs:
        if not a or not b or a == b:
            continue
        key = tuple(sorted([a, b]))
        if key not in seen:
            seen.add(key)
            out.append((a, b))
    return out


def is_conflict_pair(a: str, b: str, pairs: List[Tuple[str, str]]) -> bool:
    a = safe_tag(a)
    b = safe_tag(b)
    return any({a, b} == {x, y} for x, y in pairs)


def conflict_candidates(top_matches: List[Tuple[ColorProfile, float]], pairs: List[Tuple[str, str]]) -> List[str]:
    if not top_matches:
        return []

    tags = [m[0].broadcast_tag for m in top_matches[:4]]
    candidates = set(tags[:2])

    # If any top tag belongs to a conflict pair, include its counterpart.
    for t in tags:
        for a, b in pairs:
            if t == a:
                candidates.add(b)
            elif t == b:
                candidates.add(a)

    return sorted(candidates)


# -----------------------------
# Detection result
# -----------------------------

@dataclass
class DetectionOut:
    source: str
    frame_idx: int
    video_time_sec: float
    video_time_hms: str
    bbox_roi: Tuple[int, int, int, int]
    bbox_original: Tuple[int, int, int, int]
    det_conf: float
    plate_hsv: Tuple[float, float, float]
    matched_hud_index: Optional[int]
    matched_broadcast_tag: Optional[str]
    matched_team_id: Optional[str]
    matched_team_name: Optional[str]
    matched_team_tag: Optional[str]
    color_score: float
    color_candidates: List[dict]
    second_broadcast_tag: Optional[str]
    second_color_score: float
    text_checked: bool
    text_best_tag: Optional[str]
    text_score: float
    text_scores: Dict[str, float]
    override_reason: Optional[str]
    identity_source: str


# -----------------------------
# YOLO and processing
# -----------------------------

def run_yolo(model, image: np.ndarray, imgsz: int, conf: float, iou: float, device: str, max_det: int):
    result = model.predict(
        source=image,
        imgsz=imgsz,
        conf=conf,
        iou=iou,
        device=device,
        max_det=max_det,
        verbose=False,
    )[0]

    if result.boxes is None:
        return []

    xyxy = result.boxes.xyxy.cpu().numpy()
    confs = result.boxes.conf.cpu().numpy()

    out = []
    for box, c in zip(xyxy, confs):
        x1, y1, x2, y2 = [int(round(v)) for v in box.tolist()]
        out.append((x1, y1, x2, y2, float(c)))
    return out


def process_roi(
    source_name: str,
    roi: np.ndarray,
    roi_x: int,
    roi_y: int,
    frame_idx: int,
    time_sec: float,
    model,
    profiles: List[ColorProfile],
    text_resolver: TargetedTextResolver,
    conflict_pairs: List[Tuple[str, str]],
    args: argparse.Namespace,
) -> List[DetectionOut]:
    H, W = roi.shape[:2]
    raw_dets = run_yolo(model, roi, args.imgsz, args.conf, args.iou, args.device, args.max_det)
    detections: List[DetectionOut] = []

    for x1, y1, x2, y2, det_conf in raw_dets:
        x1 = max(0, min(W - 1, x1))
        y1 = max(0, min(H - 1, y1))
        x2 = max(0, min(W, x2))
        y2 = max(0, min(H, y2))
        if x2 <= x1 or y2 <= y1:
            continue
        if args.ignore_bottom_px > 0 and y2 > H - args.ignore_bottom_px:
            continue

        crop = roi[y1:y2, x1:x2]
        hsv = dominant_plate_hsv(crop)
        matches = match_color(hsv, profiles)
        best_p, best_score = matches[0]
        second_p, second_score = matches[1] if len(matches) > 1 else (None, 0.0)
        color_candidates = [
            {
                "rank": i + 1,
                "tag": p.broadcast_tag,
                "score": round(float(score), 4),
                "hud_index": p.hud_index,
                "team_id": p.team_id,
                "team_name": p.team_name,
            }
            for i, (p, score) in enumerate(matches[: args.candidate_count])
        ]

        final_p = best_p
        final_score = best_score
        identity_source = "hsv"
        text_checked = False
        text_best_tag = None
        text_score = 0.0
        text_scores: Dict[str, float] = {}
        override_reason = None

        # Text verification only for likely conflicts / close candidates.
        close_to_second = bool(second_p and (best_score - second_score) <= args.text_check_margin)
        conflict = bool(second_p and is_conflict_pair(best_p.broadcast_tag, second_p.broadcast_tag, conflict_pairs))
        conflict_family = any(best_p.broadcast_tag in pair for pair in conflict_pairs)

        if args.template_check_mode == "conflicts_only":
            should_text_check = bool(conflict or (conflict_family and (close_to_second or best_score < args.strong_color_score)))
        else:
            should_text_check = bool(conflict or close_to_second or conflict_family)

        if args.template_check and text_resolver.enabled and should_text_check:
            cand_tags = conflict_candidates(matches, conflict_pairs)
            text_checked = True
            text_best_tag, text_score, text_scores = text_resolver.resolve(crop, cand_tags)

            if text_best_tag:
                text_profile = next((p for p in profiles if p.broadcast_tag == text_best_tag), None)
                if text_profile and text_score >= args.min_text_confirm_score:
                    # Override only inside conflict groups. This prevents template text from rewriting unrelated teams.
                    if is_conflict_pair(best_p.broadcast_tag, text_best_tag, conflict_pairs) or best_p.broadcast_tag == text_best_tag:
                        final_p = text_profile
                        final_score = max(best_score, hsv_score_to_profile(hsv, text_profile))
                        identity_source = "hsv+template_override"
                        override_reason = f"text:{text_best_tag}:{text_score:.3f}"

            # If a known conflict remains unconfirmed, optionally mark it as unknown.
            if args.unknown_on_unconfirmed_conflict and conflict and not override_reason and best_score < args.strong_color_score:
                final_score = 0.0
                identity_source = "unknown_conflict"

        if final_score < args.min_color_score and not args.keep_unknown:
            continue

        if final_score < args.min_color_score:
            matched_hud_index = None
            matched_tag = None
            matched_team_id = None
            matched_team_name = None
            matched_team_tag = None
            identity_source = "unknown"
        else:
            matched_hud_index = final_p.hud_index
            matched_tag = final_p.broadcast_tag
            matched_team_id = final_p.team_id
            matched_team_name = final_p.team_name
            matched_team_tag = final_p.team_tag

        detections.append(DetectionOut(
            source=source_name,
            frame_idx=frame_idx,
            video_time_sec=time_sec,
            video_time_hms=time_hms(time_sec),
            bbox_roi=(x1, y1, x2, y2),
            bbox_original=(roi_x + x1, roi_y + y1, roi_x + x2, roi_y + y2),
            det_conf=det_conf,
            plate_hsv=hsv,
            matched_hud_index=matched_hud_index,
            matched_broadcast_tag=matched_tag,
            matched_team_id=matched_team_id,
            matched_team_name=matched_team_name,
            matched_team_tag=matched_team_tag,
            color_score=final_score,
            color_candidates=color_candidates,
            second_broadcast_tag=second_p.broadcast_tag if second_p else None,
            second_color_score=second_score,
            text_checked=text_checked,
            text_best_tag=text_best_tag,
            text_score=text_score,
            text_scores=text_scores,
            override_reason=override_reason,
            identity_source=identity_source,
        ))

    return detections


def draw_debug(roi: np.ndarray, detections: List[DetectionOut]) -> np.ndarray:
    img = roi.copy()

    for d in detections:
        x1, y1, x2, y2 = d.bbox_roi
        tag = d.matched_broadcast_tag or "UNK"
        label = f"{tag} c={d.color_score:.2f}"
        if d.override_reason:
            label += " T"
        if d.second_broadcast_tag:
            label += f" s={d.second_broadcast_tag}:{d.second_color_score:.2f}"

        color = (0, 255, 0) if tag != "UNK" else (0, 0, 255)
        if d.override_reason:
            color = (0, 255, 255)

        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        cv2.putText(img, label, (x1, max(0, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)

    return img


def write_outputs(out_dir: Path, detections: List[DetectionOut], profiles: List[ColorProfile]) -> None:
    ensure_dir(out_dir)

    csv_path = out_dir / "detections.csv"
    jsonl_path = out_dir / "detections.jsonl"
    summary_path = out_dir / "summary.json"

    fields = [
        "source",
        "frame_idx",
        "video_time_sec",
        "video_time_hms",
        "bbox_roi",
        "bbox_original",
        "det_conf",
        "plate_hsv",
        "matched_hud_index",
        "matched_broadcast_tag",
        "matched_team_id",
        "matched_team_name",
        "matched_team_tag",
        "color_score",
        "color_candidates",
        "second_broadcast_tag",
        "second_color_score",
        "text_checked",
        "text_best_tag",
        "text_score",
        "text_scores",
        "override_reason",
        "identity_source",
    ]

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for d in detections:
            row = {k: getattr(d, k) for k in fields}
            row["text_scores"] = json.dumps(row["text_scores"], ensure_ascii=False)
            row["color_candidates"] = json.dumps(row["color_candidates"], ensure_ascii=False)
            writer.writerow(row)

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for d in detections:
            f.write(json.dumps(d.__dict__, ensure_ascii=False) + "\n")

    by_team: Dict[str, int] = {}
    unknown = 0
    overrides = 0
    text_checked = 0

    for d in detections:
        tag = d.matched_broadcast_tag
        if tag:
            by_team[tag] = by_team.get(tag, 0) + 1
        else:
            unknown += 1
        if d.override_reason:
            overrides += 1
        if d.text_checked:
            text_checked += 1

    summary = {
        "detections": len(detections),
        "unknown": unknown,
        "template_checks": text_checked,
        "template_overrides": overrides,
        "by_team": dict(sorted(by_team.items(), key=lambda x: x[1], reverse=True)),
        "profiles": [
            {
                "hud_index": p.hud_index,
                "broadcast_tag": p.broadcast_tag,
                "team_name": p.team_name,
                "team_tag": p.team_tag,
                "team_id": p.team_id,
                "hex": p.hex_color,
                "median_hsv": p.median_hsv,
                "h": p.h_range,
                "s": p.s_range,
                "v": p.v_range,
                "source": p.source,
            }
            for p in profiles
        ],
    }

    save_json(summary_path, summary)

    print(f"Saved: {csv_path.resolve()}")
    print(f"Saved: {jsonl_path.resolve()}")
    print(f"Saved: {summary_path.resolve()}")
    print(f"Detections: {len(detections)}")
    print(f"Unknown: {unknown}")
    print(f"Template checks: {text_checked}")
    print(f"Template overrides: {overrides}")
    for tag, cnt in summary["by_team"].items():
        print(f"{tag}: {cnt}")


# -----------------------------
# Commands
# -----------------------------

def load_runtime(args: argparse.Namespace):
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError("Install ultralytics first: pip install ultralytics") from e

    config = load_json(Path(args.config))
    profiles = load_color_profiles(
        config=config,
        color_profiles=args.color_profiles,
        hsv_presets=args.hsv_presets,
        prefer_presets=args.prefer_presets,
    )
    conflict_pairs = parse_conflict_pairs(config, args.conflicts)
    text_resolver = TargetedTextResolver(profiles, args.font, args.font_size)
    model = YOLO(args.weights)

    print("Color profiles loaded:")
    for p in profiles:
        if p.broadcast_tag in {"BB", "THUG"}:
            print(f"  * {p.hud_index:02d} {p.broadcast_tag:<6} h={p.h_range} s={p.s_range} v={p.v_range} source={p.source}")
    print(f"Conflict pairs: {conflict_pairs}")
    print(f"Template resolver enabled: {text_resolver.enabled}")

    return model, profiles, text_resolver, conflict_pairs


def cmd_analyze_video(args: argparse.Namespace) -> None:
    model, profiles, text_resolver, conflict_pairs = load_runtime(args)

    video_path = Path(args.video)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0:
        fps = args.assume_fps if args.assume_fps > 0 else 30.0

    frame_step = max(1, int(round(fps / args.sample_fps)))
    max_frame = total_frames
    if args.max_seconds > 0:
        max_frame = min(max_frame, int(args.max_seconds * fps))

    out_dir = Path(args.out)
    debug_dir = out_dir / "debug"
    if args.save_debug:
        ensure_dir(debug_dir)

    all_dets: List[DetectionOut] = []

    frame_idx = 0
    pbar = tqdm(total=max_frame, desc="analyze video")
    while frame_idx < max_frame:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_step == 0:
            roi, roi_x, roi_y = central_roi(frame, args.left_ignore, args.roi_size)
            time_sec = frame_idx / fps

            dets = process_roi(
                source_name=video_path.name,
                roi=roi,
                roi_x=roi_x,
                roi_y=roi_y,
                frame_idx=frame_idx,
                time_sec=time_sec,
                model=model,
                profiles=profiles,
                text_resolver=text_resolver,
                conflict_pairs=conflict_pairs,
                args=args,
            )
            all_dets.extend(dets)

            if args.save_debug:
                dbg = draw_debug(roi, dets)
                cv2.imwrite(str(debug_dir / f"{video_path.stem}_frame_{frame_idx:07d}.jpg"), dbg, [cv2.IMWRITE_JPEG_QUALITY, 92])

        frame_idx += 1
        pbar.update(1)

    pbar.close()
    cap.release()
    write_outputs(out_dir, all_dets, profiles)


def cmd_analyze_images(args: argparse.Namespace) -> None:
    model, profiles, text_resolver, conflict_pairs = load_runtime(args)

    images_dir = Path(args.images_dir)
    images = list(iter_images(images_dir))
    if not images:
        raise RuntimeError(f"No images found: {images_dir}")

    out_dir = Path(args.out)
    debug_dir = out_dir / "debug"
    if args.save_debug:
        ensure_dir(debug_dir)

    all_dets: List[DetectionOut] = []

    for idx, img_path in enumerate(tqdm(images, desc="analyze images")):
        img = cv2.imread(str(img_path))
        if img is None:
            continue

        if img.shape[0] == args.roi_size and img.shape[1] == args.roi_size:
            roi, roi_x, roi_y = img, 0, 0
        else:
            roi, roi_x, roi_y = central_roi(img, args.left_ignore, args.roi_size)

        m = re.search(r"frame_(\d+)", img_path.stem)
        frame_idx = int(m.group(1)) if m else idx
        time_sec = frame_idx / args.assume_fps if args.assume_fps > 0 else float(idx)

        dets = process_roi(
            source_name=img_path.name,
            roi=roi,
            roi_x=roi_x,
            roi_y=roi_y,
            frame_idx=frame_idx,
            time_sec=time_sec,
            model=model,
            profiles=profiles,
            text_resolver=text_resolver,
            conflict_pairs=conflict_pairs,
            args=args,
        )
        all_dets.extend(dets)

        if args.save_debug:
            dbg = draw_debug(roi, dets)
            cv2.imwrite(str(debug_dir / img_path.name), dbg, [cv2.IMWRITE_JPEG_QUALITY, 92])

    write_outputs(out_dir, all_dets, profiles)


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--weights", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)

    p.add_argument("--color-profiles", default=None, help="Optional HUD-calibrated colors JSON")
    p.add_argument("--hsv-presets", default=None, help="Optional HSV preset JSON with slot ranges")
    p.add_argument("--prefer-presets", action="store_true", help="Use --hsv-presets as primary source over --color-profiles")

    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.20)
    p.add_argument("--iou", type=float, default=0.55)
    p.add_argument("--device", default="cpu")
    p.add_argument("--max-det", type=int, default=140)

    p.add_argument("--left-ignore", type=int, default=420)
    p.add_argument("--roi-size", type=int, default=1080)
    p.add_argument("--ignore-bottom-px", type=int, default=95)

    p.add_argument("--min-color-score", type=float, default=0.25)
    p.add_argument("--strong-color-score", type=float, default=0.88)
    p.add_argument("--keep-unknown", action="store_true")

    p.add_argument("--template-check", action="store_true")
    p.add_argument("--font", default=None)
    p.add_argument("--font-size", type=int, default=24)
    p.add_argument("--min-text-score", type=float, default=0.22, help="Backward-compatible alias for --min-text-confirm-score")
    p.add_argument("--min-text-confirm-score", type=float, default=None)
    p.add_argument("--text-check-margin", type=float, default=0.18)
    p.add_argument("--template-check-mode", choices=["conflicts_only", "broad"], default="conflicts_only")
    p.add_argument("--unknown-on-unconfirmed-conflict", action="store_true")
    p.add_argument("--candidate-count", type=int, default=5)
    p.add_argument("--conflicts", default=None, help="Extra conflict pairs, e.g. BB:THUG,DKK:DINO")

    p.add_argument("--save-debug", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect team plates, identify by HSV, and use targeted text check for conflicting colors")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze-video")
    p.add_argument("--video", required=True)
    p.add_argument("--sample-fps", type=float, default=1.0)
    p.add_argument("--max-seconds", type=float, default=0.0)
    p.add_argument("--assume-fps", type=float, default=60.0)
    add_common_args(p)
    p.set_defaults(func=cmd_analyze_video)

    p = sub.add_parser("analyze-images")
    p.add_argument("--images-dir", required=True)
    p.add_argument("--assume-fps", type=float, default=60.0)
    add_common_args(p)
    p.set_defaults(func=cmd_analyze_images)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "min_text_confirm_score", None) is None:
        args.min_text_confirm_score = args.min_text_score
    args.func(args)


if __name__ == "__main__":
    main()
