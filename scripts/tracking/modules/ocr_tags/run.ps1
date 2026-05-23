param(
  [string]$Video      = "scripts/tracking/game_sp.mp4",
  [string]$Detections = "scripts/tracking/modules/detect_plates/reports/detections.json",
  [string]$Teams      = "scripts/tracking/modules/ocr_tags/configs/teams.m-test-g1.json",
  [string]$Zones      = "scripts/tracking/configs/zones.vod.json",
  [string]$Out        = "scripts/tracking/modules/ocr_tags/reports",
  [int]   $TopN       = 30,
  [string]$TesseractCmd = "C:\Program Files\Tesseract-OCR\tesseract.exe",
  [switch]$SaveDebug
)

$ErrorActionPreference = "Stop"
$args = @(
  "scripts/tracking/modules/ocr_tags/ocr_tags.py",
  "--detections", $Detections,
  "--video",      $Video,
  "--teams",      $Teams,
  "--zones",      $Zones,
  "--out",        $Out,
  "--top-n",      $TopN,
  "--tesseract-cmd", $TesseractCmd
)
if ($SaveDebug) { $args += "--save-debug-crops" }
python @args