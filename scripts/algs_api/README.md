# ALGS API integration

Primary data source for ALGS tournaments. Liquipedia is used only as a
fallback for non-ALGS events.

## Why

`prod-api.algstools.com` exposes the official ALGS data set (seasons,
events, series, matches, POI drafts with normalized spawn coordinates,
per-match stats). It is richer and far more accurate than scraping
Liquipedia, and does not require an auth token for the read endpoints
we use.

## Components

- `client.py`  Throttled HTTP client (token bucket + jittered backoff +
  on-disk JSON cache). Default budget: ~2 req/s sustained, burst 5, with
  conservative retry on 429/5xx.
- `db.py`      SQLite schema + upsert helpers. Tables: `seasons`,
  `tournaments`, `regions`, `events`, `phases`, `series`, `matches`,
  `match_banned_legends`, `maps`, `teams`, `team_versions`,
  `characters`, `spawn_locations`, `poi_drafts`, `poi_picks`,
  `series_team_stats`, `match_team_stats`, `match_player_stats`.
  Schema is intentionally close to the API shape so a later migration
  to Postgres is mechanical.
- `sync.py`    CLI: walks seasons -> tournaments -> events -> series ->
  matches/POI/stats and upserts everything into SQLite. Re-runs are safe
  (idempotent upserts).
- `export_poi_zones.py`  CLI: reads `spawn_locations` from SQLite and
  writes `src/data/maps/<map_id>/poi_zones.json` in the format consumed
  by `src/lib/poi-zones.ts` (cx/cy in 0..1, default radius). Manual
  radius/aliases tweaks made in the admin editor are preserved by id.
- `build_poi_hints.py`  CLI: emits a hints JSON for
  `track_teams.py --poi-hints` from a given `series_id` (uses ALGS picks
  joined with canonical zones).

## SQLite location

Default DB path is `scripts/algs_api/data/algs.sqlite` (gitignored). It
is a local cache; nothing in the app reads from SQLite at runtime yet.
The same data can later be mirrored to the Lovable Cloud database.

## Typical workflow

```bash
# 1. Pull every available season in one go
python -m scripts.algs_api.sync all

# ...or pull a specific season / event / series
python -m scripts.algs_api.sync seasons
python -m scripts.algs_api.sync season --id 01KEAJYDXP9CBK44PPW7XWDNB3
python -m scripts.algs_api.sync event  --id 01KH2GCEGZH7ZYY3FFKW8R4BAF
python -m scripts.algs_api.sync series --id 01KH2HGJB9A69D3G3NW8XN73Q6

# 2. Refresh canonical POI zones for the UI
python -m scripts.algs_api.export_poi_zones --all

# 3. Build POI hints for a specific series and feed them to the tracker
python -m scripts.algs_api.build_poi_hints \
    --series 01KH2HGJB9A69D3G3NW8XN73Q6 \
    --map    storm_point \
    --out    scripts/tracking/modules/track_teams/configs/poi_hints.json
```

## Rate limiting / blocking protection

`client.py` enforces a token-bucket limit by default (`2.0` req/s, burst
`5`), adds 50-250 ms jitter between requests, and retries with
exponential backoff on `429` / `5xx` (respecting `Retry-After` when
present). All successful responses are cached on disk under
`scripts/algs_api/data/cache/` keyed by URL hash so re-runs of the
importer are essentially free until the cache is invalidated.

Override via env vars or CLI flags:

- `ALGS_API_BASE`            (default `https://prod-api.algstools.com`)
- `ALGS_API_RPS`             (default `2.0`)
- `ALGS_API_BURST`           (default `5`)
- `ALGS_API_CACHE_TTL`       seconds, default `86400`
- `ALGS_API_CACHE_DIR`       default `scripts/algs_api/data/cache`
- `ALGS_API_DB`              default `scripts/algs_api/data/algs.sqlite`

## Map id translation

ALGS uses ULIDs for map ids. We map them to the canonical short ids used
everywhere else in the project (`storm_point`, `worlds_edge`, ...).
The mapping lives in `db.MAP_ID_BY_ULID`.

## Available seasons

`GET /v1/seasons` currently returns only **Year 5** (`01JK2JQ40W0DDTZCWDB8WTWCBA`)
and **Year 6** (`01KEAJYDXP9CBK44PPW7XWDNB3`). Older ALGS years are not
exposed by the public API — for those tournaments fall back to the
Liquipedia scraper under `scripts/scrape_liquipedia/`. If the official
endpoint starts returning more season ULIDs later, `sync all` will pick
them up automatically.