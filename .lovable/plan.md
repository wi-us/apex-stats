## Цель

Убрать дубль работы в `track_teams`: HSV-детекция и идентификация slot уже сделаны в `detect_plates`. `track_teams` оставляем как «сшиватель checkpoints в мировые координаты + wipe» — но без своего HSV-прохода. Регистрация (SIFT/ORB) остаётся как сейчас.

Ожидаемый эффект для ~20-мин VOD: `track_teams` 8–15 мин → **4–7 мин** (минус собственная HSV-детекция и связанные с ней операции). Полный пайплайн при последовательном запуске: ~35–55 мин → **~28–40 мин**.

## Что меняется

### 1. `track_teams.py` — новый режим `--from-detections`

Добавить флаг:
```
--from-detections <path>   # путь к detect_plates/reports/detections.json
```

Когда флаг задан:
- **Не открывать видео для HSV-детекции** plate-блобов. Кадры читаются ТОЛЬКО для registration (как сейчас). Это уже даёт основной выигрыш.
- На каждом обрабатываемом кадре вместо вызова собственного HSV-детектора подтягиваются готовые checkpoints из `detections.json` по индексу кадра (или ближайшему предыдущему по `frame`).
- Каждый checkpoint содержит `slot` (`team_key` из HSV-пресета), `roi_xy` (центр bbox). Конвертируем `roi_xy` → координаты полного кадра (прибавляем offset ROI миникарты из `detections.roi`) → world через текущую `H`.
- Доверяем slot из detect_plates: жадное назначение по цвету заменяется прямой привязкой checkpoint → trackslot по `slot_id`. Калман остаётся для сглаживания и заполнения промежутков между checkpoints.
- Wipe-detection (`absence_sec`) считается по отсутствию checkpoints для slot — без изменений в логике.

### 2. Сопоставление кадров

`detect_plates` сэмплирует с `sample_fps` (по умолчанию 1 fps), `track_teams` — со своим `frame_step` из конфига. Нужна синхронизация:
- Загрузить `detections.json` один раз, построить словарь `frame_idx → [boxes]`.
- На каждом кадре `track_teams` брать checkpoints с `frame ≤ current` в окне ±`tolerance` (по умолчанию `sample_step / 2`). Если checkpoint в окне нет — Калман-предикт без коррекции.
- Включить и `tracked` (sub-frame KCF) и `recoveries` из `detections.json` — они тоже валидные точки.

### 3. ROI offset

`detections.json.roi = [rx, ry, rw, rh]` (миникарта в пикселях полного кадра). Координаты в `boxes[*].bbox` — относительно ROI. Перед переводом через `H` (которая в пространстве полного кадра) прибавлять `(rx, ry)`.

### 4. Что НЕ трогаем

- Registration (SIFT/RANSAC) — остаётся как есть; пользователь подтвердил, что зум меняется только на старте каждого Countdown, поэтому отдельный модуль разреженной H пока не нужен (B/C из обсуждения отложены).
- ID-switch resolver — отключаем в этом режиме (доверяем detect_plates).
- Schema `tracks.json` / `tracks.slots.json` — без изменений, фронт `/admin/tracking-lab` продолжает работать без правок.
- `config.yaml` секция `teams[*]` остаётся для маппинга `slot → name/color_hex`, но `hsv_lower/upper` в этом режиме не используются.

### 5. Изменения в `run.ps1`

Добавить параметр `-FromDetections <path>` с дефолтом на `modules/detect_plates/reports/detections.json`, пробросить в `track_teams.py`.

### 6. README

Дописать секцию «`--from-detections` режим»: что это, зачем, ограничения (нужен предварительный прогон `detect_plates`, доверие к slot-привязке HSV-детектора).

## Технические детали

```text
detect_plates/reports/detections.json
        │  frames[*].boxes[*]: { bbox:[x,y,w,h] in ROI, feat.team_key, score, source }
        │  frames[*].tracked[*], frames[*].recoveries[*] — то же по структуре
        │  roi: [rx, ry, rw, rh]
        ▼
track_teams.py (--from-detections):
   for each processed frame f:
       H = register(f)                         # как сейчас
       cps = lookup_checkpoints(f.index)       # из detections.json
       for cp in cps:
           slot = cp.feat.team_key
           xy_full = (rx + cp.cx, ry + cp.cy)
           world_xy = warp(xy_full, H, px_to_world)
           tracks[slot].update(world_xy)       # Калман-correct
       tracks.predict_missing()                 # Калман-predict для остальных
       check_wipes(absence_sec)
   stream → tracks.json + tracks.slots.json
```

Файлы под правку:
- `scripts/tracking/modules/track_teams/track_teams.py` — добавить ветку загрузки detections, отключение собственной HSV-детекции, прямую привязку slot→track.
- `scripts/tracking/modules/track_teams/push.ps1` и `run.ps1` — параметр `-FromDetections`.
- `scripts/tracking/modules/track_teams/README.md` — секция о режиме.

## План проверки

1. Прогнать `detect_plates` (уже сделано, ~13 мин).
2. Прогнать `track_teams --from-detections ...` на тех же 20 мин видео; замерить время.
3. Сверить полученный `tracks.json` с предыдущим (старый режим) на `/admin/tracking-lab`: треки команд должны лежать в тех же местах в пределах естественной погрешности.
4. (Опц.) Прогнать `eval_id_switches.py` с существующим `assets/gt_anchors.json` — coverage не должен упасть.

## Что НЕ входит в эту задачу

- Разреженная регистрация (вариант B) и выделение `register_lite` в отдельный модуль (C).
- Параллелизация пайплайна (это отдельная задача — `run_all.ps1` с группами).
- Ускорение `hud_read` (будущая итерация — основной кандидат после этого).
