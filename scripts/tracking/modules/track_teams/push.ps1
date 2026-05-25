# track_teams: run track_teams.py and (optionally) push reports/ to git.
param(
  [Parameter(Mandatory=$true)][string]$Video,
  [string]$Config = "scripts/tracking/modules/track_teams/config.example.yaml",
  [string]$Out = "scripts/tracking/modules/track_teams/reports/tracks.json",
  [string]$Anchors = "scripts/tracking/modules/motion_detect/reports/motion_tracks.json",
  [string]$StartCoords = "scripts/tracking/modules/track_teams/eval/reports/start_coords.json",
  [switch]$Show,
  [double]$ShowScale = 0.5,
  [int]$ShowEvery = 1,
  [string]$Eliminations = "scripts/tracking/modules/hud_read/reports/eliminations.json",
  [string]$FromDetections = "scripts/tracking/modules/detect_plates/reports/detections.json",
  [switch]$NoFromDetections,
  [int]$FrameStep = 0,
  [double]$Start = 0,
  [double]$End = -1,
  [switch]$NoPush
)
$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
$args = @("--video", $Video, "--config", $Config, "--out", $Out, "--start", $Start, "--end", $End)
if ($FrameStep -gt 0) { $args += @("--frame-step", $FrameStep) }
if ($StartCoords -and (Test-Path $StartCoords)) {
  $args += @("--start-coords", $StartCoords)
  Write-Host "[track_teams] using ALGS POI start-coords: $StartCoords" -ForegroundColor Green
} elseif ($Anchors -and (Test-Path $Anchors)) {
  $args += @("--anchors", $Anchors)
  Write-Host "[track_teams] using motion anchors: $Anchors" -ForegroundColor Cyan
} else {
  Write-Host "[track_teams] no start-coords / motion anchors - fallback to YAML teams" -ForegroundColor Yellow
}
if ($Show) {
  $args += @("--show", "--show-scale", $ShowScale, "--show-every", $ShowEvery)
  Write-Host "[track_teams] live overlay enabled (scale=$ShowScale, every=$ShowEvery)" -ForegroundColor Green
}
if ($Eliminations -and (Test-Path $Eliminations)) {
  $args += @("--eliminations", $Eliminations)
  Write-Host "[track_teams] using eliminations: $Eliminations" -ForegroundColor Cyan
} else {
  Write-Host "[track_teams] no eliminations file (looked at: $Eliminations) - absence-based wipe fallback" -ForegroundColor Yellow
}
if (-not $NoFromDetections -and $FromDetections -and (Test-Path $FromDetections)) {
  $args += @("--from-detections", $FromDetections)
  Write-Host "[track_teams] using detect_plates checkpoints: $FromDetections (HSV detection disabled)" -ForegroundColor Cyan
} else {
  if ($NoFromDetections) {
    Write-Host "[track_teams] -NoFromDetections: classic mode (own HSV detection)" -ForegroundColor Yellow
  } else {
    Write-Host "[track_teams] no detections file (looked at: $FromDetections) - classic mode" -ForegroundColor Yellow
  }
}
$logPath = Join-Path (Split-Path $Out -Parent) "run.log"
New-Item -ItemType Directory -Force -Path (Split-Path $logPath -Parent) | Out-Null
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& python scripts/tracking/modules/track_teams/track_teams.py @args 2>&1 |
  ForEach-Object { "$_" } | Tee-Object -FilePath $logPath
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($code -ne 0) {
  Write-Host "[track_teams] failed (exit=$code). Log tail:" -ForegroundColor Red
  Get-Content $logPath -Tail 40
  throw "track_teams.py failed"
}
if ($NoPush) { return }
git add (Split-Path $Out -Parent)
git commit -m "track_teams: run $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-Null
if ($LASTEXITCODE -eq 0) { git push }
