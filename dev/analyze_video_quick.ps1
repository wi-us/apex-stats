param(
    [Parameter(Mandatory = $true)]
    [string]$Video,

    [string]$Game = "m-test-g1",
    [string]$Device = "cpu",
    [double]$PlateSampleFps = 1.0,
    [switch]$SkipCuts,
    [switch]$SkipPlateDetector,
    [switch]$RunHud,
    [switch]$SyncToUi
)

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "_common.ps1")

Enable-TrackingEnvironment
$videoPath = Resolve-DevVideoPath -Video $Video
Show-DevContext -Video $videoPath

if (-not $SkipCuts) {
    Write-Host "[dev] stage: find_cuts" -ForegroundColor Cyan
    & (Join-Path $script:DevDir "analyze_cuts.ps1") -Video $videoPath
}

if (-not $SkipPlateDetector) {
    Write-Host "[dev] stage: plate_detector" -ForegroundColor Cyan
    & (Join-Path $script:DevDir "analyze_plate_detector.ps1") `
        -Video $videoPath `
        -Game $Game `
        -Device $Device `
        -SampleFps $PlateSampleFps `
        -SyncToUi:$SyncToUi
}

if ($RunHud) {
    Write-Host "[dev] stage: hud_read" -ForegroundColor Cyan
    & (Join-Path $script:DevDir "analyze_hud.ps1") `
        -Video $videoPath `
        -SyncToUi:$SyncToUi
}
