# batch_extract.ps1 — прогоняет detect_plates + extract_crops по всем видео в папке.
#
# Использование:
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\recognize_tags\batch_extract.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\recognize_tags\batch_extract.ps1 -VideosDir scripts\tracking\videos_in -TopN 30
#
# Логика:
#   - сканирует $VideosDir на *.mp4
#   - для каждого видео: match_id = имя файла без расширения
#   - detect_plates -> reports_batch/{match_id}/detections.json
#   - extract_crops -> dataset/raw/{match_id}/slot_XX/*.png
#   - если detections.json уже есть и -Force не передан, стадия detect_plates пропускается

param(
  [string]$VideosDir   = "scripts/tracking/videos_in",
  [string]$Hsv         = "scripts/tracking/configs/hsv_presets.storm-point.json",
  [string]$Zones       = "scripts/tracking/configs/zones.vod.json",
  [string]$DetectOut   = "scripts/tracking/modules/recognize_tags/_detect_cache",
  [string]$CropsOut    = "scripts/tracking/modules/recognize_tags/dataset/raw",
  [int]   $TopN        = 40,
  [double]$PadFrac     = 0.4,
  [double]$PadFracV    = 0.25,
  [double]$SampleFps   = 1.0,
  [switch]$Force,
  [switch]$OnlyExtract
)

$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo

if (-not (Test-Path $VideosDir)) {
  Write-Host "[err] папка не найдена: $VideosDir" -ForegroundColor Red
  Write-Host "      создай её и положи туда *.mp4"
  exit 1
}

$videos = Get-ChildItem -Path $VideosDir -Filter *.mp4 -File
if ($videos.Count -eq 0) {
  Write-Host "[err] в $VideosDir нет .mp4 файлов" -ForegroundColor Red
  exit 1
}

Write-Host "[info] найдено видео: $($videos.Count)" -ForegroundColor Cyan

$idx = 0
foreach ($v in $videos) {
  $idx++
  $matchId  = [IO.Path]::GetFileNameWithoutExtension($v.Name)
  $videoRel = Resolve-Path -Relative $v.FullName
  $detDir   = Join-Path $DetectOut $matchId
  $detJson  = Join-Path $detDir "detections.json"

  Write-Host ""
  Write-Host "===== [$idx/$($videos.Count)] $matchId =====" -ForegroundColor Yellow
  Write-Host "  video: $videoRel"

  # === Stage 1: detect_plates ===
  if (-not $OnlyExtract) {
    if ((Test-Path $detJson) -and (-not $Force)) {
      Write-Host "  [skip] detections.json уже есть (-Force чтобы перегенерировать)"
    } else {
      New-Item -ItemType Directory -Force -Path $detDir | Out-Null
      Write-Host "  [stage1] detect_plates ..."
      $pyArgs = @(
        "scripts/tracking/modules/detect_plates/detect_plates.py",
        "--video", $videoRel,
        "--hsv-presets", $Hsv,
        "--zones", $Zones,
        "--out", $detDir,
        "--sample-fps", $SampleFps,
        "--h-tol", 1, "--s-tol", 6, "--v-tol", 14,
        "--loose-h-extra", 1, "--loose-s-extra", 12, "--loose-v-extra", 20,
        "--ignore-bottom-px", 105, "--target-plate-height", 30,
        "--max-expand-x", 22, "--max-width", 220,
        "--recovery", "--emit-slots",
        "--hwaccel", "auto",
        "--track-fps", 5.0, "--adaptive-fps", 5.0
      )
      python @pyArgs
      if ($LASTEXITCODE -ne 0) {
        Write-Host "  [err] detect_plates упал для $matchId, пропускаю" -ForegroundColor Red
        continue
      }
    }
  }

  if (-not (Test-Path $detJson)) {
    Write-Host "  [err] нет $detJson, пропускаю extract" -ForegroundColor Red
    continue
  }

  # === Stage 2: extract_crops ===
  Write-Host "  [stage2] extract_crops -> $CropsOut/$matchId"
  python scripts/tracking/modules/recognize_tags/extract_crops.py `
    --detections $detJson `
    --video      $videoRel `
    --zones      $Zones `
    --match-id   $matchId `
    --out        $CropsOut `
    --top-n      $TopN `
    --pad-frac   $PadFrac `
    --pad-frac-v $PadFracV
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  [err] extract_crops упал для $matchId" -ForegroundColor Red
    continue
  }
  Write-Host "  [ok] $matchId готов" -ForegroundColor Green
}

Write-Host ""
Write-Host "[done] обработано $($videos.Count) видео" -ForegroundColor Green
Write-Host "       кропы: $CropsOut/{match_id}/slot_XX/*.png"
Write-Host "       дальше — раскидать вручную в dataset/labeled/{TAG}/"