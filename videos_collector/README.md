# videos_collector

Модуль массового сбора ALGS VOD-данных и подготовки клипов/энричмента
для пайплайна `services/analysis`.

## Что входит

- `map_vod_ingest.py` — ingest метаданных + загрузка/нарезка VOD.
- `detect_map_start.py` — map start + Teams + Rings в SQLite.
- `map_vod_soft_check.py` — проверка и мягкая синхронизация ingest без тяжелых операций.
- `vps_records_sync.py` — синхронизация клипов/источников с VPS.
- `build_algs_liquipedia_db.py` — построение вспомогательного каталога турниров.

## Что удалено из этого модуля

YouTube Analytics/OAuth-часть удалена (analytics collector, OAuth token/client secret и related файлы).

## Базовый запуск

```bash
cd videos_collector
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python map_vod_ingest.py --db-path ../output/youtube_ingest/tournaments.sqlite --output-dir ../ffmpeg_downloader/records
python detect_map_start.py --records-dir ../ffmpeg_downloader/records --db-path ../output/map_start_detection.sqlite
```

## Выходы

- `output/youtube_ingest/tournaments.sqlite`
- `output/map_start_detection.sqlite`
- `ffmpeg_downloader/records/*.mp4`
