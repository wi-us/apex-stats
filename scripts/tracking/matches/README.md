# Match Reports

Generated JSON reports are grouped by video filename:

```text
scripts/tracking/matches/<video-stem>/
  find_cuts/
  detect_plates/
  plate_detector/
  hud_read/
  ring_locator/
```

The folder is local output storage. Final web app data is still synced into
`src/data/<game>/` by the relevant `sync_to_ui.py` script.
