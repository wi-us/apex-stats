# push_logs.ps1 - collect track_teams + motion_detect report artifacts
# and push them to git so the Lovable agent can see fresh reports.
#
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\track_teams\push_logs.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\track_teams\push_logs.ps1 -NoPush
#
# We commit small report files only. Heavy overlay PNGs are NOT committed.

param(
  [switch]$NoPush
)

$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$repo = (git rev-parse --show-toplevel).Trim()
if (-not $repo) { throw "No git repo found." }
Set-Location $repo

$paths = @(
  "scripts/tracking/modules/track_teams/reports/tracks.json",
  "scripts/tracking/modules/track_teams/reports/tracks.slots.json",
  "scripts/tracking/modules/track_teams/reports/eval_id_switches.json",
  "scripts/tracking/modules/track_teams/reports/eval_id_switches.txt",
  "scripts/tracking/modules/track_teams/reports/run.log",
  "scripts/tracking/modules/motion_detect/reports/report.txt",
  "scripts/tracking/modules/motion_detect/reports/motion_tracks.json"
)

$found = @()
$missing = @()
foreach ($p in $paths) {
  if (Test-Path $p) {
    $sz = (Get-Item $p).Length / 1KB
    Write-Host ("  + {0,-70} {1,8:N1} KB" -f $p, $sz) -ForegroundColor Green
    $found += $p
  } else {
    Write-Host ("  - {0,-70} (missing)" -f $p) -ForegroundColor DarkYellow
    $missing += $p
  }
}

if ($found.Count -eq 0) {
  throw "Nothing to push - all expected reports are missing. Run push.ps1 first."
}

$totalKb = 0
foreach ($p in $found) { $totalKb += (Get-Item $p).Length / 1KB }
Write-Host ("[push_logs] total {0:N1} KB across {1} files" -f $totalKb, $found.Count) -ForegroundColor Cyan
if ($totalKb -gt 20480) {
  Write-Warning "Artifacts larger than 20 MB - not committing."
  return
}

if ($NoPush) {
  Write-Host "[ok] local-only (no-push)." -ForegroundColor Green
  return
}

git add -f @found
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
$fileCount = $found.Count
$kbInt = [int]$totalKb
$msg = "track_teams: logs $stamp ($fileCount files, $kbInt KB)"
git commit -m $msg | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "[push_logs] nothing to commit (no changes)" -ForegroundColor Yellow
  return
}

Write-Host "[push_logs] git push..." -ForegroundColor Cyan
git push

Write-Host "[ok] done. Tell the agent: look at scripts/tracking/modules/track_teams/reports/" -ForegroundColor Green
if ($missing.Count -gt 0) {
  Write-Host "[note] missing (ok if eval not yet run):" -ForegroundColor DarkYellow
  foreach ($m in $missing) { Write-Host "  - $m" -ForegroundColor DarkYellow }
}