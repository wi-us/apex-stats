# -*- coding: utf-8 -*-
# Параллельный OCR плашек + автогенерация src/data/{match_id}/slot-to-tag.json
#
# Пайплайн на матч:
#   videos_in/{match_id}.mp4
#   + detect_plates cache: {DetectOut}/{match_id}/detections.json
#   -> ocr_tags.py  -> {OcrOut}/{match_id}/slot_tags.json
#   -> apply_slot_tags.py -> src/data/{match_id}/slot-to-tag.json
#
# Перед запуском уже должен быть прогнан batch_detect.ps1 (detections.json).
#
# Пример:
#   powershell -ExecutionPolicy Bypass -File `
#     scripts\tracking\modules\ocr_tags\batch_ocr.ps1 `
#     -Teams scripts\tracking\modules\ocr_tags\configs\teams.m-test-g1.json `
#     -MaxJobs 5

param(
  [string]$VideosDir   = "scripts/tracking/videos_in",
  [string]$Teams       = "scripts/tracking/modules/ocr_tags/configs/teams.m-test-g1.json",
  [string]$Zones       = "scripts/tracking/configs/zones.vod.json",
  [string]$DetectOut   = "scripts/tracking/modules/detect_plates/_detect_cache",
  [string]$OcrOut      = "scripts/tracking/modules/ocr_tags/_ocr_cache",
  [string]$DataRoot    = "src/data",
  [int]   $TopN        = 60,
  [double]$PadFrac     = 0.4,
  [double]$PadFracV    = 0.25,
  [string]$TesseractCmd = "C:\Program Files\Tesseract-OCR\tesseract.exe",
  [switch]$Force,
  [switch]$Sequential,
  [int]   $MaxJobs     = 5
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path ".").Path

if (-not (Test-Path $VideosDir))   { throw "VideosDir not found: $VideosDir" }
if (-not (Test-Path $Teams))       { throw "Teams config not found: $Teams" }
if (-not (Test-Path $Zones))       { throw "Zones not found: $Zones" }
if ($MaxJobs -lt 1) { $MaxJobs = 1 } elseif ($MaxJobs -gt 15) { $MaxJobs = 15 }

$videos = Get-ChildItem -Path $VideosDir -Filter *.mp4 -File
if ($videos.Count -eq 0) { throw "Нет .mp4 в $VideosDir" }

$logDir = Join-Path $OcrOut "_logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$jobScript = {
  param($repoRoot, $videoPath, $matchId,
        $Teams, $Zones, $DetectOut, $OcrOut, $DataRoot,
        $TopN, $PadFrac, $PadFracV, $TesseractCmd, $Force)

  Set-Location $repoRoot
  $ErrorActionPreference = "Stop"

  $detJson = Join-Path (Join-Path $DetectOut $matchId) "detections.json"
  $ocrDir  = Join-Path $OcrOut $matchId
  $ocrJson = Join-Path $ocrDir "slot_tags.json"
  $gameDir = Join-Path $DataRoot $matchId
  $logFile = Join-Path (Join-Path $OcrOut "_logs") "$matchId.log"

  if (-not (Test-Path $detJson)) {
    Add-Content -Path $logFile -Value "[err] no detections: $detJson"
    return "NO_DETECTIONS"
  }
  New-Item -ItemType Directory -Force -Path $ocrDir  | Out-Null
  New-Item -ItemType Directory -Force -Path $gameDir | Out-Null

  Set-Content -Path $logFile -Value "[start] $matchId @ $(Get-Date -Format s)"
  Add-Content -Path $logFile -Value "video=$videoPath"

  $videoRel = Resolve-Path -Relative $videoPath
  $needOcr  = $Force -or -not (Test-Path $ocrJson)

  if ($needOcr) {
    $cmd = "python scripts/tracking/modules/ocr_tags/ocr_tags.py " +
           "--detections `"$detJson`" --video `"$videoRel`" " +
           "--teams `"$Teams`" --zones `"$Zones`" --out `"$ocrDir`" " +
           "--top-n $TopN --pad-frac $PadFrac --pad-frac-v $PadFracV " +
           "--tesseract-cmd `"$TesseractCmd`" >> `"$logFile`" 2>&1"
    Add-Content -Path $logFile -Value "[cmd] $cmd"
    cmd /c $cmd | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $ocrJson)) {
      Add-Content -Path $logFile -Value "[err] ocr_tags failed ($LASTEXITCODE)"
      return "FAIL_OCR"
    }
  } else {
    Add-Content -Path $logFile -Value "[skip] ocr cache exists"
  }

  $applyCmd = "python scripts/postprocess/apply_slot_tags.py " +
              "--slot-tags `"$ocrJson`" --game `"$gameDir`" >> `"$logFile`" 2>&1"
  Add-Content -Path $logFile -Value "[cmd] $applyCmd"
  cmd /c $applyCmd | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $logFile -Value "[err] apply_slot_tags failed ($LASTEXITCODE)"
    return "FAIL_APPLY"
  }

  Add-Content -Path $logFile -Value "[done] $matchId @ $(Get-Date -Format s)"
  return "OK"
}

Write-Host "[batch_ocr] videos=$($videos.Count) MaxJobs=$MaxJobs Teams=$Teams"

$results = @{}
if ($Sequential) {
  foreach ($v in $videos) {
    $mid = [IO.Path]::GetFileNameWithoutExtension($v.Name)
    Write-Host "  -> $mid"
    $r = & $jobScript $repoRoot $v.FullName $mid `
          $Teams $Zones $DetectOut $OcrOut $DataRoot `
          $TopN $PadFrac $PadFracV $TesseractCmd $Force.IsPresent
    $results[$mid] = $r
    Write-Host "     [$r]"
  }
} else {
  $jobs = @{}
  $queue = New-Object System.Collections.Queue
  foreach ($v in $videos) { $queue.Enqueue($v) }

  while ($queue.Count -gt 0 -or $jobs.Count -gt 0) {
    while ($jobs.Count -lt $MaxJobs -and $queue.Count -gt 0) {
      $v = $queue.Dequeue()
      $mid = [IO.Path]::GetFileNameWithoutExtension($v.Name)
      $job = Start-Job -ScriptBlock $jobScript -ArgumentList `
        $repoRoot, $v.FullName, $mid, `
        $Teams, $Zones, $DetectOut, $OcrOut, $DataRoot, `
        $TopN, $PadFrac, $PadFracV, $TesseractCmd, $Force.IsPresent
      $jobs[$job.Id] = $mid
      Write-Host "  start: $mid (job $($job.Id))"
    }
    Start-Sleep -Milliseconds 800
    foreach ($id in @($jobs.Keys)) {
      $j = Get-Job -Id $id
      if ($j.State -in @("Completed","Failed","Stopped")) {
        $mid = $jobs[$id]
        $r = Receive-Job -Id $id -ErrorAction SilentlyContinue
        if (-not $r) { $r = "FAIL_JOB" }
        $results[$mid] = "$r"
        Write-Host "  done : $mid -> $r"
        Remove-Job -Id $id -Force | Out-Null
        $jobs.Remove($id) | Out-Null
      }
    }
  }
}

Write-Host ""
Write-Host "=== summary ==="
$results.GetEnumerator() | Sort-Object Name | ForEach-Object {
  Write-Host ("  {0,-30} {1}" -f $_.Key, $_.Value)
}
Write-Host ""
Write-Host "OCR cache : $OcrOut/{match_id}/slot_tags.json"
Write-Host "Slot maps : $DataRoot/{match_id}/slot-to-tag.json"
Write-Host "Logs      : $logDir/{match_id}.log"