# APEX STATS — Карта сайта

Документ для проектирования. Сгруппированы все маршруты приложения с кратким описанием назначения каждой страницы и относительной ссылкой.

---

## 1. Публичная часть (для зрителей / аналитиков)

| Ссылка | Страница | Назначение |
|---|---|---|
| [`/`](/) | Главная | Лендинг / точка входа в приложение, навигация по основным разделам. |
| [`/login`](/login) | Вход | Аутентификация пользователя (email + Google). |
| [`/accept-invite`](/accept-invite) | Приём приглашения | Активация инвайта оператора/администратора по ссылке из письма. |
| [`/tournaments`](/tournaments) | Турниры | Список всех турниров (серии, события ALGS и т.п.). |
| [`/matches`](/matches) | Матчи | Список матчей по всем турнирам. |
| [`/matches/$matchId`](/matches/match-id) | Карточка матча | Детали конкретного матча: состав игр, команды, результаты. |
| [`/games`](/games) | Игры (раунды) | Список отдельных игр (game = один раунд внутри матча). |
| [`/games/$gameId`](/games/game-id) | Просмотр игры | Главный экран зрителя: карта, кольца, траектории команд, тайминг событий, элиминации. |
| [`/maps`](/maps) | Карты | Каталог карт пула (Storm Point, Worlds Edge и т.д.). |
| [`/maps/$mapId`](/maps/map-id) | Детали карты | Канонический скриншот карты, POI-зоны, метаданные. |
| [`/teams`](/teams) | Команды | Список всех команд с логотипами и цветами. |
| [`/teams/$teamId`](/teams/team-id) | Карточка команды | Состав, статистика, история выступлений команды. |
| [`/presentation`](/presentation) | Презентация v1 | Слайды для разборов матчей / отчётов (legacy редактор). |
| [`/presentation-2`](/presentation-2) | Презентация v2 | Новый редактор слайдов на свободном canvas. |

---

## 2. Админ-консоль (`/admin`, требует роль operator+)

Лейаут с левым сайдбаром, в нём 4 группы. Заголовок группы — относительная ссылка не имеет; ниже перечислены сами страницы.

### 2.1 Dashboard

| Ссылка | Страница | Назначение |
|---|---|---|
| [`/admin`](/admin) | Dashboard | Общий обзор админки, точка входа после логина оператора. |
| [`/admin/users`](/admin/users) | Users (только administrator) | Управление аккаунтами и ролями, рассылка инвайтов. |

### 2.2 Data — основные сущности

| Ссылка | Страница | Назначение |
|---|---|---|
| [`/admin/tournaments`](/admin/tournaments) | Tournaments | CRUD по турнирам / сериям событий. |
| [`/admin/matches`](/admin/matches) | Matches | Список матчей внутри турниров, CRUD. |
| [`/admin/matches/$matchId`](/admin/matches/match-id) | Match detail | Детальная карточка матча в админке: игры, источники VOD, команды. |
| [`/admin/maps`](/admin/maps) | Maps | Управление пулом карт. |
| [`/admin/maps/$mapId`](/admin/maps/map-id) | Map detail | Редактирование одной карты (изображение, POI, калибровка). |
| [`/admin/teams`](/admin/teams) | Teams | Список команд, цвета, ростер. |
| [`/admin/teams/$teamId`](/admin/teams/team-id) | Team detail | Детальная карточка команды: игроки, история, теги. |

### 2.3 Calibration — настройка распознавания

| Ссылка | Страница | Назначение |
|---|---|---|
| [`/admin/hsv`](/admin/hsv) | HSV | Калибровка цветовых HSV-пресетов команд (для трекинга по плашкам). |
| [`/admin/zones`](/admin/zones) | HUD Zones | Разметка зон HUD на кадре 1920×1080 (мини-карта, индикаторы, и т.д.). |
| [`/admin/polygons`](/admin/polygons) | Map Polygons | Полигоны на карте: запрещённые/безопасные/служебные зоны. |
| [`/admin/camera`](/admin/camera) | Camera Tracking | Инструмент оператора для трекинга камеры обсервера (Overview / Graphs / Settings / Debug). |
| [`/admin/poi`](/admin/poi) | POI Zones | Точки интереса (drop spots) на канонических картах. |

### 2.4 Analysis — обработка данных

| Ссылка | Страница | Назначение |
|---|---|---|
| [`/admin/processes`](/admin/processes) | Processes | Очередь и статус процессов анализа/трекинга VOD. |
| [`/admin/minimap`](/admin/minimap) | Minimap Locator | Поиск/калибровка мини-карты в кадре наблюдателя. |
| [`/admin/tracking-lab`](/admin/tracking-lab) | Tracking Lab | Просмотр результатов трекинга команд из `tracks.json` поверх канонической карты + видео. |
| [`/admin/dataset`](/admin/dataset) | Dataset Builder | Сборка YOLO-датасета из ZIP-архива размеченных кадров. |

### 2.5 System — служебное

| Ссылка | Страница | Назначение |
|---|---|---|
| [`/admin/schema`](/admin/schema) | Database Schema | Редактор/просмотр схемы БД. |
| [`/admin/diagrams`](/admin/diagrams) | Diagrams | Блок-схемы и flowcharts для отчётов и документации. |

---

## 3. Карта навигации (дерево)

```text
/
├── login
├── accept-invite
├── tournaments
├── matches
│   └── :matchId
├── games
│   └── :gameId
├── maps
│   └── :mapId
├── teams
│   └── :teamId
├── presentation
├── presentation-2
└── admin
    ├── (index — Dashboard)
    ├── users
    ├── Data
    │   ├── tournaments
    │   ├── matches/:matchId
    │   ├── maps/:mapId
    │   └── teams/:teamId
    ├── Calibration
    │   ├── hsv
    │   ├── zones
    │   ├── polygons
    │   ├── camera
    │   └── poi
    ├── Analysis
    │   ├── processes
    │   ├── minimap
    │   ├── tracking-lab
    │   └── dataset
    └── System
        ├── schema
        └── diagrams
```

---

## 4. Условные обозначения

- `$paramName` / `:paramName` — динамический сегмент URL (id сущности).
- Все админ-маршруты обёрнуты в `RouteGuard min="operator"`; страница Users требует роль `administrator`.
- Маршруты построены на TanStack Router (file-based routing), исходники в `src/routes/`.
