param(
    [string]$SourceZip = "..\plate_detector\_archive\2026-05-29\source-bundles\plate_detector.zip",
    [string]$Out = ".\datasets\slot_plates_v1"
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

python .\scripts\prepare_slot_dataset.py --source-zip $SourceZip --out $Out
