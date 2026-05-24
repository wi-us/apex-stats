# motion_detect: запустить motion_detect.py и (опционально) запушить reports/ в git.
#
# Использование (из корня репо):
#   # обычный запуск с push (для меня, чтобы Lovable-агент увидел report.txt):
#   powershell -ExecutionPolicy Bypass -File scripts/tracking/modules/motion_detect/push.ps1 -Video scripts/tracking/game.mp4
#
#   # без push (только локально):
#   powershell -ExecutionPolicy Bypass -File scripts/tracking/modules/motion_detect/run.ps1  -Video scripts/tracking/game.mp4

param(
  [Parameter(Mandatory=$true)][string]$Video,
  [string]$Cuts = "scripts/tracking/modules/find_cuts/reports/cuts.json",
  [string]$HsvPresets = "scripts/tracking/configs/hsv_presets.worlds-edge.json",
  [string]$Zones = "scripts/tracking/modules/motion_detect/configs/zones.vod.json",
  [string]$ZoneTag = "minimap",
  [double]$StartSec = 60,
  [int]$Window = 300,
  [int]$Step = 10,
  [double]$StaticThresh = 3,
  [double]$LinkDist = 80,
  [int]$DiffThresh = 12,
  [int]$ColorTol = 12,
  [int]$LooseH = 5,
  [int]$LooseSvDrop = 30,
  [double]$AgreeRadius = 0,
  [string]$Out = "scripts/tracking/modules/motion_detect/reports",
  [switch]$NoPush
)

$ErrorActionPreference = "Stop"
chcp 65001 > $null
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$callerDir = (Get-Location).Path
$repo = (git rev-parse --show-toplevel).Trim()
if (-not $repo) { throw "Не вижу git-репозитория." }

if (-not [System.IO.Path]::IsPathRooted($Video)) {
  $videoFromCaller = Join-Path $callerDir $Video
  $videoFromRepo = Join-Path $repo $Video
  if (Test-Path $videoFromCaller) {
    $Video = (Resolve-Path $videoFromCaller).Path
  } elseif (Test-Path $videoFromRepo) {
    $Video = (Resolve-Path $videoFromRepo).Path
  }
}

Set-Location $repo

if (Test-Path $Out) {
  Get-ChildItem $Out -Recurse -Force | Where-Object { $_.Name -ne ".gitkeep" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Out "overlays") | Out-Null

$logPath = Join-Path $Out "run.log"

Write-Host "[motion] запускаю motion_detect.py (win=$Window step=$Step static<$StaticThresh link=$LinkDist diff=$DiffThresh hue<$ColorTol h±$LooseH)..." -ForegroundColor Cyan
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& python scripts/tracking/modules/motion_detect/motion_detect.py `
  --video $Video `
  --cuts $Cuts `
  --hsv-presets $HsvPresets `
  --zones $Zones `
  --zone-tag $ZoneTag `
  --start-sec $StartSec `
  --window $Window --step $Step `
  --static-thresh $StaticThresh --link-dist $LinkDist `
  --diff-thresh $DiffThresh --color-tol $ColorTol `
  --loose-h $LooseH --loose-sv-drop $LooseSvDrop `
  --agree-radius $AgreeRadius `
  --out-dir $Out 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $logPath
$code = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($code -ne 0) {
  Write-Host "[motion] motion_detect.py упал (exit=$code). Последние строки лога:" -ForegroundColor Red
  Get-Content $logPath -Tail 40
  throw "motion_detect.py упал"
}

$size = (Get-ChildItem $Out -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("[motion] reports весит {0:N1} MB" -f $size) -ForegroundColor Cyan
if ($size -gt 50) {
  Write-Warning "reports больше 50 MB. Не коммичу. Уменьши -Window или сожми overlays."
  return
}

if ($NoPush) {
  Write-Host "[ok] локально готово (no-push). Reports: $Out" -ForegroundColor Green
  return
}

git add $Out
$msg = "motion: scan $(Get-Date -Format 'yyyy-MM-dd HH:mm') (win=$Window step=$Step static<$StaticThresh link=$LinkDist)"
git commit -m $msg | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "[motion] нечего коммитить" -ForegroundColor Yellow
  return
}

Write-Host "[motion] git push..." -ForegroundColor Cyan
git push

Write-Host "[ok] готово. Скажи агенту: 'посмотри scripts/tracking/modules/motion_detect/reports/report.txt'" -ForegroundColor Green
