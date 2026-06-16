param(
    [Parameter(Mandatory=$true)][string]$Weights,
    [Parameter(Mandatory=$true)][string]$Source,
    [string]$Out = ".\outputs\slot_predictions",
    [int]$ImgSize = 960,
    [double]$Conf = 0.25,
    [double]$Iou = 0.45,
    [string]$Device = "auto",
    [string]$ColorProfile = "..\..\configs\plate_detector\01KH2HK109T8T08WHXMJ1B4412.preset_colors.json"
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

python .\scripts\predict_slots.py `
    --weights $Weights `
    --source $Source `
    --out $Out `
    --imgsz $ImgSize `
    --conf $Conf `
    --iou $Iou `
    --device $Device `
    --color-profile $ColorProfile
