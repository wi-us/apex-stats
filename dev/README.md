# Dev Scripts

Quick local entry points for video analysis.

## Console

Open a new PowerShell window in `scripts/tracking` with the tracking venv active:

```powershell
.\dev\open_tracking_console.ps1
```

Activate the tracking venv in the current console by dot-sourcing:

```powershell
. .\dev\enter_tracking_venv.ps1
```

## Analysis

Run the integrated YOLO/OCR plate detector:

```powershell
.\dev\analyze_plate_detector.ps1 -Video scripts\tracking\game_5.mp4 -SyncToUi
```

Run the OpenCV detector:

```powershell
.\dev\analyze_detect_plates.ps1 -Video scripts\tracking\game_5.mp4
```

Run camera cut detection:

```powershell
.\dev\analyze_cuts.ps1 -Video scripts\tracking\game_5.mp4
```

Run HUD analysis:

```powershell
.\dev\analyze_hud.ps1 -Video scripts\tracking\game_5.mp4
```

Run a quick combined pass:

```powershell
.\dev\analyze_video_quick.ps1 -Video scripts\tracking\game_5.mp4 -SyncToUi
```

Generated JSON reports are grouped by video filename under:

```text
scripts/tracking/matches/<video-stem>/
```
