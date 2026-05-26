# hud_read: скелет. Запустить hud_read.py и (опционально) запушить reports/.
param(
  [Parameter(Mandatory=$true)][string]$Video,
  [string]$Zones = "scripts/tracking/configs/zones.vod.json",
  [ValidateSet("forward","scout","two-pass")][string]$Mode = "forward",
  [int]$ReverseStep = 1800,
  [int]$RefineBudget = 10,
  [int]$RefineLinear = 4,
  [int]$RefineRollback = 0,
  [int]$RingScoutStep = 600,
  [int]$RingRefineBudget = 10,
  [int]$RingRefineLinear = 4,
  [double]$RingStartSec = 0,
  [double]$RingDebugSec = 0,
  [switch]$RingsOnly,
  [int]$Workers = 0,
  [int]$FrameStep = 600,
  [double]$StartSec = 0,
  [double]$EndSec = 0,
  [double]$IgnoreTailSec = 30,
  [string]$OcrLang = "eng",
  [string]$TessCmd = "",
  [int]$OverlayEvery = 1,
  [int]$CropFirstN = 3,
  [int]$StaticConfirm = 3,
  [int]$StaticMaxFrames = 8,
  [string]$Out = "scripts/tracking/modules/hud_read/reports",
  [switch]$DumpPts,
  [string]$DigitTemplates = "",
  [switch]$SyncUI,
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
if ($Workers -gt 0) {
  $script = "scripts/tracking/modules/hud_read/orchestrate.py"
  $modeArg = if ($Mode -eq "scout") { "two-pass" } else { $Mode }
  $argsList = @(
    $script,
    "--video", $Video,
    "--zones", $Zones,
    "--workers", $Workers,
    "--mode", $modeArg,
    "--reverse-step", $ReverseStep,
    "--refine-budget", $RefineBudget,
    "--refine-linear", $RefineLinear,
    "--refine-rollback", $RefineRollback,
    "--frame-step", $FrameStep,
    "--start-sec", $StartSec,
    "--end-sec", $EndSec,
    "--ignore-tail-sec", $IgnoreTailSec,
    "--ocr-lang", $OcrLang,
    "--overlay-every", $OverlayEvery,
    "--crop-first-n", $CropFirstN,
    "--static-confirm", $StaticConfirm,
    "--static-max-frames", $StaticMaxFrames,
    "--out", $Out
  )
} else {
  $argsList = @(
    "scripts/tracking/modules/hud_read/hud_read.py",
    "--video", $Video,
    "--zones", $Zones,
    "--mode", $Mode,
    "--reverse-step", $ReverseStep,
    "--refine-budget", $RefineBudget,
    "--refine-linear", $RefineLinear,
    "--refine-rollback", $RefineRollback,
    "--ring-scout-step", $RingScoutStep,
    "--ring-refine-budget", $RingRefineBudget,
    "--ring-refine-linear", $RingRefineLinear,
    "--ring-start-sec", $RingStartSec,
    "--ring-debug-sec", $RingDebugSec,
    "--frame-step", $FrameStep,
    "--start-sec", $StartSec,
    "--end-sec", $EndSec,
    "--ignore-tail-sec", $IgnoreTailSec,
    "--ocr-lang", $OcrLang,
    "--overlay-every", $OverlayEvery,
    "--crop-first-n", $CropFirstN,
    "--static-confirm", $StaticConfirm,
    "--static-max-frames", $StaticMaxFrames,
    "--out", $Out
  )
  if ($RingsOnly) { $argsList += "--rings-only" }
}
if ($TessCmd) { $argsList += @("--tess-cmd", $TessCmd) }
if ($DumpPts) { $argsList += "--dump-pts" }
if ($DigitTemplates) { $argsList += @("--digit-templates", $DigitTemplates) }
& python @argsList
if ($LASTEXITCODE -ne 0) { throw "hud_read упал (rc=$LASTEXITCODE)" }
if ($SyncUI) {
  & python "scripts/tracking/modules/hud_read/sync_to_ui.py" --reports $Out
  if ($LASTEXITCODE -ne 0) { throw "sync_to_ui упал (rc=$LASTEXITCODE)" }
}
if ($NoPush) { return }
git add $Out
git commit -m "hud_read: run $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-Null
if ($LASTEXITCODE -eq 0) { git push }
