# batch_extract.ps1 — прогоняет detect_plates + extract_crops по всем видео в папке.
#
# Использование:
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\recognize_tags\batch_extract.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\recognize_tags\batch_extract.ps1 -VideosDir scripts\tracking\videos_in -TopN 30
#
# Логика:
#   - сканирует $VideosDir на *.mp4
#   - для каждого видео: match_id = имя файла без расширения
#   - detect_plates -> scripts/tracking/matches/{match_id}/detect_plates/detections.json
#   - extract_crops -> dataset/raw/{match_id}/slot_XX/*.png
#   - если detections.json уже есть и -Force не передан, стадия detect_plates пропускается

param(
  [string]$VideosDir   = "scripts/tracking/videos_in",
  [string]$Hsv         = "scripts/tracking/configs/hsv_presets.storm-point.json",
  [string]$Zones       = "scripts/tracking/configs/zones.vod.json",
  [string]$DetectOut   = "scripts/tracking/matches",
  [string]$CropsOut    = "scripts/tracking/modules/recognize_tags/dataset/raw",
  [int]   $TopN        = 40,
  [double]$PadFrac     = 0.4,
  [double]$PadFracV    = 0.25,
  [double]$SampleFps   = 1.0,
  [switch]$Force,
  [switch]$OnlyExtract,
  [int]   $MaxJobs     = 5,
  [switch]$Sequential
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

if ($Sequential) { $MaxJobs = 1 }
if ($MaxJobs -lt 1) { $MaxJobs = 1 }
if ($MaxJobs -gt 15) { $MaxJobs = 15 }

$logDir = Join-Path $DetectOut "_logs/recognize_tags"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Write-Host "[info] найдено видео: $($videos.Count), MaxJobs=$MaxJobs" -ForegroundColor Cyan

$jobScript = {
  param(
    $repo, $videoRel, $matchId, $Hsv, $Zones, $detDir, $detJson,
    $CropsOut, $TopN, $PadFrac, $PadFracV, $SampleFps,
    $Force, $OnlyExtract, $logFile
  )
  Set-Location $repo
  $env:PYTHONUTF8 = "1"
  $stage1Cmd = ""
  if (-not $OnlyExtract) {
    if ((Test-Path $detJson) -and (-not $Force)) {
      Add-Content -Path $logFile -Value "[skip] detections.json уже есть"
    } else {
      New-Item -ItemType Directory -Force -Path $detDir | Out-Null
      $stage1Cmd = "python scripts/tracking/modules/detect_plates/detect_plates.py --video `"$videoRel`" --hsv-presets `"$Hsv`" --zones `"$Zones`" --out `"$detDir`" --sample-fps $SampleFps --h-tol 1 --s-tol 6 --v-tol 14 --loose-h-extra 1 --loose-s-extra 12 --loose-v-extra 20 --ignore-bottom-px 105 --target-plate-height 30 --max-expand-x 22 --max-width 220 --recovery --emit-slots --hwaccel auto --track-fps 5.0 --adaptive-fps 5.0 >> `"$logFile`" 2>&1"
      cmd /c $stage1Cmd
      if ($LASTEXITCODE -ne 0) {
        Add-Content -Path $logFile -Value "[err] detect_plates failed"
        return "FAIL_DETECT"
      }
    }
  }
  if (-not (Test-Path $detJson)) {
    Add-Content -Path $logFile -Value "[err] нет $detJson"
    return "NO_DETECTIONS"
  }
  $stage2Cmd = "python scripts/tracking/modules/recognize_tags/extract_crops.py --detections `"$detJson`" --video `"$videoRel`" --zones `"$Zones`" --match-id `"$matchId`" --out `"$CropsOut`" --top-n $TopN --pad-frac $PadFrac --pad-frac-v $PadFracV >> `"$logFile`" 2>&1"
  cmd /c $stage2Cmd
  if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $logFile -Value "[err] extract_crops failed"
    return "FAIL_EXTRACT"
  }
  return "OK"
}

$jobs = @()
foreach ($v in $videos) {
  while (@($jobs | Where-Object { $_.State -eq 'Running' }).Count -ge $MaxJobs) {
    Start-Sleep -Milliseconds 500
  }
  $matchId  = [IO.Path]::GetFileNameWithoutExtension($v.Name)
  $videoRel = Resolve-Path -Relative $v.FullName
  $detDir   = Join-Path (Join-Path $DetectOut $matchId) "detect_plates"
  $detJson  = Join-Path $detDir "detections.json"
  $logFile  = Join-Path $logDir "${matchId}.log"
  if (Test-Path $logFile) { Remove-Item $logFile -Force }

  Write-Host "[launch] $matchId  (log: $logFile)" -ForegroundColor Cyan
  $jobs += Start-Job -Name $matchId -ScriptBlock $jobScript -ArgumentList `
    $repo, $videoRel, $matchId, $Hsv, $Zones, $detDir, $detJson, `
    $CropsOut, $TopN, $PadFrac, $PadFracV, $SampleFps, `
    [bool]$Force, [bool]$OnlyExtract, $logFile
}

Write-Host "[wait] $($jobs.Count) джобов, жду..." -ForegroundColor Yellow
$jobs | Wait-Job | Out-Null

$okCount = 0; $failCount = 0
foreach ($j in $jobs) {
  $res = Receive-Job $j
  if ($res -eq "OK") {
    Write-Host "  [ok]   $($j.Name)" -ForegroundColor Green
    $okCount++
  } else {
    $jn = $j.Name
    Write-Host "  [fail] $jn -> $res (log: $logDir\$jn.log)" -ForegroundColor Red
    $failCount++
  }
}
$jobs | Remove-Job

Write-Host ""
Write-Host "[done] ok=$okCount  fail=$failCount  всего=$($videos.Count)" -ForegroundColor Green
Write-Host "       кропы: $CropsOut/{match_id}/slot_XX/*.png"
Write-Host "       логи:  $logDir/{match_id}.log"
Write-Host "       дальше — раскидать вручную в dataset/labeled/{TAG}/"
