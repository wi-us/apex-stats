# paddle_ocr_test

Тестовый модуль: прогоняет PaddleOCR по нарезанным кропам плашек,
матчит результат против словаря команд из локальной SQLite-базы
(`scripts/algs_api/data/algs.sqlite`, таблица `teams`).

Цель — оценить, нужен ли вообще fine-tune OCR, или готовый PaddleOCR
уже даёт приемлемую точность на стримовых плашках Apex.

## Установка

```bash
pip install paddlepaddle paddleocr rapidfuzz pillow
```

(GPU: `paddlepaddle-gpu` вместо `paddlepaddle`)

## Запуск

```bash
python run_test.py \
    --crops-dir /path/to/plates_sorted \
    --db ../../../../scripts/algs_api/data/algs.sqlite \
    --out reports
```

`--crops-dir` ожидает либо плоскую папку с PNG, либо структуру
`<TAG>/*.png` (как `plates_sorted.zip`) — тогда имя папки используется
как ground truth для accuracy.

## Что считает

1. Берёт все `*.png` (рекурсивно).
2. PaddleOCR (англ., angle_cls=True) → сырые строки + conf.
3. Нормализует (A-Z0-9), матчит rapidfuzz по словарю:
   - `teams.name`, `teams.short_name` из SQLite
   - + опциональный локальный alias-файл (`aliases.json`)
4. Пишет `reports/results.json` и `reports/summary.txt` с топом ошибок.

## Выход

- `results.json` — по каждому файлу: `raw_text`, `conf`, `matched_tag`,
  `match_ratio`, `gt_tag`, `correct`.
- `summary.txt` — overall accuracy, accuracy по тегам, топ-20 mismatch.

## Дальше

Если accuracy ≥ 85% — PaddleOCR можно вшивать в пайплайн без обучения.
Если ниже — собранные кропы (у тебя уже размечены) подходят как
датасет для fine-tune Paddle rec-модели.
