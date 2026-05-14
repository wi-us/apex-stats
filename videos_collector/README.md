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

## Refresh flow (архив + новые матчи)

### 1) Архивировать старые данные и запустить свежий ingest за последний месяц

```bash
python map_vod_ingest.py \
  --db-path ../output/youtube_ingest/tournaments.sqlite \
  --output-dir ../ffmpeg_downloader/records \
  --archive-before-run \
  --recent-days 30 \
  --clip-20m
```

Что делает:
- переносит текущую ingest БД и старые mp4 в `../Archieve/ingest_archive_<timestamp>/`;
- создает новую чистую ingest БД;
- обрабатывает только видео за последние `N` дней (`--recent-days`);
- режет клипы по 20 минут (`--clip-20m`).

### 2) Обновить Liquipedia-каталог турниров/команд/лого (EMEA + APAC North)

```bash
python build_algs_liquipedia_db.py \
  --output-db algs_tournaments.sqlite \
  --tournament-url "https://liquipedia.net/apexlegends/Apex_Legends_Global_Series/2026/Split_1/Pro_League/EMEA" \
  --tournament-url "https://liquipedia.net/apexlegends/Apex_Legends_Global_Series/2026/Split_1/Pro_League/APAC_North"
```

### 3) Запустить детекцию по новым клипам

```bash
python detect_map_start.py --records-dir ../ffmpeg_downloader/records --db-path ../output/map_start_detection.sqlite
```

## Выходы

- `output/youtube_ingest/tournaments.sqlite`
- `output/map_start_detection.sqlite`
- `ffmpeg_downloader/records/*.mp4`

## Fast approximate profile (рекомендуется для batch)

Для быстрой обработки с допуском по таймингам около 5 секунд:

```bash
python detect_map_start.py --records-dir ../ffmpeg_downloader/records --db-path ../output/map_start_detection.sqlite --fast-approx --video-workers 4 --text-zones-file ../output/text_zones/mp_E-District.text-zones.json
```

Что делает `--fast-approx`:
- увеличивает шаги и упрощает refine для старта матча;
- использует coarse/refine для `Rings` и `Eliminated` по шагам в секундах;
- уменьшает OCR-нагрузку на старте (используется только зона `map_zone`).

Новые полезные флаги:
- `--ring-coarse-sec`, `--ring-rollback-sec`, `--ring-refine-window-sec`, `--ring-refine-step-sec`
- `--ring-stable-seconds`, `--ring-geometry-window-seconds`, `--ring-geometry-step-sec`
- `--elim-coarse-sec`, `--elim-refine-sec`, `--elim-refine-step-sec`
- `--start-refine-step-frames`
- `--force-clear-rings` (без него старые кольца не удаляются, если текущий прогон не нашел новых)

## Калибровка и фильтрация Rings

В `detect_map_start.py` добавлена жесткая модель эталонных диаметров (в метрах):
- Ring 1 = `1100`
- Ring 2 = `650`
- Ring 3 = `400`
- Ring 4 = `200`
- Ring 5 = `100`
- Ring 6 = `0.05`

Как это применяется:
- Эталонные диаметры переводятся в map-space радиусы через коэффициент `meters_to_map_units`.
- Коэффициент калибруется по текущему видео от найденного `ring1` (`radius_map / 550m`) и имеет fallback-значение, если `ring1` не удалось надежно измерить.
- При выборе геометрии кандидаты получают штраф за отклонение от ожидаемого радиуса и (для последующих колец) за удаление от ожидаемого центра.
- Перед записью в `Rings` каждый следующий круг проходит строгую проверку:
  - монотонное уменьшение радиуса;
  - полная вложенность в предыдущий круг;
  - допустимое отклонение от эталонного радиуса в map-space.
- Если кольцо не прошло проверку, выполняется ограниченный retry по соседним probe-окнам/приорам; при повторном провале цепочка колец останавливается (битые кольца не пишутся).

## Поведение записи в БД

- Основная запись идет в путь из `--db-path`.
- Дополнительно выполняется mirror-запись в `output/map_start_detection.sqlite`.
- `Rings` больше не затираются пустым результатом (если не передан `--force-clear-rings`).

## Мини-бенчмарк (локально)

Контрольные прогоны (10-мин видео, fast profile):
- `Y6_S1_AMERICAS_D3_G1...`: ~115s, `rings=2`
- `Y6_S1_AMERICAS_D3_G2...` после тюнинга fast refine: ~77s, `rings=2`

Грубая оценка для batch при похожих видео:
- 10 видео: ~13-20 минут
- 88 видео: ~2.0-3.0 часа (без учета фоновой нагрузки системы/диска)
