from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class MinimapCropConfig:
    x: int
    y: int
    size: int


DEFAULT_WINDOW_SIZES: tuple[int, ...] = (180, 220, 260, 300, 340, 380, 420, 480)


@dataclass
class MapLocatorConfig:
    map_id: str
    map_path: str
    minimap_crop: MinimapCropConfig
    search_scales: tuple[float, ...] = (0.18, 0.20, 0.22, 0.24, 0.26, 0.28, 0.30, 0.32, 0.35)
    method: str = "window_search"
    search_mode: str = "window"  # "window", "full", or "tiled"
    minimap_border: int = 10
    min_score: float = 0.35
    warning_score: float = 0.45
    good_score: float = 0.55
    debug_dir: Optional[str] = None
    tile_size: int = 300
    tile_overlap: int = 120
    top_n_candidates: int = 5
    window_sizes: tuple[int, ...] = DEFAULT_WINDOW_SIZES
    coarse_step: int = 24
    fine_step: int = 6
    top_k: int = 10
    min_bbox_size: int = 120
    ambiguous_score_delta: float = 0.03


@dataclass
class CandidateMatch:
    bbox: Tuple[int, int, int, int]
    score: float
    scale: float
    tile_id: str = "full"
    window_size: int = 0


@dataclass
class MinimapMatchResult:
    map_id: str
    bbox: Tuple[int, int, int, int]  # x, y, w, h on full map image
    center: Tuple[float, float]  # x, y on full map image
    score: float
    scale: float
    ok: bool
    reason: Optional[str] = None
    candidates: Optional[list[CandidateMatch]] = None
    window_size: int = 0
    ambiguous: bool = False
    suspicious: bool = False


DEFAULT_MINIMAP_CROP_1080P = MinimapCropConfig(x=48, y=60, size=240)

FAST_WINDOW_SIZES: tuple[int, ...] = (220, 300, 380)


@dataclass
class VideoTrackingSettings:
    frame_step: int = 0  # 0 = auto (~1 sample per second from video fps)
    sample_interval_sec: float = 1.0
    min_score: float = 0.35
    good_score: float = 0.55
    max_jump_distance: float = 120.0
    search_radius: int = 180
    allow_global_relock: bool = True
    relock_score: float = 0.55
    smoothing: bool = True
    smooth_window: int = 3
    fast_mode: bool = True
    save_frame_debug: bool = False
    local_coarse_step: int = 20


@dataclass
class VideoTrackingPoint:
    frame_index: int
    timestamp_sec: float
    center: tuple[float, float] | None
    smoothed_center: tuple[float, float] | None
    bbox: tuple[int, int, int, int] | None
    score: float
    window_size: int | None
    status: str
    jump_distance: float | None
    reason: str | None = None


@dataclass
class VideoTrackingResult:
    map_id: str
    video_path: str
    frame_step: int
    fps: float
    total_frames: int
    duration_sec: float
    points: list[VideoTrackingPoint]
    summary: dict
    output_dir: str


DEFAULT_SEARCH_SCALES: tuple[float, ...] = (
    0.18,
    0.20,
    0.22,
    0.24,
    0.26,
    0.28,
    0.30,
    0.32,
    0.35,
)
