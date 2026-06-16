param(
    [Parameter(Mandatory=$true)][string]$Weights,
    [Parameter(Mandatory=$true)][string]$Images,
    [Parameter(Mandatory=$true)][string]$Labels,
    [Parameter(Mandatory=$true)][string]$OutLabels,
    [int]$ImgSize = 960,
    [double]$Conf = 0.25,
    [double]$Iou = 0.45,
    [double]$MatchIou = 0.35,
    [string]$Device = "auto",
    [ValidateSet("drop", "keep-old", "keep-class")]
    [string]$UnmatchedAction = "drop",
    [int]$MaxPerSlot = 3,
    [string]$OutVisuals = "",
    [string]$ColorProfile = "..\..\configs\plate_detector\01KH2HK109T8T08WHXMJ1B4412.preset_colors.json",
    [switch]$DrawRejected
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

$argsList = @(
    ".\scripts\relabel_team_plate_dataset.py",
    "--weights", $Weights,
    "--images", $Images,
    "--labels", $Labels,
    "--out-labels", $OutLabels,
    "--imgsz", $ImgSize,
    "--conf", $Conf,
    "--iou", $Iou,
    "--match-iou", $MatchIou,
    "--device", $Device,
    "--unmatched-action", $UnmatchedAction,
    "--max-per-slot", $MaxPerSlot,
    "--color-profile", $ColorProfile
)

if ($OutVisuals -ne "") {
    $argsList += @("--out-visuals", $OutVisuals)
}
if ($DrawRejected) {
    $argsList += "--draw-rejected"
}

python @argsList
