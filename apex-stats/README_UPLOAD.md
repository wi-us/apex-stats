VPS upload bundle (minimal, for YouTube ingest only)

Upload these local files/folders to the server folder `~/apex-stats`:

1) `yt-stats/map_vod_ingest.py`
2) `yt-stats/requirements.txt`
3) `yt-stats/youtube_cookies.txt` (your private cookies export)

Create these folders on server (if missing):

- `~/apex-stats/yt-stats`
- `~/apex-stats/output/youtube_ingest/videos`

After upload, run on VPS:

1) Install deps:
   `python3 -m pip install -U pip`
   `python3 -m pip install -r yt-stats/requirements.txt`

2) Run ingest:
   `python3 yt-stats/map_vod_ingest.py --limit-videos 20 --cookies-file yt-stats/youtube_cookies.txt`

Notes:

- Do not upload local `node_modules`, `.next`, `output/tracks`, `segments`, or large raw video folders.
- Keep `youtube_cookies.txt` private.
