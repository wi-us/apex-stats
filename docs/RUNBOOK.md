# Runbook

## End-to-end flow

1. API accepts ingest job (`POST /jobs/ingest`).
2. Ingest service reads FACEIT metadata and prepares work fragment + first two rings.
3. Analysis service processes map video with team settings from `team_tracking/tracking_settings.py`.
4. Coordinates are stored in `team_tracks`.
5. Web app queries catalog + tracks and renders map timeline.

Runtime paths and source-of-truth ordering are configured in `config/runtime_paths.json`.

## Operational checklist

- Redis is up (`6379`)
- PostgreSQL is up (`5432`)
- `FACEIT_API_KEY` is set
- API is reachable from web (`NEXT_PUBLIC_API_URL`)

## Failure points

- Missing FACEIT token -> ingest worker fails immediately.
- Wrong map settings -> noisy tracks or empty tracks.
- Missing map ROI constraints -> tracker may lock onto side panels.

## Recovery

- Requeue ingest job with same `faceitMatchId`.
- Requeue analysis for same `mapId` after settings adjustment.
