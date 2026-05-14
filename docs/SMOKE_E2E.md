# Smoke E2E (ingest -> detect -> analysis -> api)

## 1) Verify runtime paths

Ensure `config/runtime_paths.json` points to your local folders/DBs.

## 2) Build and run API

```bash
npm run build -w @apex/api
npm run dev -w @apex/api
```

Ensure `.env` contains:
- `CATALOG_SOURCE=postgres`
- `JOBS_SOURCE=postgres`
- `DATABASE_URL=postgresql://apex:apex@localhost:5433/apex_stats` (local setup)

## 3) Run map-start enrichment (optional but recommended)

```bash
python videos_collector/detect_map_start.py --records-dir ffmpeg_downloader/records --db-path output/map_start_detection.sqlite
```

## 4) Run batch analysis for one video

```bash
python services/analysis/app/batch_analyze.py --video ffmpeg_downloader/records/<video>.mp4 --use-map-start-db --map-start-db-path output/map_start_detection.sqlite --workers 4 --round all
```

Expected artifacts:
- `output/tracks/*.json`
- `output/jobs.json` updated

## 5) Run all planned videos

```bash
python services/analysis/app/run_analysis_all_videos.py --workers 8 --round all --skip-analyzed --continue-on-error
```

## 6) API checks

- `GET /catalog/tournaments`
- expected: non-test tournaments from PostgreSQL
- `GET /catalog/matches/:matchId/maps`
- `GET /catalog/maps/:mapId/teams`
- `GET /catalog/maps/:mapId/tracks`
- `GET /jobs`

## 7) Web check

```bash
npm run dev -w @apex/web
```

Open UI and validate:
- map list loads
- team names match `/catalog/maps/:mapId/teams`
- tracks render for selected map

