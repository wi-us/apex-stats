# Liquipedia scraper (Apex Legends)

Скрейпит индекс турниров с Liquipedia, собирает участников и составы по играм,
кэширует в JSON и заливает в Lovable Cloud.

## Установка

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r scripts\scrape_liquipedia\requirements.txt
python -m playwright install chromium
```

## 1) Скрейп → JSON

```powershell
# A-Tier 2025 (по умолчанию)
python scripts\scrape_liquipedia\scrape.py --out scripts\scrape_liquipedia\data

# Конкретный индекс
python scripts\scrape_liquipedia\scrape.py `
  --out scripts\scrape_liquipedia\data `
  --index-url https://liquipedia.net/apexlegends/S-Tier_Tournaments/2025

# Один турнир (slug из data\index.json)
python scripts\scrape_liquipedia\scrape.py --only als-pro-league-year-5-split-1-playoffs

# Пересобрать заново
python scripts\scrape_liquipedia\scrape.py --force
```

Структура кэша:
```
data/
  index.json                # список всех турниров
  tournaments/<slug>.json   # детали + teams + games + participants
```

## 2) Загрузка в Lovable Cloud — отключено

Работаем только с JSON-кэшем. `upload.py` оставлен в репозитории, но
не используется: для него нужен прямой Postgres-доступ
(`SUPABASE_DB_URL` с паролем), которого нет в UI Lovable Cloud.

Если позже понадобится залить в БД — проще переписать `upload.py` на
Supabase REST API + `SUPABASE_SERVICE_ROLE_KEY` (этот секрет уже есть
в Cloud → Secrets). Таблицы `lp_*` в БД уже созданы миграцией.

## Замечания

- Уважает ToS Liquipedia: UA с контактом, пауза 2.5с между запросами.
  Не запускай много параллельных инстансов.
- Команды и игры берутся из финального battle-royale блока внутри
  `.mw-content-ltr.mw-parser-output`, поэтому в финале должно быть 20 команд.
- Tag берётся из `span.name.visible-xs`; если команда сама называется коротким
  тегом (`TSM`, `CIMJ`, `DGAP`), он также записывается в `tag`.
- Игры берутся из вложенных `.cell--game` строк финальной таблицы, без
  `Overall standings` и без regular-season раундов.
- В каждом объекте `games[i]` появились поля:
  - `date` — ISO-8601 в UTC (из `span.timer-object[data-timestamp]` в
    блоке `panel-content__game-schedule`);
  - `map` — название карты как на странице, плюс нормализованный
    `map_id` (`storm_point` / `worlds_edge` / `e_district` / `broken_moon` /
    `olympus` / `kings_canyon`).
  Если у турнира нет game-schedule блока, эти поля равны `null`.
- POI Drafts (если есть на странице) пишутся как `poi_drafts[stage][map_id] = [...]`
  и флаг `has_poi_drafts: true` в самом объекте турнира. Структура одного
  пика: `{rotation, draft_no, team_slug, team_name, spot}`.
- Флаг `--headed` показывает браузер для отладки.

## 3) POI hints для трекера

Когда у турнира есть `poi_drafts`, а на каноническую карту в админке
(`/admin/poi`) уже нанесены `poi_zones.json`, можно собрать файл подсказок
для `scripts/tracking/modules/track_teams/track_teams.py`:

```powershell
python scripts\scrape_liquipedia\build_poi_hints.py `
  --tournament scripts\scrape_liquipedia\data\tournaments\<slug>.json `
  --zones src\data\maps\storm_point\poi_zones.json `
  --stage finals --map storm_point `
  --out scripts\tracking\modules\track_teams\reports\poi_hints.json
```

Затем — прокинуть в трекер:
```powershell
python ... track_teams.py --poi-hints scripts\tracking\modules\track_teams\reports\poi_hints.json ...
```

Хелпер `poi_prior_weight(slot_or_tag, x_norm, y_norm)` в `track_teams.py`
возвращает множитель для скоров стартового матчера (1.0 внутри круга,
мягкое затухание снаружи). Само интегрирование prior'а в стартовую фазу
делается отдельной итерацией (плумминг готов, поведение пока не меняется).
