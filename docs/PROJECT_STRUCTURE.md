# Project structure

Principle: **root-level folders describe the role of the code**, not the history of how it was created.

## `apps/`

Runnable TypeScript applications.

- **`apps/api`** — NestJS HTTP API. Add new HTTP modules under `src/modules/<name>/`. Shared infra (runtime path loading, Postgres pool, data-source mode) lives under `src/core/`.
- **`apps/web`** — Next.js UI. Add routes under `app/`. Prefer colocating feature-specific components with their routes until a clear reuse pattern emerges; large cross-cutting UI may later move under `components/` or a `features/` folder.

## `services/`

Long-running workers and offline pipelines.

- **`services/analysis`** — Python/OpenCV analysis. Entry scripts live in `app/` (`batch_analyze.py`, `run_analysis_all_videos.py`, etc.). Shared tracking configuration and CV helpers live under `app/core/tracking/`.
- **`services/ingest-faceit`** — Node ingest for FACEIT. Source is organized under `src/` (`clients/`, `workflow/`, `detection/`, `config/`, `types/`). Add another ingest source as **`services/ingest-<source>/`** with its own package name.

## `packages/`

Published or workspace-local TypeScript libraries shared by apps/services.

- **`packages/shared`** — DTOs/contracts. Extend here when multiple TS packages need the same types.

## `config/`

Machine-readable configuration (JSON). **Do not** duplicate the same conceptual defaults in random scripts—read these files or the API/analysis wrappers around them.

- `runtime_paths.json` — databases, artifact directories, media roots
- `maps.json` — reference map assets (under `assets/maps/`)
- `team_colors.json` — BGR display colors for API; keep aligned with Python `tracking_settings.py`

## `infra/`

Local developer infrastructure (e.g. Docker Compose, Postgres init scripts). Not production VPS wiring.

## `deploy/`

Production-oriented deployment assets that are not part of the compiled apps themselves.

- **`deploy/vps/`** — compose + Node scripts for bootstrap/ETL against Postgres.

## `docs/`

Human-oriented documentation.

- **`architecture/`** — diagrams and target architecture
- **`runbooks/`** — operations: dev, smoke, policies
- **`specs/`** — module behaviour contracts
- **`diploma/`** — academic (VKR) writing

## `tools/`

Scripts and utilities that are **not** production HTTP services.

- **`tools/algs-collector/`** — ALGS ingest, SQLite enrichment, map-start/rings/camera tooling invoked from CLI or API subprocesses
- **`tools/manual-clips/`** — SQL + helpers for manual clip bookkeeping

## `assets/`

Large static inputs that are not source code (map images, etc.).

- **`assets/maps/`** — reference map PNG/WebP files consumed by analysis and the API catalog background resolver

## `archive/`

Frozen history: old prototypes, drafts, legacy requirements exports. **Do not** extend archived code for new product features.

## Where to add…

| Change | Location |
|--------|----------|
| New API module | `apps/api/src/modules/<feature>/` |
| New frontend page | `apps/web/app/<route>/` |
| New analysis script | `services/analysis/app/` (and import shared code from `app/core/` as needed) |
| New ingest source | new `services/ingest-<source>/` workspace package |
| Design prototype | `archive/design-prototypes/` (or a dated subtree under `archive/`) |

## Never commit

See [`CONTRIBUTING.md`](CONTRIBUTING.md): local DBs, raw or rendered video, build caches, `.env` secrets, Playwright output, and generated analysis dumps.
