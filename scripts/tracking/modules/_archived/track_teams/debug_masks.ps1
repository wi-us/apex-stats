# debug_masks: запускает debug_masks.py и пушит reports/debug_masks/ в git.
#
# Использование:
#   powershell -ExecutionPolicy Bypass -File scripts/tracking/modules/track_teams/debug_masks.ps1 -Video scripts/tracking/game.mp4
#   ... опционально -Slots "2,4,7,10,11,16,17" -PerSlot 6 -NoPush
param(
  [Parameter(Mandatory=$true)][string]$Video,
  [string]$Tracks  = "scripts/tracking/modules/track_teams/reports/tracks.json",
  [string]$Config  = "scripts/tracking/modules/track_teams/config.example.yaml",
  [string]$Anchors = "scripts/tracking/modules/motion_detect/reports/motion_tracks.json",
  [string]$Slots   = "",
  [int]$PerSlot    = 6,
  [int]$RoiSize    = 220,
  [switch]$FullFrame,
  [switch]$AnchorsPreview,
  [switch]$NoPush
)
$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo

$outDir = "scripts/tracking/modules/track_teams/reports/debug_masks"
if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }

$pyArgs = @(
  "scripts/tracking/modules/track_teams/debug_masks.py",
  "--video", $Video,
  "--tracks", $Tracks,
  "--config", $Config,
  "--per-slot", $PerSlot,
  "--roi-size", $RoiSize
)
if (Test-Path $Anchors) { $pyArgs += @("--anchors", $Anchors) }
if ($Slots) { $pyArgs += @("--slots", $Slots) }
if ($FullFrame) { $pyArgs += @("--full-frame") }
if ($AnchorsPreview) { $pyArgs += @("--anchors-preview") }

& python @pyArgs
if ($LASTEXITCODE -ne 0) { throw "debug_masks.py failed (exit=$LASTEXITCODE)" }

if ($NoPush) { return }
git add $outDir
git commit -m "debug_masks: $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-Null
if ($LASTEXITCODE -eq 0) { git push }