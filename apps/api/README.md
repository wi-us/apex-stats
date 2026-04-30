# API Service (NestJS)

## Endpoints

- `GET /health`
- `GET /catalog/tournaments`
- `GET /catalog/tournaments/:tournamentId/matches`
- `GET /catalog/matches/:matchId/maps`
- `GET /catalog/teams`
- `GET /catalog/maps/:mapId/teams`
- `GET /catalog/maps/:mapId/tracks?teamIds=team_1,team_14&fromSec=0&toSec=600`
- `GET /catalog/maps/:mapId/rings?fromSec=0&toSec=600`
- `POST /jobs/ingest` with `{ "faceitMatchId": "..." }`
- `POST /jobs/analysis` with `{ "mapId": "..." }`
- `GET /jobs?jobType=analysis&status=running&page=1&pageSize=20`
- `GET /jobs/:jobId`

## Jobs contract

Job response fields (both list and details):

- `id`
- `jobType` (`ingest` | `analysis`)
- `status` (`queued` | `running` | `completed` | `failed`)
- `command`
- `progressPercent`
- `queuedAt`, `startedAt`, `finishedAt`, `durationMs`
- `mapId`, `matchId`, `video`
- `teamStatuses[]` (`teamId`, `teamName`, `status`, `progressPercent`, `lastFrame`, `lastTimestampSec`, `error`)
- `errors[]`
- `payload`

## Data model mapping

This API maps to SQL schema in `infra/postgres/init.sql`:

- `tournaments`
- `matches`
- `maps`
- `teams`
- `map_team_configs`
- `team_tracks`

## Runtime paths

Filesystem/SQLite paths are resolved from `config/runtime_paths.json` (with safe defaults).
