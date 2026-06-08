# detect_plates: OpenCV-детектор плашек на миникарте + temporal recovery.
#   powershell -ExecutionPolicy Bypass -File scripts\tracking\modules\detect_plates\run.ps1 -Video scripts\tracking\game_sp.mp4
param(
  [Parameter(Mandatory=$true)][string]$Video,
  [string]$Hsv     = "scripts/tracking/configs/hsv_presets.storm-point.json",
  [string]$Zones   = "scripts/tracking/configs/zones.vod.json",
  [string]$Out     = "",
  [double]$SampleFps = 1.0,
  [int]$MaxFrames  = 0,
  [switch]$NoRecovery,
  [switch]$SaveDebug,
  [int]$DebugEvery = 1,
  [switch]$NoSeek,
  [switch]$Seek,
  [string]$Hwaccel = "auto",
  [double]$TrackFps    = 5.0,
  [double]$AdaptiveFps = 5.0,
  [switch]$NoSlots
)
$ErrorActionPreference = "Stop"
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo

if ($Out -eq "") {
  $matchId = [IO.Path]::GetFileNameWithoutExtension($Video)
  $Out = "scripts/tracking/matches/$matchId/detect_plates"
}

$pyArgs = @(
  "scripts/tracking/modules/detect_plates/detect_plates.py",
  "--video", $Video,
  "--hsv-presets", $Hsv,
  "--zones", $Zones,
  "--out", $Out,
  "--sample-fps", $SampleFps,
  "--h-tol", 1, "--s-tol", 6, "--v-tol", 14,
  "--loose-h-extra", 1, "--loose-s-extra", 12, "--loose-v-extra", 20,
  "--ignore-bottom-px", 105, "--target-plate-height", 30,
  "--max-expand-x", 22, "--max-width", 220
)
if ($MaxFrames -gt 0) { $pyArgs += @("--max-frames", $MaxFrames) }
if (-not $NoRecovery) { $pyArgs += "--recovery" }
if ($SaveDebug)       { $pyArgs += @("--save-debug", "--debug-every", $DebugEvery) }
if ($NoSeek)          { $pyArgs += "--no-seek" }
if ($Seek)            { $pyArgs += "--seek" }
if ($Hwaccel)         { $pyArgs += @("--hwaccel", $Hwaccel) }
if ($TrackFps -gt 0)  { $pyArgs += @("--track-fps", $TrackFps) }
if ($AdaptiveFps -gt 0) { $pyArgs += @("--adaptive-fps", $AdaptiveFps) }
if (-not $NoSlots)    { $pyArgs += "--emit-slots" }

python @pyArgs
