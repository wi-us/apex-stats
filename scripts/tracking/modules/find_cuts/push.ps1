# find_cuts: запустить find_cuts.py и (опционально) запушить reports/ в git.
#
# Использование из корня репо:
#   # обычный запуск (push в GitHub — для меня, чтобы Lovable-агент увидел вывод):
#   powershell -ExecutionPolicy Bypass -File scripts/tracking/modules/find_cuts/push.ps1 -Video scripts/tracking/game.mp4
#
#   # без push (только локально):
#   powershell -ExecutionPolicy Bypass -File scripts/tracking/modules/find_cuts/run.ps1  -Video scripts/tracking/game.mp4
#
# Параметры:
#   -Video      путь к mp4 (обязательно)
#   -Coarse     грубый шаг в кадрах (по умолчанию 300)
#   -Fine       шаг отката для уточнения (по умолчанию 10)
#   -Threshold  порог Δpan в канонических пикселях (по умолчанию 90)
#   -Start      старт в секундах (по умолчанию 0)
#   -End        конец в секундах (-1 = до конца)
#   -Config     путь к конфигу
#   -Out        папка вывода (reports/)
#   -NoPush     не пушить, только локальный коммит

param(
  [Parameter(Mandatory=$true)][string]$Video,
  [int]$Coarse = 300,
  [int]$Fine = 10,
  [double]$Threshold = 90,
  [double]$Start = 0,
  [double]$End = -1,
  [string]$Config = "scripts/tracking/modules/_archived/track_teams/config.example.yaml",
  [string]$Out = "",
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

if ($Out -eq "") {
  $matchId = [IO.Path]::GetFileNameWithoutExtension($Video)
  $Out = "scripts/tracking/matches/$matchId/find_cuts"
}

if (Test-Path $Out) {
  Get-ChildItem $Out -Recurse -Force | Where-Object { $_.Name -ne ".gitkeep" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$logPath = Join-Path $Out "run.log"

Write-Host "[cuts] запускаю find_cuts.py (coarse=$Coarse, fine=$Fine, threshold=$Threshold)..." -ForegroundColor Cyan
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& python scripts/tracking/modules/find_cuts/find_cuts.py `
  --video $Video --config $Config --out $Out `
  --coarse $Coarse --fine $Fine --threshold $Threshold `
  --start $Start --end $End 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $logPath
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($code -ne 0) {
  Write-Host "[cuts] find_cuts.py упал (exit=$code). Последние строки лога:" -ForegroundColor Red
  Get-Content $logPath -Tail 40
  throw "find_cuts.py упал"
}

$size = (Get-ChildItem $Out -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("[cuts] reports весит {0:N1} MB" -f $size) -ForegroundColor Cyan
if ($size -gt 50) {
  Write-Warning "reports больше 50 MB. Не коммичу."
  return
}

if ($NoPush) {
  Write-Host "[ok] локально готово (no-push). Reports: $Out" -ForegroundColor Green
  return
}

git add $Out
$msg = "find_cuts: scan $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git commit -m $msg | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "[cuts] нечего коммитить" -ForegroundColor Yellow
  return
}

Write-Host "[cuts] git push..." -ForegroundColor Cyan
git push

Write-Host "[ok] готово. Скажи агенту: 'посмотри scripts/tracking/modules/find_cuts/reports/cuts.txt'" -ForegroundColor Green
