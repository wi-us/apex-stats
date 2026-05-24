## 1. Скрейпер: расписание игр (date + map)

В `scripts/scrape_liquipedia/scrape.py` достроить парсинг блока `div.panel-content.panel-content__game-schedule` рядом с финальной battle-royale таблицей.

- Селектор строк: `div.panel-content__game-schedule > div.panel-content__game-schedule__container` (или эквивалент по разметке Liquipedia: «Game N | Map | DD MMM YYYY — HH:MM»).
- Поля на игру:
  - `date` — ISO-8601 в UTC. Liquipedia рендерит время в `<span class="timer-object" data-timestamp="...">`; берём `data-timestamp` (unix sec) и форматируем.
  - `map` — имя карты (`Storm Point`, `World's Edge`, `E-District`), + нормализованный `map_id` (`storm_point` / `worlds_edge` / `e_district`) по уже существующим ключам из `scripts/tracking/shared/canonical_maps/`.
- В `extract_teams_and_games` результат сливается с `games[i]`: каждая игра получает `date` и `map` (и `map_id`). Если расписания нет — поля `null`, остальное не ломаем.
- README: дописать строчку про новые поля.

## 2. Скрейпер: POI Drafts → JSON (приоритет)

Новый модуль внутри того же `scrape.py` (и опция CLI `--poi-only` для повторного прогона без перетаскивания всего турнира).

- Детект вкладки: на странице турнира искать заголовок `POI Drafts` (`<h2><span id="POI_Drafts">`). Если нет — пропускаем без ошибки.
- Внутри блока:
  - Вкладки `Regular Season` / `Finals` — по `ul.tabs-static` рядом с заголовком.
  - Под-вкладки по картам (`Storm Point`, `World's Edge`, `E-District`).
  - Для каждой комбинации (stage × map) парсить таблицу справа: `Rotation`, `Draft #`, `Team` (имя + ссылка на команду → slug), `Spot Picked`.
- Сохранять рядом с турниром:
  ```
  scripts/scrape_liquipedia/data/tournaments/<slug>.json
    ...
    "poi_drafts": {
      "finals":  { "storm_point": [ {rotation, draft_no, team_slug, team_name, spot}... ], ... },
      "regular": { ... }
    }
  ```
- В `index.json` помечать `has_poi_drafts: true|false` для быстрой фильтрации.
- Координаты подписей на картинке карты в этом этапе НЕ берём — они для каждого турнира свои и почти бесполезны вне контекста. Геометрию задаём сами в админке (шаг 3).

## 3. Канонические POI-зоны на карте (frontend, JSON)

Цель: единый каталог точек интереса на каждой канонической мини-карте + круглая «зона поиска» вокруг каждой.

- Хранилище (JSON в репо, БД позже):
  ```
  src/data/maps/<map_id>/poi_zones.json
  ```
  Схема одного POI:
  ```json
  { "id": "storm-catcher", "name": "Storm Catcher",
    "aliases": ["StormCatcher"], "cx": 0.62, "cy": 0.41, "r": 0.035 }
  ```
  `cx/cy/r` — нормализованные координаты относительно canonical-карты (0..1), как в `shared/canonical_maps/`.
- Алиасы нужны, чтобы матчить разнобой названий из таблиц Liquipedia (`The Wall` vs `Wall`, `Cenote Cave` vs `Cenote` и т.п.).

## 4. Админ-инструмент: разметка POI зон

Новый роут `src/routes/admin.poi.tsx` (доступ через существующий `_authenticated`/admin guard, по образцу `admin.zones.tsx`).

UI:
- Селект карты (storm_point / worlds_edge / e_district) → загружает соответствующую canonical минимапу + текущий `poi_zones.json`.
- Канва (SVG поверх PNG карты):
  - клик = создать POI (диалог: name, aliases),
  - drag центра = переместить, drag по краю = менять `r`,
  - правый клик / кнопка Delete = удалить.
- Кнопка `Export JSON` — скачивает обновлённый `poi_zones.json`. Сохранение в файл репо делает разработчик (как в существующих ring/zones admin-страницах). Если позже захотим автосохранение, добавим серверную функцию.
- Боковая таблица: импорт списка POI из выбранного турнира (`poi_drafts`) — кнопкой «Добавить недостающие POI как новые зоны» (создаёт zone-заглушки в центре карты для всех spot’ов, которых ещё нет, чтобы быстро доразметить).

## 5. Использование POI в детекте стартовых позиций

`scripts/tracking/modules/track_teams/track_teams.py` (стартовая фаза, та что использует `assets/gt_anchors.json`).

Новая опциональная подсказка `--poi-hints <path>`:

1. На вход — конкретный турнир + карта; скрипт берёт `poi_zones.json` + соответствующий `poi_drafts[stage][map]` из JSON-кэша скрейпера.
2. Для каждой команды (по team tag → liquipedia team slug через справочник) находит её spot, по spot → POI zone (центр + r).
3. Если zone найдена — приоритетно искать стартовое плашко-движение команды в круге `(cx,cy,r)`; вне круга — штраф.
4. Если нет POI-таблицы для турнира — fallback на текущую логику без изменений.

Конкретно: добавить в существующий стартовый матчер «prior»-bias по слотам команд, который умножает score-карту на маску круга. Никаких других веток алгоритма не трогаем.

## 6. Технические детали

- Парсинг расписания и POI: только в существующем `scrape.py`, без новых зависимостей. Liquipedia таймстампы — `span.timer-object[data-timestamp]`.
- Team-resolution: для POI Drafts матчим `<a href="/apexlegends/<TeamSlug>">` → тот же slug, что в `teams[]` турнира; имя команды берём из таблицы, fallback из ссылки.
- Канонические map-id: `Storm Point→storm_point`, `World's Edge→worlds_edge`, `E-District→e_district`, `Broken Moon→broken_moon`, `Olympus→olympus`, `Kings Canyon→kings_canyon`. Хелпер `normalize_map(name)` положить в `scrape.py`.
- Никаких миграций БД в этом плане. Таблицы `lp_*` остаются; будущая миграция БД — отдельной задачей.
- Затрагиваем:
  - `scripts/scrape_liquipedia/scrape.py` (game schedule + POI Drafts)
  - `scripts/scrape_liquipedia/README.md`
  - `src/data/maps/{storm_point,worlds_edge,e_district}/poi_zones.json` (пустые стартовые)
  - `src/routes/admin.poi.tsx` (новый)
  - `src/lib/poi-zones.ts` (типы + io)
  - `scripts/tracking/modules/track_teams/track_teams.py` (поддержка `--poi-hints`)

## 7. Порядок реализации

1. Расписание игр (`date`, `map`) + рескрейп.
2. POI Drafts парсер + рескрейп + `has_poi_drafts` в индексе.
3. Заглушки `poi_zones.json` + `admin.poi.tsx` редактор.
4. `--poi-hints` в `track_teams.py` + точечный e2e на одном турнире.
