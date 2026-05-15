# Integration Brief

- Entry point: `map_vod_ingest.py`
- Map start enrichment: `detect_map_start.py`
- Optional sync to workstation: `vps_records_sync.py`
- Storage:
  - `output/youtube_ingest/tournaments.sqlite`
  - `output/map_start_detection.sqlite`

## Flow

1. Fetch ALGS videos/metadata via `yt-dlp`.
2. Build/update ingest catalog SQLite.
3. Cut per-game VOD clips to `ffmpeg_downloader/records`.
4. Run map-start/rings/teams detection.
5. Feed artifacts into `services/analysis` and API.
