param(
  [string]$Video      = "scripts/tracking/game_sp.mp4",
  [string]$Detections = "scripts/tracking/modules/detect_plates/reports/detections.json",
  [string]$Zones      = "scripts/tracking/configs/zones.vod.json",
  [string]$MatchId    = "m-test-g1",
  [string]$Out        = "scripts/tracking/modules/recognize_tags/dataset/raw",
  [int]   $TopN       = 80,
  [double]$PadFrac    = 0.4,
  [double]$PadFracV   = 0.25
)

$ErrorActionPreference = "Stop"
python scripts/tracking/modules/recognize_tags/extract_crops.py `
  --detections $Detections `
  --video      $Video `
  --zones      $Zones `
  --match-id   $MatchId `
  --out        $Out `
  --top-n      $TopN `
  --pad-frac   $PadFrac `
  --pad-frac-v $PadFracV