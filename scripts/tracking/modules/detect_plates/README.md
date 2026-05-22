# detect_plates

OpenCV-детектор плашек команд на миникарте (порт build_dataset_opencv.py)
+ temporal recovery: "стоять на месте и расширять радиус поиска".

## Что взяли из исходника

- detect_colored_plates_opencv — основная логика (strict seed -> loose expand
  -> refine to label band -> expand_to_mask_segment с локальным окном).
- plate_quality_reject_reason — фильтр против ложных bbox по карте.
- nms_boxes_by_slot + remove_cross_team_duplicates — NMS внутри слота, мягкий
  кросс-командный дедуп.

## Что добавили: RecoveryTracker

Per-slot память между кадрами:

- нашли -> last_box, miss=0, level=0;
- не нашли -> miss++, level++ (cap = --rec-max-level, default 4);
- на следующих кадрах повторный проход только для этого слота:
    - HSV-допуски расширяются: h_tol += rec_h_step*level, аналогично S/V;
    - поиск ограничен квадратом вокруг last_box, радиус
      radius_base + radius_step*level, cap radius_cap;
- после --rec-max-lost-frames подряд промахов слот выпадает из recovery.

Так мы не теряем слот из-за единичного шума/перекрытия HUD и не уходим
в гипер-широкую маску по всему кадру.

## Что добавили: sub-frame трекинг + slots-агрегатор + seek

- **tracker_light.MultiSlotTracker** — между keyframe протягивает каждый
  слот KCF-трекером (fallback: Farneback OF в локальном окне). На каждом
  следующем keyframe трекеры re-init'ятся от свежих детекций. Включается
  флагом `--track-fps N` (рекомендуется 5).
- **--adaptive-fps N** — для слотов, которых нет на keyframe (recovery),
  поднимается частота сэмплирования промежутка до N fps. Дёшево, т.к.
  расходуется только когда что-то реально потеряно.
- **--emit-slots** — после прогона пишет `reports/slots/<team>.json` и
  `reports/trajectories.json` (см. `aggregate_slots.py`).
- **--seek** (default on) — `cv2.CAP_PROP_POS_FRAMES` вместо чтения всех
  60·N кадров; на нашем 7-мин VOD это даёт ×5–×8 ускорения. Если seek
  отдаёт пустой кадр — автоматический fallback на `grab()`-loop.
- **--save-debug** (default **off**) и **--debug-every K** — debug-jpg
  только при явном включении (раньше писалось всегда, десятки МБ).

Типичный полный прогон под наш кейс:

  powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\detect_plates\run.ps1 \
    -Video scripts\tracking\game_sp.mp4 -SampleFps 1 -TrackFps 5 -AdaptiveFps 5

Без sub-frame трекинга и без slots (только детекция, максимально быстро):

  powershell ... run.ps1 -Video ... -NoSlots -TrackFps 0 -AdaptiveFps 0

## ROI

ROI миникарты берётся из scripts/tracking/configs/zones.vod.json
(зона tag=minimap). Координаты в базе 1920x1080, масштабируются под реальный
размер кадра автоматически.

## Запуск

  powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\detect_plates\run.ps1 \
    -Video scripts\tracking\game_sp.mp4 -SampleFps 1

Без recovery (как "голый" порт):

  powershell ... run.ps1 -Video ... -NoRecovery

## Отчёты

- reports/debug/f*.jpg — overlay с accepted/rejected (как в исходнике).
- reports/detections.json — на каждый кадр: accepted, rejected, recoveries
  (какие слоты восстановили и на каком level), by_slot.

## Куда это встраивается в наш пайплайн

Этот модуль — альтернатива detect_teams.py для детекции по плашкам
(а не по цветным точкам стрелок). Его выходы можно:

1. использовать как dataset для будущего YOLO-классификатора (план Б);
2. конвертировать в anchors для track_teams (bbox -> centroid ->
   canonical_px). Это снимает нагрузку с motion_detect на ранней игре,
   когда плашки видны почти всегда.

## Идея standing+expand в track_teams

Та же логика применима к существующему track_teams.py:

- roi_expand_px уже работает пространственно;
- но HSV-границы статичны. По аналогии с RecoveryTracker можно на пропусках
  слота временно расширять HSV в da_weights / delta_color на level*{1,8,10}
  и сбрасывать при reacquire. Это даст устойчивость к компрессии и теням
  без жертв точности на здоровых слотах.
