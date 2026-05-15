from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .io import imread_bgr
from .preprocessing import (
    MatchingFeatures,
    build_valid_matching_mask,
    crop_inner_minimap,
    crop_minimap,
    preprocess_for_matching,
    preprocess_matching_features,
    resize_keep_aspect,
)
from .types import CandidateMatch, MapLocatorConfig, MinimapMatchResult


def _load_map_bgr(map_path: str) -> np.ndarray:
    path = Path(map_path)
    if not path.is_file():
        raise FileNotFoundError(f"map file not found: {map_path}")
    img = imread_bgr(path)
    if img is None:
        raise ValueError(f"failed to read map image: {map_path}")
    return img


def normalized_cross_correlation(
    a: np.ndarray,
    b: np.ndarray,
    mask: np.ndarray | None = None,
) -> float:
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)

    if mask is not None:
        m = mask > 0
        if m.shape != a.shape:
            m = cv2.resize(
                (m.astype(np.uint8) * 255),
                (a.shape[1], a.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ) > 0
        if int(m.sum()) < 100:
            return -1.0
        a = a[m]
        b = b[m]

    a = a - float(a.mean())
    b = b - float(b.mean())
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom <= 1e-6:
        return -1.0
    return float((a * b).sum() / denom)


def compare_images(
    minimap: MatchingFeatures,
    candidate: MatchingFeatures,
    mask: np.ndarray | None = None,
) -> float:
    gray_score = normalized_cross_correlation(minimap.gray, candidate.gray, mask)
    edge_score = normalized_cross_correlation(minimap.edges, candidate.edges, mask)
    return 0.6 * edge_score + 0.4 * gray_score


def _score_window(
    full_feat: MatchingFeatures,
    minimap_feat: MatchingFeatures,
    mask: np.ndarray,
    x: int,
    y: int,
    window_size: int,
    target_wh: tuple[int, int],
) -> float:
    fh, fw = full_feat.gray.shape[:2]
    if window_size > fw or window_size > fh:
        return -1.0
    crop_g = full_feat.gray[y : y + window_size, x : x + window_size]
    crop_e = full_feat.edges[y : y + window_size, x : x + window_size]
    tw, th = target_wh
    resized = MatchingFeatures(
        gray=cv2.resize(crop_g, (tw, th), interpolation=cv2.INTER_AREA),
        edges=cv2.resize(crop_e, (tw, th), interpolation=cv2.INTER_AREA),
    )
    return compare_images(minimap_feat, resized, mask)


def _neighbor_window_sizes(ws: int, window_sizes: tuple[int, ...]) -> list[int]:
    if ws not in window_sizes:
        return [ws]
    idx = window_sizes.index(ws)
    sizes = [ws]
    if idx > 0:
        sizes.append(window_sizes[idx - 1])
    if idx < len(window_sizes) - 1:
        sizes.append(window_sizes[idx + 1])
    return sizes


def _is_edge_suspicious(
    bbox: tuple[int, int, int, int],
    map_shape: tuple[int, int],
    score: float,
    min_score: float,
) -> bool:
    x, y, w, h = bbox
    fh, fw = map_shape
    margin = 40
    at_edge = x < margin or y < margin or (x + w) > (fw - margin) or (y + h) > (fh - margin)
    return at_edge and score < (min_score + 0.1)


def _build_result_from_best(
    config: MapLocatorConfig,
    best: CandidateMatch,
    ranked: list[CandidateMatch],
    map_shape: tuple[int, int],
) -> MinimapMatchResult:
    bx, by, bw, bh = best.bbox
    center = (bx + bw / 2.0, by + bh / 2.0)
    ambiguous = False
    if len(ranked) >= 2:
        ambiguous = abs(ranked[0].score - ranked[1].score) < config.ambiguous_score_delta

    suspicious = _is_edge_suspicious(best.bbox, map_shape, best.score, config.min_score)
    bbox_too_small = bw < config.min_bbox_size or bh < config.min_bbox_size

    ok = best.score >= config.min_score and not bbox_too_small
    reason: str | None = None
    if bbox_too_small:
        reason = "bbox_too_small"
        ok = False
    elif best.score < config.min_score:
        reason = "score_below_threshold"
        ok = False
    elif ambiguous:
        reason = "ambiguous_match"

    return MinimapMatchResult(
        map_id=config.map_id,
        bbox=best.bbox,
        center=center,
        score=best.score,
        scale=best.scale,
        ok=ok,
        reason=reason,
        candidates=ranked[: config.top_n_candidates],
        window_size=best.window_size,
        ambiguous=ambiguous,
        suspicious=suspicious,
    )


def locate_by_window_search(
    minimap_processed: MatchingFeatures,
    full_map_processed: MatchingFeatures,
    config: MapLocatorConfig,
    valid_mask: np.ndarray,
) -> tuple[MinimapMatchResult, list[CandidateMatch]]:
    mh, mw = minimap_processed.gray.shape[:2]
    target_wh = (mw, mh)
    fh, fw = full_map_processed.gray.shape[:2]
    map_shape = (fh, fw)

    coarse_candidates: list[CandidateMatch] = []
    for window_size in config.window_sizes:
        if window_size > fw or window_size > fh:
            continue
        y = 0
        while y + window_size <= fh:
            x = 0
            while x + window_size <= fw:
                score = _score_window(
                    full_map_processed,
                    minimap_processed,
                    valid_mask,
                    x,
                    y,
                    window_size,
                    target_wh,
                )
                if score > -0.99:
                    coarse_candidates.append(
                        CandidateMatch(
                            bbox=(x, y, window_size, window_size),
                            score=score,
                            scale=float(window_size) / max(1, mh),
                            tile_id="window",
                            window_size=window_size,
                        )
                    )
                x += config.coarse_step
            y += config.coarse_step

    if not coarse_candidates:
        return (
            MinimapMatchResult(
                map_id=config.map_id,
                bbox=(0, 0, 0, 0),
                center=(0.0, 0.0),
                score=0.0,
                scale=0.0,
                ok=False,
                reason="no_window_matches",
                candidates=[],
                window_size=0,
            ),
            [],
        )

    coarse_candidates.sort(key=lambda c: c.score, reverse=True)
    seeds = coarse_candidates[: max(config.top_k, config.top_n_candidates)]

    refined: list[CandidateMatch] = []
    radius = config.coarse_step * 2
    for seed in seeds:
        sx, sy, _, _ = seed.bbox
        ws0 = seed.window_size or seed.bbox[2]
        for ws in _neighbor_window_sizes(ws0, config.window_sizes):
            if ws > fw or ws > fh:
                continue
            for dy in range(-radius, radius + 1, config.fine_step):
                for dx in range(-radius, radius + 1, config.fine_step):
                    x = sx + dx
                    y = sy + dy
                    if x < 0 or y < 0 or x + ws > fw or y + ws > fh:
                        continue
                    score = _score_window(
                        full_map_processed,
                        minimap_processed,
                        valid_mask,
                        x,
                        y,
                        ws,
                        target_wh,
                    )
                    if score > -0.99:
                        refined.append(
                            CandidateMatch(
                                bbox=(x, y, ws, ws),
                                score=score,
                                scale=float(ws) / max(1, mh),
                                tile_id="window_fine",
                                window_size=ws,
                            )
                        )

    all_candidates = refined if refined else coarse_candidates
    all_candidates.sort(key=lambda c: c.score, reverse=True)

    # Deduplicate near-identical positions (keep best score).
    deduped: list[CandidateMatch] = []
    for cand in all_candidates:
        cx, cy = cand.bbox[0], cand.bbox[1]
        if any(
            abs(cx - d.bbox[0]) < config.fine_step and abs(cy - d.bbox[1]) < config.fine_step
            for d in deduped
        ):
            continue
        deduped.append(cand)

    ranked = deduped[: max(config.top_k, config.top_n_candidates)]
    best = ranked[0]
    result = _build_result_from_best(config, best, ranked, map_shape)
    return result, all_candidates


def _window_sizes_around(prev_ws: int, fh: int, fw: int) -> list[int]:
    sizes = sorted(
        {
            max(80, prev_ws - 80),
            max(80, prev_ws - 40),
            prev_ws,
            prev_ws + 40,
            prev_ws + 80,
        }
    )
    return [ws for ws in sizes if ws <= fw and ws <= fh]


def locate_by_window_search_local(
    minimap_processed: MatchingFeatures,
    full_map_processed: MatchingFeatures,
    config: MapLocatorConfig,
    valid_mask: np.ndarray,
    center_x: float,
    center_y: float,
    search_radius: int,
    window_sizes_hint: list[int] | None = None,
    coarse_step: int = 12,
) -> tuple[MinimapMatchResult, list[CandidateMatch]]:
    mh, mw = minimap_processed.gray.shape[:2]
    target_wh = (mw, mh)
    fh, fw = full_map_processed.gray.shape[:2]
    map_shape = (fh, fw)

    cx = int(round(center_x))
    cy = int(round(center_y))
    r = max(20, int(search_radius))
    x0 = max(0, cx - r)
    y0 = max(0, cy - r)
    x1 = min(fw, cx + r)
    y1 = min(fh, cy + r)

    ws_list = window_sizes_hint or list(config.window_sizes)
    candidates: list[CandidateMatch] = []
    for window_size in ws_list:
        if window_size > fw or window_size > fh:
            continue
        y = y0
        while y + window_size <= y1:
            x = x0
            while x + window_size <= x1:
                score = _score_window(
                    full_map_processed,
                    minimap_processed,
                    valid_mask,
                    x,
                    y,
                    window_size,
                    target_wh,
                )
                if score > -0.99:
                    candidates.append(
                        CandidateMatch(
                            bbox=(x, y, window_size, window_size),
                            score=score,
                            scale=float(window_size) / max(1, mh),
                            tile_id="window_local",
                            window_size=window_size,
                        )
                    )
                x += coarse_step
            y += coarse_step

    if not candidates:
        return (
            MinimapMatchResult(
                map_id=config.map_id,
                bbox=(0, 0, 0, 0),
                center=(0.0, 0.0),
                score=0.0,
                scale=0.0,
                ok=False,
                reason="no_local_window_matches",
                candidates=[],
                window_size=0,
            ),
            [],
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    ranked = candidates[: max(config.top_k, config.top_n_candidates)]
    best = ranked[0]
    result = _build_result_from_best(config, best, ranked, map_shape)
    return result, candidates


def build_tiles(
    image_shape: tuple[int, int],
    tile_size: int = 300,
    overlap: int = 120,
) -> list[tuple[str, int, int, int, int]]:
    h, w = image_shape[:2]
    tiles: list[tuple[str, int, int, int, int]] = []
    step = max(1, tile_size - overlap)
    ty = 0
    row = 0
    while ty < h:
        th = min(tile_size, h - ty)
        tx = 0
        col = 0
        while tx < w:
            tw = min(tile_size, w - tx)
            tile_id = f"r{row}_c{col}"
            tiles.append((tile_id, tx, ty, tw, th))
            if tx + tw >= w:
                break
            tx += step
            col += 1
        if ty + th >= h:
            break
        ty += step
        row += 1
    return tiles


def _match_template_on_region(
    template: np.ndarray,
    region: np.ndarray,
    scale: float,
    tile_id: str,
    offset_x: int = 0,
    offset_y: int = 0,
) -> Optional[CandidateMatch]:
    th, tw = template.shape[:2]
    rh, rw = region.shape[:2]
    if th > rh or tw > rw or th < 8 or tw < 8:
        return None
    result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    x = int(max_loc[0]) + offset_x
    y = int(max_loc[1]) + offset_y
    return CandidateMatch(
        bbox=(x, y, tw, th),
        score=float(max_val),
        scale=float(scale),
        tile_id=tile_id,
    )


def locate_on_full_map(
    minimap_processed: np.ndarray,
    full_map_processed: np.ndarray,
    config: MapLocatorConfig,
) -> tuple[MinimapMatchResult, list[CandidateMatch]]:
    candidates: list[CandidateMatch] = []
    fh, fw = full_map_processed.shape[:2]

    for scale in config.search_scales:
        template = resize_keep_aspect(minimap_processed, scale)
        th, tw = template.shape[:2]
        if th > fh or tw > fw:
            continue
        cand = _match_template_on_region(template, full_map_processed, scale, "full")
        if cand is not None:
            candidates.append(cand)

    if not candidates:
        return (
            MinimapMatchResult(
                map_id=config.map_id,
                bbox=(0, 0, 0, 0),
                center=(0.0, 0.0),
                score=0.0,
                scale=0.0,
                ok=False,
                reason="no_valid_template_scale",
                candidates=[],
            ),
            [],
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    best = candidates[0]
    bx, by, bw, bh = best.bbox
    center = (bx + bw / 2.0, by + bh / 2.0)
    bbox_too_small = bw < config.min_bbox_size or bh < config.min_bbox_size
    ok = best.score >= config.min_score and not bbox_too_small
    reason = None
    if bbox_too_small:
        reason = "bbox_too_small"
        ok = False
    elif not ok:
        reason = "score_below_threshold"
    result = MinimapMatchResult(
        map_id=config.map_id,
        bbox=best.bbox,
        center=center,
        score=best.score,
        scale=best.scale,
        ok=ok,
        reason=reason,
        candidates=candidates[: config.top_n_candidates],
        window_size=best.bbox[2],
    )
    return result, candidates


def locate_on_tiles(
    minimap_processed: np.ndarray,
    full_map_processed: np.ndarray,
    config: MapLocatorConfig,
) -> tuple[MinimapMatchResult, list[CandidateMatch]]:
    all_candidates: list[CandidateMatch] = []
    fh, fw = full_map_processed.shape[:2]
    tiles = build_tiles((fh, fw), config.tile_size, config.tile_overlap)

    for scale in config.search_scales:
        template = resize_keep_aspect(minimap_processed, scale)
        th, tw = template.shape[:2]
        if th > fh or tw > fw:
            continue
        for tile_id, tx, ty, tw_tile, th_tile in tiles:
            region = full_map_processed[ty : ty + th_tile, tx : tx + tw_tile]
            cand = _match_template_on_region(
                template, region, scale, tile_id, offset_x=tx, offset_y=ty
            )
            if cand is not None:
                all_candidates.append(cand)

    if not all_candidates:
        return (
            MinimapMatchResult(
                map_id=config.map_id,
                bbox=(0, 0, 0, 0),
                center=(0.0, 0.0),
                score=0.0,
                scale=0.0,
                ok=False,
                reason="no_tile_matches",
                candidates=[],
            ),
            [],
        )

    all_candidates.sort(key=lambda c: c.score, reverse=True)
    best = all_candidates[0]
    bx, by, bw, bh = best.bbox
    center = (bx + bw / 2.0, by + bh / 2.0)
    bbox_too_small = bw < config.min_bbox_size or bh < config.min_bbox_size
    ok = best.score >= config.min_score and not bbox_too_small
    reason = None
    if bbox_too_small:
        reason = "bbox_too_small"
        ok = False
    elif not ok:
        reason = "score_below_threshold"
    result = MinimapMatchResult(
        map_id=config.map_id,
        bbox=best.bbox,
        center=center,
        score=best.score,
        scale=best.scale,
        ok=ok,
        reason=reason,
        candidates=all_candidates[: config.top_n_candidates],
        window_size=best.bbox[2],
    )
    return result, all_candidates


def locate_minimap_on_map(
    frame_bgr: np.ndarray,
    config: MapLocatorConfig,
) -> MinimapMatchResult:
    if frame_bgr is None or frame_bgr.size == 0:
        raise ValueError("frame_bgr is empty")

    minimap_raw = crop_minimap(frame_bgr, config.minimap_crop)
    minimap_inner = crop_inner_minimap(minimap_raw, config.minimap_border)
    valid_mask = build_valid_matching_mask(minimap_inner, border_px=max(8, config.minimap_border))
    minimap_clean = minimap_inner
    minimap_feat = preprocess_matching_features(minimap_clean)

    full_map_bgr = _load_map_bgr(config.map_path)
    full_map_feat = preprocess_matching_features(full_map_bgr)

    mode = (config.search_mode or "window").lower()
    if mode == "window":
        result, _ = locate_by_window_search(minimap_feat, full_map_feat, config, valid_mask)
    elif mode == "tiled":
        minimap_edges = minimap_feat.edges
        full_edges = full_map_feat.edges
        result, _ = locate_on_tiles(minimap_edges, full_edges, config)
    else:
        minimap_edges = minimap_feat.edges
        full_edges = full_map_feat.edges
        result, _ = locate_on_full_map(minimap_edges, full_edges, config)

    return result


def locate_minimap_sequence(
    video_path: str,
    map_id: str,
    map_path: str,
    output_dir: str,
    every_n_frames: int = 60,
    max_frames: int | None = None,
    config: Optional[MapLocatorConfig] = None,
    debug_video: bool = False,
) -> list[MinimapMatchResult]:
    from .debug_viz import save_debug_visualization, write_debug_video

    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"video not found: {video_path}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if config is None:
        from .types import DEFAULT_MINIMAP_CROP_1080P

        config = MapLocatorConfig(
            map_id=map_id,
            map_path=map_path,
            minimap_crop=DEFAULT_MINIMAP_CROP_1080P,
            debug_dir=str(out),
        )
    else:
        config = MapLocatorConfig(
            map_id=config.map_id or map_id,
            map_path=config.map_path or map_path,
            minimap_crop=config.minimap_crop,
            search_scales=config.search_scales,
            method=config.method,
            search_mode=config.search_mode,
            minimap_border=config.minimap_border,
            min_score=config.min_score,
            warning_score=config.warning_score,
            good_score=config.good_score,
            debug_dir=str(out),
            tile_size=config.tile_size,
            tile_overlap=config.tile_overlap,
            top_n_candidates=config.top_n_candidates,
            window_sizes=config.window_sizes,
            coarse_step=config.coarse_step,
            fine_step=config.fine_step,
            top_k=config.top_k,
            min_bbox_size=config.min_bbox_size,
            ambiguous_score_delta=config.ambiguous_score_delta,
        )

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"failed to open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    full_map_bgr = _load_map_bgr(map_path)
    results: list[tuple[int, float, MinimapMatchResult]] = []
    debug_frames: list[tuple[np.ndarray, MinimapMatchResult, int, float]] = []
    frame_index = 0
    processed = 0
    step = max(1, int(every_n_frames))

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if max_frames is not None and processed >= max_frames:
                break
            if frame_index % step == 0:
                ts = frame_index / fps if fps > 0 else 0.0
                result = locate_minimap_on_map(frame, config)
                results.append((frame_index, ts, result))
                prefix = f"frame_{frame_index:06d}"
                save_debug_visualization(
                    frame_bgr=frame,
                    config=config,
                    result=result,
                    full_map_bgr=full_map_bgr,
                    output_dir=out,
                    prefix=prefix,
                    frame_index=frame_index,
                    timestamp_sec=ts,
                )
                if debug_video:
                    debug_frames.append((frame.copy(), result, frame_index, ts))
                processed += 1
            frame_index += 1
    finally:
        cap.release()

    summary_path = out / "sequence_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "video": str(path),
                "map_id": map_id,
                "map_path": map_path,
                "frame_count": frame_index,
                "sampled_frames": processed,
                "results": [
                    {
                        "frame_index": fi,
                        "timestamp_sec": ts,
                        "ok": r.ok,
                        "score": r.score,
                        "scale": r.scale,
                        "window_size": r.window_size,
                        "ambiguous": r.ambiguous,
                        "center": {"x": r.center[0], "y": r.center[1]},
                        "bbox": {
                            "x": r.bbox[0],
                            "y": r.bbox[1],
                            "w": r.bbox[2],
                            "h": r.bbox[3],
                        },
                        "reason": r.reason,
                    }
                    for fi, ts, r in results
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if debug_video and debug_frames:
        write_debug_video(
            debug_frames, full_map_bgr, config, out / "debug_video.mp4", fps=min(fps / step, 10.0)
        )

    return [r for _, _, r in results]
