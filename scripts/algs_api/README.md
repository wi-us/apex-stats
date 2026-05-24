
## Push to Lovable Cloud (Supabase)

После локальной синхронизации SQLite можно одной командой залить весь
кэш в Lovable Cloud:

```bash
pip install -r scripts/algs_api/requirements.txt
python -m scripts.algs_api.push_supabase
# или выборочно:
python -m scripts.algs_api.push_supabase --only series matches series_poi_stats
```

Скрипт делает batched `upsert(on_conflict=<pk>)` в таблицы `public.algs_*`
(те же имена, что в SQLite, но с префиксом `algs_`). Безопасно гонять
повторно — обновятся только изменившиеся строки.

### Получение `SUPABASE_SERVICE_ROLE_KEY`

`SUPABASE_URL` уже лежит в `.env`. А вот сервисный ключ — приватный
(он обходит RLS и должен быть только у тебя, никогда не коммить и не
класть в код фронта).

1. Открой проект в Lovable → кнопка **Cloud** (иконка облака в правом
   верхнем углу редактора) → **Backend** → ссылка **Open Supabase
   project**. Это откроет admin-панель этого проекта.
2. В Supabase: **Project Settings → API → Project API keys**.
3. Скопируй значение `service_role` (`secret`). Это длинный JWT.
4. Положи его в локальный `.env` рядом с уже существующим
   `SUPABASE_URL`:

   ```
   SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...
   ```

   `python-dotenv` подцепит его автоматически при запуске скрипта.
   Альтернатива — экспорт в shell:

   ```powershell
   $env:SUPABASE_SERVICE_ROLE_KEY="eyJhbGciOi..."
   ```

5. Проверь, что `.env` уже в `.gitignore` (он там стандартно).

> Service role обходит RLS — используй его только в этом скрипте,
> запускаемом локально/из CI. На фронте всегда `VITE_SUPABASE_PUBLISHABLE_KEY`.

### Рекомендуемое расписание

| Команда | Частота |
| --- | --- |
| `sync refresh` (live + upcoming) | каждые 2–5 мин |
| `sync upcoming` | 10 мин |
| `sync standings --season <id>` | 6 ч |
| `sync all --skip-existing` | 1 раз в сутки |
| `push_supabase` | после каждого успешного `sync` |
# ALGS API integration

Primary data source for ALGS tournaments. Liquipedia is used only as a
fallback for non-ALGS events.

## Components

- `client.py` — throttled HTTP client (token bucket + jittered backoff +
  on-disk JSON cache). All public read endpoints we use are wrapped here.
- `db.py` — SQLite schema + upserts. Tables mirror the API shape so a
  later migration to Postgres is mechanical.
- `sync.py` — CLI: walks the API and upserts everything into SQLite.
- `export_poi_zones.py` — writes `src/data/maps/<id>/poi_zones.json`.
- `build_poi_hints.py` — emits hints JSON for `track_teams.py`.

## SQLite location

Default DB path: `scripts/algs_api/data/algs.sqlite` (gitignored).

## Tables (new)

In addition to the existing season/event/series/match/POI tables:

- `series_weapon_stats` — kills per weapon (with ammo & gun type)
- `series_character_stats` — kills + total damage per legend
- `series_character_compositions` — popular legend trios
- `series_player_agg` — per-player kills, **assists**, averages
- `series_banned_legends_agg` — series-wide bans
- `series_poi_stats` — per-POI avg pick / damage / survival / placement
- `event_teams`, `event_standings`, `event_schedule`
- `phase_teams`, `phase_standings`
- `season_standings_teams`, `season_standings_players`
- `cc_leaderboard_teams`, `cc_leaderboard_players` — Challenger Circuit
- `live_streams` — Twitch/etc streams per live series
- `sync_state` — `(kind, ident) -> (fetched_at, status)`, used as the
  TTL gate to skip recently-synced resources.
- `matches.winner_damage`, `matches.winner_kills`, `matches.winner_team_id`
- `match_player_stats.character_id`

## Sub-commands

```bash
# Reference + full walk (heavy)
python -m scripts.algs_api.sync reference
python -m scripts.algs_api.sync all                # every season
python -m scripts.algs_api.sync all --skip-existing

# Targeted
python -m scripts.algs_api.sync season    --id <SEASON_ULID>
python -m scripts.algs_api.sync event     --id <EVENT_ULID>
python -m scripts.algs_api.sync series    --id <SERIES_ULID>

# Standings & leaderboards
python -m scripts.algs_api.sync standings --season <SEASON_ULID>
python -m scripts.algs_api.sync cc        --season <SEASON_ULID> --event <EVENT_ULID>

# Live state (cheap, safe to call often)
python -m scripts.algs_api.sync upcoming
python -m scripts.algs_api.sync live
python -m scripts.algs_api.sync refresh            # upcoming+live+live-series

# Bypass freshness gates
python -m scripts.algs_api.sync --force series --id ...
```

## Update cadence (how to avoid getting blocked)

Three layers protect us:

1. **Token-bucket throttle** in `client.py` — default 2 req/s, burst 5,
   with 50–250 ms jitter and exponential backoff on 429/5xx.
2. **On-disk HTTP cache** under `scripts/algs_api/data/cache/` — every
   successful response is keyed by URL; default TTL 24 h.
3. **`sync_state` freshness gate** in `sync.py` — skips resources that
   were already synced inside their freshness window.

Recommended schedule:

| Job | Command | Frequency |
|---|---|---|
| Live tickers | `sync refresh` | every **2–5 min** during play windows |
| Upcoming list | `sync upcoming` | every **10 min** |
| Standings refresh | `sync standings --season <year>` | every **6 h** |
| Full season walk | `sync all --skip-existing` | **daily** (off-peak) |
| Reference data | `sync reference` | **weekly** |

Built-in TTLs (override with `--force`):

- live streams: 2 min · upcoming: 10 min · standings/CC: 6 h
- team detail: 7 d · completed series: 30 d (effectively "once")
- in-progress series: re-fetched at most every 5 min

404 / 400 responses are cached so we never re-hammer endpoints that
don't exist for a given id. Series listed in an event's structure but
without published matches yet are logged as `skip ... (404)` and the
walk continues.

## What the API does NOT expose

- Per-kill feed (killer → victim, weapon, distance) — does not exist.
- Per-player damage at match level — only `winner.damage` per match
  (team total) and `damage` per legend over a series.
- Older ALGS years (Year 1–4) — only Year 5 and Year 6 are returned by
  `GET /v1/seasons`. For older tournaments, use the Liquipedia scraper.

## Map id translation

ALGS uses ULIDs for map ids. Mapping to our canonical short ids
(`storm_point`, `worlds_edge`, …) lives in `db.MAP_ID_BY_ULID`.

## Env vars

- `ALGS_API_BASE` (default `https://prod-api.algstools.com`)
- `ALGS_API_RPS` (default `2.0`), `ALGS_API_BURST` (default `5`)
- `ALGS_API_CACHE_TTL` (seconds, default `86400`)
- `ALGS_API_CACHE_DIR`, `ALGS_API_DB`
