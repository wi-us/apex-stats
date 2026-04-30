# Architecture Target

## Chosen Direction

Selected strategy: **B -> C**:
- B: unified data-contract/runtime paths and clean module boundaries
- C: PostgreSQL as primary Source of Truth for API catalog/jobs

Goals for this stage:
- Keep active runtime in `apps/*`, `services/*`, `team_tracking/*`, `videos_collector/*`.
- Centralize runtime paths and data source priorities in one config.
- Reduce drift between API, ingest, and analysis defaults.
- Mark root `main.py` + `src/*` as legacy flow (frozen, not removed yet).

## Source Of Truth Policy

- **Catalog tournaments DB:** `config/runtime_paths.json` -> `databases.tournaments` (ordered fallback list).
- **Map start + rings + teams DB:** `config/runtime_paths.json` -> `databases.mapStartDetection`.
- **Tracks artifacts:** `config/runtime_paths.json` -> `artifacts.tracksDir`.
- **Jobs ledger:** `config/runtime_paths.json` -> `artifacts.jobsStore`.
- **Map admin settings:** `config/runtime_paths.json` -> `artifacts.mapAdminSettings`.

## Runtime Ownership

- `apps/api`: read/write catalog + admin + jobs through configured runtime paths.
- `services/ingest`: update jobs ledger through configured runtime paths.
- `services/analysis`: write tracks/jobs/admin artifacts through configured runtime paths.
- `videos_collector`: writes ingest/enrichment SQLite files consumed by API/analysis.

## Legacy Policy

`main.py` and `src/*` are treated as legacy FACEIT CLI path:
- No new features.
- Bugfix-only if needed for old flows.
- New development goes to `services/*` + `apps/*`.

