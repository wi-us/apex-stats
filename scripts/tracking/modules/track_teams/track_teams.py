#!/usr/bin/env python3
"""
track_teams.py — обработка VOD Apex и формирование tracks.json.

Пайплайн:
  1. Регистрация кадра -> каноническая карта (ORB + RANSAC homography).
     Из H извлекаются zoom, pan, rotation, ransac_inliers.
  2. Детекция плашек команд по HSV из config.yaml.
  3. Перевод центроидов и стрелок в мировые координаты через H + калибровку.
  4. Трекинг в мировых координатах (простой Калман + жадное назначение).
  5. Потоковая запись tracks.json (без накопления в RAM).

Запуск:
    python track_teams.py --video game.mp4 --config config.example.yaml --out tracks.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import yaml
from tqdm import tqdm
from collections import Counter

# Local import — file lives next to this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _slot_palette import (  # noqa: E402
    slot_color_bgr as _slot_color_bgr,
    slot_color_hex as _slot_color_hex,
)
try:
    from scipy.optimize import linear_sum_assignment as _hungarian
except ImportError:  # pragma: no cover
    _hungarian = None

# --------------------------- POI hints (optional) ---------------------------
# Hints are loaded from a JSON map { slot_or_tag: {"cx": .., "cy": .., "r": ..} }
# in normalized canonical-map coordinates (0..1, square). They are intended as
# a prior bias for the start-position matcher: a team's initial detection
# should land inside its POI circle, and detections outside the circle should
# be penalized. The matcher integration is wired in a follow-up; this module
# loads and exposes the data so downstream stages can opt in.
POI_HINTS: dict[str, dict[str, float]] = {}


def load_poi_hints(path: Path) -> dict[str, dict[str, float]]:
    """Load POI hint file. Keys are slot ids ("slot_3") or team tags ("TSM")."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[poi-hints] failed to load {path}: {e}", file=sys.stderr)
        return {}
    out: dict[str, dict[str, float]] = {}
    for k, v in (raw or {}).items():
        try:
            out[str(k)] = {
                "cx": float(v["cx"]),
                "cy": float(v["cy"]),
                "r": float(v["r"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def poi_prior_weight(slot_or_tag: str, x_norm: float, y_norm: float, soft: float = 2.0) -> float:
    """Return a multiplicative weight in [0..1] for a candidate position.

    1.0 inside the POI circle, smoothly decays outside (Gaussian falloff with
    sigma = soft * r). Returns 1.0 if no hint is registered for the slot/tag.
    """
    hint = POI_HINTS.get(slot_or_tag)
    if not hint:
        return 1.0
    cx, cy, r = hint["cx"], hint["cy"], hint["r"]
    d2 = (x_norm - cx) ** 2 + (y_norm - cy) ** 2
    if d2 <= r * r:
        return 1.0
    sigma = max(1e-4, soft * r)
    return float(math.exp(-d2 / (2.0 * sigma * sigma)))


# ----------------------------- Config & maps -----------------------------

@dataclass
class TeamCfg:
    id: str
    name: str
    hsv_lower: np.ndarray
    hsv_upper: np.ndarray
    hsv_lower2: Optional[np.ndarray] = None
    hsv_upper2: Optional[np.ndarray] = None
    color_hex: str = "#ffffff"
    slot: Optional[int] = None       # 1..20, matches motion_detect/hsv_presets
    slot_id: Optional[str] = None    # canonical "slot_<N>"; falls back to id
    # LAB range derived from HSV range (filled lazily by SlotTracker).
    lab_lower: Optional[np.ndarray] = None
    lab_upper: Optional[np.ndarray] = None
    # Per-slot detection overrides (None → fall back to global det_cfg).
    min_area: Optional[float] = None
    max_area: Optional[float] = None
    morph_kernel: Optional[int] = None


@dataclass
class CanonicalMap:
    name: str
    image: np.ndarray            # grayscale, full-size
    size: tuple[int, int]        # (W, H)
    world_bounds: dict
    px_to_world: np.ndarray      # 3x3 affine fit canonical_px -> world


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_teams(cfg: dict) -> list[TeamCfg]:
    out = []
    palette = ["#ef4444", "#3b82f6", "#eab308", "#22c55e", "#a855f7", "#ec4899", "#06b6d4", "#f97316"]
    for i, t in enumerate(cfg.get("teams", [])):
        slot = t.get("slot")
        slot_id = t.get("slot_id") or (f"slot_{int(slot)}" if slot is not None else str(t["id"]))
        out.append(TeamCfg(
            id=str(t["id"]),
            name=str(t.get("name", t["id"])),
            hsv_lower=np.array(t["hsv_lower"], dtype=np.uint8),
            hsv_upper=np.array(t["hsv_upper"], dtype=np.uint8),
            hsv_lower2=np.array(t["hsv_lower2"], dtype=np.uint8) if "hsv_lower2" in t else None,
            hsv_upper2=np.array(t["hsv_upper2"], dtype=np.uint8) if "hsv_upper2" in t else None,
            color_hex=t.get("color", palette[i % len(palette)]),
            slot=int(slot) if slot is not None else None,
            slot_id=slot_id,
        ))
    return out


def _hex_to_hsv_center(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    px = np.uint8([[[b, g, r]]])  # BGR for cv2
    H, S, V = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0]
    return int(H), int(S), int(V)


def teams_from_anchors(path: Path, h_tol: int = 10,
                       s_min_floor: int = 60, v_min_floor: int = 60,
                       s_drop: int = 80, v_drop: int = 80,
                       hsv_preset: dict[int, dict] | None = None) -> list[TeamCfg]:
    """Build TeamCfg list directly from motion_detect/reports/motion_tracks.json.
    Each motion-detected slot becomes one team with HSV range derived from its
    hex color (H ± h_tol, S/V wide around the source). Hue wrap is handled with
    hsv_lower2/hsv_upper2 like the YAML 'red' team.
    If `hsv_preset` is provided (slot -> {h:[lo,hi], s:[lo,hi], v:[lo,hi]}),
    those manually-calibrated ranges take precedence over the derived ones."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    results = raw.get("results", [])
    out: list[TeamCfg] = []
    for r in results:
        slot = r.get("slot")
        if slot is None:
            continue
        hex_str = r.get("hex", "#888888")
        slot_int = int(slot)
        lo = hi = lo2 = hi2 = None
        preset_used = False
        if hsv_preset and slot_int in hsv_preset:
            p = hsv_preset[slot_int]
            h_lo, h_hi = int(p["h"][0]), int(p["h"][1])
            s_lo, s_hi = int(p["s"][0]), int(p["s"][1])
            v_lo, v_hi = int(p["v"][0]), int(p["v"][1])
            if h_lo <= h_hi:
                lo = np.array([h_lo, s_lo, v_lo], dtype=np.uint8)
                hi = np.array([h_hi, s_hi, v_hi], dtype=np.uint8)
            else:
                # hue wrap (e.g. red): split into two ranges
                lo  = np.array([h_lo, s_lo, v_lo], dtype=np.uint8)
                hi  = np.array([179,  s_hi, v_hi], dtype=np.uint8)
                lo2 = np.array([0,    s_lo, v_lo], dtype=np.uint8)
                hi2 = np.array([h_hi, s_hi, v_hi], dtype=np.uint8)
            preset_used = True
        else:
            H, S, V = _hex_to_hsv_center(hex_str)
            s_lo = max(s_min_floor, S - s_drop)
            v_lo = max(v_min_floor, V - v_drop)
            h_low = H - h_tol
            h_high = H + h_tol
            if h_low < 0:
                lo  = np.array([0, s_lo, v_lo], dtype=np.uint8)
                hi  = np.array([h_high, 255, 255], dtype=np.uint8)
                lo2 = np.array([179 + h_low, s_lo, v_lo], dtype=np.uint8)
                hi2 = np.array([179, 255, 255], dtype=np.uint8)
            elif h_high > 179:
                lo  = np.array([h_low, s_lo, v_lo], dtype=np.uint8)
                hi  = np.array([179, 255, 255], dtype=np.uint8)
                lo2 = np.array([0, s_lo, v_lo], dtype=np.uint8)
                hi2 = np.array([h_high - 179, 255, 255], dtype=np.uint8)
            else:
                lo = np.array([h_low,  s_lo, v_lo], dtype=np.uint8)
                hi = np.array([h_high, 255, 255], dtype=np.uint8)
        # Optional per-team detection overrides from the preset.
        ov_min_area = ov_max_area = ov_morph = None
        if hsv_preset and slot_int in hsv_preset:
            p = hsv_preset[slot_int]
            if p.get("min_area") is not None:
                ov_min_area = float(p["min_area"])
            if p.get("max_area") is not None:
                ov_max_area = float(p["max_area"])
            if p.get("morph_kernel") is not None:
                ov_morph = int(p["morph_kernel"])
        out.append(TeamCfg(
            id=f"slot_{slot_int}",
            name=str(r.get("team_name") or f"Team {slot_int}"),
            hsv_lower=lo, hsv_upper=hi,
            hsv_lower2=lo2, hsv_upper2=hi2,
            color_hex=hex_str,
            slot=slot_int,
            slot_id=f"slot_{slot_int}",
            min_area=ov_min_area,
            max_area=ov_max_area,
            morph_kernel=ov_morph,
        ))
    if hsv_preset:
        used = sum(1 for r in results if r.get("slot") is not None and int(r["slot"]) in hsv_preset)
        print(f"[info] hsv_preset: applied to {used}/{len(out)} slots (others use anchor-derived HSV)")
    return out


def fit_affine_px_to_world(points: list[dict]) -> np.ndarray:
    """Least-squares fit of 2D affine: world = A * [px; 1]. Returns 3x3."""
    src = np.array([p["canonical_px"] for p in points], dtype=np.float64)
    dst = np.array([p["world"] for p in points], dtype=np.float64)
    n = len(src)
    if n < 3:
        raise ValueError("Нужно минимум 3 calibration_points")
    M = np.zeros((2 * n, 6))
    b = np.zeros(2 * n)
    for i, ((x, y), (X, Y)) in enumerate(zip(src, dst)):
        M[2 * i] = [x, y, 1, 0, 0, 0]
        M[2 * i + 1] = [0, 0, 0, x, y, 1]
        b[2 * i] = X
        b[2 * i + 1] = Y
    a, *_ = np.linalg.lstsq(M, b, rcond=None)
    return np.array([[a[0], a[1], a[2]], [a[3], a[4], a[5]], [0, 0, 1]])


def load_canonical_map(name: str, base_dir: Path) -> CanonicalMap:
    meta_path = base_dir / f"{name}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    img_path = base_dir / meta["image"]
    if not img_path.exists():
        print(f"[warn] {img_path} не найден — использую серый плейсхолдер. Регистрация будет работать плохо.")
        W, H = meta["canonical_size"]
        img = np.full((H, W), 128, dtype=np.uint8)
    else:
        img = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise RuntimeError(f"Не смог прочитать {img_path}")
        real_h, real_w = img.shape[:2]
        meta_size = tuple(meta.get("canonical_size", [real_w, real_h]))
        if meta_size != (real_w, real_h):
            print(f"[info] canonical_size в JSON {meta_size} не совпадает с реальным {(real_w, real_h)} — использую реальный.")
            meta["canonical_size"] = [real_w, real_h]
    return CanonicalMap(
        name=name,
        image=img,
        size=tuple(meta["canonical_size"]),
        world_bounds=meta.get("world_bounds", {"x": [0, 1000], "y": [0, 1000]}),
        px_to_world=fit_affine_px_to_world(meta["calibration_points"]),
    )


# ------------------------- Frame registration ----------------------------

class FrameRegistrar:
    """Считает гомографию frame_px -> canonical_px."""

    def __init__(self, cmap: CanonicalMap, reg_cfg: dict):
        self.cmap = cmap
        self.cfg = reg_cfg
        detector = reg_cfg.get("detector", "orb").lower()
        n = int(reg_cfg.get("max_features", 1500))
        if detector == "sift":
            self.detector = cv2.SIFT_create(nfeatures=n)
            self.norm = cv2.NORM_L2
        else:
            self.detector = cv2.ORB_create(nfeatures=n, fastThreshold=7)
            self.norm = cv2.NORM_HAMMING
        self.use_clahe = bool(reg_cfg.get("clahe", True))
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) if self.use_clahe else None
        # Прекомпьют фич канонической карты (downscale для скорости)
        target_w = int(reg_cfg.get("canonical_target_w", 1600))
        H, W = cmap.image.shape[:2]
        self.scale = min(1.0, target_w / W)
        small = cv2.resize(cmap.image, (int(W * self.scale), int(H * self.scale))) if self.scale < 1 else cmap.image
        small_eq = self.clahe.apply(small) if self.clahe is not None else small
        self.map_small = small_eq
        self.kp_map, self.des_map = self.detector.detectAndCompute(small_eq, None)
        print(f"[info] canonical features: {0 if self.des_map is None else len(self.des_map)} (detector={detector}, clahe={self.use_clahe})")
        self.bf = cv2.BFMatcher(self.norm, crossCheck=False)
        self.ratio = float(reg_cfg.get("match_ratio", 0.75))
        self.reproj = float(reg_cfg.get("ransac_reproj_px", 5.0))
        self.min_inliers = int(reg_cfg.get("min_inliers", 25))
        # Some low-inlier SIFT matches still produce a numeric homography, but
        # it can be wildly wrong (e.g. zoom hundreds of times or pan far outside
        # the map). Reject those before they can project good detections to bad
        # world positions.
        self.min_zoom = float(reg_cfg.get("min_zoom", 0.08))
        self.max_zoom = float(reg_cfg.get("max_zoom", 8.0))
        self.pan_margin_px = float(reg_cfg.get("pan_margin_px", 768.0))
        roi = reg_cfg.get("roi", [0, 0, 1, 1])
        self.roi = tuple(float(v) for v in roi)

    def _homography_plausible(self, H: np.ndarray, frame_shape: tuple[int, int]) -> bool:
        if H is None or not np.isfinite(H).all() or abs(float(H[2, 2])) < 1e-9:
            return False
        decomp = decompose_homography(H)
        z = float(decomp["zoom"])
        if not (self.min_zoom <= z <= self.max_zoom):
            return False
        fh, fw = frame_shape[:2]
        cx, cy = map_point(H, (fw / 2.0, fh / 2.0))
        cw, ch = self.cmap.size
        m = self.pan_margin_px
        return (-m <= cx <= cw + m) and (-m <= cy <= ch + m)

    def _crop_roi(self, gray: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
        h, w = gray.shape[:2]
        x0 = int(self.roi[0] * w); y0 = int(self.roi[1] * h)
        x1 = int(self.roi[2] * w); y1 = int(self.roi[3] * h)
        return gray[y0:y1, x0:x1], (x0, y0)

    def register(self, frame_bgr: np.ndarray):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        roi_img, (ox, oy) = self._crop_roi(gray)
        if self.clahe is not None:
            roi_img = self.clahe.apply(roi_img)
        kp_f, des_f = self.detector.detectAndCompute(roi_img, None)
        if des_f is None or self.des_map is None or len(kp_f) < 8:
            return None, 0
        try:
            knn = self.bf.knnMatch(des_f, self.des_map, k=2)
        except cv2.error:
            return None, 0
        good = []
        for pair in knn:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < self.ratio * n.distance:
                good.append(m)
        if len(good) < 8:
            return None, len(good)
        src = np.float32([(kp_f[m.queryIdx].pt[0] + ox, kp_f[m.queryIdx].pt[1] + oy) for m in good]).reshape(-1, 1, 2)
        dst = np.float32([self.kp_map[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        # rescale map points back to full canonical
        if self.scale != 1.0:
            dst = dst / self.scale
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, self.reproj)
        if H is None:
            return None, 0
        inliers = int(mask.sum()) if mask is not None else 0
        if not self._homography_plausible(H, gray.shape):
            return None, inliers
        if inliers < self.min_inliers:
            return H, inliers   # return anyway, mark low_conf upstream
        return H, inliers


def decompose_homography(H: np.ndarray) -> dict:
    """Грубое разложение H на zoom (средний масштаб), rotation, pan (центр кадра)."""
    a, b = H[0, 0], H[0, 1]
    c, d = H[1, 0], H[1, 1]
    sx = math.hypot(a, c)
    sy = math.hypot(b, d)
    zoom = (sx + sy) / 2.0
    rotation_deg = math.degrees(math.atan2(c, a))
    return {"zoom": float(zoom), "rotation_deg": float(rotation_deg)}


def map_point(H: np.ndarray, pt_xy: tuple[float, float]) -> tuple[float, float]:
    v = np.array([pt_xy[0], pt_xy[1], 1.0])
    w = H @ v
    return float(w[0] / w[2]), float(w[1] / w[2])


# ------------------------- HSV → LAB helper ------------------------------

def _hsv_to_lab_pixel(hsv: tuple[int, int, int]) -> np.ndarray:
    px = np.array([[[hsv[0], hsv[1], hsv[2]]]], dtype=np.uint8)
    bgr = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    return lab[0, 0].astype(np.int16)


def build_lab_range_from_hsv(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Approximate LAB range from HSV bounds, expanded on A/B for shadows/compression."""
    lo_lab = _hsv_to_lab_pixel((int(lo[0]), int(lo[1]), int(lo[2])))
    hi_lab = _hsv_to_lab_pixel((int(hi[0]), int(hi[1]), int(hi[2])))
    l_min = int(max(0, min(lo_lab[0], hi_lab[0]) - 20))
    l_max = int(min(255, max(lo_lab[0], hi_lab[0]) + 20))
    a_min = int(max(0, min(lo_lab[1], hi_lab[1]) - 28))
    a_max = int(min(255, max(lo_lab[1], hi_lab[1]) + 28))
    b_min = int(max(0, min(lo_lab[2], hi_lab[2]) - 28))
    b_max = int(min(255, max(lo_lab[2], hi_lab[2]) + 28))
    return (
        np.array([l_min, a_min, b_min], dtype=np.uint8),
        np.array([l_max, a_max, b_max], dtype=np.uint8),
    )


# --------------------- Anchors (from motion_detect) ----------------------

def load_minimap_affine(map_name: str, base_dir: Path) -> Optional[np.ndarray]:
    """Load minimap_px -> canonical_px affine. Returns 3x3 or None if no file."""
    p = base_dir / f"{map_name}.minimap_affine.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    pts = [{"canonical_px": q["minimap_px"], "world": q["canonical_px"]} for q in raw["points"]]
    return fit_affine_px_to_world(pts)


def load_anchors(path: Path,
                 teams: list[TeamCfg],
                 mini_affine: Optional[np.ndarray],
                 cmap: "CanonicalMap") -> dict[str, dict]:
    """Read motion_detect/reports/motion_tracks.json and convert each slot's
    consensus_xy (minimap pixels) into canonical+world coordinates.

    Returns { team_id: { 'slot': int, 'slot_id': str, 'conf': 'HIGH|MED|LOW|MISS',
                          'world':(x,y), 'canonical_px':(x,y),
                          'r0_canonical_px': float | None } }.
    Teams without a 'slot' field in config are skipped (no way to match)."""
    if not path.exists():
        print(f"[warn] anchors file {path} not found — стартую без motion-якорей")
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Optional start_anchors.json (lives next to motion_tracks.json) — даёт
    # стартовый радиус r0 в минимап-пикселях, который motion_detect выбрал
    # по уверенности (HIGH/MED/LOW).
    start_anchors_path = path.with_name("start_anchors.json")
    r0_by_slot: dict[int, float] = {}
    r0_by_conf_minimap: dict[str, float] = {"HIGH": 25.0, "MED": 40.0, "LOW": 70.0}
    if start_anchors_path.exists():
        try:
            sa = json.loads(start_anchors_path.read_text(encoding="utf-8"))
            r0_by_conf_minimap.update(sa.get("r0_by_conf", {}) or {})
            for _key, a in (sa.get("anchors") or {}).items():
                slot = a.get("slot")
                if slot is not None and a.get("r0_minimap_px") is not None:
                    r0_by_slot[int(slot)] = float(a["r0_minimap_px"])
        except Exception as e:  # noqa: BLE001
            print(f"[warn] start_anchors.json present but unreadable: {e}")
    # Масштаб мини-мап → канон. Берём из affine (определитель ≈ (scale)^2).
    if mini_affine is not None:
        det = float(abs(mini_affine[0, 0] * mini_affine[1, 1]
                        - mini_affine[0, 1] * mini_affine[1, 0]))
        mini_to_canon_scale = math.sqrt(det) if det > 0 else 1.0
    else:
        mini_to_canon_scale = 1.0
    # build slot -> best result
    by_slot: dict[int, dict] = {}
    for r in raw.get("results", []):
        slot = r.get("slot")
        if slot is None:
            continue
        prev = by_slot.get(slot)
        order = {"HIGH": 0, "MED": 1, "LOW": 2, "MISS": 3}
        if prev is None or order.get(r.get("confidence", "MISS"), 9) < order.get(prev.get("confidence", "MISS"), 9):
            by_slot[slot] = r
    out: dict[str, dict] = {}
    if mini_affine is None:
        print("[warn] нет minimap_affine.json для карты — anchor xy переведу как identity")
    for t in teams:
        if t.slot is None:
            continue
        r = by_slot.get(t.slot)
        if r is None or not r.get("consensus_xy"):
            out[t.id] = {"slot": t.slot, "slot_id": t.slot_id or f"slot_{t.slot}",
                         "conf": "MISS", "world": None, "canonical_px": None,
                         "r0_canonical_px": None}
            continue
        mx, my = r["consensus_xy"]
        if mini_affine is not None:
            cx, cy = map_point(mini_affine, (float(mx), float(my)))
        else:
            cx, cy = float(mx), float(my)
        wx, wy = map_point(cmap.px_to_world, (cx, cy))
        conf = r.get("confidence", "MISS")
        r0_mini = r0_by_slot.get(t.slot, r0_by_conf_minimap.get(conf, 70.0))
        r0_canon = float(r0_mini) * mini_to_canon_scale
        out[t.id] = {
            "slot": t.slot, "slot_id": t.slot_id or f"slot_{t.slot}",
            "conf": conf,
            "world": (wx, wy), "canonical_px": (cx, cy),
            "r0_canonical_px": r0_canon,
        }
    return out


def teams_from_start_coords(path: Path,
                            hsv_preset: dict[int, dict] | None = None) -> list[TeamCfg]:
    """Build TeamCfg list from start_coords.json (ALGS POI picks + slot palette).

    Each slot becomes one team:
      - id / slot_id: ``slot_N``
      - name: ALGS team_name (fallback ``Team N``)
      - color: HUD VOD palette (`_slot_palette.slot_color_hex`)
      - HSV: from manually-calibrated preset; if missing, derived from palette hex.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    slots = raw.get("slots") or {}
    out: list[TeamCfg] = []
    for slot_key in sorted(slots.keys(), key=lambda k: int(k.split("_")[1])):
        slot_int = int(slot_key.split("_")[1])
        entry = slots[slot_key] or {}
        hex_str = _slot_color_hex(slot_int)
        team_tag = (entry.get("team_tag") or "").strip()
        team_name = entry.get("team_name") or f"Team {slot_int}"
        display_name = f"{team_tag} · {team_name}" if team_tag else team_name

        lo = hi = lo2 = hi2 = None
        if hsv_preset and slot_int in hsv_preset:
            p = hsv_preset[slot_int]
            h_lo, h_hi = int(p["h"][0]), int(p["h"][1])
            s_lo, s_hi = int(p["s"][0]), int(p["s"][1])
            v_lo, v_hi = int(p["v"][0]), int(p["v"][1])
            if h_lo <= h_hi:
                lo = np.array([h_lo, s_lo, v_lo], dtype=np.uint8)
                hi = np.array([h_hi, s_hi, v_hi], dtype=np.uint8)
            else:
                lo  = np.array([h_lo, s_lo, v_lo], dtype=np.uint8)
                hi  = np.array([179,  s_hi, v_hi], dtype=np.uint8)
                lo2 = np.array([0,    s_lo, v_lo], dtype=np.uint8)
                hi2 = np.array([h_hi, s_hi, v_hi], dtype=np.uint8)
        else:
            H, S, V = _hex_to_hsv_center(hex_str)
            s_lo = max(60, S - 80)
            v_lo = max(60, V - 80)
            h_tol = 10
            h_low, h_high = H - h_tol, H + h_tol
            if h_low < 0:
                lo  = np.array([0, s_lo, v_lo], dtype=np.uint8)
                hi  = np.array([h_high, 255, 255], dtype=np.uint8)
                lo2 = np.array([179 + h_low, s_lo, v_lo], dtype=np.uint8)
                hi2 = np.array([179, 255, 255], dtype=np.uint8)
            elif h_high > 179:
                lo  = np.array([h_low, s_lo, v_lo], dtype=np.uint8)
                hi  = np.array([179, 255, 255], dtype=np.uint8)
                lo2 = np.array([0, s_lo, v_lo], dtype=np.uint8)
                hi2 = np.array([h_high - 179, 255, 255], dtype=np.uint8)
            else:
                lo = np.array([h_low,  s_lo, v_lo], dtype=np.uint8)
                hi = np.array([h_high, 255, 255], dtype=np.uint8)

        ov_min_area = ov_max_area = ov_morph = None
        if hsv_preset and slot_int in hsv_preset:
            p = hsv_preset[slot_int]
            if p.get("min_area") is not None:
                ov_min_area = float(p["min_area"])
            if p.get("max_area") is not None:
                ov_max_area = float(p["max_area"])
            if p.get("morph_kernel") is not None:
                ov_morph = int(p["morph_kernel"])

        out.append(TeamCfg(
            id=f"slot_{slot_int}",
            name=display_name,
            hsv_lower=lo, hsv_upper=hi,
            hsv_lower2=lo2, hsv_upper2=hi2,
            color_hex=hex_str,
            slot=slot_int,
            slot_id=f"slot_{slot_int}",
            min_area=ov_min_area,
            max_area=ov_max_area,
            morph_kernel=ov_morph,
        ))
    return out


def load_start_anchors(path: Path,
                       teams: list[TeamCfg],
                       cmap: "CanonicalMap") -> dict[str, dict]:
    """Build anchors_map from start_coords.json (ALGS POI picks).

    Координаты уже в canonical-norm (0..1), affine минимапы не нужен.
    Все слоты получают ``conf='HIGH'`` — POI pick трактуется как
    semantic ground truth точки старта.
    """
    if not path.exists():
        print(f"[warn] start-coords file {path} not found")
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    slots_data = raw.get("slots") or {}
    W, H = cmap.size
    out: dict[str, dict] = {}
    for t in teams:
        if t.slot is None:
            continue
        entry = slots_data.get(f"slot_{t.slot}")
        algs = (entry or {}).get("algs")
        if not algs or algs.get("cx_norm") is None:
            out[t.id] = {"slot": t.slot, "slot_id": t.slot_id or f"slot_{t.slot}",
                         "conf": "MISS", "world": None, "canonical_px": None,
                         "r0_canonical_px": None}
            continue
        cx = float(algs["cx_norm"]) * W
        cy = float(algs["cy_norm"]) * H
        r0 = float(algs.get("r_norm", 0.03)) * W
        wx, wy = map_point(cmap.px_to_world, (cx, cy))
        out[t.id] = {
            "slot": t.slot,
            "slot_id": t.slot_id or f"slot_{t.slot}",
            "conf": "HIGH",
            "world": (wx, wy),
            "canonical_px": (cx, cy),
            "r0_canonical_px": r0,
            "poi_id": (entry.get("poi") or {}).get("id") if entry else None,
            "poi_name": (entry.get("poi") or {}).get("name") if entry else None,
        }
    return out


# ----------------------------- Detection ---------------------------------

def detect_team_blobs(frame_bgr: np.ndarray, teams: list[TeamCfg], det_cfg: dict):
    """Возвращает [{team_id, frame_px:(x,y), bbox, angle_frame_deg}]."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    k = int(det_cfg.get("morph_kernel", 3))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    min_a = float(det_cfg.get("min_area_px", 60))
    max_a = float(det_cfg.get("max_area_px", 1200))
    arr_min = float(det_cfg.get("arrow_min_area_px", 12))
    eps_rel = float(det_cfg.get("arrow_approx_eps", 0.05))

    out = []
    for t in teams:
        m_hsv = cv2.inRange(hsv, t.hsv_lower, t.hsv_upper)
        if t.hsv_lower2 is not None and t.hsv_upper2 is not None:
            m_hsv |= cv2.inRange(hsv, t.hsv_lower2, t.hsv_upper2)
        # PR-1: HSV ∩ LAB with soft fallback to HSV-only when intersection is too sparse.
        if t.lab_lower is None:
            t.lab_lower, t.lab_upper = build_lab_range_from_hsv(t.hsv_lower, t.hsv_upper)
        m_lab = cv2.inRange(lab, t.lab_lower, t.lab_upper)
        mask = cv2.bitwise_and(m_hsv, m_lab)
        if cv2.countNonZero(mask) < 8:
            mask = m_hsv
        # per-team area/morph overrides
        tmin = float(t.min_area) if t.min_area is not None else min_a
        tmax = float(t.max_area) if t.max_area is not None else max_a
        tk = int(t.morph_kernel) if t.morph_kernel is not None else k
        if tk != k:
            kernel_t = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (tk, tk))
        else:
            kernel_t = kernel
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_t)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_t)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            if area < tmin or area > tmax:
                continue
            x, y, w, h = cv2.boundingRect(c)
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            cx = M["m10"] / M["m00"]; cy = M["m01"] / M["m00"]
            # arrow direction via approxPolyDP triangle near bbox
            angle = None
            approx = cv2.approxPolyDP(c, eps_rel * cv2.arcLength(c, True), True)
            if len(approx) == 3 and cv2.contourArea(approx) >= arr_min:
                pts = approx.reshape(-1, 2)
                # tip = farthest vertex from centroid
                d = np.linalg.norm(pts - np.array([cx, cy]), axis=1)
                tip = pts[int(np.argmax(d))]
                angle = math.degrees(math.atan2(tip[1] - cy, tip[0] - cx))
            out.append({
                "team_id": t.id,
                "frame_px": (float(cx), float(cy)),
                "bbox": (int(x), int(y), int(w), int(h)),
                "angle_frame_deg": angle,
                "score": float(area / max_a),
            })
    return out


# ============== Detect-first → associate (new pipeline) =================
#
# Старый путь (`SlotTracker.update`) сканирует кадр по очереди для каждого
# слота вокруг его предыдущей точки. Это надёжно для медленных слотов, но
# легко «прилипает» к чужой плашке: seed на чужой команде → фантом сидит
# неподвижно, post-hoc-фильтр его не убирает, если HUD говорит «жив».
#
# Новый путь:
#   1. detect_candidates_in_minimap_roi(frame, teams) — одной HSV-операцией
#      на minimap-ROI находим ВСЕ blob'ы цвета каждой команды.
#   2. associate_hungarian(candidates, slot_trackers, H, t_now) — строим
#      cost-матрицу и решаем глобальный ассайн scipy.linear_sum_assignment.
#      Один кандидат → максимум один slot, нет «приклеивания» нескольких
#      слотов к одной точке.
#   3. Для назначенных пар вызываем SlotTracker.accept_observation(...);
#      для остальных слотов — note_miss().
#
# Включается флагом `da_strategy: detect_first` в конфиге. Без флага работает
# старая логика (baseline).

def load_minimap_roi_bbox(zones_path: Optional[Path]) -> Optional[tuple[int, int, int, int]]:
    """Возвращает (x, y, w, h) первой зоны с tag='minimap' в zones.vod.json.
    None если файла нет или нет такой зоны."""
    if zones_path is None or not Path(zones_path).exists():
        return None
    try:
        raw = json.loads(Path(zones_path).read_text(encoding="utf-8"))
        for z in raw.get("zones", []):
            if z.get("tag") == "minimap":
                return (int(z["x"]), int(z["y"]), int(z["w"]), int(z["h"]))
    except Exception as e:
        print(f"[warn] failed to read zones {zones_path}: {e}")
    return None


def load_checkpoints_from_detections(
    det_path: Path, teams: list["TeamCfg"],
) -> tuple[dict[int, list[dict]], list[int], int, float]:
    """Превращает detect_plates/detections.json в {frame_idx: [candidates]}.

    Каждый candidate уже в координатах ПОЛНОГО кадра (rx+cx, ry+cy) и
    содержит team_id (через slot→team_id мап из teams[]).

    Возвращает (by_frame, sorted_frames, sample_step, src_fps), где
    sample_step — расстояние между keyframe'ами detect_plates (для tolerance).
    """
    raw = json.loads(Path(det_path).read_text(encoding="utf-8"))
    roi = raw.get("roi") or [0, 0, 0, 0]
    rx, ry = int(roi[0]), int(roi[1])
    src_fps = float(raw.get("fps") or 30.0)
    # slot aliases -> team_id (str). detect_plates may emit either numeric
    # slot=19 or verbose team_key="slot_19_Team 19"; accept both.
    slot_to_team: dict[str, str] = {}
    for tcfg in teams:
        if tcfg.slot is not None:
            slot_int = int(tcfg.slot)
            for alias in (str(slot_int), f"slot_{slot_int}", f"slot_{slot_int:02d}", str(tcfg.slot_id or "")):
                if alias:
                    slot_to_team[alias] = tcfg.id
    by_frame: dict[int, list[dict]] = {}
    n_skipped_unknown_slot = 0

    def _slot_aliases(slot_raw) -> list[str]:
        key = str(slot_raw).strip()
        aliases = [key]
        try:
            n = int(float(key))
            aliases.extend([str(n), f"slot_{n}", f"slot_{n:02d}"])
        except (TypeError, ValueError):
            pass
        m = re.search(r"slot[_\-\s]*0*(\d+)", key, flags=re.IGNORECASE)
        if m:
            n = int(m.group(1))
            aliases.extend([str(n), f"slot_{n}", f"slot_{n:02d}"])
        return aliases

    def _push(frame: int, slot_raw, bbox, score):
        nonlocal n_skipped_unknown_slot
        if slot_raw is None:
            n_skipped_unknown_slot += 1
            return
        team_id = next((slot_to_team[a] for a in _slot_aliases(slot_raw) if a in slot_to_team), None)
        if team_id is None:
            n_skipped_unknown_slot += 1
            return
        if not bbox or len(bbox) < 4:
            return
        x, y, w, h = bbox
        if w <= 0 or h <= 0:
            return
        cx = float(x) + float(w) / 2.0
        cy = float(y) + float(h) / 2.0
        det_fx = rx + cx
        det_fy = ry + cy
        area = float(w) * float(h)
        cand = {
            "team_id": team_id,
            "frame_px": (det_fx, det_fy),
            "bbox": (int(rx + x), int(ry + y), int(w), int(h)),
            "area": area,
            "color_score": float(score) if score is not None else 0.5,
        }
        by_frame.setdefault(int(frame), []).append(cand)

    frames = raw.get("frames", []) or []
    for f in frames:
        kf = int(f.get("frame", 0))
        for b in f.get("boxes", []) or []:
            feat = b.get("feat") or {}
            slot_raw = feat.get("team_key") or feat.get("slot") or feat.get("dominant_team_id")
            _push(kf, slot_raw, b.get("bbox"), b.get("score"))
        for rec in f.get("recoveries", []) or []:
            _push(kf, rec.get("team_key"), rec.get("bbox"), None)
        for tr in f.get("tracked", []) or []:
            _push(int(tr.get("frame", kf)), tr.get("slot"), tr.get("bbox"), None)

    sorted_frames = sorted(by_frame.keys())
    # sample_step: median gap between consecutive frame indices in detections.
    if len(sorted_frames) >= 2:
        gaps = [sorted_frames[i + 1] - sorted_frames[i]
                for i in range(len(sorted_frames) - 1)]
        gaps.sort()
        sample_step = max(1, gaps[len(gaps) // 2])
    else:
        sample_step = max(1, int(round(src_fps)))  # ~1 fps fallback

    print(f"[info] from-detections: loaded {sum(len(v) for v in by_frame.values())} "
          f"candidates over {len(sorted_frames)} sample frames "
          f"(sample_step={sample_step}, src_fps={src_fps}, "
          f"slots_with_team_id={len(slot_to_team)}, "
          f"skipped_unknown_slot={n_skipped_unknown_slot})")
    return by_frame, sorted_frames, sample_step, src_fps


def pick_checkpoints_for_frame(
    current_frame: int,
    by_frame: dict[int, list[dict]],
    sorted_frames: list[int],
    tolerance: int,
) -> list[dict]:
    """Берём все кандидаты из detections-кадров в окне [current-tol, current+tol].
    Дедупликация по team_id: выживает ближайший к current_frame, при равенстве —
    с большим color_score.
    """
    if not sorted_frames:
        return []
    import bisect as _bisect
    lo = current_frame - tolerance
    hi = current_frame + tolerance
    li = _bisect.bisect_left(sorted_frames, lo)
    ri = _bisect.bisect_right(sorted_frames, hi)
    best: dict[str, tuple[int, float, dict]] = {}
    for idx in sorted_frames[li:ri]:
        d = abs(idx - current_frame)
        for c in by_frame.get(idx, ()):
            tid = c["team_id"]
            prev = best.get(tid)
            score = float(c.get("color_score") or 0.0)
            if prev is None or d < prev[0] or (d == prev[0] and score > prev[1]):
                best[tid] = (d, score, c)
    return [v[2] for v in best.values()]


def detect_candidates_in_minimap_roi(
    frame_bgr: np.ndarray,
    teams: list[TeamCfg],
    minimap_bbox: Optional[tuple[int, int, int, int]],
    H: np.ndarray,
    det_cfg: dict,
) -> list[dict]:
    """Для каждого слота строим HSV-маску внутри minimap-ROI и достаём blob'ы.
    Возвращает плоский список:
      { team_id, frame_px:(x,y), canonical_px:(cx,cy),
        area, color_score (доля площади контура в маске),
        bbox:(x,y,w,h) }
    color_score ∈ (0..1] — пропорция площади blob'а от его bbox, выше = плотнее заливка.
    """
    fh, fw = frame_bgr.shape[:2]
    if minimap_bbox is None:
        x0, y0, x1, y1 = 0, 0, fw, fh
    else:
        mx, my, mw, mh = minimap_bbox
        # zones.vod.json в base 1920x1080 — переведём в фактический размер кадра.
        sx = fw / 1920.0
        sy = fh / 1080.0
        x0 = max(0, int(mx * sx))
        y0 = max(0, int(my * sy))
        x1 = min(fw, int((mx + mw) * sx))
        y1 = min(fh, int((my + mh) * sy))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return []
    roi = frame_bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
    k = int(det_cfg.get("morph_kernel", 3))
    base_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    min_a = float(det_cfg.get("min_area_px", 40))
    max_a = float(det_cfg.get("max_area_px", 2400))
    out: list[dict] = []
    for t in teams:
        m_hsv = cv2.inRange(hsv, t.hsv_lower, t.hsv_upper)
        if t.hsv_lower2 is not None and t.hsv_upper2 is not None:
            m_hsv |= cv2.inRange(hsv, t.hsv_lower2, t.hsv_upper2)
        # PR-1: HSV ∩ LAB with soft fallback to HSV-only when intersection is too sparse.
        if t.lab_lower is None:
            t.lab_lower, t.lab_upper = build_lab_range_from_hsv(t.hsv_lower, t.hsv_upper)
        m_lab = cv2.inRange(lab, t.lab_lower, t.lab_upper)
        mask = cv2.bitwise_and(m_hsv, m_lab)
        if cv2.countNonZero(mask) < 8:
            mask = m_hsv
        # per-team area/morph overrides
        tmin = float(t.min_area) if t.min_area is not None else min_a
        tmax = float(t.max_area) if t.max_area is not None else max_a
        tk = int(t.morph_kernel) if t.morph_kernel is not None else k
        kernel = base_kernel if tk == k else cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (tk, tk))
        if tk > 1:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = float(cv2.contourArea(c))
            if area < tmin or area > tmax:
                continue
            x, y, w, h = cv2.boundingRect(c)
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            local_cx = M["m10"] / M["m00"]
            local_cy = M["m01"] / M["m00"]
            det_fx = x0 + local_cx
            det_fy = y0 + local_cy
            cand_cx, cand_cy = map_point(H, (det_fx, det_fy))
            color_score = area / max(1.0, float(w * h))
            out.append({
                "team_id": t.id,
                "frame_px": (det_fx, det_fy),
                "canonical_px": (cand_cx, cand_cy),
                "area": area,
                "color_score": float(color_score),
                "bbox": (int(x0 + x), int(y0 + y), int(w), int(h)),
            })
    return out


def _eff_w(base: dict, overrides: dict, slot_int) -> dict:
    """Merge per-slot weight overrides on top of base weights.

    overrides format: { "slot_11": {delta_color_mismatch: 10.0, ...}, ... }.
    Unknown slot or missing block → returns base unchanged.
    """
    if not overrides or slot_int is None:
        return base
    ov = overrides.get(f"slot_{int(slot_int)}")
    if not ov:
        return base
    merged = dict(base)
    merged.update(ov)
    return merged


def _bbox_iou_xywh(a: tuple, b: tuple) -> float:
    """IoU of two bboxes in (x, y, w, h) form. PR-2 identity anchor."""
    if not a or not b:
        return 0.0
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    a_x2, a_y2 = ax + aw, ay + ah
    b_x2, b_y2 = bx + bw, by + bh
    ix1 = max(ax, bx); iy1 = max(ay, by)
    ix2 = min(a_x2, b_x2); iy2 = min(a_y2, b_y2)
    iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
    inter = float(iw * ih)
    if inter <= 0:
        return 0.0
    union = float(aw * ah + bw * bh - inter)
    return inter / union if union > 1e-6 else 0.0


def compute_late_game_gate_shrink(slot_trackers: dict, t_now: float,
                                   cfg: dict) -> tuple[float, dict | None]:
    """Late-game collision protection: when live tracks slip into a tiny ring,
    shrink everyone's gate so the Hungarian solver stops snatching neighbours.

    Trigger: median pairwise canonical_px distance between live trackers
    drops below `cluster_threshold_px` AND t_now >= t_min_sec. Returns
    multiplier in (0, 1] applied uniformly to every slot's gate_radius_mult.
    """
    if not cfg or not cfg.get("enabled", False):
        return 1.0, None
    if t_now < float(cfg.get("t_min_sec", 300.0)):
        return 1.0, None
    pts = []
    for st in slot_trackers.values():
        if st.state in ("wiped", "inactive") or st.wiped:
            continue
        if st.canonical_px is None:
            continue
        pts.append(st.canonical_px)
    if len(pts) < 4:
        return 1.0, None
    dists = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dists.append(math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]))
    dists.sort()
    median_d = dists[len(dists) // 2]
    thresh = float(cfg.get("cluster_threshold_px", 250.0))
    if median_d >= thresh:
        return 1.0, None
    shrink = float(cfg.get("gate_shrink", 0.4))
    info = {"t": round(t_now, 1), "median_d": round(median_d, 1),
            "thresh": thresh, "shrink": shrink, "n_live": len(pts)}
    return shrink, info


def associate_hungarian(
    candidates: list[dict],
    slot_trackers: dict,
    t_now: float,
    weights: dict,
    near_miss: Optional[Counter] = None,
    near_miss_threshold: float = 0.25,
    debug_sink: Optional[list] = None,
) -> dict[str, dict]:
    """Глобальное назначение кандидатов слотам венгерским алгоритмом.

    Cost(slot, cand):
        gate-check: cand доступен слоту только если cand.canonical_px попадает
        в motion-gate слота вокруг предсказания (или вокруг init_canonical_px
        для не-активированных слотов). Иначе cost = +inf.

        cost = β·norm(world_dist_to_prediction) + γ·shape_penalty + δ·color_mismatch_hint
               − ε·hysteresis(prev_assigned == cand.team_id)

        color_mismatch_hint = 0 если cand.team_id == slot.team.id (то же
        семейство цвета — кандидата произвёл цветовой match именно этого
        слота), иначе 0.5 (помечено как «чужой цвет», но допустимо если
        размер/позиция совпадают).

    Возвращает {team_id: cand_dict} только для назначенных пар.
    """
    if not candidates or not slot_trackers:
        return {}
    if _hungarian is None:
        # Fallback: жадный по cost, чтобы скрипт работал без scipy.
        return _associate_greedy(candidates, slot_trackers, t_now, weights,
                                 near_miss=near_miss,
                                 near_miss_threshold=near_miss_threshold,
                                 debug_sink=debug_sink)

    slots = list(slot_trackers.values())
    n_slots = len(slots)
    n_cands = len(candidates)
    INF = 1e6
    cost = np.full((n_slots, n_cands), INF, dtype=np.float64)
    # Per-cell breakdown (only filled when debug_sink is requested).
    breakdown: list[list[Optional[dict]]] = (
        [[None] * n_cands for _ in range(n_slots)] if debug_sink is not None else []
    )

    # Per-slot weight overrides + dynamic late-game gate shrink (see _eff_w).
    overrides = weights.get("slot_overrides") or {}
    dyn_gate_shrink = float(weights.get("_dyn_gate_shrink", 1.0))

    for i, st in enumerate(slots):
        if st.state in ("wiped", "inactive"):
            continue
        if st.wiped:
            continue
        w = _eff_w(weights, overrides, getattr(st.team, "slot", None))
        beta = float(w.get("beta_world", 1.0))
        gamma = float(w.get("gamma_shape", 0.3))
        delta = float(w.get("delta_color_mismatch", 0.5))
        eps = float(w.get("eps_hysteresis", 0.2))
        eps_iou = float(w.get("eps_iou_bonus", 0.6))   # PR-2: bonus for IoU≥iou_gate with prev bbox
        iou_gate = float(w.get("iou_gate", 0.10))
        gate_mult = float(w.get("gate_radius_mult", 1.0)) * dyn_gate_shrink
        fallback_gate_px = float(w.get("fallback_gate_canonical_px", 200.0))
        eta_anchor = float(w.get("eta_anchor", 0.5))
        # Prediction in canonical px.
        if st.canonical_px is not None and st.last_seen_t is not None:
            dt = min(getattr(st, "dt_cap_s", 20.0),
                     max(0.0, t_now - st.last_seen_t))
            v_eff = max(getattr(st, "v_max_px_s", 60.0),
                        getattr(st, "v_observed_peak_px_s", 0.0)
                        * getattr(st, "v_observed_boost", 1.8))
            radius = min(getattr(st, "gate_cap_px", 450.0),
                         v_eff * dt + getattr(st, "gate_slack_px", 20.0))
            pred_cx = st.canonical_px[0] + (
                st.vx * dt if not st.canonical_px_stale else 0.0)
            pred_cy = st.canonical_px[1] + (
                st.vy * dt if not st.canonical_px_stale else 0.0)
        elif st.init_canonical_px is not None:
            pred_cx, pred_cy = st.init_canonical_px
            radius = fallback_gate_px
        else:
            # Нет ни seed, ни истории — нечего ассайнить.
            continue
        radius *= gate_mult

        # Прогрессивный стартовый якорь: на ранних кадрах принудительно
        # ограничиваем поиск кругом (init_canonical_px, r_anchor).
        anchor_r = st.anchor_radius_at(t_now) if hasattr(st, "anchor_radius_at") else None
        anchor_cx = anchor_cy = None
        if anchor_r is not None and st.init_canonical_px is not None:
            anchor_cx, anchor_cy = st.init_canonical_px

        for j, cand in enumerate(candidates):
            cand_cx, cand_cy = cand["canonical_px"]
            d = math.hypot(cand_cx - pred_cx, cand_cy - pred_cy)
            if d > radius:
                continue
            # Soft-gate: вне якоря не исключаем, а штрафуем cost-ом,
            # пропорционально (d_anchor/r - 1). Сам якорь продолжает
            # притягивать к стартовой точке, но при отсутствии цели
            # внутри r ассоциация всё равно случится.
            anchor_pen = 0.0
            if anchor_r is not None:
                da_an = math.hypot(cand_cx - anchor_cx, cand_cy - anchor_cy)
                if da_an > anchor_r:
                    anchor_pen = (da_an / max(1.0, anchor_r)) - 1.0
            world_term = d / max(1.0, radius)
            # shape_penalty: blob со слишком низким color_score штрафуем.
            shape_pen = 1.0 - min(1.0, max(0.0, cand["color_score"]))
            color_mismatch = 0.0 if cand["team_id"] == st.team.id else delta
            c = (beta * world_term + gamma * shape_pen + color_mismatch
                 + eta_anchor * anchor_pen)
            hyst_bonus = 0.0
            iou_bonus = 0.0
            iou_val = 0.0
            # Hysteresis: предыдущий tracked-blob этого же слота получает скидку
            if getattr(st, "last_frame_px", None) is not None:
                lfx, lfy = st.last_frame_px
                cfx, cfy = cand["frame_px"]
                if math.hypot(cfx - lfx, cfy - lfy) < 25.0:
                    c -= eps
                    hyst_bonus = eps
            # PR-2: identity anchor — если кандидат сильно перекрывается с прошлым bbox
            # этого слота, даём ему скидку (предотвращает "перепрыг" на чужую плашку
            # того же цвета в массовых сценах вроде final-battle).
            prev_bb = getattr(st, "last_bbox", None)
            cand_bb = cand.get("bbox")
            if prev_bb is not None and cand_bb is not None:
                iou = _bbox_iou_xywh(prev_bb, cand_bb)
                if iou >= iou_gate:
                    c -= eps_iou * iou
                    iou_bonus = eps_iou * iou
                    iou_val = iou
            cost[i, j] = max(0.0, c)
            if debug_sink is not None:
                breakdown[i][j] = {
                    "d_pred_px": round(d, 1),
                    "radius_px": round(radius, 1),
                    "world": round(beta * world_term, 4),
                    "shape": round(gamma * shape_pen, 4),
                    "color_mismatch": round(color_mismatch, 4),
                    "anchor_pen": round(eta_anchor * anchor_pen, 4),
                    "hyst_bonus": round(hyst_bonus, 4),
                    "iou_bonus": round(iou_bonus, 4),
                    "iou": round(iou_val, 3),
                    "same_color": bool(cand["team_id"] == st.team.id),
                }

    # Pad to square so unmatched rows/cols are allowed.
    n = max(n_slots, n_cands)
    pad = np.full((n, n), INF / 2, dtype=np.float64)
    pad[:n_slots, :n_cands] = cost
    row_ind, col_ind = _hungarian(pad)
    result: dict[str, dict] = {}
    winners_by_slot: dict[int, int] = {}
    for r, c in zip(row_ind, col_ind):
        if r >= n_slots or c >= n_cands:
            continue
        if cost[r, c] >= INF / 2:
            continue
        st = slots[r]
        result[st.team.id] = candidates[c]
        winners_by_slot[r] = c
        # Учёт попадания внутрь стартового якоря (для LOW-watchdog).
        if st.init_canonical_px is not None:
            cand_cx, cand_cy = candidates[c]["canonical_px"]
            if math.hypot(cand_cx - st.init_canonical_px[0],
                          cand_cy - st.init_canonical_px[1]) <= max(
                              st.anchor_r0_px, st.near_anchor_radius_px):
                st.anchor_inside_hits += 1
        # Near-miss: кто ещё хотел этого же кандидата?
        if near_miss is not None:
            win_c = cost[r, c]
            col = cost[:, c]
            for ri in range(n_slots):
                if ri == r or col[ri] >= INF / 2:
                    continue
                # Конкурент «рядом» — в пределах threshold от победителя.
                if col[ri] <= win_c + max(0.05, near_miss_threshold):
                    loser = slots[ri]
                    near_miss[(st.team.id, loser.team.id)] += 1
    # Debug dump: per-slot view of best/second/components (only finite cells).
    if debug_sink is not None:
        for i, st in enumerate(slots):
            # Gather all gated candidates for this slot.
            gated: list[dict] = []
            for j in range(n_cands):
                if cost[i, j] >= INF / 2:
                    continue
                cand = candidates[j]
                bd = breakdown[i][j] or {}
                gated.append({
                    "j": int(j),
                    "cand_team_id": cand["team_id"],
                    "cand_canonical_px": [round(cand["canonical_px"][0], 1),
                                          round(cand["canonical_px"][1], 1)],
                    "color_score": round(float(cand.get("color_score") or 0.0), 3),
                    "cost": round(float(cost[i, j]), 4),
                    **bd,
                })
            if not gated and i not in winners_by_slot:
                # nothing to report for this slot
                continue
            gated.sort(key=lambda x: x["cost"])
            win_j = winners_by_slot.get(i)
            best_cost = gated[0]["cost"] if gated else None
            second_cost = gated[1]["cost"] if len(gated) > 1 else None
            margin = (second_cost - best_cost) if (best_cost is not None and second_cost is not None) else None
            entry = {
                "slot_team_id": st.team.id,
                "slot": getattr(st.team, "slot", None),
                "slot_id": getattr(st.team, "slot_id", None),
                "state": st.state,
                "winner_j": (int(win_j) if win_j is not None else None),
                "winner_cost": (round(float(cost[i, win_j]), 4)
                                if win_j is not None else None),
                "best_cost": best_cost,
                "second_cost": second_cost,
                "margin": (round(float(margin), 4) if margin is not None else None),
                "n_gated": len(gated),
                # cap candidates list to keep jsonl manageable
                "candidates": gated[:6],
            }
            debug_sink.append(entry)
    return result


def _associate_greedy(candidates, slot_trackers, t_now, weights,
                       near_miss: Optional[Counter] = None,
                       near_miss_threshold: float = 0.25,
                       debug_sink: Optional[list] = None):
    """Жадный fallback без scipy. Учитывает те же веса, что и hungarian,
    чтобы варианты конфигов (color_first/motion_first/...) реально различались
    даже когда scipy не установлен."""
    assigned_cands: set[int] = set()
    result: dict[str, dict] = {}
    overrides = weights.get("slot_overrides") or {}
    dyn_gate_shrink = float(weights.get("_dyn_gate_shrink", 1.0))
    # Считаем (cost, slot, cand_j) по всем парам, сортируем и жадно назначаем.
    pairs: list[tuple[float, "SlotTracker", int]] = []
    for st in slot_trackers.values():
        if st.state in ("wiped", "inactive") or st.wiped:
            continue
        w = _eff_w(weights, overrides, getattr(st.team, "slot", None))
        beta = float(w.get("beta_world", 1.0))
        gamma = float(w.get("gamma_shape", 0.3))
        delta = float(w.get("delta_color_mismatch", 0.5))
        eps = float(w.get("eps_hysteresis", 0.2))
        eps_iou = float(w.get("eps_iou_bonus", 0.6))
        iou_gate = float(w.get("iou_gate", 0.10))
        gate_mult = float(w.get("gate_radius_mult", 1.0)) * dyn_gate_shrink
        fallback_gate_px = float(w.get("fallback_gate_canonical_px", 200.0))
        eta_anchor = float(w.get("eta_anchor", 0.5))
        # δ ≥ 1.0 — фактически запрет кросс-цвета (как color_first.yaml).
        allow_cross_color = delta < 1.0
        if st.canonical_px is not None:
            pred = st.canonical_px
            radius = min(getattr(st, "gate_cap_px", 450.0),
                         getattr(st, "v_max_px_s", 60.0) * 1.0 + 50.0)
        elif st.init_canonical_px is not None:
            pred = st.init_canonical_px
            radius = fallback_gate_px
        else:
            continue
        radius *= gate_mult
        anchor_r = st.anchor_radius_at(t_now) if hasattr(st, "anchor_radius_at") else None
        anchor_cx = anchor_cy = None
        if anchor_r is not None and st.init_canonical_px is not None:
            anchor_cx, anchor_cy = st.init_canonical_px
        for j, cand in enumerate(candidates):
            same_color = cand["team_id"] == st.team.id
            if not same_color and not allow_cross_color:
                continue
            cx, cy = cand["canonical_px"]
            d = math.hypot(cx - pred[0], cy - pred[1])
            if d > radius:
                continue
            anchor_pen = 0.0
            if anchor_r is not None:
                da_an = math.hypot(cx - anchor_cx, cy - anchor_cy)
                if da_an > anchor_r:
                    anchor_pen = (da_an / max(1.0, anchor_r)) - 1.0
            world_term = d / max(1.0, radius)
            shape_pen = 1.0 - min(1.0, max(0.0, cand.get("color_score", 1.0)))
            color_mismatch = 0.0 if same_color else delta
            c = (beta * world_term + gamma * shape_pen + color_mismatch
                 + eta_anchor * anchor_pen)
            lfp = getattr(st, "last_frame_px", None)
            if lfp is not None:
                cfx, cfy = cand["frame_px"]
                if math.hypot(cfx - lfp[0], cfy - lfp[1]) < 25.0:
                    c -= eps
            prev_bb = getattr(st, "last_bbox", None)
            cand_bb = cand.get("bbox")
            if prev_bb is not None and cand_bb is not None:
                iou = _bbox_iou_xywh(prev_bb, cand_bb)
                if iou >= iou_gate:
                    c -= eps_iou * iou
            pairs.append((max(0.0, c), st, j))
    pairs.sort(key=lambda p: p[0])
    # Для near-miss: для каждого cand_j собираем минимальный cost каждого слота.
    per_cand_costs: dict[int, list[tuple[float, str]]] = {}
    if near_miss is not None:
        for c, st, j in pairs:
            per_cand_costs.setdefault(j, []).append((c, st.team.id))
    used_slots: set[str] = set()
    for c, st, j in pairs:
        if j in assigned_cands or st.team.id in used_slots:
            continue
        assigned_cands.add(j)
        used_slots.add(st.team.id)
        result[st.team.id] = candidates[j]
        if st.init_canonical_px is not None:
            cand_cx, cand_cy = candidates[j]["canonical_px"]
            if math.hypot(cand_cx - st.init_canonical_px[0],
                          cand_cy - st.init_canonical_px[1]) <= max(
                              st.anchor_r0_px, st.near_anchor_radius_px):
                st.anchor_inside_hits += 1
        if near_miss is not None:
            for other_c, other_sid in per_cand_costs.get(j, []):
                if other_sid == st.team.id:
                    continue
                if other_c <= c + max(0.05, near_miss_threshold):
                    near_miss[(st.team.id, other_sid)] += 1
    return result


# ------------------------------ Tracker ----------------------------------

# Per-slot local tracker (inspired by apex-stats SimpleArrowTracker, simplified
# because we already work in world coords via homography).

class SlotTracker:
    """Локальный трекер одного слота: ищет плашку в ROI кадра вокруг
    последней проекции своей канонической позиции.

    Состояние хранится в canonical_px (потому что кадр двигается, а карта — нет).
    """

    def __init__(self, team: TeamCfg, slot_cfg: dict, init_canonical_px: Optional[tuple[float, float]],
                 elim_t: Optional[float] = None,
                 anchor_conf: str = "MISS", hud_alive: bool = False,
                 anchor_r0_canonical_px: Optional[float] = None):
        self.team = team
        self.canonical_px: Optional[tuple[float, float]] = init_canonical_px
        # Immutable copy of the seed anchor (canonical px). Used by the strict
        # active-slot filter to verify detections actually land near the
        # placard motion_detect originally locked onto.
        self.init_canonical_px: Optional[tuple[float, float]] = init_canonical_px
        self.last_frame_px: Optional[tuple[float, float]] = None
        # PR-2: last bbox in frame px (from detect-first candidate), used as
        # identity anchor in the association cost.
        self.last_bbox: Optional[tuple[int, int, int, int]] = None
        # ROI / detection
        self.roi_size: int = int(slot_cfg.get("roi_size", 220))
        self.min_roi: int = int(slot_cfg.get("min_roi", 120))
        self.max_roi_expand_px: int = int(slot_cfg.get("max_roi_expand_px", 400))
        self.roi_expand_step_px: int = int(slot_cfg.get("roi_expand_step_px", 100))
        self.roi_expand_px: int = 0
        self.min_area: float = float(team.min_area if team.min_area is not None else slot_cfg.get("min_area_px", 40))
        self.max_area: float = float(team.max_area if team.max_area is not None else slot_cfg.get("max_area_px", 2400))
        self.morph_kernel: int = int(team.morph_kernel if team.morph_kernel is not None else slot_cfg.get("morph_kernel", 5))
        # Stabilisation
        self.center_deadzone_px: float = float(slot_cfg.get("center_deadzone_px", 2.0))
        self.max_center_step_px: float = float(slot_cfg.get("max_center_step_px", 24.0))
        self.center_smoothing_alpha: float = float(slot_cfg.get("center_smoothing_alpha", 0.35))
        # detect_plates already assigns a concrete slot id; in --from-detections
        # mode we should trust that measurement instead of slowly dragging the
        # old anchor toward it through anti-switch hysteresis.
        self.trust_from_detections: bool = bool(slot_cfg.get("trust_from_detections", True))
        # Anti-jump (PR-4: defaults bumped — 30/3 was too lax, swaps slipped through).
        self.jump_switch_threshold_px: float = float(slot_cfg.get("jump_switch_threshold_px", 80.0))
        self.switch_confirm_frames: int = int(slot_cfg.get("switch_confirm_frames", 6))
        # PR-4: TTL on pending hypothesis. If we sit in switch_wait for too
        # many frames without confirming, drop the hypothesis entirely so the
        # slot can re-attach to whatever is actually nearby instead of getting
        # stuck in limbo.
        self.pending_ttl_frames: int = int(slot_cfg.get(
            "pending_ttl_frames", max(8, self.switch_confirm_frames * 2)))
        self.pending_canon: Optional[tuple[float, float]] = None
        self.pending_hits: int = 0
        self.pending_age: int = 0
        # Full-frame recovery: если ROI промахивается N+ кадров подряд,
        # каждые `recover_interval` кадров ищем плашку по всему кадру
        # в окрестности предсказания (`recover_gate_px` каноники).
        self.recover_after_misses: int = int(slot_cfg.get("recover_after_misses", 10))
        self.recover_interval: int = int(slot_cfg.get("recover_interval", 5))
        self.recover_gate_px: float = float(slot_cfg.get("recover_gate_px", 600.0))
        self.n_recovered: int = 0
        # Time-aware motion model (canonical px / sec).
        motion = slot_cfg.get("motion", {}) or {}
        self.v_max_px_s: float = float(motion.get("v_max_px_s", 60.0))
        self.gate_slack_px: float = float(motion.get("gate_slack_px", 20.0))
        self.gate_cap_px: float = float(motion.get("gate_cap_px", 450.0))
        self.dt_cap_s: float = float(motion.get("dt_cap_s", 20.0))
        self.velocity_alpha: float = float(motion.get("velocity_alpha", 0.5))
        # Adaptive: remember observed peak speed so "mobile" slots auto-widen the gate.
        self.v_observed_peak_px_s: float = 0.0
        self.v_observed_decay: float = float(motion.get("v_observed_decay", 0.97))
        self.v_observed_boost: float = float(motion.get("v_observed_boost", 1.8))
        self.vx: float = 0.0
        self.vy: float = 0.0
        self.last_seen_t: Optional[float] = None
        self.canonical_px_stale: bool = init_canonical_px is None
        self.wiped: bool = False
        # Authoritative wipe time from HUD (eliminations.json). When t_now >= elim_t,
        # the slot is force-wiped — no more detection work, not counted as `lost`.
        self.elim_t: Optional[float] = elim_t
        # Active-slot filter: если за первые N processed-кадров слот так и не
        # дал ни одной успешной детекции — помечаем как `inactive` и больше
        # не тратим CPU/не плодим ложные плашки чужих команд похожего тона.
        # Защищены: anchor HIGH/MED (motion_detect его реально видел) и
        # HUD-alive (HUD подтверждает, что команда жива).
        self.anchor_conf: str = anchor_conf
        self.hud_alive: bool = hud_alive
        self.inactive_after_misses: int = int(slot_cfg.get("inactive_after_misses", 60))
        self.ever_detected: bool = False
        self.n_inactive: int = 0
        # Strict active-slot criteria (anti-fantom slots).
        # `activated` flips True only when K consecutive detections land within
        # `near_anchor_radius_px` of the seed anchor (in CANONICAL pixels —
        # invariant of camera zoom/pan). Lone false positives on other teams'
        # placards project far from the seed in canonical space and never
        # count, so colors-not-in-this-match retire cleanly.
        self.near_anchor_radius_px: float = float(slot_cfg.get("near_anchor_radius_canonical_px", 120.0))
        self.min_consecutive_for_active: int = int(slot_cfg.get("min_consecutive_for_active", 3))
        self.near_anchor_consecutive: int = 0
        self.activated: bool = False
        # ------------------------------------------------------------------
        # Прогрессивный стартовый якорь (см. README): первые `anchor_lock_sec`
        # секунд кандидаты должны лежать внутри (init_canonical_px, r0).
        # Дальше за `anchor_grow_sec` радиус линейно растёт до `anchor_r_max`.
        # Конфиг slot_tracker:
        #   anchor_lock_sec, anchor_grow_sec, anchor_r_max  (если r_max < 0 →
        #   используем near_anchor_radius_canonical_px как потолок).
        self.anchor_lock_sec: float = float(slot_cfg.get("anchor_lock_sec", 0.0))
        self.anchor_grow_sec: float = float(slot_cfg.get("anchor_grow_sec", 0.0))
        _r_max_cfg = float(slot_cfg.get("anchor_r_max", -1.0))
        self.anchor_r_max_px: float = (
            _r_max_cfg if _r_max_cfg > 0 else self.near_anchor_radius_px)
        # Фолбэк r0 (минимап-pixels по дефолту 70) если motion_detect не дал
        # своего значения. Берём из slot_cfg для удобства тюнинга.
        default_r0 = float(slot_cfg.get("anchor_r0_fallback_canonical_px", 70.0))
        self.anchor_r0_px: float = (
            float(anchor_r0_canonical_px) if anchor_r0_canonical_px is not None
            else default_r0)
        self.anchor_t0: Optional[float] = None      # фиксируется на первом кадре
        self.anchor_lost: bool = False              # true когда LOW-якорь не нашёл цели
        self.anchor_inside_hits: int = 0
        # Post-hoc cleanup threshold: if a slot finished the run with fewer than
        # this many `tracked` frames AND was never activated, all its entries
        # are rewritten to `inactive` in tracks.json.
        self.min_tracked_for_active: int = int(slot_cfg.get("min_tracked_for_active", 20))
        # Если tracked-доля от (tracked+wiped) ниже этого порога — фантом,
        # даже если absolute tracked перевалил за min_tracked_for_active.
        self.min_tracked_ratio_for_active: float = float(
            slot_cfg.get("min_tracked_ratio_for_active", 0.20))
        # Telemetry counters (filled by run loop).
        self.n_tracked = 0
        self.n_low_conf = 0
        self.n_hold = 0
        self.n_coast = 0
        self.n_lost = 0
        self.n_wiped = 0
        self.n_switches = 0
        self.score_sum = 0.0
        self.score_n = 0
        # state_reason histogram for diagnostics.
        self.reason_hist: dict[str, int] = {}
        # Telemetry
        self.state: str = "init"
        self.state_reason: str = "init"
        self.mask_mode: str = "hsv+lab"
        self.confidence: float = 1.0
        self.consecutive_detections: int = 0
        self.lost_frames: int = 0
        self.last_score: float = 0.0
        # LAB range (built once)
        if team.lab_lower is None:
            team.lab_lower, team.lab_upper = build_lab_range_from_hsv(team.hsv_lower, team.hsv_upper)

    # ---- mask & detection ------------------------------------------------
    def _color_mask(self, roi_bgr: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
        m_hsv = cv2.inRange(hsv, self.team.hsv_lower, self.team.hsv_upper)
        if self.team.hsv_lower2 is not None and self.team.hsv_upper2 is not None:
            m_hsv |= cv2.inRange(hsv, self.team.hsv_lower2, self.team.hsv_upper2)
        lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)
        m_lab = cv2.inRange(lab, self.team.lab_lower, self.team.lab_upper)
        mask = cv2.bitwise_and(m_hsv, m_lab)
        self.mask_mode = "hsv+lab"
        if cv2.countNonZero(mask) < 8:
            mask = m_hsv
            self.mask_mode = "hsv_only_fallback"
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.morph_kernel, self.morph_kernel))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        # Дополнительный сильный close, чтобы залить дырки от букв
        # внутри плашки (NAME / RANK), иначе fill падает и shape-фильтр рубит.
        kclose = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (max(7, self.morph_kernel + 4),) * 2
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kclose)
        return mask

    def _effective_roi_size(self) -> int:
        base = self.roi_size + self.roi_expand_px
        # «Захват»: уменьшаем ROI после серии успешных детекций
        if self.consecutive_detections > 15:
            base = max(int(self.roi_size * 0.4), self.min_roi) + self.roi_expand_px
        return max(base, self.min_roi)

    def _find_in_roi(self, roi_bgr: np.ndarray, target_local: tuple[float, float]) -> Optional[tuple[int, int, int, int, float]]:
        if roi_bgr.size == 0:
            self.state_reason = "roi_empty"
            return None
        mask = self._color_mask(roi_bgr)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            self.state_reason = "mask_too_sparse"
            return None
        cand = []
        for c in cnts:
            area = cv2.contourArea(c)
            if area < self.min_area or area > self.max_area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if w < 3 or h < 3:
                continue
            aspect = w / max(1.0, h)
            fill = area / max(1.0, float(w * h))
            # Плашки с текстом дают «дырявую» маску, fill часто 0.08..0.20.
            # Аспект расширен под зум-аут (узкие плашки) и стрелки.
            if not (0.25 <= aspect <= 16.0 and fill >= 0.08):
                continue
            cand.append((x, y, w, h, float(area)))
        if not cand:
            self.state_reason = "shape_reject"
            return None
        # Score: area + proximity to expected (last) center
        max_area = max(c[4] for c in cand)
        tx, ty = target_local
        roi_h = roi_bgr.shape[0]
        best = None
        best_score = -1e9
        for x, y, w, h, area in cand:
            cx = x + w / 2.0
            cy = y + h / 2.0
            dist = math.hypot(cx - tx, cy - ty)
            area_score = area / max(1e-6, max_area)
            dist_penalty = dist / max(1.0, float(roi_h))
            score = area_score * 1.0 - dist_penalty * 0.6
            if score > best_score:
                best_score = score
                best = (x, y, w, h, area)
        self.last_score = float(max(0.0, min(1.0, best_score)))
        return best

    # ---- full-frame recovery -------------------------------------------
    def _recover_global(self, frame_bgr: np.ndarray, H: np.ndarray,
                        pred_canon: tuple[float, float]
                        ) -> Optional[tuple[float, float, float, float, float]]:
        """Search the whole frame for a team-color blob near `pred_canon`.
        Returns (frame_cx, frame_cy, canon_cx, canon_cy, area) or None."""
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, self.team.hsv_lower, self.team.hsv_upper)
        if self.team.hsv_lower2 is not None and self.team.hsv_upper2 is not None:
            m |= cv2.inRange(hsv, self.team.hsv_lower2, self.team.hsv_upper2)
        lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
        m_lab = cv2.inRange(lab, self.team.lab_lower, self.team.lab_upper)
        mask = cv2.bitwise_and(m, m_lab)
        if cv2.countNonZero(mask) < 8:
            mask = m
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.morph_kernel,) * 2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        kclose = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (max(7, self.morph_kernel + 4),) * 2
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kclose)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        best_d = 1e18
        for c in cnts:
            area = cv2.contourArea(c)
            if area < self.min_area or area > self.max_area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if w < 3 or h < 3:
                continue
            aspect = w / max(1.0, h)
            fill = area / max(1.0, float(w * h))
            if not (0.25 <= aspect <= 16.0 and fill >= 0.08):
                continue
            cx = x + w / 2.0
            cy = y + h / 2.0
            ccx, ccy = map_point(H, (cx, cy))
            d = math.hypot(ccx - pred_canon[0], ccy - pred_canon[1])
            if d < best_d and d <= self.recover_gate_px:
                best_d = d
                best = (cx, cy, ccx, ccy, float(area))
        return best

    # ---- main update -----------------------------------------------------
    def update(self, frame_bgr: np.ndarray, H: np.ndarray, t_now: float = 0.0) -> Optional[dict]:
        """Run one frame. Returns dict with canonical_px / frame_px / state, or None if untrackable yet."""
        # Active-slot filter: once a slot is declared inactive, freeze it cheaply.
        if self.state == "inactive":
            self.n_inactive += 1
            return self._snapshot()
        # HUD-authoritative wipe: as soon as the elimination timestamp is reached,
        # the slot is permanently wiped — skip all detection work to keep the report
        # clean and avoid burning CPU on a team that no longer exists on the map.
        if not self.wiped and self.elim_t is not None and t_now >= self.elim_t:
            self.wiped = True
            self.state = "wiped"
            self.state_reason = f"hud_wiped@{self.elim_t}"
            return self._snapshot()
        if self.wiped:
            self.state = "wiped"
            if not self.state_reason.startswith("hud_wiped") and not self.state_reason.startswith("wiped"):
                self.state_reason = "wiped"
            return self._snapshot()
        if self.canonical_px is None:
            self.state = "lost"
            self.state_reason = "no_anchor"
            return None
        # dt since last confirmed observation — drives the motion budget.
        if self.last_seen_t is None:
            dt = self.dt_cap_s
        else:
            dt = min(self.dt_cap_s, max(0.0, t_now - self.last_seen_t))
        # Adaptive v_max: take max of configured baseline and observed peak (with boost).
        v_eff = max(self.v_max_px_s, self.v_observed_peak_px_s * self.v_observed_boost)
        radius = min(self.gate_cap_px, v_eff * dt + self.gate_slack_px)
        # Predicted canonical position from last velocity (zero after miss).
        pred_cx = self.canonical_px[0] + (self.vx * dt if not self.canonical_px_stale else 0.0)
        pred_cy = self.canonical_px[1] + (self.vy * dt if not self.canonical_px_stale else 0.0)
        # Project canonical → frame via H_inv to find ROI center.
        try:
            H_inv = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            self.state = "lost"
            self.state_reason = "H_singular"
            return None
        fx, fy = map_point(H_inv, (pred_cx, pred_cy))
        fh, fw = frame_bgr.shape[:2]
        if not (0 <= fx < fw and 0 <= fy < fh):
            self.state = "lost"
            self.state_reason = "out_of_frame"
            self.lost_frames += 1
            self._on_miss()
            return self._snapshot()
        rs = self._effective_roi_size()
        x0 = max(0, int(fx - rs // 2))
        y0 = max(0, int(fy - rs // 2))
        x1 = min(fw, x0 + rs)
        y1 = min(fh, y0 + rs)
        roi = frame_bgr[y0:y1, x0:x1]
        target_local = (fx - x0, fy - y0)
        det = self._find_in_roi(roi, target_local)
        if det is None:
            # Full-frame recovery: ROI давно мажет — поищем плашку по всему
            # кадру в окрестности предсказания. Не каждый кадр, чтобы не жечь CPU.
            if (self.lost_frames >= self.recover_after_misses
                    and (self.lost_frames - self.recover_after_misses)
                        % max(1, self.recover_interval) == 0):
                rec = self._recover_global(frame_bgr, H, (pred_cx, pred_cy))
                if rec is not None:
                    rcx, rcy, rccx, rccy, rarea = rec
                    self.canonical_px = (rccx, rccy)
                    self.last_frame_px = (rcx, rcy)
                    self.state = "tracked"
                    self.state_reason = "recovered_global"
                    self.canonical_px_stale = False
                    self.last_seen_t = t_now
                    self.consecutive_detections = 1
                    self.lost_frames = 0
                    self.confidence = max(self.confidence, 0.5)
                    self.last_score = 0.5
                    self.vx = 0.0
                    self.vy = 0.0
                    self.roi_expand_px = 0
                    self.n_recovered += 1
                    self.ever_detected = True
                    self._note_near_anchor_hit(rccx, rccy)
                    return self._snapshot()
            self._on_miss()
            return self._snapshot()
        x, y, w, h, area = det
        # Frame-pixel center of the detected blob
        det_fx = x0 + x + w / 2.0
        det_fy = y0 + y + h / 2.0
        # Project back to canonical
        cand_cx, cand_cy = map_point(H, (det_fx, det_fy))
        # Time-aware gating: must lie within motion budget around prediction.
        dist_pred = math.hypot(cand_cx - pred_cx, cand_cy - pred_cy)
        if dist_pred > radius:
            self.state_reason = f"out_of_gate({dist_pred:.0f}>{radius:.0f}px,dt={dt:.1f}s)"
            self._on_miss()
            return self._snapshot()
        # Anti-jump confirmation in canonical space (relative to last KNOWN pos).
        last_cx, last_cy = self.canonical_px
        jump = math.hypot(cand_cx - last_cx, cand_cy - last_cy)
        jump_thresh = max(self.jump_switch_threshold_px, 2.0 * radius)
        if jump > jump_thresh and self.consecutive_detections > 0:
            if self.pending_canon is not None:
                pd = math.hypot(cand_cx - self.pending_canon[0], cand_cy - self.pending_canon[1])
                if pd <= 8.0:
                    self.pending_hits += 1
                else:
                    self.pending_canon = (cand_cx, cand_cy)
                    self.pending_hits = 1
            else:
                self.pending_canon = (cand_cx, cand_cy)
                self.pending_hits = 1
            if self.pending_hits < self.switch_confirm_frames:
                # Hold previous position; don't commit jump yet.
                self.state = "hold"
                self.state_reason = f"switch_wait_{self.pending_hits}/{self.switch_confirm_frames}"
                self.confidence = max(0.35, self.confidence * 0.92)
                self.last_frame_px = (fx, fy)
                return self._snapshot()
            else:
                self.pending_canon = None
                self.pending_hits = 0
                self.state_reason = "switch_confirmed"
                self.n_switches += 1
                # Reset velocity on confirmed jump.
                self.vx = 0.0
                self.vy = 0.0
        else:
            self.pending_canon = None
            self.pending_hits = 0

        # Smooth toward observation. Step budget scales with motion budget.
        dx = cand_cx - last_cx
        dy = cand_cy - last_cy
        dist = math.hypot(dx, dy)
        step_budget = max(self.max_center_step_px, radius)
        if dist > self.center_deadzone_px:
            if dist > step_budget:
                scale = step_budget / max(1e-6, dist)
                dx *= scale
                dy *= scale
            new_cx = last_cx + dx * self.center_smoothing_alpha
            new_cy = last_cy + dy * self.center_smoothing_alpha
            # Update EMA velocity from the smoothed move.
            if self.last_seen_t is not None and (t_now - self.last_seen_t) > 1e-3:
                inst_vx = (new_cx - last_cx) / (t_now - self.last_seen_t)
                inst_vy = (new_cy - last_cy) / (t_now - self.last_seen_t)
                self.vx = self.velocity_alpha * inst_vx + (1 - self.velocity_alpha) * self.vx
                self.vy = self.velocity_alpha * inst_vy + (1 - self.velocity_alpha) * self.vy
                # Track observed peak speed for adaptive gating.
                inst_speed = math.hypot(inst_vx, inst_vy)
                if inst_speed > self.v_observed_peak_px_s:
                    self.v_observed_peak_px_s = inst_speed
            self.canonical_px = (new_cx, new_cy)
        self.last_frame_px = (det_fx, det_fy)
        self.state = "tracked"
        self.canonical_px_stale = False
        self.last_seen_t = t_now
        if self.state_reason != "switch_confirmed":
            self.state_reason = "detected"
        self.confidence = min(1.0, self.confidence * 0.6 + 0.4 + 0.0)
        self.consecutive_detections += 1
        self.lost_frames = 0
        self.ever_detected = True
        # cand_cx/cand_cy = detection projected back to canonical (computed above).
        self._note_near_anchor_hit(cand_cx, cand_cy)
        # Gradually shrink expanded ROI back.
        self.roi_expand_px = max(0, self.roi_expand_px - 20)
        return self._snapshot()

    def _on_miss(self) -> None:
        self.lost_frames += 1
        self.consecutive_detections = 0
        # A single miss breaks the "consecutive near-anchor hits" streak.
        self.near_anchor_consecutive = 0
        # PR-4: a miss is also evidence that the pending switch hypothesis
        # was wrong — don't keep counting toward confirmation across gaps.
        if self.pending_canon is not None:
            self.pending_age += 1
            if self.pending_age > self.pending_ttl_frames:
                self.pending_canon = None
                self.pending_hits = 0
                self.pending_age = 0
        self.confidence = max(0.1, self.confidence - 0.07)
        # Slowly forget old peak so a one-off rocket ride doesn't keep gate huge forever.
        self.v_observed_peak_px_s *= self.v_observed_decay
        if self.lost_frames > 5:
            # Slowly expand ROI to recover.
            self.roi_expand_px = min(self.max_roi_expand_px, self.roi_expand_px + self.roi_expand_step_px)
        # Mark canonical position stale so it is not redrawn as "current".
        self.canonical_px_stale = True
        if self.state == "lost":
            return
        # 1st miss → low_conf; >1 miss → coast (no real observation for a while).
        if self.lost_frames <= 1:
            self.state = "low_conf"
        else:
            self.state = "coast"
        # Active-slot filter: never seen on screen + not protected -> retire.
        # Tightened: use `activated` (requires K consecutive near-anchor hits),
        # not just any single detection. Lone false positives on other teams'
        # placards no longer keep a fantom slot alive forever.
        if (not self.activated
                and self.inactive_after_misses > 0
                and self.lost_frames >= self.inactive_after_misses
                and self.anchor_conf not in ("HIGH", "MED")
                and not self.hud_alive
                and not self.wiped):
            self.state = "inactive"
            self.state_reason = f"never_detected_{self.lost_frames}f"

    def _note_near_anchor_hit(self, cand_cx: float, cand_cy: float) -> None:
        """Detection projected to canonical (cand_cx, cand_cy) succeeded.
        If it's close enough to the seed anchor, count it toward the
        activation streak. Far hits (chasing some other team's placard
        whose canonical projection is elsewhere on the map) reset the streak."""
        if self.init_canonical_px is None:
            # No seed anchor: motion_detect не видел этот слот вообще
            # (anchor MISS). Любая «детекция» здесь — это зацеп за чужой
            # плакард похожего тона. Активировать НЕЛЬЗЯ — пусть слот
            # уйдёт в inactive по misses / post-hoc.
            self.near_anchor_consecutive = 0
            return
        else:
            d = math.hypot(cand_cx - self.init_canonical_px[0],
                           cand_cy - self.init_canonical_px[1])
            if d <= self.near_anchor_radius_px:
                self.near_anchor_consecutive += 1
            else:
                self.near_anchor_consecutive = 0
        if (not self.activated
                and self.near_anchor_consecutive >= self.min_consecutive_for_active):
            self.activated = True

    def anchor_radius_at(self, t_now: float) -> Optional[float]:
        """Прогрессивный радиус стартового якоря (canonical px) на момент t_now.

        Возвращает None, когда якорь больше не активен — это значит ассоциатор
        должен использовать обычный motion-gate без жёсткого фильтра:
          - якорь не сидирован (init_canonical_px is None), или
          - anchor_lock_sec == 0 (фича выключена), или
          - время вышло (t > lock+grow), или
          - LOW-якорь промахнулся первые lock секунд (anchor_lost=True).
        """
        if self.init_canonical_px is None:
            return None
        if self.anchor_lock_sec <= 0.0:
            return None
        if self.anchor_lost:
            return None
        if self.anchor_t0 is None:
            self.anchor_t0 = float(t_now)
        dt = float(t_now) - float(self.anchor_t0)
        lock = self.anchor_lock_sec
        grow = self.anchor_grow_sec
        r0 = self.anchor_r0_px
        r_max = self.anchor_r_max_px
        # LOW-anchor watchdog: после lock-окна без единого попадания внутрь
        # отключаем фильтр (motion_detect мог зацепиться за случайный блик).
        if (dt >= lock and self.anchor_conf == "LOW"
                and self.anchor_inside_hits == 0):
            self.anchor_lost = True
            return None
        if dt <= lock:
            return r0
        if grow <= 0 or dt >= (lock + grow):
            return None  # отпускаем якорь — дальше обычный motion-gate
        # Линейный рост r0 → r_max в окне [lock, lock+grow].
        u = (dt - lock) / grow
        return r0 + (r_max - r0) * u

    def _snapshot(self) -> dict:
        return {
            "team_id": self.team.id,
            "slot_id": self.team.slot_id or self.team.id,
            "canonical_px": [round(self.canonical_px[0], 1), round(self.canonical_px[1], 1)] if self.canonical_px else None,
            "frame_px": [round(self.last_frame_px[0], 1), round(self.last_frame_px[1], 1)] if self.last_frame_px else None,
            "state": self.state,
            "state_reason": self.state_reason,
            "mask_mode": self.mask_mode,
            "confidence": round(float(self.confidence), 3),
            "score": round(float(self.last_score), 3),
        }


    # ---- detect-first API ---------------------------------------------------
    def accept_observation(self, det: dict, t_now: float,
                           det_source: str = "hungarian") -> dict:
        """Применить уже-выбранную ассоциатором детекцию. det содержит
        canonical_px, frame_px, area, color_score. Делает то же что update()
        делает на хвосте после успешной HSV-детекции (смуссинг, EMA velocity,
        активация near-anchor), но без поиска по ROI."""
        # HUD/elim guards — те же, что в update().
        if self.state == "inactive":
            self.n_inactive += 1
            return self._snapshot()
        if not self.wiped and self.elim_t is not None and t_now >= self.elim_t:
            self.wiped = True
            self.state = "wiped"
            self.state_reason = f"hud_wiped@{self.elim_t}"
            return self._snapshot()
        if self.wiped:
            self.state = "wiped"
            return self._snapshot()

        cand_cx, cand_cy = det["canonical_px"]
        det_fx, det_fy = det["frame_px"]

        if self.canonical_px is not None:
            last_cx, last_cy = self.canonical_px
            dx = cand_cx - last_cx
            dy = cand_cy - last_cy
            dist = math.hypot(dx, dy)
            direct_measurement = (det_source == "from_detections" and self.trust_from_detections)
            # PR-2: pending-switch hysteresis. Если кандидат скакнул дальше
            # jump_switch_threshold_px от прошлого центра — НЕ принимаем сразу,
            # требуем switch_confirm_frames подряд таких же скачков рядом.
            jump_thresh = max(self.jump_switch_threshold_px, 2.0 * self.max_center_step_px)
            if dist > jump_thresh and not direct_measurement:
                if self.pending_canon is not None:
                    pd = math.hypot(cand_cx - self.pending_canon[0],
                                    cand_cy - self.pending_canon[1])
                    if pd < jump_thresh:
                        self.pending_hits += 1
                        self.pending_age = 0
                    else:
                        self.pending_canon = (cand_cx, cand_cy)
                        self.pending_hits = 1
                        self.pending_age = 0
                else:
                    self.pending_canon = (cand_cx, cand_cy)
                    self.pending_hits = 1
                    self.pending_age = 0
                if self.pending_hits < self.switch_confirm_frames:
                    # Не двигаем canonical_px, держим прошлый якорь.
                    self.state = "hold"
                    self.state_reason = f"switch_wait_{self.pending_hits}/{self.switch_confirm_frames}"
                    self.confidence = max(0.3, self.confidence * 0.85)
                    # Возвращаем snapshot без обновления позиции.
                    return self._snapshot()
                # Подтверждено — сбрасываем и принимаем как обычно.
                self.pending_canon = None
                self.pending_hits = 0
                self.pending_age = 0
            else:
                self.pending_canon = None
                self.pending_hits = 0
                self.pending_age = 0
            step_budget = dist if direct_measurement else max(self.max_center_step_px, 200.0)
            if dist > self.center_deadzone_px:
                if dist > step_budget:
                    scale = step_budget / max(1e-6, dist)
                    dx *= scale
                    dy *= scale
                alpha = 1.0 if direct_measurement else self.center_smoothing_alpha
                new_cx = last_cx + dx * alpha
                new_cy = last_cy + dy * alpha
                if self.last_seen_t is not None and (t_now - self.last_seen_t) > 1e-3:
                    inst_vx = (new_cx - last_cx) / (t_now - self.last_seen_t)
                    inst_vy = (new_cy - last_cy) / (t_now - self.last_seen_t)
                    self.vx = self.velocity_alpha * inst_vx + (1 - self.velocity_alpha) * self.vx
                    self.vy = self.velocity_alpha * inst_vy + (1 - self.velocity_alpha) * self.vy
                    inst_speed = math.hypot(inst_vx, inst_vy)
                    if inst_speed > self.v_observed_peak_px_s:
                        self.v_observed_peak_px_s = inst_speed
                self.canonical_px = (new_cx, new_cy)
        else:
            self.canonical_px = (cand_cx, cand_cy)

        self.last_frame_px = (det_fx, det_fy)
        # PR-2: запомнить bbox кандидата как identity-якорь для следующих кадров.
        cand_bb = det.get("bbox")
        if cand_bb is not None:
            self.last_bbox = tuple(int(v) for v in cand_bb)
        self.state = "tracked"
        self.canonical_px_stale = False
        self.last_seen_t = t_now
        self.state_reason = f"detect_first:{det_source}"
        self.confidence = min(1.0, self.confidence * 0.6 + 0.4)
        self.last_score = float(det.get("color_score", 0.5))
        self.consecutive_detections += 1
        self.lost_frames = 0
        self.ever_detected = True
        self.roi_expand_px = max(0, self.roi_expand_px - 20)
        self._note_near_anchor_hit(cand_cx, cand_cy)
        return self._snapshot()

    def note_miss(self, t_now: float) -> dict:
        """Слот не получил ассайн на этом кадре. Делегирует _on_miss и
        возвращает snapshot — для совместимости с main loop."""
        if self.state == "inactive":
            self.n_inactive += 1
            return self._snapshot()
        if not self.wiped and self.elim_t is not None and t_now >= self.elim_t:
            self.wiped = True
            self.state = "wiped"
            self.state_reason = f"hud_wiped@{self.elim_t}"
            return self._snapshot()
        if self.wiped:
            self.state = "wiped"
            return self._snapshot()
        # Mirror update()'s out-of-frame/no-anchor short-circuit.
        if self.canonical_px is None:
            self.state = "lost"
            self.state_reason = "no_anchor"
            return self._snapshot()
        self.state_reason = "no_assignment"
        self._on_miss()
        return self._snapshot()


@dataclass
class Track:
    team_id: str
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    angle: Optional[float] = None
    miss: int = 0
    state: str = "alive"
    last_conf: float = 1.0
    slot_id: Optional[str] = None
    last_seen_t: float = 0.0
    wiped_at_t: Optional[float] = None

    def predict(self):
        self.x += self.vx
        self.y += self.vy

    def update(self, mx: float, my: float, angle: Optional[float], conf: float, q: float, r: float):
        # poor man's Kalman: blend predicted and measured
        k = r / (r + q)
        nx = self.x + (mx - self.x) * (1 - k)
        ny = self.y + (my - self.y) * (1 - k)
        self.vx = 0.7 * self.vx + 0.3 * (nx - self.x)
        self.vy = 0.7 * self.vy + 0.3 * (ny - self.y)
        self.x = nx; self.y = ny
        if angle is not None:
            self.angle = angle
        self.miss = 0
        self.state = "alive"
        self.last_conf = conf


class WorldTracker:
    def __init__(self, cfg: dict):
        self.tracks: dict[str, Track] = {}   # one track per team for now
        self.max_gap = int(cfg.get("max_gap_frames", 30))
        self.gate = float(cfg.get("gating_world_dist", 50.0))
        self.q = float(cfg.get("process_noise", 1.0))
        self.r = float(cfg.get("measurement_noise", 4.0))
        # wipe detection
        wcfg = cfg.get("wipe", {}) or {}
        self.wipe_absence_sec = float(wcfg.get("absence_sec", 45.0))
        self.wipe_respect_cuts = bool(wcfg.get("respect_cuts", True))
        # cuts handled by main loop (it freezes last_seen_t around camera cuts)
        self.slot_anchors: dict[str, dict] = {}  # team_id -> anchor info
        self.cur_t: float = 0.0
        self.new_wipes: list[dict] = []
        # HUD-confirmed alive team_ids — absence-based wipe MUST NOT fire for these.
        self.hud_alive_protected: set[str] = set()
        # init-warmup: пока t < init_warmup_sec, не создаём новые треки в углах
        # карты и со слабым score (фикс для THUG в углу до приземления).
        self.init_warmup_sec = float(cfg.get("init_warmup_sec", 0.0))
        self.init_reject_world_margin = float(cfg.get("init_reject_world_margin", 0.0))
        self.init_min_score = float(cfg.get("init_min_score", 0.0))
        self.canonical_size: tuple[float, float] | None = None

    def set_canonical_size(self, size: tuple[float, float]):
        self.canonical_size = (float(size[0]), float(size[1]))

    def _in_warmup_edge(self, x: float, y: float) -> bool:
        """True если точка в запретной кромке карты во время warmup."""
        if self.canonical_size is None or self.init_reject_world_margin <= 0:
            return False
        W, H = self.canonical_size
        m = self.init_reject_world_margin
        return x < m or y < m or x > (W - m) or y > (H - m)

    def set_anchors(self, anchors: dict[str, dict]):
        self.slot_anchors = anchors or {}
        for team_id, a in self.slot_anchors.items():
            if a.get("conf") in ("HIGH", "MED") and a.get("world") is not None:
                wx, wy = a["world"]
                self.tracks[team_id] = Track(
                    team_id=team_id, x=wx, y=wy,
                    state="alive", last_conf=1.0 if a["conf"] == "HIGH" else 0.7,
                    slot_id=a.get("slot_id"), last_seen_t=0.0,
                )

    def step(self, detections_world: list[dict], t: float):
        self.cur_t = t
        self.new_wipes = []
        # 1 predict
        for tr in self.tracks.values():
            tr.predict()
            tr.miss += 1
            if tr.miss > 0 and tr.state == "alive":
                tr.state = "low_conf"
            if tr.miss > self.max_gap:
                tr.state = "lost"
            # 1b wipe detection: long unbroken absence -> mark wiped
            if (tr.wiped_at_t is None
                    and tr.last_seen_t > 0
                    and tr.team_id not in self.hud_alive_protected
                    and (t - tr.last_seen_t) >= self.wipe_absence_sec):
                tr.wiped_at_t = round(t, 2)
                tr.state = "lost"
                self.new_wipes.append({
                    "slot_id": tr.slot_id or tr.team_id,
                    "team_id": tr.team_id,
                    "t": tr.wiped_at_t,
                    "last_world": [round(tr.x, 2), round(tr.y, 2)],
                })
        # 2 group detections by team and pick closest to existing track or pick highest score
        by_team: dict[str, list[dict]] = {}
        for d in detections_world:
            by_team.setdefault(d["team_id"], []).append(d)
        for team_id, dets in by_team.items():
            tr = self.tracks.get(team_id)
            if tr is not None and tr.wiped_at_t is not None and t >= tr.wiped_at_t:
                # команда уже выбита по HUD/absence — игнорим ложные детекции
                continue
            chosen = None
            if tr is not None and tr.state != "lost":
                dets_in_gate = [d for d in dets if math.hypot(d["world"][0] - tr.x, d["world"][1] - tr.y) <= self.gate]
                pool = dets_in_gate or dets
                chosen = min(pool, key=lambda d: math.hypot(d["world"][0] - tr.x, d["world"][1] - tr.y))
            else:
                chosen = max(dets, key=lambda d: d.get("score", 0))
            mx, my = chosen["world"]
            angle = chosen.get("angle_world_deg")
            if tr is None or tr.state == "lost":
                anchor = self.slot_anchors.get(team_id, {})
                # init-warmup: фильтруем заведомо мусорные старты
                if (self.init_warmup_sec > 0
                        and t < self.init_warmup_sec):
                    score = float(chosen.get("score", 1.0) or 0.0)
                    if score < self.init_min_score:
                        continue
                    if self._in_warmup_edge(mx, my):
                        continue
                self.tracks[team_id] = Track(
                    team_id=team_id, x=mx, y=my, angle=angle,
                    last_conf=chosen.get("score", 1.0),
                    slot_id=anchor.get("slot_id"), last_seen_t=t,
                )
            else:
                tr.update(mx, my, angle, chosen.get("score", 1.0), self.q, self.r)
                tr.last_seen_t = t

    def snapshot(self) -> list[dict]:
        out = []
        for tr in self.tracks.values():
            if tr.state == "lost":
                continue
            out.append({
                "team_id": tr.team_id,
                "slot_id": tr.slot_id or tr.team_id,
                "world": [round(tr.x, 2), round(tr.y, 2)],
                "angle_world_deg": None if tr.angle is None else round(tr.angle, 1),
                "state": tr.state,
                "confidence": round(float(tr.last_conf), 3),
            })
        return out


# ---------------------------- Main pipeline ------------------------------

class LiveViewer:
    """Realtime cv2.imshow overlay: canonical map + POI plan + live tracks.

    На первой ошибке cv2 (нет GUI / headless) выключается автоматически.
    Управление: Q/Esc — досрочный выход (graceful, tracks.json дозаписывается).
    """
    WINDOW = "track_teams (Q/Esc to quit)"

    def __init__(self, cmap: "CanonicalMap", anchors_map: dict[str, dict],
                 teams: list[TeamCfg], scale: float = 0.5, every: int = 1):
        self.disabled = False
        self.scale = max(0.1, float(scale))
        self.every = max(1, int(every))
        self.requested_stop = False
        self._tick = 0
        try:
            bg = cv2.imread(
                str(Path(__file__).resolve().parents[2]
                    / "shared" / "canonical_maps" / f"{cmap.name}.png"),
                cv2.IMREAD_COLOR,
            )
        except Exception:
            bg = None
        if bg is None:
            # fallback: grayscale canonical → BGR
            gray = cmap.image if cmap.image is not None else np.zeros(
                (cmap.size[1], cmap.size[0]), dtype=np.uint8)
            bg = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        H0, W0 = bg.shape[:2]
        out_w = max(320, int(round(W0 * self.scale)))
        out_h = max(320, int(round(H0 * self.scale)))
        self._size = (out_w, out_h)
        self._cW, self._cH = cmap.size
        bg = cv2.resize(bg, self._size, interpolation=cv2.INTER_AREA)
        self._bg = (bg * 0.55).astype(np.uint8)
        # pre-bake POI plan layer (yellow circles + tag labels).
        self._plan = self._bg.copy()
        self._tag_by_slot: dict[int, str] = {}
        for t in teams:
            if t.slot is None:
                continue
            a = anchors_map.get(t.id) or {}
            tag = ""
            if t.name:
                tag = t.name.split("·")[0].strip()
            self._tag_by_slot[t.slot] = tag or f"S{t.slot}"
            if a.get("canonical_px") is None:
                continue
            cx, cy = a["canonical_px"]
            r0 = a.get("r0_canonical_px") or (0.03 * self._cW)
            px = int(round(cx / self._cW * out_w))
            py = int(round(cy / self._cH * out_h))
            rr = max(6, int(round(r0 / self._cW * out_w)))
            cv2.circle(self._plan, (px, py), rr, (0, 220, 240), 1, cv2.LINE_AA)
            label = self._tag_by_slot[t.slot]
            cv2.putText(self._plan, label, (px - 14, py - rr - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 220, 240), 1, cv2.LINE_AA)
        try:
            cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.WINDOW, out_w, out_h)
        except cv2.error as e:  # pragma: no cover
            print(f"[show] cv2 GUI unavailable ({e}) — отключаю --show", file=sys.stderr)
            self.disabled = True

    def _world_to_canvas(self, wx: float, wy: float,
                         cmap: "CanonicalMap") -> tuple[int, int]:
        # world ↔ canonical px is affine (см. fit_affine_px_to_world). Используем
        # inverse через cv2: но проще, у нас уже canonical_px у трека есть.
        out_w, out_h = self._size
        return (int(round(wx / self._cW * out_w)),
                int(round(wy / self._cH * out_h)))

    def render(self, frame_idx: int, t_now: float,
               tracks_world: list[dict]) -> None:
        if self.disabled:
            return
        self._tick += 1
        if self._tick % self.every != 0:
            return
        img = self._plan.copy()
        alive = 0
        for s in tracks_world:
            if s.get("state") in ("lost", "wiped"):
                continue
            cpx = s.get("canonical_px")
            if cpx is None:
                continue
            slot = s.get("slot")
            if slot is None:
                continue
            alive += 1
            cx, cy = cpx
            out_w, out_h = self._size
            px = int(round(cx / self._cW * out_w))
            py = int(round(cy / self._cH * out_h))
            color = _slot_color_bgr(int(slot))
            cv2.circle(img, (px, py), 7, color, -1, cv2.LINE_AA)
            cv2.circle(img, (px, py), 8, (0, 0, 0), 1, cv2.LINE_AA)
            tag = self._tag_by_slot.get(int(slot), f"S{slot}")
            cv2.putText(img, tag, (px + 9, py - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (255, 255, 255), 1, cv2.LINE_AA)
        bar_h = 24
        cv2.rectangle(img, (0, 0), (img.shape[1], bar_h), (0, 0, 0), -1)
        mm = int(t_now // 60); ss = int(t_now - mm * 60)
        txt = (f"t={mm:02d}:{ss:02d}  frame={frame_idx}  alive={alive}  "
               f"plan=yellow  Q/Esc=quit")
        cv2.putText(img, txt, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (220, 220, 220), 1, cv2.LINE_AA)
        try:
            cv2.imshow(self.WINDOW, img)
            k = cv2.waitKey(1) & 0xFF
            if k in (27, ord("q"), ord("Q")):
                self.requested_stop = True
        except cv2.error as e:  # pragma: no cover
            print(f"[show] cv2 GUI error ({e}) — отключаю --show", file=sys.stderr)
            self.disabled = True

    def close(self) -> None:
        if self.disabled:
            return
        try:
            cv2.destroyWindow(self.WINDOW)
        except cv2.error:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--frame-step", type=int, default=None)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=-1.0)
    ap.add_argument("--preview", type=Path, default=None)
    ap.add_argument("--debug-frame", type=int, default=None)
    ap.add_argument("--anchors", type=Path, default=None,
                    help="motion_detect/reports/motion_tracks.json для инициализации треков")
    ap.add_argument("--start-coords", type=Path, default=None,
                    help="track_teams/eval/reports/start_coords.json — ALGS POI picks + "
                         "team_tag/name/color per slot. Если задано, используем как "
                         "primary источник команд и стартовых якорей (--anchors игнорируется).")
    ap.add_argument("--show", action="store_true",
                    help="Открыть live-окно cv2 с canonical-картой, POI-планом и треками.")
    ap.add_argument("--show-scale", type=float, default=0.5,
                    help="Масштаб live-окна относительно canonical (default 0.5).")
    ap.add_argument("--show-every", type=int, default=1,
                    help="Рисовать каждый N-й обработанный кадр (default 1).")
    ap.add_argument("--eliminations", type=Path, default=None,
                    help="hud_read/reports/eliminations.json — точные t_first_dead по слоту, "
                         "если задано, заменяет absence-based wipe детекцию")
    ap.add_argument("--from-detections", type=Path, default=None,
                    help="detect_plates/reports/detections.json — взять готовые "
                         "checkpoints (slot уже идентифицирован), отключить "
                         "собственную HSV-детекцию. Доверяем team_key из detect_plates.")
    ap.add_argument("--from-detections-tolerance-frames", type=int, default=0,
                    help="окно поиска checkpoint вокруг текущего кадра, в кадрах. "
                         "0 = auto (sample_step из detections.json).")
    ap.add_argument("--poi-hints", type=Path, default=None,
                    help="JSON {slot|tag: {cx,cy,r}} с приоритетными зонами высадки "
                         "(нормированные координаты канонической карты). "
                         "Используется как prior в стартовом матчере (опционально).")
    ap.add_argument("--da-debug-log", type=Path, default=None,
                    help="JSONL: per-frame DA-разбор (best/second/Δ + компоненты cost "
                         "по слотам и кандидатам). Тяжёлый файл — используй для "
                         "целевых отрезков (--start/--end).")
    ap.add_argument("--da-debug-from", type=float, default=None,
                    help="С какой секунды начинать писать --da-debug-log (default: с 0).")
    ap.add_argument("--da-debug-to", type=float, default=None,
                    help="До какой секунды писать --da-debug-log (default: до конца).")
    args = ap.parse_args()

    if not args.video.exists():
        print(f"[err] не нашёл видео: {args.video}", file=sys.stderr); sys.exit(2)

    if args.poi_hints:
        global POI_HINTS
        POI_HINTS = load_poi_hints(args.poi_hints)
        print(f"[poi-hints] loaded {len(POI_HINTS)} entries from {args.poi_hints}",
              file=sys.stderr)

    cfg = load_config(args.config)
    # anchors path (CLI overrides config); if present, derive 20 teams from it
    # and ignore YAML 'teams:' — this matches motion_detect's per-slot palette.
    anchors_path = args.anchors
    if anchors_path is None and cfg.get("anchors_file"):
        anchors_path = (args.config.parent / cfg["anchors_file"]).resolve()

    # --start-coords (ALGS POI picks) имеет приоритет над --anchors (motion).
    start_coords_path: Path | None = args.start_coords
    if start_coords_path is not None and not start_coords_path.exists():
        print(f"[warn] --start-coords {start_coords_path} not found — ignoring",
              file=sys.stderr)
        start_coords_path = None
    if start_coords_path is not None:
        print(f"[info] start-coords (ALGS POI picks) source: {start_coords_path}")
        # анхоры из motion в этой ветке не используем
        anchors_path = None

    teams: list[TeamCfg] = []
    if start_coords_path is not None or (anchors_path and Path(anchors_path).exists()):
        # Try to load manually calibrated HSV preset for this canonical map.
        # Search order: configs/ next to YAML, then shared/configs, then
        # motion_detect/configs (legacy location). Filename pattern:
        # hsv_presets.<canonical_map_with_dashes>.json
        cmap_name = cfg.get("canonical_map", "storm_point")
        preset_basename = f"hsv_presets.{cmap_name.replace('_', '-')}.json"
        preset_candidates = [
            (args.config.parent / "configs" / preset_basename),
            (Path(__file__).resolve().parents[2] / "configs" / preset_basename),
            (Path(__file__).resolve().parents[1] / "motion_detect" / "configs" / preset_basename),
        ]
        hsv_preset: dict[int, dict] | None = None
        preset_src: Path | None = None
        for cand in preset_candidates:
            if cand.exists():
                try:
                    raw_preset = json.loads(cand.read_text(encoding="utf-8"))
                    hsv_preset = {}
                    for t in raw_preset.get("teams", []):
                        if t.get("slot") is None or "h" not in t or "s" not in t or "v" not in t:
                            continue
                        entry: dict = {"h": t["h"], "s": t["s"], "v": t["v"]}
                        # PR-1: per-team detection overrides (good_tracker parity).
                        for k in ("min_area", "max_area", "morph_kernel",
                                  "morph_kernel_size", "outlier_threshold_ratio"):
                            if k in t and t[k] is not None:
                                key = "morph_kernel" if k == "morph_kernel_size" else k
                                entry[key] = t[k]
                        hsv_preset[int(t["slot"])] = entry
                    preset_src = cand
                    break
                except Exception as e:
                    print(f"[warn] failed to parse hsv preset {cand}: {e}")
        if preset_src:
            print(f"[info] hsv_preset loaded: {preset_src} ({len(hsv_preset or {})} slots)")
        else:
            print(f"[info] hsv_preset not found for canonical_map={cmap_name} — using anchor-derived HSV")
        if start_coords_path is not None:
            teams = teams_from_start_coords(start_coords_path, hsv_preset=hsv_preset)
            print(f"[info] teams: {len(teams)} from start_coords ({start_coords_path})")
        else:
            teams = teams_from_anchors(Path(anchors_path), hsv_preset=hsv_preset)
            print(f"[info] teams: {len(teams)} auto-generated from anchors ({anchors_path})")
    if not teams:
        teams = parse_teams(cfg)
        if anchors_path:
            print(f"[warn] anchors file {anchors_path} unusable — fell back to YAML teams ({len(teams)})")
    if not teams:
        print("[err] в config не описано ни одной команды и нет --anchors", file=sys.stderr); sys.exit(2)
    canonical_dir = (args.config.parent / "canonical_maps").resolve()
    if not canonical_dir.exists():
        canonical_dir = (Path(__file__).resolve().parents[2] / "shared" / "canonical_maps").resolve()
    cmap = load_canonical_map(cfg.get("canonical_map", "storm_point"), canonical_dir)
    reg = FrameRegistrar(cmap, cfg.get("registration", {}))
    det_cfg = cfg.get("detection", {})
    trk = WorldTracker(cfg.get("tracking", {}))
    trk.set_canonical_size((cmap.size[0], cmap.size[1]))
    anchors_map: dict[str, dict] = {}
    if start_coords_path is not None:
        anchors_map = load_start_anchors(start_coords_path, teams, cmap)
        trk.set_anchors(anchors_map)
        n_high = sum(1 for a in anchors_map.values() if a.get("conf") == "HIGH")
        print(f"[info] start-anchors: {n_high}/{len(teams)} HIGH (ALGS POI picks)")
    elif anchors_path:
        mini_affine = load_minimap_affine(cmap.name, canonical_dir)
        anchors_map = load_anchors(Path(anchors_path), teams, mini_affine, cmap)
        trk.set_anchors(anchors_map)
        print(f"[info] anchors: {sum(1 for a in anchors_map.values() if a.get('conf') in ('HIGH','MED'))} HIGH/MED, {sum(1 for a in anchors_map.values() if a.get('conf') == 'LOW')} LOW")
    frame_step = int(args.frame_step or cfg.get("frame_step", 3))

    # ---- Detect-first → associate (new pipeline, opt-in) --------------------
    da_strategy = str(cfg.get("da_strategy", "per_slot_roi")).lower()
    da_weights = cfg.get("da_weights", {}) or {}
    da_debug_near_miss = bool(cfg.get("da_debug_near_miss", False))
    near_miss_counter: Counter = Counter() if da_debug_near_miss else None
    late_game_cfg = cfg.get("late_game", {}) or {}
    late_game_events: list[dict] = []
    minimap_bbox = None
    if da_strategy == "detect_first":
        zones_cfg_path = cfg.get("zones_file")
        if zones_cfg_path:
            zones_path = (args.config.parent / zones_cfg_path).resolve()
        else:
            # default: shared zones.vod.json
            zones_path = (Path(__file__).resolve().parents[2]
                          / "configs" / "zones.vod.json")
        minimap_bbox = load_minimap_roi_bbox(zones_path)
        if minimap_bbox is None:
            print(f"[warn] da_strategy=detect_first но minimap-ROI не найдена "
                  f"({zones_path}) — буду сканировать весь кадр")
        else:
            print(f"[info] da_strategy=detect_first, minimap-ROI={minimap_bbox} "
                  f"(zones={zones_path})")
        if _hungarian is None:
            print("[info] associate=greedy(weight-aware) — scipy не установлен "
                  "(для hungarian: `pip install scipy`)")
        else:
            print("[info] associate=hungarian (scipy)")
    else:
        print(f"[info] da_strategy={da_strategy} (старая логика)")

    # ---- from-detections mode (use detect_plates checkpoints) ---------------
    from_det_index: Optional[dict[int, list[dict]]] = None
    from_det_frames: list[int] = []
    from_det_tol: int = 0
    if args.from_detections is not None:
        if not Path(args.from_detections).exists():
            print(f"[err] --from-detections файл не найден: {args.from_detections}",
                  file=sys.stderr)
            sys.exit(2)
        from_det_index, from_det_frames, sample_step, _det_fps = \
            load_checkpoints_from_detections(Path(args.from_detections), teams)
        from_det_tol = (args.from_detections_tolerance_frames
                        if args.from_detections_tolerance_frames > 0
                        else max(1, sample_step))
        print(f"[info] from-detections mode ON: собственная HSV-детекция отключена, "
              f"tolerance=±{from_det_tol} кадров. da_strategy игнорируется.")

    # ---- HUD eliminations (authoritative wipe times) -------------------------
    elim_path = args.eliminations
    if elim_path is None and cfg.get("eliminations_file"):
        elim_path = (args.config.parent / cfg["eliminations_file"]).resolve()
    if elim_path is None:
        # Last-resort default: the standard hud_read output location.
        guess = (Path(__file__).resolve().parents[1] / "hud_read" / "reports" / "eliminations.json")
        if guess.exists():
            elim_path = guess
    if elim_path is None:
        # Fallback: synced UI copy (src/data/<match>/eliminations.json) — useful
        # when hud_read was run with --out pointing into src/data and the
        # canonical reports/ slot is empty.
        # __file__ = <repo>/scripts/tracking/modules/track_teams/track_teams.py
        # parents: [0]=track_teams [1]=modules [2]=tracking [3]=scripts [4]=<repo>
        repo_root = Path(__file__).resolve().parents[4]
        for guess in sorted((repo_root / "src" / "data").glob("*/eliminations.json")):
            if guess.exists():
                elim_path = guess
                break
    elim_by_slot: dict[int, float] = {}
    hud_alive_slots: set[int] = set()   # slots HUD explicitly marks as alive at match end
    if elim_path and Path(elim_path).exists():
        try:
            raw_elim = json.loads(Path(elim_path).read_text(encoding="utf-8"))
            for slot_key, info in (raw_elim.get("teams", {}) or {}).items():
                try:
                    s = int(slot_key)
                except (TypeError, ValueError):
                    continue
                t_dead = info.get("t_first_dead")
                if t_dead is not None:
                    elim_by_slot[s] = float(t_dead)
                else:
                    hud_alive_slots.add(s)
            print(f"[info] eliminations: {len(elim_by_slot)} dead + {len(hud_alive_slots)} alive (HUD-confirmed) from {elim_path}")
        except Exception as e:
            print(f"[warn] failed to read eliminations {elim_path}: {e}")
    else:
        print("[info] eliminations: not provided — falling back to absence-based wipe detection")

    # Per-slot local trackers (the actual detection workhorse). They seed from
    # motion_detect anchors when available and project canonical → frame each step.
    slot_cfg = dict(det_cfg)  # inherit min/max area, morph_kernel as defaults
    slot_cfg.update(cfg.get("slot_tracker", {}) or {})
    slot_trackers: dict[str, SlotTracker] = {}
    for t in teams:
        a = anchors_map.get(t.id, {}) or {}
        init_canon = None
        if a.get("canonical_px") is not None:
            init_canon = (float(a["canonical_px"][0]), float(a["canonical_px"][1]))
        elim_t = elim_by_slot.get(t.slot) if t.slot is not None else None
        anchor_conf = str(a.get("conf", "MISS"))
        hud_alive = (t.slot is not None and t.slot in hud_alive_slots)
        anchor_r0 = a.get("r0_canonical_px")
        slot_trackers[t.id] = SlotTracker(
            t, slot_cfg, init_canon, elim_t=elim_t,
            anchor_conf=anchor_conf, hud_alive=hud_alive,
            anchor_r0_canonical_px=(float(anchor_r0) if anchor_r0 is not None else None),
        )
    print(f"[info] slot trackers: {sum(1 for s in slot_trackers.values() if s.canonical_px is not None)}/{len(slot_trackers)} seeded with canonical anchor")
    # Pre-seed WorldTracker with HUD wipe times so the sidecar reflects HUD truth
    # instead of (often wrong / early) absence-based detection.
    for t in teams:
        if t.slot in elim_by_slot:
            tr = trk.tracks.get(t.id)
            if tr is not None:
                tr.wiped_at_t = round(elim_by_slot[t.slot], 2)
        elif t.slot in hud_alive_slots:
            # HUD says this team is alive at match end — protect from absence-fallback
            # so a long off-minimap stretch (rotations, edges of map) doesn't fake a wipe.
            trk.hud_alive_protected.add(t.id)
    if hud_alive_slots:
        print(f"[info] absence-wipe protected: {len(trk.hud_alive_protected)} teams (HUD-alive)")

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        print("[err] cv2 не открыл видео", file=sys.stderr); sys.exit(2)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    start_frame = int(args.start * fps)
    end_frame = total if args.end < 0 else min(total, int(args.end * fps))
    if start_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    preview_writer = None
    if args.preview is not None:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        preview_writer = cv2.VideoWriter(str(args.preview), fourcc, fps / frame_step,
                                         (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                          int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))))

    live_viewer: Optional["LiveViewer"] = None
    if args.show:
        live_viewer = LiveViewer(cmap, anchors_map, teams,
                                 scale=args.show_scale,
                                 every=max(1, args.show_every))
        if live_viewer.disabled:
            live_viewer = None
        else:
            print(f"[show] live overlay enabled (scale={args.show_scale}, every={args.show_every})")

    # Streaming JSON writer
    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fout = open(out_path, "w", encoding="utf-8")
    meta = {
        "video": str(args.video.name),
        "fps_source": float(fps),
        "fps_processed": float(fps / frame_step),
        "frame_count": int(end_frame - start_frame),
        "canonical_map": cmap.name,
        "canonical_size": [int(cmap.size[0]), int(cmap.size[1])],
        "world_bounds": cmap.world_bounds,
        "teams": [{"id": t.id, "name": t.name, "color": t.color_hex} for t in teams],
        "slots": [
            {
                "slot_id": t.slot_id or t.id,
                "slot": t.slot,
                "team_id": t.id,
                "name": t.name,
                "color": t.color_hex,
                "anchor_conf": (anchors_map.get(t.id, {}) or {}).get("conf", "MISS"),
                "anchor_world": (lambda a: [round(a[0], 2), round(a[1], 2)] if a else None)(
                    (anchors_map.get(t.id, {}) or {}).get("world")),
                "wiped_at_t": None,
            } for t in teams
        ],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema_version": 2,
        "da_strategy": da_strategy,
    }
    fout.write('{"meta":'); json.dump(meta, fout, ensure_ascii=False); fout.write(',"frames":[')
    first = True

    # ---- DA debug log (optional, JSONL) -------------------------------------
    da_dbg_fp = None
    da_dbg_from = float(args.da_debug_from) if args.da_debug_from is not None else None
    da_dbg_to = float(args.da_debug_to) if args.da_debug_to is not None else None
    if args.da_debug_log is not None:
        args.da_debug_log.parent.mkdir(parents=True, exist_ok=True)
        da_dbg_fp = open(args.da_debug_log, "w", encoding="utf-8")
        print(f"[info] DA-debug log -> {args.da_debug_log} "
              f"(from={da_dbg_from}, to={da_dbg_to})")

    pbar = tqdm(total=(end_frame - start_frame), unit="f", desc="track")
    frame_idx = start_frame
    processed = 0
    try:
        while frame_idx < end_frame:
            ok, frame = cap.read()
            if not ok:
                break
            if (frame_idx - start_frame) % frame_step != 0:
                frame_idx += 1
                pbar.update(1)
                continue

            H, inliers = reg.register(frame)
            if H is None:
                cam = {"registration": "failed", "ransac_inliers": int(inliers)}
                tracks_world = []
            else:
                decomp = decompose_homography(H)
                # pan: где центр кадра попадает на канонической карте
                fw = frame.shape[1]; fh = frame.shape[0]
                cx_can, cy_can = map_point(H, (fw / 2, fh / 2))
                low = inliers < reg.min_inliers
                cam = {
                    "registration": "low_confidence" if low else "ok",
                    "ransac_inliers": int(inliers),
                    "zoom": round(decomp["zoom"], 4),
                    "rotation_deg": round(decomp["rotation_deg"], 2),
                    "pan_canonical": [round(cx_can, 1), round(cy_can, 1)],
                }
                # ---- Per-slot local detection in frame ROI ---------------
                t_now = (frame_idx - start_frame) / fps
                world_dets: list[dict] = []
                slot_snaps: list[dict] = []
                if from_det_index is not None:
                    # ---- from-detections: checkpoints из detect_plates ----
                    if low and not bool(cfg.get("from_detections_accept_low_conf_registration", False)):
                        cam["from_detections_skip"] = "low_confidence_registration"
                        cps = []
                    else:
                        cps = pick_checkpoints_for_frame(
                            frame_idx, from_det_index, from_det_frames, from_det_tol)
                    assigns: dict[str, tuple[int, float, dict]] = {}
                    for c in cps:
                        # canonical_px вычисляем по текущей H (рег. — единственное,
                        # что мы тут делаем сами).
                        cx_can, cy_can = map_point(H, c["frame_px"])
                        c2 = dict(c)
                        c2["canonical_px"] = (cx_can, cy_can)
                        prev = assigns.get(c["team_id"])
                        score = float(c.get("color_score") or 0.0)
                        if prev is None or score > prev[1]:
                            assigns[c["team_id"]] = (int(c.get("_dt", 0)), score, c2)
                    for t in teams:
                        st = slot_trackers[t.id]
                        packed = assigns.get(t.id)
                        det = packed[2] if packed is not None else None
                        if det is not None:
                            snap = st.accept_observation(
                                det, t_now, det_source="from_detections")
                        else:
                            snap = st.note_miss(t_now)
                        if snap is None:
                            continue
                        slot_snaps.append(snap)
                elif da_strategy == "detect_first":
                    candidates = detect_candidates_in_minimap_roi(
                        frame, teams, minimap_bbox, H, det_cfg)
                    dyn_shrink, lg_info = compute_late_game_gate_shrink(
                        slot_trackers, t_now, late_game_cfg)
                    if lg_info is not None:
                        late_game_events.append(lg_info)
                    da_weights_dyn = dict(da_weights)
                    da_weights_dyn["_dyn_gate_shrink"] = dyn_shrink
                    # DA debug sink — пишем только в окне [da_dbg_from, da_dbg_to].
                    in_dbg_window = (
                        da_dbg_fp is not None
                        and (da_dbg_from is None or t_now >= da_dbg_from)
                        and (da_dbg_to is None or t_now <= da_dbg_to)
                    )
                    dbg_sink: Optional[list] = [] if in_dbg_window else None
                    assigns = associate_hungarian(
                        candidates, slot_trackers, t_now, da_weights_dyn,
                        near_miss=near_miss_counter,
                        debug_sink=dbg_sink)
                    # PR-4 (revised): frame-level sanity gate. Раньше любой
                    # кадр, где >= N слотов «прыгали» дальше fixed-порога от
                    # прошлой canonical_px, отбрасывался целиком — это
                    # выкашивало 100% кадров до 420s, потому что на старте
                    # все 20 игроков реально движутся быстро и сравнение
                    # идёт со «стоячей» позицией.
                    # Новая логика:
                    #   * порог по умолчанию поднят (см. конфиг);
                    #   * jump меряем от Kalman-предсказания (vx,vy * dt),
                    #     а не от устаревшей canonical_px;
                    #   * если порог пробили — изолируем именно эти слоты
                    #     (note_miss), остальные слоты кадра остаются
                    #     валидными и получают accept_observation.
                    #   * frame_dropped ставим только если бракованных слотов
                    #     ≥ frame_sanity_max_jumps — это редкий ложный
                    #     глобальный случай (cut/killcam/сорванная H),
                    #     отдельно логируем причину.
                    frame_jump_thresh = float(da_weights.get(
                        "frame_sanity_jump_px", 250.0))
                    frame_jump_max = int(da_weights.get(
                        "frame_sanity_max_jumps", 8))
                    bad_jumps = 0
                    worst_jump_px = 0.0
                    bad_slot_ids: list[str] = []
                    for t in teams:
                        det = assigns.get(t.id)
                        if det is None:
                            continue
                        st = slot_trackers[t.id]
                        if st.canonical_px is None:
                            continue
                        # Predict from Kalman: extrapolate (vx,vy) since last_seen_t.
                        pred_x, pred_y = st.canonical_px
                        if (not st.canonical_px_stale
                                and st.last_seen_t is not None):
                            dt = max(0.0, t_now - st.last_seen_t)
                            pred_x += st.vx * dt
                            pred_y += st.vy * dt
                        dx = det["canonical_px"][0] - pred_x
                        dy = det["canonical_px"][1] - pred_y
                        d2 = dx * dx + dy * dy
                        if d2 > worst_jump_px * worst_jump_px:
                            worst_jump_px = math.sqrt(d2)
                        if d2 > (frame_jump_thresh * frame_jump_thresh):
                            bad_jumps += 1
                            bad_slot_ids.append(t.id)
                    frame_dropped = bad_jumps >= frame_jump_max
                    if bad_jumps > 0:
                        cam["frame_sanity_drop"] = {
                            "bad_jumps": bad_jumps,
                            "threshold_px": frame_jump_thresh,
                            "max_jumps": frame_jump_max,
                            "worst_jump_px": round(worst_jump_px, 1),
                            "bad_slot_ids": list(bad_slot_ids),
                            "frame_dropped": bool(frame_dropped),
                        }
                    bad_slot_set = set(bad_slot_ids)
                    if dbg_sink is not None and da_dbg_fp is not None:
                        dbg_record = {
                            "t": round(t_now, 3),
                            "frame": int(frame_idx),
                            "n_candidates": len(candidates),
                            "dyn_gate_shrink": round(float(dyn_shrink), 3),
                            "frame_dropped": bool(frame_dropped),
                            "bad_jumps": int(bad_jumps),
                            "worst_jump_px": round(worst_jump_px, 1),
                            "bad_slot_ids": list(bad_slot_ids),
                            "candidates": [
                                {
                                    "j": j,
                                    "team_id": c["team_id"],
                                    "canonical_px": [round(c["canonical_px"][0], 1),
                                                     round(c["canonical_px"][1], 1)],
                                    "color_score": round(float(c.get("color_score") or 0.0), 3),
                                }
                                for j, c in enumerate(candidates)
                            ],
                            "slots": dbg_sink,
                        }
                        da_dbg_fp.write(json.dumps(dbg_record, ensure_ascii=False) + "\n")
                    for t in teams:
                        st = slot_trackers[t.id]
                        det = assigns.get(t.id)
                        # Slot-level isolation: дропаем только бракованные
                        # слоты, кроме случая полного frame_dropped.
                        if (det is not None
                                and not frame_dropped
                                and t.id not in bad_slot_set):
                            snap = st.accept_observation(det, t_now)
                        else:
                            snap = st.note_miss(t_now)
                        if snap is None:
                            continue
                        slot_snaps.append(snap)
                else:
                    for t in teams:
                        st = slot_trackers[t.id]
                        snap = st.update(frame, H, t_now=t_now)
                        if snap is None:
                            continue
                        slot_snaps.append(snap)
                for t in teams:
                    st = slot_trackers[t.id]
                    # Only emit world detections for actually observed states.
                    if st.canonical_px is not None and st.state == "tracked":
                        wx, wy = map_point(cmap.px_to_world, st.canonical_px)
                        world_dets.append({
                            "team_id": t.id,
                            "world": (wx, wy),
                            "score": st.last_score,
                            "angle_world_deg": None,
                        })
                # Telemetry pass on slot_snaps.
                for snap in slot_snaps:
                    st = slot_trackers[snap["team_id"]]
                    # Telemetry.
                    s = snap.get("state", "")
                    if s == "tracked":   st.n_tracked += 1
                    elif s == "low_conf": st.n_low_conf += 1
                    elif s == "hold":     st.n_hold += 1
                    elif s == "coast":    st.n_coast += 1
                    elif s == "lost":     st.n_lost += 1
                    elif s == "wiped":    st.n_wiped += 1
                    if s == "tracked":
                        st.score_sum += st.last_score
                        st.score_n += 1
                    # Record dominant state_reason (strip numeric tails for grouping).
                    # Skip wiped frames — they're not real misses and would dominate the histogram.
                    if s != "wiped":
                        rr = snap.get("state_reason", "") or ""
                        rr_key = rr.split("(")[0].split("@")[0] or "?"
                        st.reason_hist[rr_key] = st.reason_hist.get(rr_key, 0) + 1
                # WorldTracker остаётся только для wipe-логики (длительное отсутствие).
                # Feed it only confirmed (tracked) detections to avoid wipe-resets on hold.
                tracked_dets = [d for d in world_dets if any(
                    s["team_id"] == d["team_id"] and s["state"] == "tracked" for s in slot_snaps
                )]
                trk.step(tracked_dets, t_now)
                # Merge WorldTracker wipe state with slot snapshots.
                wipe_states = {tr.team_id: tr for tr in trk.tracks.values()}
                tracks_world = []
                for snap in slot_snaps:
                    tr = wipe_states.get(snap["team_id"])
                    # WorldTracker absence-wipe is a fallback only when SlotTracker
                    # hasn't been told by HUD that the slot is gone. If SlotTracker
                    # already marked wiped (via elim_t), keep its "wiped" state.
                    if (tr is not None and tr.wiped_at_t is not None
                            and t_now >= tr.wiped_at_t
                            and snap.get("state") != "wiped"):
                        snap["state"] = "wiped"
                        snap["state_reason"] = f"wiped@{tr.wiped_at_t}"
                        slot_trackers[snap["team_id"]].wiped = True
                    # world coord (from current canonical)
                    # Don't expose stale positions as if they were observations.
                    st_obj = slot_trackers.get(snap["team_id"])
                    if (snap.get("canonical_px") is not None
                            and snap.get("state") == "tracked"
                            and st_obj is not None
                            and not st_obj.canonical_px_stale):
                        wx, wy = map_point(cmap.px_to_world, snap["canonical_px"])
                        snap["world"] = [round(wx, 2), round(wy, 2)]
                    else:
                        snap["world"] = None
                    tracks_world.append(snap)

            record = {
                "t": round((frame_idx - start_frame) / fps, 3),
                "frame": int(frame_idx),
                "camera": cam,
                "tracks": tracks_world,
            }
            if H is not None and trk.new_wipes:
                record["wipes"] = trk.new_wipes
            if not first:
                fout.write(",")
            json.dump(record, fout, ensure_ascii=False)
            first = False

            if preview_writer is not None:
                vis = frame.copy()
                for s in tracks_world:
                    if "frame_px" not in s:
                        continue
                    if s.get("frame_px") is None:
                        continue
                    x, y = s["frame_px"]
                    color = (0, 255, 0) if s.get("state") == "tracked" else (0, 200, 255)
                    cv2.circle(vis, (int(x), int(y)), 10, color, 2)
                    cv2.putText(vis, s["team_id"], (int(x) + 12, int(y)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                preview_writer.write(vis)

            if live_viewer is not None:
                live_viewer.render(frame_idx, (frame_idx - start_frame) / fps,
                                   tracks_world)
                if live_viewer.requested_stop:
                    print("[show] stop requested via key — завершаю обработку",
                          file=sys.stderr)
                    break

            if args.debug_frame is not None and frame_idx == args.debug_frame:
                dbg = args.out.parent / f"debug_frame_{frame_idx}.png"
                cv2.imwrite(str(dbg), frame)
                print(f"[debug] saved {dbg}")

            processed += 1
            frame_idx += 1
            pbar.update(1)
    finally:
        pbar.close()
        cap.release()
        if preview_writer is not None:
            preview_writer.release()
        if live_viewer is not None:
            live_viewer.close()
        fout.write("]}")
        fout.close()
        if da_dbg_fp is not None:
            da_dbg_fp.close()
            print(f"[ok] DA-debug log written -> {args.da_debug_log}")
    # sidecar: финальные wiped_at_t per slot (мета пишется стримом до накопления wipes)
    slots_final = []
    for t in teams:
        tr = trk.tracks.get(t.id)
        slots_final.append({
            "slot_id": t.slot_id or t.id,
            "slot": t.slot,
            "team_id": t.id,
            "name": t.name,
            "color": t.color_hex,
            "anchor_conf": (anchors_map.get(t.id, {}) or {}).get("conf", "MISS"),
            "wiped_at_t": (tr.wiped_at_t if tr is not None else None),
        })
    (out_path.parent / (out_path.stem + ".slots.json")).write_text(
        json.dumps({"slots": slots_final}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[ok] processed {processed} frames -> {out_path}")
    # ---- Near-miss diagnostics -----------------------------------------
    if near_miss_counter is not None and near_miss_counter:
        slot_by_id = {t.id: (t.slot or "?") for t in teams}
        print(f"[near-miss] top conflicts (winner_slot <- loser_slot : count):")
        for (winner, loser), n in near_miss_counter.most_common(20):
            print(f"  slot_{slot_by_id.get(winner)} <- "
                  f"slot_{slot_by_id.get(loser)}  ({winner} <- {loser})  n={n}")
    # ---- Late-game gate-shrink diagnostics ------------------------------
    if late_game_events:
        print(f"[late-game] gate-shrink triggered on {len(late_game_events)} frames "
              f"(cluster_threshold_px={late_game_cfg.get('cluster_threshold_px')}, "
              f"shrink={late_game_cfg.get('gate_shrink')}):")
        for ev in late_game_events[:10]:
            print(f"  t={ev['t']:>7.1f}  median_d={ev['median_d']:>5.1f}  "
                  f"n_live={ev['n_live']}")
        if len(late_game_events) > 10:
            print(f"  ... +{len(late_game_events) - 10} more")
    # ---- Post-hoc active-slot cleanup ----------------------------------
    # A slot is "fantom" if after the full run:
    #   * never activated (no streak of K near-anchor detections), AND
    #   * либо мало tracked, либо tracked << wiped (ratio).
    # HUD-alive больше НЕ защищает: команда может быть жива по HUD,
    # но трек при этом висит на чужой плашке (ratio резко падает).
    # All its frame entries are rewritten to state=inactive and detection
    # fields are nulled, so downstream consumers see a clean "didn't play".
    fantom_team_ids: set[str] = set()
    for t in teams:
        st = slot_trackers[t.id]
        denom = st.n_tracked + st.n_wiped
        ratio = (st.n_tracked / denom) if denom > 0 else 0.0
        below_ratio = denom >= 30 and ratio < st.min_tracked_ratio_for_active
        below_abs = st.n_tracked < st.min_tracked_for_active
        # Сильный сигнал: соотношение tracked/(tracked+wiped) катастрофически
        # низкое. Срабатывает независимо от `activated` / hud_alive — у живой
        # команды ratio всегда >> 0.5, а у фантома (трек висит на чужой
        # плашке) ratio ~0.1 потому что absence-wipe постоянно стирает точку.
        if below_ratio:
            fantom_team_ids.add(t.id)
            continue
        # Слабый сигнал: команда так и не активировалась и почти нет детекций.
        if not st.activated and not st.hud_alive and below_abs:
            fantom_team_ids.add(t.id)
    if fantom_team_ids:
        print(f"[post-hoc] retiring {len(fantom_team_ids)} fantom slot(s): "
              + ", ".join(sorted(slot_trackers[tid].team.slot_id or tid
                                 for tid in fantom_team_ids)))
        try:
            doc = json.loads(out_path.read_text(encoding="utf-8"))
            converted = 0
            for fr in doc.get("frames", []):
                for snap in fr.get("tracks", []):
                    if snap.get("team_id") in fantom_team_ids and snap.get("state") != "wiped":
                        prev = snap.get("state")
                        snap["state"] = "inactive"
                        snap["state_reason"] = "post_hoc_fantom"
                        snap["canonical_px"] = None
                        snap["frame_px"] = None
                        snap["world"] = None
                        snap["confidence"] = 0.0
                        snap["score"] = 0.0
                        converted += 1
                        # Update telemetry: move count from old bucket to inactive.
                        st = slot_trackers[snap["team_id"]]
                        if prev == "tracked":   st.n_tracked = max(0, st.n_tracked - 1)
                        elif prev == "low_conf": st.n_low_conf = max(0, st.n_low_conf - 1)
                        elif prev == "hold":     st.n_hold = max(0, st.n_hold - 1)
                        elif prev == "coast":    st.n_coast = max(0, st.n_coast - 1)
                        elif prev == "lost":     st.n_lost = max(0, st.n_lost - 1)
                        st.n_inactive += 1
            out_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            print(f"[post-hoc] rewrote {converted} frame entries -> inactive")
        except Exception as e:
            print(f"[warn] post-hoc cleanup failed: {e}")
    # Per-slot tracking summary (compare runs at a glance).
    print("\n[summary] per-slot state distribution")
    print(f"{'slot':<10}{'tracked':>9}{'low_conf':>10}{'hold':>7}{'coast':>7}{'lost':>7}"
          f"{'wiped':>7}{'inact':>7}{'alive%':>8}{'switch':>8}{'avg_sc':>8}")
    for t in teams:
        st = slot_trackers[t.id]
        avg = (st.score_sum / st.score_n) if st.score_n else 0.0
        alive = st.n_tracked + st.n_low_conf + st.n_hold + st.n_coast + st.n_lost
        alive_pct = (100.0 * (st.n_tracked + st.n_low_conf) / alive) if alive else 0.0
        print(f"{t.id:<10}{st.n_tracked:>9}{st.n_low_conf:>10}{st.n_hold:>7}"
              f"{st.n_coast:>7}{st.n_lost:>7}{st.n_wiped:>7}{st.n_inactive:>7}"
              f"{alive_pct:>7.1f}%"
              f"{st.n_switches:>8}{avg:>8.2f}")
    # Dominant state_reason per slot — what is actually failing where.
    print("\n[summary] dominant state_reason per slot (top 3)")
    print(f"{'slot':<10}{'v_peak_px/s':>13}  reasons")
    for t in teams:
        st = slot_trackers[t.id]
        top = sorted(st.reason_hist.items(), key=lambda kv: -kv[1])[:3]
        top_str = ", ".join(f"{k}={v}" for k, v in top)
        print(f"{t.id:<10}{st.v_observed_peak_px_s:>13.1f}  {top_str}")


if __name__ == "__main__":
    main()