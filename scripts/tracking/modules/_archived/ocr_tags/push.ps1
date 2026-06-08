# Скопировать reports/slot_tags.json в src/data/<game> и обновить slot-to-tag.json.
param(
  [string]$Game = "m-test-g1"
)
$ErrorActionPreference = "Stop"
python scripts/postprocess/apply_slot_tags.py `
    --slot-tags scripts/tracking/modules/ocr_tags/reports/slot_tags.json `
    --game      "src/data/$Game"