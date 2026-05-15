# Observer tracking and ring size cap — implementation spec

This document describes the behaviour implemented in `tools/algs-collector/detect_map_start.py` and how to replicate it in another module.

## Goals

1. **Strict ring size ceiling**: no detected ring radius may exceed the canonical Apex diameter for that ring (`RING_DIAMETERS_METERS`).
2. **Observer camera sampling**: estimate zoom/pan from how the minimap-safe circle sits inside the tracked minimap ROI, on a **fixed 10-frame grid**.
3. **Phase-aware motion**: distinguish ring motion vs observer motion; treat `COUNTDOWN` as nominally static; within one `CLOSING` segment, estimate a single “ring motion pace” and flag spikes compatible with observer zoom.
4. **Persistence**: write one **`Camreman` row per sample** (every 10th analysed frame inside the ring timeline window). Fallback to sparse ring-derived jumps only when the sampler produces no rows.

## Ring — strict maximum radius (map units)

Canonical diameters (`RING_DIAMETERS_METERS`) define the maximum **physical** ring size. Map-space radius cap:

- Helper: `max_ring_radius_map_units(ring_number, meters_to_map_units)`.
- It equals canonical radius in map pixels (respects `MAP_RING_RADIUS_MAP_OVERRIDE` / calibration the same way as `expected_ring_radius_map_units`).
- Enforcement layers:
  - **Detection**: `detect_ring_geometry_in_frame` rejects candidates with `radius > max_radius_map_units` when passed.
  - **Window aggregation**: after `weighted_median` of radii in `estimate_ring_geometry_over_window`, **clamp again** so aggregation cannot exceed the cap.
  - **Enrichment**: retry/probe logic already rejects oversized candidates; **fallback geometry** clamps with `min(fallback_radius, max_radius_for_ring)` where applicable.

## Observer — ROI and four margins

Coordinates from detection are **map-normalised** `(0–1080)²`.

For each video frame:

1. Compute minimap rectangle in **frame pixels** with `ring_minimap_bounds(frame)` (scales logical `MAP_ROI_*` to frame size).
2. Project `(x_map, y_map, r_map)` into that rectangle via `ring_map_to_frame(...)`, yielding `(px, py, pr)` in frame pixels (`pr ≥ 1`).
3. Four **clearances** (pixels) from ROI edges to circle:

   - `margin_left = max(0, (px - pr) - x1)`
   - `margin_right = max(0, x2 - (px + pr))`
   - `margin_top = max(0, (py - pr) - y1)`
   - `margin_bottom = max(0, y2 - (py + pr))`

4. Temporal deltas (between consecutive samples, `Δt ≥ 10/fps`):  
   `speed_i = abs(Δmargin_i) / Δt`,  
   `speed_sample = max(speed_left, speed_right, speed_top, speed_bottom)`.

Tolerances scale with **`roi_px_span = x2 - x1`** so 1080p and other scales behave similarly.

## Detection on the observer path

- `collect_observer_motion_rows(...)` calls `detect_ring_geometry_in_frame` with **`arc_only_mode=True`** (fast arc-first path).
- Expected centre/radius and min/max clamps come from the **ring row active at timestamp** `_resolve_ring_row_for_timestamp`.

## State machine (`stable` / `suspicious`)

**Enter `suspicious`** when **any**:

- **Abrupt speed**: `speed_sample > max(1.75, median(recent_speeds) × 2.6)`.
- **Abrupt margin jump**: large `max(delta margins)` vs scale-aware `edge_tol`, or spike vs mean delta.
- **COUNTDOWN motion**: phase is countdown and speed above a scale-aware threshold (zone should be static aside from observer).
- **CLOSING spike vs ring baseline**: exponentially smoothed `closing_motion_speed_ema` exceeded by a factor (observer-style zoom interrupting coherent ring motion).

**Return to `stable`**

- Count consecutive suspicious samples aligned with smoothed ambient speed (“calming down”).
- Require **`stable_window_frames`** worth of logical time: `stable_needed = ceil(120 / sample_step_frames)` samples (with `sample_step_frames=10` ⇒ **≈120 video frames** between samples).
- Additionally require **low variance** in the last `stable_needed` speed samples (“uniform drift”) before accepting stable recovery.

Each **new CLOSING phase** resets `closing_motion_speed_ema` so ring closing speed is re-estimated **after countdown gaps**.

While **`stable`** and **`phase == closing`**, refresh:

`closing_motion_speed_ema ← 0.88 * ema + 0.12 * speed_sample`.

On **`observer_action`**, clear **`closing_motion_speed_ema`** and phase baselines that are keyed by observer disruption.

Also maintained: `phase_speed_baseline[(ring_no, phase)]` — EWMA baseline dropped on observer action (auxiliary bookkeeping for cross-phase cues).

## Phases `_derive_ring_phase_at_timestamp`

From DB ring row `[time_start, time_end]` and canonical `RING_PHASE_SEQUENCE` durations:

- **`closing`** for `ring_number = N`: from `time_start` through `closing` duration (`RING N CLOSING` length).
- **`countdown`**: intervals before ring start (`RING 1 COUNTDOWN` implicit modelling) or after closing through `time_end`.

## Persistence — `Camreman`

- **Primary**: rows from `collect_observer_motion_rows` only (dense ~10-frame grid over ring window).
- **Fallback**: `infer_camreman_rows_from_rings` **only if** the sampler yields zero rows (e.g. missing zones geometry).
- **Visualisation jump dump** (`_camera_jump_events`): appended to `camreman_rows` in `analyze_video` **only when** enrichment produced **no** camreman samples (backward compatibility / debug aid).

Upsert clears previous rows per `game_id` and inserts `{ timestamp_sec, x, y, camera_size }`; schema unchanged — **no migration**.

`camera_size` ≈ observable viewport diameter proxy:

- `zoom_ratio = detected_radius / max(expected_radius_ring_row, epsilon)`
- `camera_size = clip(1080 / zoom_ratio, 120, 1080)`

Coordinates `x`,`y` are **map-normalised circle centre**.

## Minimal port steps (another module)

1. Copy helpers: `max_ring_radius_map_units`, optionally `min_ring_radius_map_units`, `ring_phase_duration_seconds`, phase derivation.
2. Wire **max_radius** through every detector and **post-aggregate clamp**.
3. Implement `_minimap_edge_margins_px` equivalent for your ROI definition.
4. Run **10-frame** loop over your analysis window; reuse or reimplement arc-first detection.
5. Port **stable/suspicious** transitions and **CLOSING EMA / COUNTDOWN quiescence** rules.
6. Write **dense** `Camreman` inserts; fallback policy as above.

## Validation checklist

- [ ] For each `ring_number`, no stored `radius` above `max_ring_radius_map_units` for that calibration.
- [ ] `Camreman` timestamps follow ~`10/fps` spacing when rings exist (except gaps from failed arc reads).
- [ ] Synthetic zoom on minimap ⇒ `observer_action`/suspicious interval in logs; post-stabilisation returns to stable with uniform variance test.
- [ ] **`COUNTDOWN`**: marginal speeds near zero absent observer motion.
- [ ] Fallback path: if sampler offline, sparse ring-derived `Camreman` still populates.

