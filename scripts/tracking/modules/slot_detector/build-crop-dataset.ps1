param(
    [string]$Images = "..\plate_detector\dataset_strict_ed_v5_tight\images\train",
    [string]$Labels = ".\outputs\relabeled_slots\labels\train",
    [string]$Out = ".\datasets\slot_crop_v1",
    [double]$ValRatio = 0.2,
    [double]$Padding = 0.18,
    [int]$Seed = 37
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

python .\scripts\build_slot_crop_dataset.py `
    --images $Images `
    --labels $Labels `
    --out $Out `
    --val-ratio $ValRatio `
    --padding $Padding `
    --seed $Seed
