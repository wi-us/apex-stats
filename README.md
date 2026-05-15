# Apex Stats Platform

Production-oriented monorepo for Apex Legends competitive match processing:

1. FACEIT ingest and video preprocessing
2. OpenCV tracking analysis
3. Web visualization (tournaments → matches → maps → timeline player)

PostgreSQL is the primary source of truth for catalog and jobs in normal operation. SQLite and hybrid modes remain available for local development and import/ETL fallbacks (see `deploy/vps` ETL scripts and environment flags below).

## Repository layout

| Path | Role |
|------|------|
| `apps/api` | NestJS API: catalog, jobs, workspace, map-start orchestration |
| `apps/web` | Next.js UI: tournament/match/map viewer and admin tools |
| `services/analysis` | Python batch tracking pipeline (`app/` entry scripts) |
| `services/ingest-faceit` | Node worker: FACEIT metadata + VOD preprocessing (BullMQ) |
| `packages/shared` | Shared TypeScript contracts |
| `config/` | `runtime_paths.json`, `maps.json`, `team_colors.json` — runtime configuration |
| `infra/` | Local Docker Compose (Postgres, Redis, etc.) |
| `deploy/vps/` | VPS-oriented compose + DB/ETL helper scripts |
| `tools/algs-collector/` | ALGS VOD ingest, map-start enrichment, ring/camera utilities |
| `tools/manual-clips/` | Manual clip job helpers (SQL + sync scripts) |
| `assets/maps/` | Static reference map images for analysis and API backgrounds |
| `archive/design-prototypes/` | HTML design prototypes and local static preview helpers |
| `docs/` | Engineering docs, runbooks, specs, diploma materials — see `docs/README.md` |

Further detail: [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md). Contributing rules: [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md).

## Quick start

### 1) Infrastructure

```bash
docker compose -f infra/docker-compose.yml up -d
```

### 2) API

```bash
npm install
npm run dev -w @apex/api
```

Open `http://localhost:4000/docs` when configured.

### 3) Web

```bash
npm run dev -w @apex/web
```

Default dev URL: `http://localhost:8004` (see package scripts).

### 4) Ingest worker

```bash
npm run dev -w @apex/ingest-faceit
```

### 5) Analysis batch

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r services/analysis/requirements.txt
python services/analysis/app/batch_analyze.py --video <path_to_video.mp4> --map mp_storm_point
```

## Runtime configuration

- Paths to databases, artifacts, and media roots: **`config/runtime_paths.json`** (see defaults in `apps/api/src/core/runtime-paths.ts` and `services/analysis/app/runtime_paths.py`).
- Map asset filenames / directory: **`config/maps.json`** (maps live under **`assets/maps/`**).
- Team display colors (BGR) for API fallbacks: **`config/team_colors.json`** (keep aligned with `services/analysis/app/core/tracking/tracking_settings.py`).

## ALGS refresh (ingest + enrichment)

From repo root:

```bash
cd tools/algs-collector

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

Map-start detector (used by API admin flows and CLI): **`tools/algs-collector/detect_map_start.py`**.

## Environment

See **`.env.example`**. Common flags:

- `CATALOG_SOURCE=sqlite|postgres|hybrid`
- `JOBS_SOURCE=sqlite|postgres|hybrid`
- `DATABASE_URL` — Postgres connection string when using postgres/hybrid modes

## Documentation index

- Architecture target: [`docs/architecture/target.md`](docs/architecture/target.md)
- Dev runbook: [`docs/runbooks/dev-runbook.md`](docs/runbooks/dev-runbook.md)
- Smoke / E2E: [`docs/runbooks/smoke-e2e.md`](docs/runbooks/smoke-e2e.md)
- Output policy: [`docs/runbooks/output-policy.md`](docs/runbooks/output-policy.md)

## Legacy

Older one-off Python dependencies from early experiments live in **`archive/legacy/root-requirements.txt`**. Frozen HTML design exploration lives under **`archive/design-prototypes/`**. Use **`archive/`** (standard spelling) for historical material; active development belongs under `apps/`, `services/`, `tools/`, and `deploy/`.
