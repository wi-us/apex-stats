param(
    [string]$Data = ".\datasets\slot_crop_v1",
    [string]$Model = "yolov8n-cls.pt",
    [int]$Epochs = 60,
    [int]$ImgSize = 96,
    [int]$Batch = 64,
    [string]$Device = "auto",
    [string]$Name = "slot_crop_classifier_v1"
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

python .\scripts\train_slot_classifier.py `
    --data $Data `
    --model $Model `
    --epochs $Epochs `
    --imgsz $ImgSize `
    --batch $Batch `
    --device $Device `
    --project ".\runs\classify" `
    --name $Name
