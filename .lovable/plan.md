## Цель

Развить `detect_plates` в трекер: добавить sub-frame трекинг, агрегацию по слотам, адаптивный up-sampling, и ускорить весь прогон с >10 мин до 2–3 мин.

## Что делаем

### 1. Sub-frame трекинг (лёгкий) — `tracker_light.py`

Между keyframe (1 fps = каждые 60 кадров) для каждого найденного слота:
- OpenCV `TrackerKCF` (быстрый, нативный C++) — пробуем первым;
- fallback: центроид + Farneback optical flow в окне 60×60 вокруг last_box.

На каждом keyframe — re-init трекеров от свежих детекций. Никакой межслотовой логики, просто протяжка `(x,y,w,h)` между опорными кадрами. Per-slot NMS пропускаем (по словам — fallback хватает).

### 2. `slots/<team_key>.json` агрегатор

Новый модуль на выходе:
```
reports/
  detections.json        # как сейчас, keyframes
  trajectories.json      # все слоты, плотная сетка
  slots/
    NRG.json             # [{t, frame, x, y, w, h, cx, cy, score, source}]
    TSM.json             # source ∈ {detect, recover, track}
    ...
```
`cx, cy` — центр bbox в координатах ROI миникарты.

### 3. Адаптивный up-sampling (пункт 5)

Триггер: если на keyframe слот ушёл в recovery (miss ≥ 1), для следующего интервала между keyframes сэмплируем не `step=60`, а `step=12` (5 fps) только для этого слота через локальный recovery-ROI. Дорого только когда реально что-то теряем.

### 4. Ускорение

Сейчас узкие места (из логов 10+ мин на ~7 мин видео при 1 fps):
- `cv2.VideoCapture.read()` читает **все** 60·N кадров, а используется 1 из 60;
- `tqdm` тикает на каждом кадре → лишний sys-call;
- debug-jpg на каждый keyframe (qty=88) — десятки МБ диска.

Меры:
- **seek через `CAP_PROP_POS_FRAMES`** на keyframe вместо `read()` всех промежуточных (для mp4 с keyint=60 это безопасно; fallback на `grab()`-loop если seek даёт чёрный кадр);
- `--workers N` — пул процессов (`concurrent.futures.ProcessPoolExecutor`), keyframes делятся по чанкам, каждый worker открывает свой `VideoCapture`. RecoveryTracker остаётся последовательным проходом по уже собранным детекциям (cheap), запускается после parallel-стадии;
- debug-jpg по флагу `--save-debug` (по умолчанию off для production-прогона), `--debug-every 10`;
- `tqdm` тикает раз в keyframe, а не на каждый прочитанный кадр.

Ожидание: 10 мин → ~2 мин на 8 ядрах + seek.

### 5. CLI

Расширение `detect_plates.py` и `run.ps1`:
- `--workers N` (default = `os.cpu_count()//2`);
- `--seek` (default on) / `--no-seek`;
- `--save-debug` (default off), `--debug-every K`;
- `--adaptive-fps 5` — up-sample-fps для recovered слотов;
- `--emit-slots` — пишет `slots/*.json` + `trajectories.json`.

### 6. Что НЕ делаем

- per-slot NMS (пункт 1 — отложено по решению пользователя);
- калибровка в canonical (пункт 4 — отложено);
- speed-sanity фильтр (пункт 6 — отложено).

## Файлы

- `scripts/tracking/modules/detect_plates/detect_plates.py` — добавить parallel pipeline, seek, флаги;
- `scripts/tracking/modules/detect_plates/tracker_light.py` — новый, KCF + optical-flow fallback;
- `scripts/tracking/modules/detect_plates/aggregate_slots.py` — новый, пост-процессинг detections.json → trajectories + slots/;
- `scripts/tracking/modules/detect_plates/run.ps1` — новые флаги;
- `scripts/tracking/modules/detect_plates/README.md` — обновить раздел "Что добавили".

## План валидации

1. Базовый прогон с `--no-recovery --no-emit-slots --no-save-debug --workers 8 --seek` → замер времени, сравнение `accepted` с текущим.
2. Полный прогон `--recovery --adaptive-fps 5 --emit-slots` → проверить, что `trajectories.json` плотный (≥10 точек/слот/сек), что recoveries не выросли против baseline.
3. Визуализация одного слота: построить (cx,cy) во времени, ID-switch'и видны глазом.
