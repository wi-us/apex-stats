## Цель

Вернуться к параметрическому свипу вместо точечных правок. Прогнать ~40 вариантов трекинга на первых **5 минутах** видео параллельно (N джоб), оценить каждый по двум критериям:

1. **Идентификация на старте** — старые GT-якоря в окне [0..30s] (есть).
2. **Качество трекинга на 5 мин** — coverage / id-switches / средний gap для каждого слота за всё окно [0..300s] (новое).

Победитель = тот, кто и точку определил, и за 5 минут не сорвался на похожий цвет.

---

## Что меняем (точечно, инфраструктура уже есть)

### 1. `scripts/tracking/modules/track_teams/sweep_initial.py`

- `--end` дефолт `30.0` → `300.0` (5 мин).
- Добавить флаг `--gt-end` (опционально) — отдельная отсечка для GT-оценки старта; по умолчанию `30.5` (как было).
- **AXES** убрать малозначащие оси (`morph_kernel`, `init_min_score`, `min_area_px`-3-точки) и заменить их на те, что мы крутили вручную, плюс новые «анти-цветовые»:
  ```
  motion.gate_cap_px           : [120, 200, 350]
  motion.v_max_px_s            : [40, 60, 90]
  jump_switch_threshold_px     : [40, 80, 150]
  switch_confirm_frames        : [3, 8]
  anchor_lock_sec              : [0, 10, 30]
  near_anchor_radius_canonical_px : [80, 120, 200]
  da_strategy                  : [detect_first, color_first, hybrid]
  da_weights.delta_color_mismatch : [2.0, 5.0, 10.0]
  frame_step                   : [30, 60]
  ```
  Декартово произведение урезается до `--max-variants` (по умолчанию **40**).

- **Новая метрика `evaluate_long_window()`** — поверх существующей. Для каждого слота за всё окно [0..end]:
  - `coverage_pct` — доля кадров, где трек слота присутствует;
  - `id_switches` — сколько раз ближайший «чужой» трек оказывался ближе своего больше чем на K кадров подряд (сигнал «слипания» с похожим цветом);
  - `mean_jump_px` — медиана прыжков своего трека (сигнал «дёргается»).
  - Считаем `confusion_count[slot_i][slot_j]` — сколько раз ближайший трек был с тэгом j вместо i. Это даёт прямой ответ «кто с кем путается».

- **Итоговый sort_key** для победителя:
  ```
  (-start_correct, -avg_coverage, +total_id_switches, +d_med_start)
  ```
  То есть: сначала чтобы старт определялся, потом чтобы трек жил все 5 минут, потом чтобы не путался, потом минимальный сдвиг.

- В `sweep_report.txt` добавить блок:
  ```
  LONG-WINDOW [0..300s] (best 5):
    rank cov%  switches  med_jump  tag
  CONFUSION TOP (winner): пары slot_a ↔ slot_b с >N перепутываниями
  ```

### 2. `scripts/tracking/modules/track_teams/sweep_initial.ps1`

- `End` дефолт `30.0` → `300.0`.
- `MaxVariants` дефолт `150` → `40`.
- `Jobs` дефолт `8` оставить, но добавить подсказку в Write-Host: «5 мин × 40 вариантов / N jobs ≈ ETA».

### 3. Запуск (PowerShell, как любит юзер)

```powershell
powershell -ExecutionPolicy Bypass -File `
  scripts\tracking\modules\track_teams\sweep_initial.ps1 `
  -Video "D:\path\game_sp2.mp4" `
  -End 300 -Jobs 6 -MaxVariants 40
```

Артефакты как и раньше — три файла:
- `reports/sweep_initial/sweep_report.txt`
- `reports/sweep_initial/sweep_report.json`
- `reports/sweep_initial/winner.config.yaml` + `winner_tracks(.slots).json`

---

## Технические детали

- `evaluate_long_window()` читает уже сгенерированный `tracks_*.json` (никаких дополнительных прогонов).
- `confusion` строится по `slot_id`/`team_id` в кадре — без GT, только по соседству на карте.
- Промежуточные tracks-файлы по-прежнему удаляются (но `--keep-intermediate` остаётся для дебага одного варианта).
- Anchors / eliminations не трогаем, motion_tracks нужно один раз пересобрать с `-Window` покрывающим 300 сек — sweep сам напишет WARN если не покроет.

---

## Чего НЕ делаем

- Не правим `track_teams.py` — все оси параметризации уже там есть (мы их и крутили).
- Не возвращаем «жёсткий» `gate_cap_px=160` из последней правки — он станет одним из 3 значений на оси и проиграет/выиграет честно.
- Не меняем UI и фронтенд.
