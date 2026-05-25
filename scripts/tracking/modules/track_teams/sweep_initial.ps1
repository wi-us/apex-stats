# PowerShell wrapper для sweep_initial.py.
# (resync)
# Пример:
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\track_teams\sweep_initial.ps1 `
#     -Video "D:\path\game.mp4" -Jobs 10 -MaxVariants 80
param(
  [Parameter(Mandatory=$true)][string]$Video,
  [string]$Anchors = "scripts/tracking/modules/motion_detect/reports/motion_tracks.json",
  [string]$Eliminations = "scripts/tracking/modules/hud_read/reports/eliminations.json",
  [double]$End = 300.0,
  [double]$MatchPx = 150.0,
  [int]$Jobs = 6,
  [int]$MaxVariants = 40,
  [switch]$KeepIntermediate
)
$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo

# ETA-подсказка: один прогон 5 мин видео занимает ~30-90s в зависимости от frame_step.
$etaMin = [math]::Round(($End / 60.0) * $MaxVariants / [math]::Max(1, $Jobs), 1)
Write-Host "[sweep] $MaxVariants variants x ${End}s video / $Jobs jobs ~= $etaMin min wallclock" -ForegroundColor Yellow

$argsList = @(
  "scripts/tracking/modules/track_teams/sweep_initial.py",
  "--video", $Video,
  "--anchors", $Anchors,
  "--eliminations", $Eliminations,
  "--end", $End,
  "--match-px", $MatchPx,
  "--jobs", $Jobs,
  "--max-variants", $MaxVariants
)
if ($KeepIntermediate) { $argsList += "--keep-intermediate" }
python @argsList