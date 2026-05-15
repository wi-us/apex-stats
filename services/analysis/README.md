# Analysis Service

Python service that runs team tracking on a map fragment using the stabilized settings in `app/core/tracking/tracking_settings.py`.

Runtime DB/artifact paths are resolved via `config/runtime_paths.json` (with built-in defaults).

## Run

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
python app/batch_analyze.py --video data/raw/match.mp4 --map mp_storm_point --team 1 --round 1 --visualize
```

## Useful flags

- `--round 1|2|all` - time windows: `0-600`, `600-900`, `0-900`.
- `--max-seconds N` - extra upper bound by timestamp.
- `--workers N` - parallel team analysis workers.
- `--performance-report` - benchmark load report.
- `--benchmark-streams 1,5,10,20` - concurrent stream levels for report.
- `--zones-file path/to/map.zones.json` - polygon file for forbidden-zone filtering.
- `--disable-zone-filter` - disable zone filtering even when zones file exists.
- `--selection-strategy nearest|rightmost|label_arrow` - candidate selection in crowded scenes.
- `--calibration-seconds 30` - first pass for bbox-size calibration, then full re-analysis.
- `--predict-seconds 1.5` - predict-short window before tracker switches to hold mode.
- `--switch-confirm-frames 3` - confirmation frames required before switching to another nearby teammate.
- `--max-step-px 16` - max center/right-edge step per frame for anti-jump stabilization.
- `--job-id analysis-...` - external id for admin-tracking in `output/jobs.json`.
- `--disable-observer-stabilization` - fallback to legacy frame-space only mode.
- `--stabilization-check-every 12` - background confidence + map registration update cadence.
- `--stabilization-low-threshold 0.45 --stabilization-high-threshold 0.65` - hysteresis thresholds for re-localization.
- `--stabilization-fail-limit 3` - degrade transform state after consecutive failed registrations.
- `--benchmark-pan-zoom` - append observer camera benchmark metrics to output JSON.
- automatic PostgreSQL sync runs after output save when `CATALOG_SOURCE=postgres|hybrid` and DB connection is configured (`.env` / env vars).
- `--disable-postgres-sync` - keep output only in JSON files without DB upsert.

## Zone drawing tool

Draw reliability zones on the map and save them as JSON:

```bash
python app/draw_zones.py --map mp_storm_point
```

Optional params:

- `--map-image assets/maps/mp_storm_point.webp` - explicit source image.
- `--output output/zones/mp_storm_point.zones.json` - output file path.
- `--transient-max-dwell 8` - default dwell limit for transient zones.

Controls inside window:

- `LMB` add point
- `Enter` finish polygon
- `1/2/3` mode (`forbidden` / `transient` / `trusted`)
- `U` undo point, `C` clear current polygon, `D` delete last zone
- `S` save JSON, `Q` or `Esc` exit

## Ring HSV Tuner

Interactive tuning for white ring HSV thresholds (sampled every N frames):

```bash
python app/ring_hsv_tuner.py --video ffmpeg_downloader/my_match.mp4 --map mp_storm_point --sample-step 1000
```

Optional params:

- `--start-seconds 0`
- `--end-seconds 900`
- `--output output/ring_hsv_tuner.json`

Keys:

- `A/D` or arrows - previous/next sampled frame
- `P` - print current preset to console
- `S` - save preset JSON
- `Q` / `Esc` - exit

## Crowd stability test

Recommended command for dense same-color teammate groups:

```bash
python app/batch_analyze.py --video data/raw/my_match.mp4 --map mp_storm_point --team 1 --round all --visualize --selection-strategy rightmost --calibration-seconds 30 --predict-seconds 1.5 --switch-confirm-frames 3 --max-step-px 16
```

Success criteria:

- no jumps to "middle point" between teammates during crowd overlap;
- right edge remains stable when nearby same-color labels appear/disappear;
- state transitions in debug overlay are explainable: `tracked` -> `predict` (up to 1-2s) -> `hold` only when reacquire fails.

Note: forbidden polygons are now applied in two stages:
- online gating inside tracker candidate selection (primary analysis pass);
- final safety post-filter on saved trajectory points.

## Runtime status + logs

- Every log line now includes command and elapsed time context.
- Per-team processing logs are emitted on every new `1%` of processed frames.
- Job status is mirrored to `output/jobs.json` (`queued/running/completed/failed`, progress, per-team statuses, errors).

## Output extensions

`output/tracks.json` now includes:
- `errors` - structured per-team errors (`team_id`, `team_name`, `stage`, `message`);
- `jobId` - analysis job id;
- `rings` - detected white ring timeline (`timestampSec`, `x`, `y`, `radius`, `segment`, `confidence`).
- per-point quality fields: `mapX/mapY`, `bgConfidence`, `transformResidual`, `transformState`, `sourceFrameX/sourceFrameY`, `mapSpaceValid`, `backupFrameSpace`.
- optional `observerBenchmark` section (`--benchmark-pan-zoom`) with frame-space vs map-space jitter comparison.

## Minimap Locator

Experimental utility for locating the broadcast minimap crop on the full Apex map image.
Uses OpenCV edge-based multi-scale template matching (not wired into `batch_analyze.py`).

Single frame:

```bash
cd services/analysis
python app/minimap_locator/cli.py \
  --frame path/to/frame.jpg \
  --map-id mp_storm_point \
  --map-path ../../assets/maps/mp_storm_point.png \
  --output-dir ../../output/minimap_locator/test_frame \
  --minimap-x 48 --minimap-y 60 --minimap-size 240 \
  --search-mode full
```

Video (sample every N frames):

```bash
python app/minimap_locator/cli.py \
  --video ../../ffmpeg_downloader/records/example.mp4 \
  --map-id mp_storm_point \
  --map-path ../../assets/maps/mp_storm_point.png \
  --output-dir ../../output/minimap_locator/test_video \
  --every-n-frames 60 \
  --search-mode tiled \
  --debug-video
```

CLI flags: `--minimap-border`, `--min-score`, `--search-scales`, `--tile-size`, `--tile-overlap`, `--max-frames`.

`map_id` is always explicit; `map_path` can be omitted only if the file exists under `assets/maps/` from `config/runtime_paths.json`.

Debug output (when `--output-dir` is set): crop overlay, raw/processed minimap, map with bbox, patch zoom, JSON per frame, optional `debug_video.mp4`.

## Smoke test

1. Run analysis:

```bash
python app/batch_analyze.py --video data/raw/my_match.mp4 --map mp_storm_point --round all --workers 5 --selection-strategy rightmost --calibration-seconds 30 --predict-seconds 1.5 --switch-confirm-frames 5 --max-step-px 16 --zones-file output/zones/mp_storm_point.zones.json
```

2. Verify:
- console logs show `[cmd=... t=+...]` and per-team `progress=...%`;
- `output/jobs.json` contains running/completed job with per-team status and errors;
- `output/tracks.json` has `teams`, `errors`, `rings`.
