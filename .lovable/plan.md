## Цель

1. Стартовые позиции команд в `track_teams` берём из **ALGS POI picks** (точные координаты POI зон + team_tag / name / color из палитры слота), а не из шумного `motion_tracks.json`.
2. Во время `run.ps1` показывать **live окно cv2** с canonical-картой, POI-планом и текущими треками, чтобы видеть проблемы по ходу анализа.

## Источник данных

Используем уже существующий `start_coords.json` (его генерит `build_start_coords.py` из ALGS API + POI zones). Каждый слот содержит:
- `team_tag`, `team_name`
- `poi.id`, `poi.name`
- `algs.cx_norm / cy_norm / r_norm` — точный центр POI зоны в canonical
- `motion.*` (опционально, игнорируем по умолчанию)

Цвет команды берём из палитры HUD VOD по номеру слота (`SLOT_HEX[slot-1]`) — она уже зашита в `render_live_overlay.py` и `src/lib/team-colors.ts`.

## Изменения

### 1. `scripts/tracking/modules/track_teams/track_teams.py`

- Новый CLI флаг `--start-coords <path>`. Имеет приоритет над `--anchors`.
- Новая функция `teams_from_start_coords(path, hsv_preset)` — параллель `teams_from_anchors`:
  - читает `start_coords.json`
  - создаёт `TeamCfg` для каждого `slot_N` с `team_tag`, `team_name`, HSV из preset
- Новая функция `load_start_anchors(path, teams, cmap)`:
  - возвращает `anchors_map` той же формы, что `load_anchors`, но:
    - `conf = "HIGH"` (ALGS — semantic ground truth)
    - `init_canonical_px = (algs.cx_norm * W, algs.cy_norm * H)`
    - `anchor_r_px = algs.r_norm * W`
    - `world = cmap.px_to_world(canonical_px)`
    - НЕ зависит от `minimap_affine` (координаты уже в canonical norm)
- В `main()`: если `args.start_coords` — используем новые функции, иначе текущий путь.
- Новый флаг `--show` (+ опц. `--show-scale 0.5`, `--show-every N`):
  - на каждом обработанном фрейме рисуем canonical-карту, POI-план (жёлтые круги), треки (цветные кружки + подпись slot/tag), HUD с `t`, `alive`, числом ассоциаций
  - `cv2.imshow("track_teams", img)` + `cv2.waitKey(1)`; Esc/Q → graceful exit, дописать tracks.json
  - всё под `try/except` — если нет GUI, печатаем warning и продолжаем без окна

### 2. `scripts/tracking/modules/track_teams/push.ps1`

- Новый параметр `-StartCoords` (дефолт: `scripts\tracking\modules\track_teams\eval\reports\start_coords.json`).
- Если файл существует → передаём `--start-coords` и НЕ передаём `--anchors`.
- Новый switch `-Show` → пробрасывает `--show`.

### 3. `run.ps1`

Сейчас это однострочный прокси (`& push.ps1 @args -NoPush`). Оставляем как есть — `-Show` автоматически прокинется через `@args`.

### 4. README

Коротко добавить раздел "Старт от ALGS POI picks" + флаг `-Show`.

## Использование (для тебя, после изменений)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\track_teams\run.ps1 `
  -Video D:\videos\01J650EJ4F9HMP8PVKK2Z1NQNP.mp4 `
  -StartCoords scripts\tracking\modules\track_teams\eval\reports\start_coords.json `
  -Show
```

Откроется окно cv2 с canonical Storm Point: жёлтые POI планы по слотам с тегами команд (BB, CRT, DINO, …) и закрашенные точки треков, бегущие по карте по мере обработки видео.

## Что НЕ делаем в этом тикете

- Не трогаем `motion_detect` (отдельная задача).
- Не меняем формат `tracks.json`.
- Не меняем `/admin/tracking-lab`.

## Технические детали

- Размер окна cv2: `--show-scale` (по умолчанию 0.5 от canonical 2048 → 1024px), масштабируем `INTER_AREA`.
- Цвет слота: тот же `SLOT_HEX` массив из `render_live_overlay.py` — вынесу в `track_teams/_slot_palette.py` чтобы переиспользовать.
- `--show-every`: рисуем не каждый processed frame, а каждый N-й (default 1) — на slow машинах.
- Чтобы окно не блокировало запись `tracks.json`: рендер делается между шагами основного цикла, `waitKey(1)` non-blocking.
- Headless защита: оборачиваем `cv2.imshow` в `try/except cv2.error`; при первой ошибке выключаем `--show` и продолжаем.
