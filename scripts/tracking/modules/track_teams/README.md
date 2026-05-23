# track_teams — онлайн-трекер команд в мировых координатах

Главный потоковый шаг пайплайна. Принимает VOD, для каждого кадра
регистрирует его в каноническую карту (SIFT+RANSAC), находит плашки
команд по HSV, переводит координаты из пикселей кадра в мировые
координаты карты и ведёт треки через простой Калман + жадное
назначение по цвету. На выходе — `tracks.json` для
`/admin/tracking-lab`.

## Зависимости

- `shared/canonical_maps/<map>.png` + `<map>.json` — карта и калибровка.
- `config.example.yaml` (или свой) — единственный конфиг трекера.
- (опц.) `shared/canonical_maps/<map>.minimap_affine.json` — аффинка
  ROI миникарты → канонические пиксели. Нужна, только если используешь
  `--anchors` от `motion_detect`. См. шаблон в `shared/canonical_maps/`.
- (опц.) `modules/motion_detect/reports/motion_tracks.json` — стартовые
  якоря, подаются через `--anchors`.
- (опционально) `modules/find_cuts/reports/cuts.json` — на следующих
  итерациях, чтобы не трекать через каты камеры.

## Запуск

```powershell
# с push:
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\track_teams\push.ps1 `
  -Video scripts\tracking\game.mp4

# локально:
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\track_teams\run.ps1 `
  -Video scripts\tracking\game.mp4
```

## Параметры

| Флаг       | Дефолт | Что делает |
|---|---|---|
| `-Video`   | — | путь к mp4 (обязательно) |
| `-Config`  | `modules/track_teams/config.example.yaml` | конфиг |
| `-Out`     | `modules/track_teams/reports/tracks.json` | результат |
| `-FrameStep` | 0 (= из config) | шаг между обрабатываемыми кадрами |
| `-Start`   | 0  | начало в секундах |
| `-End`     | -1 | конец в секундах (-1 = до конца) |
| `-FromDetections` | `modules/detect_plates/reports/detections.json` | checkpoints из `detect_plates` (см. ниже) |
| `-NoFromDetections` | — | отключить режим checkpoints и пойти классическим путём |
| `-NoPush`  | — | (только push.ps1) без коммита |

`track_teams.py` дополнительно поддерживает `--anchors <motion_tracks.json>`
— тогда треки инициализируются от консенсус-точек `motion_detect`
(HIGH/MED — сразу alive-трек, LOW — подсказка, MISS — ждём детекцию).
Чтобы это работало, у каждой команды в `config.yaml` должно быть поле
`slot: <N>` (1..20, как в `hsv_presets.json`).

Параметры самого `track_teams.py` (через `config.yaml`):

| Секция | Что регулирует |
|---|---|
| `registration.*` | SIFT/ORB, `max_features`, `ransac_reproj_px`, `min_inliers` |
| `detection.*`    | размеры/морфология HSV-blob'ов команды |
| `tracking.*`     | `max_gap_frames`, `gating_world_dist`, шумы Калмана |
| `teams[]`        | HSV-диапазоны команд (берём из `/admin/hsv`) |
| `canonical_map`  | имя файла в `shared/canonical_maps/` |

## Тюнинг

| Симптом | Что крутить |
|---|---|
| Треки прыгают через всю карту | поднять `tracking.gating_world_dist` ↓ |
| Команда теряется при пропадании плашки | поднять `tracking.max_gap_frames` |
| Регистрация слабая → шумные координаты | см. README `debug_register`; повысить `registration.max_features`, включить `clahe` |
| Две команды путаются | сузить HSV в `/admin/hsv` и перезалить `hsv_presets.<map>.json` |

## Вывод

`reports/tracks.json` — см. `shared/schema/tracks.schema.json` и
`docs/tracking-lab.md` в корне репо. Файл загружается на
`/admin/tracking-lab` (drag-and-drop) для визуализации.

Рядом пишется sidecar `tracks.slots.json` с финальным `wiped_at_t` per slot
(основной файл стримится, поэтому wiped попадают в `frames[*].wipes[]`,
а финальная сводка — в sidecar). Фронт читает оба.

## Метрика качества (ID-switches)

Ручные GT-точки лежат в `assets/gt_anchors.json` (формат
`{t, slot_id, world_xy}`). Прогон:

```powershell
python scripts/tracking/modules/track_teams/eval_id_switches.py `
  --tracks scripts/tracking/modules/track_teams/reports/tracks.json `
  --gt     scripts/tracking/modules/track_teams/assets/gt_anchors.json `
  --out    scripts/tracking/modules/track_teams/reports/eval_id_switches.json
```

На выходе — `eval_id_switches.json/.txt` с coverage, медианной/p95 px-ошибкой
и общим количеством ID-switches per slot. Цель — гнать в ноль на сегментах
между POV-катами.

### Как накидать ~30 GT-точек (быстрый способ)

Сейчас в `assets/gt_anchors.json` лежит всего 5 точек → coverage 0%.
Чтобы метрика заработала, добавь 20–25 ручных опор:

1. Открой `/admin/tracking-lab` и брось туда последний `tracks.json`
   (или `reports/matrix/tracks_baseline.json`).
2. Прокрути таймлайн в **5–6 разных моментов** матча (старт, 1-е кольцо,
   середина, финал и т.д.).
3. Для каждого момента выбери **4–5 разных команд (slot_id)**, которые
   ты уверенно видишь на миникарте.
4. Кликни по позиции команды на канонической карте — внизу UI/в hover
   показывается `canonical_px` точки. Запиши `(t, slot_id, world_xy)`.
5. Добавь запись в `points[]`:

   ```json
   { "t": 240.5, "slot_id": "slot_3", "world_xy": [612, 304] }
   ```

Координаты — в canonical-пикселях (то же пространство, что
`meta.canonical_size` в `tracks.json`, и то же, что отрисовывает
`tracking-lab`). Не нужно идеальной точности — метрика считает
ID-switch как «ближайший трек к GT поменял id». 30 точек — потолок,
что реально полезен.

## Wipe-детект

Параметры в `config.yaml` (секция `tracking.wipe`):

| ключ            | дефолт | что |
|---|---|---|
| `absence_sec`   | 45.0   | сколько секунд без детекции → считаем выбитыми |
| `respect_cuts`  | true   | (TODO) не считать POV-кат за отсутствие |

После `wiped_at_t` трек закрывается окончательно и не реанимируется
ложными HSV-срабатываниями (типичный кейс: тёмная команда + декорация
карты того же оттенка).

## Что НЕ делаем здесь (возможные улучшения)

- Поднять HIGH/MED для тёмных команд (5/9/11/14/16) в `motion_detect`
  — template matching, time-aggregated HSV. Сейчас они идут как LOW
  и стартуют от первой реальной детекции.
- Использовать `cuts.json` в wipe-детекте (`respect_cuts` — заглушка).
- Multi-instance треки на слот (сейчас один трек на слот; для late-game
  с двумя выжившими игроками одной команды этого хватает).

## Режим `--from-detections` (быстрый путь)

Если в `modules/detect_plates/reports/detections.json` уже лежит свежий
прогон `detect_plates` — `track_teams` может полностью пропустить
собственную HSV-детекцию и использовать готовые «checkpoints» с уже
привязанным `team_key` (= slot из `hsv_presets.<map>.json`).

Что делает `track_teams` в этом режиме:

1. Читает `detections.json` один раз, строит индекс `frame_idx → [candidates]`
   в координатах ПОЛНОГО кадра (учитывает `roi`-offset миникарты).
2. На каждом обрабатываемом кадре делает регистрацию (SIFT/ORB — как обычно).
3. Берёт checkpoints в окне `±tolerance` кадров (по умолчанию = sample_step
   из detections.json, т.е. ~1 сек). Дедупликация по слоту: ближайший к
   текущему кадру побеждает.
4. Для каждого checkpoint считает `canonical_px` через текущую `H` и
   напрямую отдаёт в `SlotTracker.accept_observation(...)` своему слоту —
   жадное HSV-сопоставление и ассоциация отключаются (доверяем
   `detect_plates`).
5. Wipe-detection / sidecar / схема `tracks.json` — без изменений.

### Запуск

```powershell
# 1) сначала detect_plates (если ещё не запускали)
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\detect_plates\run.ps1 `
  -Video scripts\tracking\game_sp.mp4 -SampleFps 1 -TrackFps 2 -AdaptiveFps 5

# 2) track_teams в режиме from-detections (по умолчанию включён,
#    если файл существует)
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\track_teams\push.ps1 `
  -Video scripts\tracking\game_sp.mp4
```

Чтобы вернуться к классическому режиму:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\track_teams\push.ps1 `
  -Video scripts\tracking\game_sp.mp4 -NoFromDetections
```

### Ожидаемый эффект

`track_teams` на 20-мин VOD: 8–15 мин → **4–7 мин**. Снимается дубль
HSV-операций; регистрация остаётся узким местом этого модуля.

### Ограничения

- Нужен предварительный прогон `detect_plates` (на тех же `zones.vod.json`
  и `hsv_presets.<map>.json`, иначе slot-маппинг сломается).
- ID-switch resolver выключен: если HSV-детектор спутал близкие цвета —
  ошибка пройдёт в трек. На текущей калибровке `detect_plates` это редкий
  кейс; если станет проблемой — добавим скоростной валидатор.
- Параметр `da_strategy` из `config.yaml` в этом режиме игнорируется.
