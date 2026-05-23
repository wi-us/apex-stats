# recognize_tags

Stage 2+3a pipeline (PaddleOCR + CNN classifier) — replacement for ocr_tags.

## Layout
- extract_crops.py — builds dataset of plate crops from detections.json + video
- paddle_ocr.py     — drop-in OCR replacement (TBD)
- train_classifier.py — trains MobileNetV3-small on labeled/ (TBD)
- cnn_infer.py      — inference for trained model (TBD)
- vote.py           — fuses paddle + cnn + color (TBD)

## Dataset structure
- dataset/raw/{slot_id}/*.png — all extracted crops (unlabeled)
- dataset/labeled/{TAG}/*.png — manually moved here for training
- dataset/_review/*.png       — ambiguous, needs manual decision

