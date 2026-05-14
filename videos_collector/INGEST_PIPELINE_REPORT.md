# Hard Reset and Unified Ingest Pipeline

## SQLite schema (single source of truth)

```sql
CREATE TABLE videos (
  youtube_video_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  published_at TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  has_map_keyword INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE tournaments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tournament_id TEXT NOT NULL UNIQUE, -- Y{year}_Split_{split}_{tournament_name}
  tournament_name TEXT NOT NULL,
  region TEXT NOT NULL,
  split_number INTEGER NOT NULL,
  day INTEGER NOT NULL,
  year_number INTEGER NOT NULL,
  team_ids_json TEXT NOT NULL DEFAULT '[]',
  source_video_id TEXT NOT NULL UNIQUE REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
  published_at TEXT NOT NULL
);

CREATE TABLE games (
  id TEXT PRIMARY KEY,
  tournament_id TEXT NOT NULL REFERENCES tournaments(tournament_id) ON DELETE CASCADE,
  youtube_video_id TEXT NOT NULL REFERENCES videos(youtube_video_id) ON DELETE CASCADE,
  game_number INTEGER NOT NULL,
  start_sec INTEGER NOT NULL,
  duration_sec INTEGER NOT NULL,
  output_filename TEXT NOT NULL,
  output_path TEXT NOT NULL,
  download_status TEXT NOT NULL, -- planned | downloaded | completed | failed
  error_message TEXT,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_games_video_game ON games (youtube_video_id, game_number);

CREATE TABLE video_marks (
  youtube_video_id TEXT PRIMARY KEY,
  status TEXT NOT NULL, -- viewed | completed
  note TEXT,
  updated_at TEXT NOT NULL
);
```

## Tools and roles

- `yt-dlp`: channel listing, metadata fetch, full source video download.
- `ffmpeg`: local cut of 10-minute clips by `Game N` timestamps.
- `sqlite3`: pipeline state (`videos`, `tournaments`, `games`, `video_marks`).
- `ssh/scp`: VPS -> local transfer and remote cleanup.
- `deno` + `yt-dlp` EJS args: challenge solving for YouTube extraction.

## End-to-end flow

1. List videos from `@algs_vods`.
2. Load metadata and parse `Game` timestamps (+ best-effort region/day/split/tournament/year).
3. Cache non-map videos and viewed/completed states in DB.
4. Download full source video to `ffmpeg_downloader/records/_sources/{video_id}.mp4`.
5. Cut per-game clips into `ffmpeg_downloader/records`.
6. Mark games in DB (`downloaded` on ingest side).
7. Sync to local PC via `vps_records_sync.py`:
   - transfer new files;
   - treat same-size local files as already transferred;
   - delete remote clip files when requested;
   - mark synced games as `completed` in remote DB.
8. Cleanup source files in `_sources` when all clips for that video are transferred and removed from remote `records`.
9. Update `video_marks`:
   - `completed` when all planned games are finished,
   - `viewed` when parsed/checked but not fully completed.

## Implemented changes from plan

- Removed anchor-based auto-marking (`seed viewed until anchor ID`).
- Added explicit hard reset mode in ingest:
  - removes DB file,
  - removes `*.mp4` in output dir (including `_sources`),
  - recreates working folders.
- Kept one-line progress behavior for `yt-dlp` download stage (no forced newline progress).
- Unified ingest/sync status contract by writing `games.download_status='completed'` in remote DB after transfer confirmation.

## Run commands

### Ingest on VPS (normal run)

```bash
python videos_collector/map_vod_ingest.py \
  --channel-url "https://www.youtube.com/@algs_vods/videos" \
  --db-path "output/youtube_ingest/tournaments.sqlite" \
  --output-dir "ffmpeg_downloader/records"
```

### Ingest on VPS with hard reset

```bash
python videos_collector/map_vod_ingest.py \
  --hard-reset \
  --channel-url "https://www.youtube.com/@algs_vods/videos" \
  --db-path "output/youtube_ingest/tournaments.sqlite" \
  --output-dir "ffmpeg_downloader/records"
```

### Continuous sync from VPS to local

```bash
python videos_collector/vps_records_sync.py \
  --host "user@your-vps" \
  --remote-dir "~/www/wi-us.ru/apex-stats/ffmpeg_downloader/records" \
  --remote-db "~/www/wi-us.ru/apex-stats/output/youtube_ingest/tournaments.sqlite" \
  --remote-sources-dir "~/www/wi-us.ru/apex-stats/ffmpeg_downloader/records/_sources" \
  --local-dir "ffmpeg_downloader/records" \
  --watch \
  --delete-remote \
  --cleanup-sources
```

## Post-change acceptance checklist

- No anchor-seed skip behavior remains.
- `tournaments.id` is autoincrement, `day` exists, `tournament_id` format is enforced.
- Download progress from `yt-dlp` is single-line style.
- Remote already-synced files are deleted when `--delete-remote` is enabled.
- `_sources/{video_id}.mp4` gets removed after all related clips are transferred and remote clips are gone.

