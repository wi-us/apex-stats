# Ingest Service

Node.js worker for FACEIT match ingest pipeline.

## Responsibilities

1. Authenticate against FACEIT API.
2. Fetch match metadata (match id, map, teams, VOD source).
3. Download VOD.
4. Detect work fragment boundaries.
5. Detect first two ring timestamps.
6. Return normalized ingest payload for API/database.

## Queue

- Queue name: `ingest-jobs`
- Payload: `{ faceitMatchId: string }`

## Run

```bash
npm install
npm run dev -w @apex/ingest-faceit
```

## Environment

- `FACEIT_API_KEY`
- `FACEIT_API_URL` (default `https://open.faceit.com/data/v4`)
- `REDIS_URL`
