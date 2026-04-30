# yt-stats

Минимальный сборщик YouTube Analytics в SQLite.

## Что собирает

- `top_videos` (с `videoTitle`, `videoType=SHORTS/FULL`, `durationSeconds`)
- `daily_overview`
- `traffic_sources`
- `geo_countries`
- `devices`
- `operating_systems`
- `platforms` (`youtubeProduct`)
- `player_types` (`insightPlaybackLocationType`)
- `demographics` (`ageGroup + gender`)

## Настройка

1. В Google Cloud включите:
   - YouTube Analytics API
   - YouTube Data API v3
2. Создайте OAuth Client ID типа `Desktop app`.
3. Сохраните JSON как `client_secret.json`.

## Запуск

```bash
cd yt-stats
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py --start-date 2026-03-01 --end-date 2026-04-01
python main.py --all-time
```

## Результат

- SQLite файл: `yt_stats.sqlite` (или путь из `YT_DB_PATH`)
- Таблица метаданных: `runs`
- Таблицы отчетов: `report_<report_name>`

Подробнее для интеграции: `INTEGRATION_BRIEF.md`.
