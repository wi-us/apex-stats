param(
  [string]$Data    = "scripts/tracking/modules/recognize_tags/dataset/labeled",
  [string]$Out     = "scripts/tracking/modules/recognize_tags/models",
  [int]   $Epochs  = 60,
  [int]   $Batch   = 32,
  [int]   $ImgSize = 48
)

$ErrorActionPreference = "Stop"
python scripts/tracking/modules/recognize_tags/train_classifier.py `
  --data     $Data `
  --out      $Out `
  --epochs   $Epochs `
  --batch    $Batch `
  --img-size $ImgSize