# Apex Stats Platform

Production-oriented monorepo for Apex Legends competitive match processing:

1. FACEIT ingest and video preprocessing
2. OpenCV tracking analysis
3. Web visualization (tournaments -> matches -> maps -> timeline player)

## Architecture

- `apps/api` - NestJS API for catalog and job orchestration
- `apps/web` - Next.js UI with team filters and time slider
- `services/ingest` - Node worker for FACEIT metadata + VOD preprocessing
- `services/analysis` - Python batch tracking pipeline
- `packages/shared` - shared DTO contracts
- `infra` - PostgreSQL + Redis bootstrap
- `Server` - minimal VPS production contour (api/web/postgres/redis + runbook)
- `team_tracking` - retained core CV modules used by analysis pipeline
- `videos_collector` - ALGS YouTube ingest + map start/rings enrichment utilities

Active architecture target is documented in `docs/ARCHITECTURE_TARGET.md`.
Runtime source paths are centralized in `config/runtime_paths.json`.

## Quick Start

### 1) Infrastructure

```bash
docker compose -f infra/docker-compose.yml up -d
```

### 2) API

```bash
npm install
npm run dev -w @apex/api
```

API docs: `http://localhost:4000/docs`

### 3) Web

```bash
npm run dev -w @apex/web
```

Web app: `http://localhost:8004`

### 4) Ingest worker

```bash
npm run dev -w @apex/ingest
```

### 5) Analysis batch

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r services/analysis/requirements.txt
python services/analysis/app/batch_analyze.py --video <path_to_video.mp4> --map mp_storm_point
```

## ALGS Refresh Commands

```bash
cd videos_collector

python map_vod_ingest.py \
  --db-path ../output/youtube_ingest/tournaments.sqlite \
  --output-dir ../ffmpeg_downloader/records \
  --archive-before-run \
  --recent-days 30 \
  --clip-20m

python build_algs_liquipedia_db.py \
  --output-db algs_tournaments.sqlite \
  --tournament-url "https://liquipedia.net/apexlegends/Apex_Legends_Global_Series/2026/Split_1/Pro_League/EMEA" \
  --tournament-url "https://liquipedia.net/apexlegends/Apex_Legends_Global_Series/2026/Split_1/Pro_League/APAC_North"
```

## Environment

See `.env.example` for required variables.

Data source flags:
- `CATALOG_SOURCE=sqlite|postgres|hybrid`
- `JOBS_SOURCE=sqlite|postgres|hybrid`

## Legacy Notice

Legacy flow was moved to `Archieve/legacy_flow/` (`main.py`, `src/*`, old config files).
All new development should go into `apps/*` and `services/*`.

## Archieve

Archived/legacy and local non-runtime materials are tracked in `Archieve/README.md`.

## Scope implemented in this baseline

- End-to-end skeleton for ingest -> analysis -> web
- Centralized map/team settings and frame skip usage for tracking
- API and UI contract for tournament/match/map/team track exploration

