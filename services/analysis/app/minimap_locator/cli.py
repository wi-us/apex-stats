from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

_APP_ROOT = Path(__file__).resolve().parents[1]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from minimap_locator.debug_viz import save_debug_visualization
from minimap_locator.io import imread_bgr
from minimap_locator.locator import locate_minimap_on_map
from minimap_locator.types import (
    DEFAULT_MINIMAP_CROP_1080P,
    DEFAULT_SEARCH_SCALES,
    MapLocatorConfig,
    MinimapCropConfig,
    VideoTrackingSettings,
)
from minimap_locator.video_tracker import track_minimap_in_video


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_map_path(map_id: str, map_path: str | None) -> str:
    if map_path:
        p = Path(map_path)
        if p.is_file():
            return str(p.resolve())
        root = _project_root()
        for candidate in (
            root / map_path,
            root / "assets" / "maps" / Path(map_path).name,
            root / "maps" / Path(map_path).name,
        ):
            if candidate.is_file():
                return str(candidate.resolve())
        raise FileNotFoundError(f"map file not found: {map_path}")

    root = _project_root()
    maps_cfg = root / "config" / "maps.json"
    if maps_cfg.is_file():
        try:
            payload = json.loads(maps_cfg.read_text(encoding="utf-8"))
            maps_dir = root / str(payload.get("mapsDir", "assets/maps"))
            for name in (f"{map_id}.png", f"{map_id}.webp"):
                candidate = maps_dir / name
                if candidate.is_file():
                    return str(candidate.resolve())
        except Exception:
            pass

    runtime_cfg = root / "config" / "runtime_paths.json"
    if runtime_cfg.is_file():
        try:
            payload = json.loads(runtime_cfg.read_text(encoding="utf-8"))
            media = payload.get("media", {})
            maps_dir = root / str(media.get("mapsDir", "assets/maps"))
            for ext in (".png", ".webp"):
                candidate = maps_dir / f"{map_id}{ext}"
                if candidate.is_file():
                    return str(candidate.resolve())
        except Exception:
            pass

    raise FileNotFoundError(
        f"map_path required: could not resolve map_id={map_id!r}. Pass --map-path explicitly."
    )


def _parse_scales(raw: str | None) -> tuple[float, ...]:
    if not raw:
        return DEFAULT_SEARCH_SCALES
    return tuple(float(x.strip()) for x in raw.split(",") if x.strip())


def build_config(args: argparse.Namespace) -> MapLocatorConfig:
    crop = MinimapCropConfig(
        x=int(args.minimap_x),
        y=int(args.minimap_y),
        size=int(args.minimap_size),
    )
    map_path = _resolve_map_path(args.map_id, args.map_path)
    debug_dir = str(Path(args.output_dir).resolve()) if args.output_dir else None
    return MapLocatorConfig(
        map_id=args.map_id,
        map_path=map_path,
        minimap_crop=crop,
        search_scales=_parse_scales(args.search_scales),
        search_mode=args.search_mode,
        minimap_border=int(args.minimap_border),
        min_score=float(args.min_score),
        good_score=float(getattr(args, "good_score", 0.55)),
        debug_dir=debug_dir,
        tile_size=int(args.tile_size),
        tile_overlap=int(args.tile_overlap),
    )


def run_frame(args: argparse.Namespace) -> int:
    frame_path = Path(args.frame)
    if not frame_path.is_file():
        print(f"error: frame not found: {frame_path}", file=sys.stderr)
        return 1
    frame = imread_bgr(frame_path)
    if frame is None:
        print(f"error: failed to read frame: {frame_path}", file=sys.stderr)
        return 1

    config = build_config(args)
    out_dir = Path(config.debug_dir or args.output_dir or "output/minimap_locator/frame")
    config = MapLocatorConfig(**{**config.__dict__, "debug_dir": str(out_dir)})

    full_map = imread_bgr(config.map_path)
    if full_map is None:
        print(f"error: failed to read map: {config.map_path}", file=sys.stderr)
        return 1

    result = locate_minimap_on_map(frame, config)
    save_debug_visualization(
        frame_bgr=frame,
        config=config,
        result=result,
        full_map_bgr=full_map,
        output_dir=out_dir,
        prefix="frame_000001",
        frame_index=0,
        timestamp_sec=0.0,
    )

    print(
        json.dumps(
            {
                "ok": result.ok,
                "map_id": result.map_id,
                "score": result.score,
                "scale": result.scale,
                "window_size": result.window_size,
                "ambiguous": result.ambiguous,
                "suspicious": result.suspicious,
                "bbox": {
                    "x": result.bbox[0],
                    "y": result.bbox[1],
                    "w": result.bbox[2],
                    "h": result.bbox[3],
                },
                "center": {"x": result.center[0], "y": result.center[1]},
                "reason": result.reason,
                "search_mode": config.search_mode,
                "top_candidates": [
                    {
                        "x": c.bbox[0],
                        "y": c.bbox[1],
                        "w": c.bbox[2],
                        "h": c.bbox[3],
                        "score": c.score,
                        "window_size": c.window_size or c.bbox[2],
                    }
                    for c in (result.candidates or [])
                ],
                "output_dir": str(out_dir),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if result.ok else 2


def run_video(args: argparse.Namespace) -> int:
    config = build_config(args)
    out_dir = Path(config.debug_dir or args.output_dir or "output/minimap_locator/video")
    out_dir.mkdir(parents=True, exist_ok=True)

    frame_step = int(args.frame_step)
    if frame_step <= 0 and args.every_n_frames is not None and args.every_n_frames > 0:
        frame_step = int(args.every_n_frames)

    settings = VideoTrackingSettings(
        frame_step=frame_step,
        sample_interval_sec=float(args.sample_interval_sec),
        min_score=float(args.min_score),
        good_score=float(args.good_score),
        max_jump_distance=float(args.max_jump_distance),
        search_radius=int(args.search_radius),
        allow_global_relock=bool(args.allow_global_relock),
        relock_score=float(args.relock_score),
        smoothing=bool(args.smoothing),
        fast_mode=bool(args.fast_mode),
        save_frame_debug=bool(args.save_frame_debug),
    )

    tracking = track_minimap_in_video(
        video_path=args.video,
        map_id=args.map_id,
        map_path=config.map_path,
        output_dir=str(out_dir),
        crop_config=config.minimap_crop,
        settings=settings,
        config=config,
        minimap_border=int(args.minimap_border),
        max_frames=int(args.max_frames) if args.max_frames is not None else None,
        debug_video=bool(args.debug_video),
        trajectory_image=bool(args.trajectory_image),
    )

    print(
        json.dumps(
            {
                "ok": tracking.summary.get("accepted_points", 0) > 0,
                "map_id": tracking.map_id,
                "output_dir": tracking.output_dir,
                "summary": tracking.summary,
                "result_json": str(out_dir / "result.json"),
                "trajectory_image": str(out_dir / "trajectory_map.jpg"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if tracking.summary.get("accepted_points", 0) > 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Locate broadcast minimap on full Apex map (screenshot + video tracking)."
    )
    parser.add_argument("--frame", help="Single frame image path")
    parser.add_argument("--video", help="Video file path")
    parser.add_argument("--map-id", required=True, help="Map identifier, e.g. mp_storm_point")
    parser.add_argument("--map-path", help="Explicit path to full map image")
    parser.add_argument("--output-dir", default="output/minimap_locator/run", help="Debug output directory")
    parser.add_argument("--minimap-x", type=int, default=DEFAULT_MINIMAP_CROP_1080P.x)
    parser.add_argument("--minimap-y", type=int, default=DEFAULT_MINIMAP_CROP_1080P.y)
    parser.add_argument("--minimap-size", type=int, default=DEFAULT_MINIMAP_CROP_1080P.size)
    parser.add_argument("--minimap-border", type=int, default=12)
    parser.add_argument(
        "--search-mode",
        choices=("window", "full", "tiled"),
        default="window",
    )
    parser.add_argument("--search-scales", help="Comma-separated scales for legacy modes")
    parser.add_argument("--every-n-frames", type=int, default=None, help="Deprecated alias for --frame-step")
    parser.add_argument(
        "--frame-step",
        type=int,
        default=0,
        help="Video sampling step in frames (0 = auto from fps * sample-interval-sec)",
    )
    parser.add_argument(
        "--sample-interval-sec",
        type=float,
        default=1.0,
        help="When frame-step=0, sample about once per this many seconds",
    )
    parser.add_argument("--fast-mode", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--save-frame-debug", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=0.35)
    parser.add_argument("--good-score", type=float, default=0.55)
    parser.add_argument("--max-jump-distance", type=float, default=120.0)
    parser.add_argument("--search-radius", type=int, default=180)
    parser.add_argument("--allow-global-relock", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--relock-score", type=float, default=0.55)
    parser.add_argument("--smoothing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--tile-size", type=int, default=300)
    parser.add_argument("--tile-overlap", type=int, default=120)
    parser.add_argument("--debug-video", action="store_true")
    parser.add_argument("--trajectory-image", action=argparse.BooleanOptionalAction, default=True)

    args = parser.parse_args(argv)
    if bool(args.frame) == bool(args.video):
        parser.error("specify exactly one of --frame or --video")
    if args.frame:
        return run_frame(args)
    return run_video(args)


if __name__ == "__main__":
    raise SystemExit(main())
