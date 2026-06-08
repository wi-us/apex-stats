param(
  [Parameter(Mandatory=$true)][string]$Video,
  [Parameter(Mandatory=$true)][string]$Rings,
  [Parameter(Mandatory=$true)][string]$Zones,
  [string]$Cuts = "",
  [string]$MinimapZone = "camera roi",
  [string]$Canonical = "storm_point",
  [int]$MaxRing = 3,
  [string]$Out = "",
  [switch]$NoPush
)
$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
if ($Out -eq "") {
  $matchId = [IO.Path]::GetFileNameWithoutExtension($Video)
  $Out = "scripts/tracking/matches/$matchId/ring_locator"
}
if (Test-Path $Out) {
  Get-ChildItem $Out -Recurse -Force | Where-Object { $_.Name -ne ".gitkeep" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$argsList = @(
  "scripts/tracking/modules/ring_locator/ring_locator.py",
  "--video", $Video,
  "--rings", $Rings,
  "--zones", $Zones,
  "--minimap-zone", $MinimapZone,
  "--canonical", $Canonical,
  "--max-ring", $MaxRing,
  "--out", $Out
)
if ($Cuts) { $argsList += @("--cuts", $Cuts) }
& python @argsList
if ($LASTEXITCODE -ne 0) { throw "ring_locator упал (rc=$LASTEXITCODE)" }
if ($NoPush) { return }
git add $Out
git commit -m "ring_locator: run $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-Null
if ($LASTEXITCODE -eq 0) { git push }
