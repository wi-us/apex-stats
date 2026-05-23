# debug_register — sanity-check регистрации кадра в каноническую карту

Берёт N равномерно распределённых кадров VOD, прогоняет регистрацию
кадра → каноническая карта (та же логика, что и в `track_teams`),
и выгружает картинки + текстовый отчёт. Нужен на этапе калибровки
канонической карты или новой `config.yaml`: если здесь регистрация
плохая — все последующие модули будут шуметь.

## Зависимости

- `shared/canonical_maps/<map>.png` + `<map>.json` — каноническая карта.
- `modules/track_teams/config.example.yaml` — общий конфиг
  (берёт оттуда `canonical_map`, `registration.*`, `roi`).

## Запуск

```powershell
# с push в GitHub (для моего аккаунта, чтобы Lovable-агент увидел вывод):
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\debug_register\push.ps1 `
  -Video scripts\tracking\game.mp4

# локально, без push:
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\debug_register\run.ps1 `
  -Video scripts\tracking\game.mp4
```

## Параметры

| Флаг | Дефолт | Что делает |
|---|---|---|
| `-Video`  | — | путь к mp4 (обязательно) |
| `-N`      | 6 | сколько кадров пробовать |
| `-Config` | `modules/track_teams/config.example.yaml` | конфиг регистрации |
| `-Out`    | `modules/debug_register/reports` | папка вывода |
| `-NoPush` | — | (только push.ps1) только локальный коммит |

## Вывод (`reports/`)

- `canonical.png` — каноническая карта (та же, что в конфиге).
- `frame_<N>.png` — кадр из VOD.
- `matches_<N>.png` — SIFT-матчи кадр↔карта (визуально видно качество).
- `overlay_<N>.png` — каноническая карта с наложенным контуром кадра.
- `report.txt` — таблица: кадр, число инлайеров, reproj-error, низкий ли confidence.

## Тюнинг

| Симптом | Что крутить |
|---|---|
| Мало инлайеров на всех кадрах | проверить ROI и `canonical_target_w` в `config.yaml`, поднять `max_features` |
| Регистрация только на ярких кадрах | включить `clahe: true` |
| Хорошо на одной карте, плохо на другой | проверить, что в `shared/canonical_maps/` есть нужный `<map>.png` + 4+ контрольные точки в `<map>.json` |
