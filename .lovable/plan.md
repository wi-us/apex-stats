
## Идея

Сейчас `motion_detect` уже даёт `consensus_xy` для каждой команды (HIGH/MED/LOW) — это и есть «точные стартовые координаты». Но в `track_teams` они используются мягко: радиус `near_anchor_radius_canonical_px = 120` и общий гейт `fallback_gate_canonical_px = 250`. На стартовых кадрах это позволяет трекеру «перепрыгивать» на чужую плашку того же цвета.

Предлагаю превратить стартовые координаты в **жёсткие якоря с прогрессивным радиусом**: первые N секунд матча команда ищется только в маленьком круге вокруг своей стартовой точки, дальше радиус расширяется по мере роста уверенности трека.

## Что делаем

### 1. Зафиксировать стартовые точки

Новый файл `modules/motion_detect/reports/start_anchors.json`:

```json
{
  "frame_t": 67.2,
  "anchors": {
    "slot_1":  { "x": 812.3, "y": 1402.1, "conf": "HIGH", "r0": 25 },
    "slot_5":  { "x": 1140.0, "y": 980.5, "conf": "MED",  "r0": 40 },
    "slot_11": { "x": 612.7, "y": 1750.0, "conf": "LOW",  "r0": 70 },
    ...
  }
}
```

`r0` зависит от `conf`:
- HIGH → 25 px (≈ полтора размера плашки)
- MED  → 40 px
- LOW  → 70 px (всё ещё в 1.7× меньше текущего 120)

Источник — уже существующие `motion_tracks.json` + `consensus_xy`. Просто отдельный экспорт «последнего кадра окна» как точка старта, а не центроид всего окна.

### 2. Прогрессивный радиус в `track_teams`

В `slot_tracker` добавляем новую секцию:

```yaml
slot_tracker:
  start_anchors_file: ../../motion_detect/reports/start_anchors.json
  anchor_lock_sec: 15.0          # держим жёсткий радиус первые 15 сек
  anchor_grow_sec: 30.0          # расширяемся линейно до anchor_lock_sec+grow_sec
  anchor_r_max: 120.0            # потолок = текущий near_anchor_radius
```

Логика на каждом кадре `t`:
```
dt = t - anchor_t0
if dt < anchor_lock_sec:           r = r0
elif dt < anchor_lock_sec+grow_sec: r = lerp(r0, r_max, (dt-lock)/grow)
else:                               r = r_max  (= обычное поведение)
```

Кандидаты-плашки вне `(anchor.xy, r)` для этого slot'а **исключаются** на стадии ассоциации (DA), а не только штрафуются. Это и есть «захват не прыгает».

### 3. Защита от LOW-якорей

Если `conf=LOW` И за `anchor_lock_sec` внутри радиуса не нашли ни одной детекции → переходим к старому поведению (`r = r_max`) и помечаем slot `anchor_lost: true` в отчёте. Это исключает зависание на ложной точке.

### 4. Совместимость

- Новый `da_weights` блок не трогаем — все веса остаются.
- Если `start_anchors_file` не задан → ведём себя как сейчас (никаких регрессий для baseline-конфигов).
- Добавляем sweep-вариант `da.start_anchored.yaml` для сравнения с `winner.config.yaml`.

## Технические детали

**Файлы под изменение** (только tracking, бизнес-логика трекера — не UI):
- `scripts/tracking/modules/motion_detect/motion_detect.py` — добавить экспорт `start_anchors.json` (последний устойчивый кадр окна + r0 по conf).
- `scripts/tracking/modules/track_teams/track_teams.py` (или эквивалент в текущей структуре) — загрузка `start_anchors_file`, прогрессивный радиус, исключение вне-радиуса кандидатов.
- `scripts/tracking/modules/track_teams/configs/da.start_anchored.yaml` — новый конфиг для A/B.
- README `motion_detect` и `track_teams` — описать `start_anchors.json` и параметры.

**Параметры по умолчанию** подобраны под 60fps / canonical 2048×2048 (storm_point); потом тюнятся sweep'ом.

**Метрика успеха**: на `m-test-g1` g1 — снизить число slot-swap'ов на первых 30 секундах (видно в overlay'ах `track_teams`) и поднять долю кадров с `agreed_with_anchor=true` в первые 15 сек до ≥ 95% для HIGH/MED.

## Что НЕ делаем в этом шаге

- Не трогаем OCR/PaddleOCR — это отдельная ветка (whitelist по `event_id`, она уже в работе).
- Не меняем структуру `tracks.slots.json` — поле `wiped_at_t` и slot-id остаются как есть.
- Не переписываем `motion_detect` целиком — только добавляем экспорт.
