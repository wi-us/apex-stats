# Runs N variants of track_teams.py on one video and stores
# tracks_<tag>.json + run_<tag>.log under reports/matrix/.
# Default is sequential. Use -Parallel to run all via Start-Job.
param(
  [Parameter(Mandatory=$true)][string]$Video,
  [double]$Start = 0,
  [double]$End = -1,
  [int]$FrameStep = 0,
  [string]$Anchors = "scripts/tracking/modules/motion_detect/reports/motion_tracks.json",
  [string]$Eliminations = "scripts/tracking/modules/hud_read/reports/eliminations.json",
  [switch]$Sequential,
  # Параллельные джобы. Дефолт 6 (как раньше), потолок 15 — не больше,
  # чтобы не положить CPU/диск/память. -Sequential форсит 1.
  [int]$MaxJobs = 6,
  # Фильтр: если задан, прогоняем только варианты с такими тегами (через запятую).
  [string]$Only = ""
)
$ErrorActionPreference = "Stop"
# Параллельный режим по умолчанию; -Sequential форсирует последовательный.
$Parallel = -not $Sequential
if ($MaxJobs -lt 1) { $MaxJobs = 1 }
if ($MaxJobs -gt 15) {
  Write-Host "[matrix] -MaxJobs $MaxJobs > 15, clamping to 15" -ForegroundColor Yellow
  $MaxJobs = 15
}
if ($Sequential) { $MaxJobs = 1 }
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo

function Resolve-ExistingPath($label, $path) {
  if ($path -eq "" -or -not (Test-Path -LiteralPath $path)) {
    Write-Host "[matrix] $label не найден: $path" -ForegroundColor Red
    Write-Host "[matrix] Передайте корректный путь, например: -Video C:\path\to\game.mp4" -ForegroundColor Yellow
    exit 2
  }
  return (Resolve-Path -LiteralPath $path).Path
}

$Video = Resolve-ExistingPath "video" $Video
$Anchors = Resolve-ExistingPath "anchors" $Anchors
$Eliminations = Resolve-ExistingPath "eliminations" $Eliminations

$matrix = @(
  @{ tag = "baseline";          config = "scripts/tracking/modules/track_teams/configs/da.baseline.yaml" },
  @{ tag = "color_first";       config = "scripts/tracking/modules/track_teams/configs/da.color_first.yaml" },
  @{ tag = "hybrid";            config = "scripts/tracking/modules/track_teams/configs/da.hybrid.yaml" },
  @{ tag = "detect_first";      config = "scripts/tracking/modules/track_teams/configs/da.detect_first.yaml" },
  @{ tag = "motion_first";      config = "scripts/tracking/modules/track_teams/configs/da.motion_first.yaml" },
  @{ tag = "strict_shape";      config = "scripts/tracking/modules/track_teams/configs/da.strict_shape.yaml" },
  # === Sweep вокруг color_first (победитель matrix-7/8) ===
  @{ tag = "cf_block_color";    config = "scripts/tracking/modules/track_teams/configs/da.cf_block_color.yaml" },
  @{ tag = "cf_tight_gate";     config = "scripts/tracking/modules/track_teams/configs/da.cf_tight_gate.yaml" },
  @{ tag = "cf_strong_hyst";    config = "scripts/tracking/modules/track_teams/configs/da.cf_strong_hyst.yaml" },
  @{ tag = "cf_sanity_strict";  config = "scripts/tracking/modules/track_teams/configs/da.cf_sanity_strict.yaml" },
  @{ tag = "cf_sanity_loose";   config = "scripts/tracking/modules/track_teams/configs/da.cf_sanity_loose.yaml" },
  @{ tag = "cf_confirm_long";   config = "scripts/tracking/modules/track_teams/configs/da.cf_confirm_long.yaml" },
  @{ tag = "cf_lategame_aggro"; config = "scripts/tracking/modules/track_teams/configs/da.cf_lategame_aggro.yaml" },
  @{ tag = "cf_lategame_early"; config = "scripts/tracking/modules/track_teams/configs/da.cf_lategame_early.yaml" },
  @{ tag = "cf_dense_step";     config = "scripts/tracking/modules/track_teams/configs/da.cf_dense_step.yaml" }
)

if ($Only -ne "") {
  $wanted = $Only -split "[,\s]+" | Where-Object { $_ -ne "" }
  $matrix = $matrix | Where-Object { $wanted -contains $_.tag }
  if (-not $matrix) { Write-Host "[matrix] -Only '$Only' не совпало ни с одним тегом" -ForegroundColor Red; exit 2 }
}

foreach ($m in $matrix) {
  $m.config = Resolve-ExistingPath "config '$($m.tag)'" $m.config
}

$outDir = "scripts/tracking/modules/track_teams/reports/matrix"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

foreach ($m in $matrix) {
  Remove-Item -LiteralPath "$outDir/tracks_$($m.tag).json" -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath "$outDir/tracks_$($m.tag).slots.json" -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath "$outDir/run_$($m.tag).log" -ErrorAction SilentlyContinue
}

function Invoke-One($tag, $config) {
  $out = "$outDir/tracks_$tag.json"
  $log = "$outDir/run_$tag.log"
  $extra = ""
  if ($FrameStep -gt 0) { $extra = " --frame-step $FrameStep" }
  # Запускаем через cmd /c, чтобы stderr был обычным текстом и PS не красил его в красное.
  $cmd = "python scripts/tracking/modules/track_teams/track_teams.py --video `"$Video`" --config `"$config`" --out `"$out`" --start $Start --end $End --anchors `"$Anchors`" --eliminations `"$Eliminations`"$extra 2>&1"
  Write-Host "[matrix] $tag -> $out" -ForegroundColor Cyan
  $ErrorActionPreference = "Continue"
  cmd /c $cmd | Tee-Object -FilePath $log
  $ErrorActionPreference = "Stop"
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[matrix] $tag FAILED - see $log" -ForegroundColor Red
  } else {
    Write-Host "[matrix] $tag OK" -ForegroundColor Green
  }
}

if ($Parallel) {
  Write-Host "[matrix] $($matrix.Count) variants, MaxJobs=$MaxJobs" -ForegroundColor Yellow
  $jobs = @()
  foreach ($m in $matrix) {
    # Throttle: ждём, пока активных Running-джоб станет < MaxJobs.
    while (@($jobs | Where-Object { $_.State -eq 'Running' }).Count -ge $MaxJobs) {
      Start-Sleep -Milliseconds 500
    }
    Write-Host "[matrix] launch $($m.tag)" -ForegroundColor Cyan
    $jobs += Start-Job -Name $m.tag -ScriptBlock {
      param($repo, $tag, $config, $Video, $Start, $End, $FrameStep, $Anchors, $Eliminations, $outDir)
      Set-Location $repo
      $env:PYTHONUTF8 = "1"
      $out = "$outDir/tracks_$tag.json"
      $log = "$outDir/run_$tag.log"
      $extra = ""
      if ($FrameStep -gt 0) { $extra = " --frame-step $FrameStep" }
      $cmd = "python scripts/tracking/modules/track_teams/track_teams.py --video `"$Video`" --config `"$config`" --out `"$out`" --start $Start --end $End --anchors `"$Anchors`" --eliminations `"$Eliminations`"$extra > `"$log`" 2>&1"
      cmd /c $cmd
    } -ArgumentList $repo, $m.tag, $m.config, $Video, $Start, $End, $FrameStep, $Anchors, $Eliminations, $outDir
  }
  Write-Host "[matrix] queued $($jobs.Count) jobs - waiting for completion..." -ForegroundColor Yellow
  $jobs | Wait-Job | Out-Null
  $jobs | Receive-Job
  $failed = @($jobs | Where-Object { $_.State -ne 'Completed' })
  $jobs | Remove-Job
} else {
  foreach ($m in $matrix) { Invoke-One $m.tag $m.config }
}

$missing = @($matrix | Where-Object { -not (Test-Path -LiteralPath "$outDir/tracks_$($_.tag).json") })
if ($missing.Count -gt 0) {
  Write-Host "[matrix] failed/missing outputs: $($missing.tag -join ', ')" -ForegroundColor Red
  Write-Host "[matrix] смотрите run_<tag>.log в $outDir" -ForegroundColor Yellow
  exit 1
}

# Summary table.
python scripts/tracking/modules/track_teams/compare_matrix.py --dir $outDir
