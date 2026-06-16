param(
    [string]$Images = "..\plate_detector\dataset_strict_ed_v5_tight\images\train",
    [string]$Labels = ".\outputs\relabeled_slots\labels\train",
    [string]$Out = ".\datasets\slot_plates_relabel_v1",
    [double]$ValRatio = 0.2,
    [int]$Seed = 37,
    [switch]$IncludeEmpty
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

$argsList = @(
    ".\scripts\build_relabel_dataset.py",
    "--images", $Images,
    "--labels", $Labels,
    "--out", $Out,
    "--val-ratio", $ValRatio,
    "--seed", $Seed
)

if ($IncludeEmpty) {
    $argsList += "--include-empty"
}

python @argsList
