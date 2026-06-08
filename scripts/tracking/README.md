# Tracking Lab - local scripts

Pipeline for processing Apex Legends VODs. Heavy CV/ML work runs locally; the web app only consumes generated JSON artifacts from `src/data/<game>/`.

## Install

```powershell
cd scripts\tracking
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Active Structure

```text
scripts/tracking/
  requirements.txt
  configs/
    plate_detector/ # match/team configs for the integrated YOLO/OCR detector
  matches/          # per-video JSON reports, one folder per video stem
  shared/
    canonical_maps/
    schema/
  modules/
    find_cuts/       # camera cut detection
    detect_plates/   # current plate detection and slot tracking
    plate_detector/  # integrated YOLO/OCR plate detector from the former sibling project
    recognize_tags/  # current tag crop extraction / CNN training area
    hud_read/        # HUD timeline, rings, eliminations
    ring_locator/    # ring geometry on canonical maps
    _archived/       # replaced experiments and legacy modules
```

Archived modules:

- `detect_teams/`
- `debug_register/`
- `motion_detect/`
- `ocr_tags/`
- `paddle_ocr_test/`
- `track_teams/`

## Current Run Order

1. `find_cuts` - detect camera cuts in the VOD.
2. `detect_plates` or `plate_detector` - detect team plates and build slot trajectories.
3. `recognize_tags` - extract/tag crops and train/infer team tags.
4. `hud_read` - read match HUD, ring phases, alive teams, eliminations.
5. `ring_locator` - convert ring data to canonical map geometry.
6. `scripts/postprocess/*` - apply tags and clean `tracks.json`.
7. `hud_read/sync_to_ui.py` - copy reports to `src/data/<game>/`.

The web app depends on the final JSON files in `src/data/<game>/`, not on the archived Python modules.

## Integrated Plate Detector

The former top-level `plate_detector` project now lives at:

```text
scripts/tracking/modules/plate_detector/
```

Use the tracking wrapper from `scripts/tracking`:

```powershell
.\run_plate_detector.ps1 -Video modules\plate_detector\videos\<vod>.mp4 -SyncToUi
```

This wrapper runs color profile export, optional POI priors, YOLO/OCR detection,
track building, and then `modules/plate_detector/sync_to_ui.py`. Generated files
stay under `matches/<video-name>/plate_detector/`; only selected JSON/report
artifacts are copied into `src/data/<game>/`.

By default the match folder name is derived from the video filename. For example,
`videos/game_5.mp4` writes JSON reports to:

```text
scripts/tracking/matches/game_5/plate_detector/
```

The same match-folder convention is used by active wrappers for `find_cuts`,
`detect_plates`, `hud_read`, and `ring_locator` when `-Out` is not passed.
