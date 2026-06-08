# Plate Detector Integration

This module was moved from the former top-level `plate_detector` folder into the
unified Apex Stats tracking tree.

## Entry Points

From `scripts/tracking`:

```powershell
.\run_plate_detector.ps1 -Video modules\plate_detector\videos\<vod>.mp4 -RunName latest -SyncToUi
```

From this module:

```powershell
.\run.ps1 -Video videos\<vod>.mp4 -RunName latest -SyncToUi
```

## Output Contract

- Detection reports are written to `scripts/tracking/matches/<video-name>/plate_detector/`.
- `scripts/build_tracks_from_detections.py` writes
  `scripts/tracking/matches/<video-name>/plate_detector/tracks.json`.
- `sync_to_ui.py` copies `tracks.json` and writes `tracks.slots.json` to
  `src/data/<game>/`.
- Use `-WriteSlotTags` only when plate metadata should replace the existing
  `slot-to-tag.json`.

## Archived / Local-Only Data

Config files are centralized in `scripts/tracking/configs/`:

- shared configs: `zones.vod.json`, `hsv_presets.*.json`
- plate detector match configs: `configs/plate_detector/*.json`

Heavy local assets remain in this module but are ignored by git:

- `_archive/`
- `videos/`
- `clips/`
- `runs/`
- `outputs/`, `reports/`, `loops/`
- training datasets and SQLite dumps

The older README is kept as project history and command reference.
