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
- `team_tracking` - retained core CV modules used by analysis pipeline
- `yt-stats` - ALGS YouTube ingest + map start/rings enrichment utilities

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

## Environment

See `.env.example` for required variables.

## Legacy Notice

The root `main.py` + `src/*` flow is legacy and kept for backward compatibility only.
All new development should go into `apps/*` and `services/*`.

## Scope implemented in this baseline

- End-to-end skeleton for ingest -> analysis -> web
- Centralized map/team settings and frame skip usage for tracking
- API and UI contract for tournament/match/map/team track exploration
