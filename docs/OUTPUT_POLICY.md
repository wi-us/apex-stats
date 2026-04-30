# Output and Artifact Policy

This repository treats local runtime data as **derived artifacts**, not source code.

## Source-of-truth by domain

- `config/runtime_paths.json` defines canonical runtime paths and fallback order.
- `output/youtube_ingest/tournaments.sqlite` (fallback `output/tournaments.sqlite`) is the ingest catalog DB.
- `output/map_start_detection.sqlite` is the map start / Teams / Rings enrichment DB.
- `output/tracks/*.json` is the analysis track artifact store.
- `output/jobs.json` is the operational job ledger shared by API + ingest + analysis.

## Keep out of git

- `output/**`
- `apps/web/.next/**`
- `apps/api/dist/**`
- local Python envs (`.venv`, `yt-stats/.venv`)
- ad-hoc copied artifacts like `*копия*.json`

## Runtime cleanup recommendations

- Periodically clear stale `output/tracks/test_*.json`.
- Keep only canonical DB file (or document when fallback DB is intentionally used).
- Clean local build folders before release snapshots (`.next`, `dist`).
