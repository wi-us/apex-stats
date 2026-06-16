param(
    [string]$Data = ".\datasets\slot_plates_v1\data.yaml",
    [string]$Model = "yolov8n.pt",
    [int]$Epochs = 80,
    [int]$ImgSize = 960,
    [int]$Batch = 4,
    [string]$Device = "auto",
    [string]$Name = "slot_plate_v1"
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

python .\scripts\train_slot_yolo.py `
    --data $Data `
    --model $Model `
    --epochs $Epochs `
    --imgsz $ImgSize `
    --batch $Batch `
    --device $Device `
    --project ".\runs\detect" `
    --name $Name
