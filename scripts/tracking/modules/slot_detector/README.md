# Slot Detector

Experimental YOLOv8 module for detecting Apex Legends team plates with a slot class.

The existing `plate_detector` model detects one class only:

```yaml
0: team_plate
```

This module keeps detection separate and trains a second model where the class means the match slot:

```yaml
0: SLOT_01
1: SLOT_02
...
19: SLOT_20
```

It is useful when old labels were marked by team color/slot and newer datasets were later collapsed to `team_plate`.

## Prepare Dataset From Old Zip

```powershell
cd "C:\projs\apex stats\apex-tracer-insight-37d9f483\scripts\tracking\modules\slot_detector"
python .\scripts\prepare_slot_dataset.py `
  --source-zip "..\plate_detector\_archive\2026-05-29\source-bundles\plate_detector.zip" `
  --out ".\datasets\slot_plates_v1"
```

The script preserves original label class ids and creates:

```text
datasets/slot_plates_v1/
  images/train
  images/val
  labels/train
  labels/val
  data.yaml
  dataset_stats.json
```

## Train YOLOv8

```powershell
python .\scripts\train_slot_yolo.py `
  --data ".\datasets\slot_plates_v1\data.yaml" `
  --model yolov8n.pt `
  --epochs 80 `
  --imgsz 960 `
  --batch 4 `
  --device 0
```

Best weights will be saved under:

```text
runs/detect/slot_plate_v1/weights/best.pt
```

Use `--device cpu` if CUDA is unavailable.

## Predict Slots

```powershell
python .\scripts\predict_slots.py `
  --weights ".\runs\detect\slot_plate_v1\weights\best.pt" `
  --source "..\plate_detector\runs\detect\runs\pred_v2_hardneg_pool-2\game_ol_frame_0000000.jpg" `
  --out ".\outputs\slot_predict_test"
```

Outputs:

```text
predictions.csv
predictions.jsonl
visuals/
```

Visuals use project color profiles from `scripts/tracking/configs/plate_detector/*.preset_colors.json` when available.

## Relabel Existing `team_plate` Labels Into Slots

Use this when you already have images and YOLO labels where every object is class `0: team_plate`, and you want to replace class `0` with the slot predicted by the slot model while keeping the old bbox coordinates.

```powershell
python .\scripts\relabel_team_plate_dataset.py `
  --weights ".\runs\detect\slot_plate_v1\weights\best.pt" `
  --images "..\plate_detector\dataset_strict_ed_v5_tight\images\train" `
  --labels "..\plate_detector\dataset_strict_ed_v5_tight\labels\train" `
  --out-labels ".\outputs\relabeled_slots\labels\train"
```

The script matches old boxes to slot predictions by IoU. It writes:

```text
*.txt               # relabeled YOLO labels
visuals/train/*.jpg # annotated images with slot labels and team colors
relabel_report.csv
unmatched.txt
review/unmatched/     # crops for manual review, not training
```

By default:

- `device=auto` is used. If the installed PyTorch build is CPU-only, the scripts fall back to CPU. Use `-Device 0` only when `torch.cuda.is_available()` is true.
- unmatched boxes are dropped from training labels and kept in `unmatched.txt`, visuals, and `review/unmatched/`;
- no more than 3 boxes per slot are saved for one image. Use `-MaxPerSlot 0` to disable this filter.
- visual debug images show only boxes saved to training labels. Use `-DrawRejected` to also draw unmatched and dropped boxes.

## Build Dataset From Relabeled Labels

```powershell
.\build-relabel-dataset.ps1 `
  -Images "..\plate_detector\dataset_strict_ed_v5_tight\images\train" `
  -Labels ".\outputs\relabeled_slots\labels\train" `
  -Out ".\datasets\slot_plates_relabel_v1"
```

Empty relabeled files are skipped by default, because visible unmatched plates with empty labels would teach YOLO that those plates are background.

Train on the relabeled dataset:

```powershell
.\train.ps1 `
  -Data ".\datasets\slot_plates_relabel_v1\data.yaml" `
  -Name "slot_plate_relabel_v1" `
  -Epochs 80 `
  -ImgSize 960 `
  -Batch 4
```

## Hybrid Detector + Crop Classifier

Recommended pipeline:

1. Use the old `team_plate` YOLO model to find plate boxes.
2. Crop each plate with padding.
3. Classify the crop into `SLOT_01` ... `SLOT_20`.
4. Use HSV profiles and short history as supporting signals.

Build crop classification dataset:

```powershell
.\build-crop-dataset.ps1 `
  -Images "..\plate_detector\dataset_strict_ed_v5_tight\images\train" `
  -Labels ".\outputs\relabeled_slots\labels\train" `
  -Out ".\datasets\slot_crop_v1"
```

Train crop classifier:

```powershell
.\train-classifier.ps1 `
  -Data ".\datasets\slot_crop_v1" `
  -Name "slot_crop_classifier_v1" `
  -Epochs 60 `
  -ImgSize 96 `
  -Batch 64
```

Run hybrid prediction:

```powershell
.\hybrid-predict.ps1 `
  -Source "..\plate_detector\runs\detect\runs\pred_v2_hardneg_pool-2\game_ol_frame_0000000.jpg" `
  -ClassifierWeights ".\runs\classify\slot_crop_classifier_v1\weights\best.pt" `
  -Out ".\outputs\hybrid_slots_test" `
  -SaveCrops
```

Outputs:

```text
hybrid_predictions.csv
hybrid_predictions.jsonl
visuals/
crops/                 # optional with -SaveCrops
review/conflicts/      # low-confidence or contradictory decisions
```

Each row includes `bbox_original`, `det_conf`, `slot_id`, `slot_conf`, `top3`, `hsv_candidates`, `identity_source`, and `is_color_distorted`.

## Notes

- The module does not replace the production `plate_detector`.
- Slot names can be changed in `data.yaml` later if a real class-name map is recovered.
- Slot detection is expected to be less universal than generic plate detection because slot appearance depends on broadcast colors and tournament HUD style.
