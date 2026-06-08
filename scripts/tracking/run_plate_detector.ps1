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

$ErrorActionPreference = "Stop"
$ModuleRun = Join-Path $PSScriptRoot "modules\plate_detector\run.ps1"
if (-not [System.IO.Path]::IsPathRooted($Video)) {
    $Video = Join-Path $PSScriptRoot $Video
}

& $ModuleRun `
    -Video $Video `
    -Game $Game `
    -RunName $RunName `
    -SeriesId $SeriesId `
    -MatchNumber $MatchNumber `
    -Device $Device `
    -SampleFps $SampleFps `
    -SyncToUi:$SyncToUi `
    -WriteSlotTags:$WriteSlotTags
