# Integration Brief

- Entry point: `main.py`
- Core: `youtube_stats_collector.py`
- Storage: SQLite (`YT_DB_PATH`, default `./yt_stats.sqlite`)
- Auth: OAuth desktop flow, scopes:
  - `yt-analytics.readonly`
  - `youtube.readonly`

## Flow

1. Authenticate (`token.json` cache).
2. Fetch channel (`channels.list(mine=True)`).
3. Query analytics reports.
4. Enrich `top_videos` with titles (`videos.list`).
5. Persist one run to SQLite.

## SQLite schema

- `runs(id, channel_id, channel_title, start_date, end_date, collected_at)`
- `report_<name>(run_id, row_index, <dynamic text columns...>)`

All report values are stored as `TEXT` for easy cross-project ingestion.

## Current reports

- `report_top_videos` (`videoTitle` included)
- `report_daily_overview`
- `report_traffic_sources`
- `report_geo_countries`
- `report_devices`
- `report_operating_systems`
- `report_platforms`
- `report_player_types`
- `report_demographics`
