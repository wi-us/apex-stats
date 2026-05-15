# Contributing

## Do not commit

- Local SQLite / DuckDB / ad-hoc `.db` files
- Raw or rendered match videos (`*.mp4`, `*.mov`, `*.mkv`, `*.webm`, `*.avi`)
- Generated video outputs and huge frame dumps
- TypeScript incremental cache (`*.tsbuildinfo`)
- Python caches (`.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `__pycache__/`)
- Playwright or other HTML test reports (`playwright-report/`, `test-results/`)
- Temporary analysis artifacts under `output/` that are machine-specific
- Secrets (`.env`, tokens, cookies); only commit `.env.example` patterns

## Before opening a PR

From the repository root:

```bash
npm install
npm run build -ws --if-present
npm run test -ws --if-present
```

### Python analysis service

```bash
pip install -r services/analysis/requirements.txt
```

Run or re-read the smoke flow in [`docs/runbooks/smoke-e2e.md`](runbooks/smoke-e2e.md) when touching ingest, map-start, or analysis batch paths.

## Structural expectations

Follow [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md): keep production code under `apps/`, `services/`, and `packages/`; put CLIs and research tooling under `tools/`; keep large static inputs under `assets/`; archive obsolete material instead of leaving it at the repository root.
