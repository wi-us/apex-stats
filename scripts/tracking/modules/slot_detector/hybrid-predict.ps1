param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$ClassifierWeights,
    [string]$DetectorWeights = "..\plate_detector\runs\detect\runs\team_plate_v2_hardneg_cpu\weights\best.pt",
    [string]$Out = ".\outputs\hybrid_slots",
    [int]$DetImgSize = 960,
    [int]$ClsImgSize = 96,
    [double]$DetConf = 0.25,
    [double]$DetIou = 0.45,
    [double]$SlotMinConf = 0.28,
    [double]$Padding = 0.18,
    [double]$SampleFps = 1.0,
    [int]$MaxPerSlot = 3,
    [string]$Device = "auto",
    [string]$ColorProfile = "..\..\configs\plate_detector\01KH2HK109T8T08WHXMJ1B4412.preset_colors.json",
    [switch]$SaveCrops
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

$argsList = @(
    ".\scripts\hybrid_slot_pipeline.py",
    "--source", $Source,
    "--detector-weights", $DetectorWeights,
    "--classifier-weights", $ClassifierWeights,
    "--out", $Out,
    "--det-imgsz", $DetImgSize,
    "--cls-imgsz", $ClsImgSize,
    "--det-conf", $DetConf,
    "--det-iou", $DetIou,
    "--slot-min-conf", $SlotMinConf,
    "--padding", $Padding,
    "--sample-fps", $SampleFps,
    "--max-per-slot", $MaxPerSlot,
    "--device", $Device,
    "--color-profile", $ColorProfile
)

if ($SaveCrops) {
    $argsList += "--save-crops"
}

python @argsList
