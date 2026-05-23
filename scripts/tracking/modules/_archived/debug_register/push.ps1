# debug_register: запустить debug_register.py и (опционально) запушить reports/ в git.
#
# Использование:
#   powershell -ExecutionPolicy Bypass -File scripts/tracking/modules/debug_register/push.ps1 -Video scripts/tracking/game.mp4
#   powershell -ExecutionPolicy Bypass -File scripts/tracking/modules/debug_register/run.ps1  -Video scripts/tracking/game.mp4

param(
  [Parameter(Mandatory=$true)][string]$Video,
  [int]$N = 6,
  [string]$Config = "scripts/tracking/modules/track_teams/config.example.yaml",
  [string]$Out = "scripts/tracking/modules/debug_register/reports",
  [switch]$NoPush
)

$ErrorActionPreference = "Stop"
chcp 65001 > $null
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$repo = (git rev-parse --show-toplevel).Trim()
if (-not $repo) { throw "Не вижу git-репозитория." }
Set-Location $repo

if (Test-Path $Out) {
  Get-ChildItem $Out -Recurse -Force | Where-Object { $_.Name -ne ".gitkeep" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Write-Host "[debug] запускаю debug_register.py (n=$N)..." -ForegroundColor Cyan
python scripts/tracking/modules/debug_register/debug_register.py --video $Video --config $Config --out $Out --n $N
if ($LASTEXITCODE -ne 0) { throw "debug_register.py упал" }

$size = (Get-ChildItem $Out -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("[debug] reports весит {0:N1} MB" -f $size) -ForegroundColor Cyan
if ($size -gt 50) {
  Write-Warning "reports больше 50 MB. Уменьши -N."
  return
}

if ($NoPush) {
  Write-Host "[ok] локально готово (no-push). Reports: $Out" -ForegroundColor Green
  return
}

git add $Out
$msg = "debug_register: run $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git commit -m $msg | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "[debug] нечего коммитить" -ForegroundColor Yellow
  return
}

Write-Host "[debug] git push..." -ForegroundColor Cyan
git push

Write-Host "[ok] готово. Скажи агенту: 'посмотри scripts/tracking/modules/debug_register/reports/report.txt'" -ForegroundColor Green
