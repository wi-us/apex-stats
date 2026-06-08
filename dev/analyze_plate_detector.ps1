param(
    [Parameter(Mandatory = $true)]
    [string]$Video,

    [string]$Game = "m-test-g1",
    [string]$RunName = "",
    [string]$SeriesId = "01KH2HK109T8T08WHXMJ1B4412",
    [int]$MatchNumber = 5,
    [string]$Device = "cpu",
    [double]$SampleFps = 1.0,
    [switch]$SyncToUi,
    [switch]$WriteSlotTags
)

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "_common.ps1")

Enable-TrackingEnvironment
$videoPath = Resolve-DevVideoPath -Video $Video
Show-DevContext -Video $videoPath

$runner = Join-Path $script:TrackingRoot "run_plate_detector.ps1"
& $runner `
    -Video $videoPath `
    -Game $Game `
    -RunName $RunName `
    -SeriesId $SeriesId `
    -MatchNumber $MatchNumber `
    -Device $Device `
    -SampleFps $SampleFps `
    -SyncToUi:$SyncToUi `
    -WriteSlotTags:$WriteSlotTags
