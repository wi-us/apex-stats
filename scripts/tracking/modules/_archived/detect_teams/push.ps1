# detect_teams: запустить detect_teams.py и (опционально) запушить reports/ в git.
param(
  [Parameter(Mandatory=$true)][string]$Video,
  [string]$Cuts = "scripts/tracking/modules/find_cuts/reports/cuts.json",
  [string]$HsvPresets = "scripts/tracking/configs/hsv_presets.worlds-edge.json",
  [string]$Zones = "scripts/tracking/modules/motion_detect/configs/zones.vod.json",
  [string]$ZoneTags = "team,minimap",
  [int]$Frames = 40,
  [int]$Step = 600,
  [string]$Out = "scripts/tracking/modules/detect_teams/reports",
  [switch]$NoPush
)
$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
if (Test-Path $Out) {
  Get-ChildItem $Out -Recurse -Force | Where-Object { $_.Name -ne ".gitkeep" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
& python scripts/tracking/modules/detect_teams/detect_teams.py `
  --video $Video --cuts $Cuts --hsv-presets $HsvPresets `
  --zones $Zones --zone-tags $ZoneTags `
  --frames $Frames --step $Step --out-dir $Out
if ($LASTEXITCODE -ne 0) { throw "detect_teams.py упал" }
if ($NoPush) { return }
git add $Out
git commit -m "detect_teams: scan $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-Null
if ($LASTEXITCODE -eq 0) { git push }
