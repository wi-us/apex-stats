# ocr_tags

OCR-калибратор `slot_id → team_tag`. Решает задачу: HSV-цвета двух команд
могут совпадать (или плыть на границах кольца/красной зоны), но текст
тега на плашке миникарты уникален. Один прогон на матч — даёт надёжное
соответствие `slot_id → "ELTE"/"SRC"/...`, которое потом подменяет
поле `slot-to-tag.json` и снимает «склейки» двух слотов под одним тегом.

## Зависимости

```
pip install pytesseract rapidfuzz opencv-contrib-python
```

Tesseract OCR (бинарь) должен быть установлен отдельно. На Windows:
https://github.com/UB-Mannheim/tesseract/wiki — путь до `tesseract.exe`
передаётся через `--tesseract-cmd`.

## Пример

```
python scripts/tracking/modules/ocr_tags/ocr_tags.py `
    --detections scripts/tracking/modules/detect_plates/reports/detections.json `
    --video      scripts/tracking/game_sp.mp4 `
    --teams      scripts/tracking/modules/ocr_tags/configs/teams.m-test-g1.json `
    --zones      scripts/tracking/configs/zones.vod.json `
    --out        scripts/tracking/modules/ocr_tags/reports `
    --top-n 30 --save-debug-crops `
    --tesseract-cmd "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## Выход

`reports/slot_tags.json`:
```json
{
  "assignments": { "slot_3": "FXI", "slot_20": "SR2", ... },
  "per_slot": {
    "slot_3": { "tag": "FXI", "weight": 18.4, "samples": 30,
                "debug": { "raw": [...], "weights": {...} } }
  }
}
```

## Шрифты

`fonts/TTLakes-Regular.woff`, `fonts/TTLakes-Medium.woff` — используется на
трансляции Apex. Сейчас используются только для возможного дообучения
Tesseract (`tesstrain`); базовый pipeline работает на дефолтной модели
`eng` с whitelist `[A-Z0-9]`.

## После прогона

```
python scripts/postprocess/apply_slot_tags.py `
    --slot-tags scripts/tracking/modules/ocr_tags/reports/slot_tags.json `
    --game      src/data/m-test-g1
```

Скрипт обновляет `src/data/m-test-g1/slot-to-tag.json` и пишет
`meta.trimmed.ocr_tags` в `tracks.json`.