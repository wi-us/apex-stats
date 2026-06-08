. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "_common.ps1")

Enable-TrackingEnvironment
Set-Location -LiteralPath $script:TrackingRoot
Show-DevContext

Write-Host "[dev] venv is active in this console scope if the script was dot-sourced:" -ForegroundColor Yellow
Write-Host "      . .\dev\enter_tracking_venv.ps1" -ForegroundColor Yellow
