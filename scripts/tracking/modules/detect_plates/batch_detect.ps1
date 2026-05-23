# batch_detect.ps1 — параллельный detect_plates по всем видео в папке.
#
# Запуск:
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\detect_plates\batch_detect.ps1 -MaxJobs 5
#
# Что делает:
#   - сканит $VideosDir на *.mp4
#   - для каждого видео: match_id = имя файла без расширения
#   - запускает detect_plates.py -> $DetectOut/{match_id}/detections.json
#   - параллельно до $MaxJobs (дефолт 5, потолок 15)
#   - логи каждой задачи: $DetectOut/_logs/{match_id}.log
#
# После него -> batch_extract.ps1 -OnlyExtract

param(
  [string]$VideosDir   = "scripts/tracking/videos_in",
  [string]$Hsv         = "scripts/tracking/configs/hsv_presets.storm-point.json",
  [string]$Zones       = "scripts/tracking/configs/zones.vod.json",
  [string]$DetectOut   = "scripts/tracking/modules/recognize_tags/_detect_cache",
  [double]$SampleFps   = 1.0,
  [int]   $MaxJobs     = 5,
  [switch]$Force,
  [switch]$Sequential
)

$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo

if ($Sequential) { $MaxJobs = 1 }
if ($MaxJobs -lt 1)  { $MaxJobs = 1 }
if ($MaxJobs -gt 15) { $MaxJobs = 15 }

if (-not (Test-Path $VideosDir)) {
  Write-Host "[err] папка не найдена: $VideosDir" -ForegroundColor Red
  exit 1
}
$videos = Get-ChildItem -Path $VideosDir -Filter *.mp4 -File
if ($videos.Count -eq 0) {
  Write-Host "[err] в $VideosDir нет .mp4" -ForegroundColor Red
  exit 1
}

$logDir = Join-Path $DetectOut "_logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Write-Host "[info] видео=$($videos.Count)  MaxJobs=$MaxJobs" -ForegroundColor Cyan

$jobScript = {
  param($repo, $videoRel, $matchId, $Hsv, $Zones, $detDir, $detJson, $SampleFps, $Force, $logFile)
  Set-Location $repo
  $env:PYTHONUTF8 = "1"
  if ((Test-Path $detJson) -and (-not $Force)) {
    Add-Content -Path $logFile -Value "[skip] detections.json уже есть"
    return "SKIP"
  }
  New-Item -ItemType Directory -Force -Path $detDir | Out-Null
  $cmd = "python scripts/tracking/modules/detect_plates/detect_plates.py " +
         "--video `"$videoRel`" --hsv-presets `"$Hsv`" --zones `"$Zones`" --out `"$detDir`" " +
         "--sample-fps $SampleFps --h-tol 1 --s-tol 6 --v-tol 14 " +
         "--loose-h-extra 1 --loose-s-extra 12 --loose-v-extra 20 " +
         "--ignore-bottom-px 105 --target-plate-height 30 " +
         "--max-expand-x 22 --max-width 220 --recovery --emit-slots " +
         "--hwaccel auto --track-fps 5.0 --adaptive-fps 5.0 " +
         ">> `"$logFile`" 2>&1"
  cmd /c $cmd
  if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $logFile -Value "[err] detect_plates exit=$LASTEXITCODE"
    return "FAIL"
  }
  if (-not (Test-Path $detJson)) {
    Add-Content -Path $logFile -Value "[err] detections.json не создан"
    return "NO_OUTPUT"
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
  $detDir   = Join-Path $DetectOut $matchId
  $detJson  = Join-Path $detDir "detections.json"
  $logFile  = Join-Path $logDir "${matchId}.log"
  if (Test-Path $logFile) { Remove-Item $logFile -Force }

  Write-Host "[launch] $matchId" -ForegroundColor Cyan
  $jobs += Start-Job -Name $matchId -ScriptBlock $jobScript -ArgumentList `
    $repo, $videoRel, $matchId, $Hsv, $Zones, $detDir, $detJson, $SampleFps, [bool]$Force, $logFile
}

Write-Host "[wait] $($jobs.Count) джобов, жду..." -ForegroundColor Yellow
$jobs | Wait-Job | Out-Null

$ok = 0; $skip = 0; $fail = 0
foreach ($j in $jobs) {
  $res = Receive-Job $j
  $jn = $j.Name
  switch ($res) {
    "OK"   { Write-Host "  [ok]   $jn" -ForegroundColor Green;  $ok++ }
    "SKIP" { Write-Host "  [skip] $jn" -ForegroundColor DarkGray; $skip++ }
    default {
      Write-Host "  [fail] $jn -> $res (log: $logDir\$jn.log)" -ForegroundColor Red
      $fail++
    }
  }
}
$jobs | Remove-Job

Write-Host ""
Write-Host "[done] ok=$ok  skip=$skip  fail=$fail  всего=$($videos.Count)" -ForegroundColor Green
Write-Host "       детекции: $DetectOut/{match_id}/detections.json"
Write-Host "       дальше:   batch_extract.ps1 -OnlyExtract -MaxJobs 5"
