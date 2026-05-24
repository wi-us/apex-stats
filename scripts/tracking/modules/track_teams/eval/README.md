# Стартовые координаты команд + live overlay

Два независимых источника стартовых координат на тестовом матче:

1. **ALGS API (план):** POI-пики команд из `algs_api/data/algs.sqlite`
   (таблица `poi_picks`). Это то, куда команда *собиралась* высадиться —
   semantic ground truth, доступный по любой ALGS-серии.
2. **motion_detect (факт):** медиана точек первого стабильного кластера
   по каждому slot из `motion_detect/reports/motion_tracks.json` после
   `--warmup-sec` (по умолчанию 30 c — конец дропа).

## 1. Собрать `start_coords.json`

Нужен `series_id` (ULID) тестового матча. Если ещё не синкал серию:
```powershell
python -m scripts.algs_api.sync series --id <SERIES_ULID>
```

Затем:
```powershell
python -m scripts.tracking.modules.track_teams.eval.build_start_coords `
    --series <SERIES_ULID> --map storm_point `
    --motion scripts/tracking/modules/motion_detect/reports/motion_tracks.json `
    --canonical scripts/tracking/shared/canonical_maps/storm_point.json `
    --out scripts/tracking/modules/track_teams/eval/reports/start_coords.json
```

На выходе per-slot: `{algs:{cx_norm,cy_norm,r_norm}, motion:{cx_norm,cy_norm,n_points}, delta_norm}`.
`delta_norm` — расстояние план↔факт (нормализованное 0..1). Большое значение =
команда сменила drop, либо motion_detect промахнулся.

## 2. Рендер overlay-видео

```powershell
python -m scripts.tracking.modules.track_teams.eval.render_live_overlay `
    --tracks src/data/m-test-g1/tracks.json `
    --start-coords scripts/tracking/modules/track_teams/eval/reports/start_coords.json `
    --map scripts/tracking/shared/canonical_maps/storm_point.png `
    --rings src/data/m-test-g1/ring_geometry_v2.json `
    --eliminations src/data/m-test-g1/eliminations.json `
    --out scripts/tracking/modules/track_teams/eval/reports/overlay.mp4 `
    --fps 10 --step-sec 1.0
```

На видео:
- жёлтые пустые круги — ALGS POI-пики (план)
- белые крестики — motion_detect drop-центры (факт)
- закрашенные кружки с номером — live-позиции 20 слотов из `tracks.json`
  (цвет = HUD VOD палитра, мёртвые скрываются по `eliminations.json`)
- голубой круг — активная кольцевая фаза

Зависимости: `opencv-python`, `numpy`.
