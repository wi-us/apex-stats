# recognize_tags

Stage 2+3a pipeline (PaddleOCR + CNN classifier) — replacement for ocr_tags.

## Layout
- extract_crops.py — builds dataset of plate crops from detections.json + video
- paddle_ocr.py     — drop-in OCR replacement (TBD)
- train_classifier.py — trains MobileNetV3-small on labeled/  ✅
- cnn_infer.py      — inference for trained model (TBD)
- vote.py           — fuses paddle + cnn + color (TBD)

## Dataset structure
- dataset/raw/{slot_id}/*.png — all extracted crops (unlabeled)
- dataset/labeled/{TAG}/*.png — manually moved here for training
- dataset/_review/*.png       — ambiguous, needs manual decision

## Training (CNN classifier)

### 1. Install ML deps (отдельно от tracking/requirements.txt)

CPU:
```
pip install -r scripts/tracking/modules/recognize_tags/requirements-ml.txt
```

CUDA 12.1 (если есть NVIDIA GPU):
```
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install -r scripts/tracking/modules/recognize_tags/requirements-ml.txt
```

### 2. Подготовить датасет

`dataset/labeled/{TAG}/*.png` — по одной папке на тег. Папки с префиксом `_`
(например `_review`) и папки без картинок игнорируются.

### 3. Запуск

```
powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\recognize_tags\run_train.ps1
```

или напрямую:

```
python scripts/tracking/modules/recognize_tags/train_classifier.py `
  --data scripts/tracking/modules/recognize_tags/dataset/labeled `
  --out  scripts/tracking/modules/recognize_tags/models `
  --epochs 60 --batch 32 --img-size 48
```

### 4. Что получится

- `models/tag_classifier.pt` — лучший чекпойнт по val_acc (early stopping).
- `models/labels.json` — порядок классов + img_size (нужен для инференса).
- `models/train_report.txt` — best val_acc, 10 самых слабых классов, распределение.

### Тюнинг под малый датасет (~30 кропов/класс)

- Stratified split 80/20 + WeightedRandomSampler + class-weighted CE — компенсируют
  дисбаланс (у тебя сейчас перекос есть, см. min/max в логах).
- Аугментации без hue-jitter: цвет плашки команды — сильный признак, его не трогаем.
- Если val_acc застрял <0.85 — собери больше кропов через `run_extract.ps1` с
  бо́льшим `-TopN` или прогоном по нескольким матчам с одинаковыми тегами.

