$ErrorActionPreference = "Stop"

$script:DevDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepoRoot = Split-Path -Parent $script:DevDir
$script:TrackingRoot = Join-Path $script:RepoRoot "scripts\tracking"
$script:VenvActivate = Join-Path $script:TrackingRoot ".venv\Scripts\Activate.ps1"

function Enable-TrackingEnvironment {
    chcp 65001 > $null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    if (Test-Path -LiteralPath $script:VenvActivate) {
        & $script:VenvActivate
    }
    else {
        Write-Warning "Tracking venv not found: $script:VenvActivate"
        Write-Warning "Create it from scripts/tracking with: python -m venv .venv"
    }
}

function Resolve-DevVideoPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Video
    )

    if ([System.IO.Path]::IsPathRooted($Video)) {
        return $Video
    }

    $candidates = @(
        (Join-Path (Get-Location).Path $Video),
        (Join-Path $script:TrackingRoot $Video),
        (Join-Path $script:RepoRoot $Video)
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return (Join-Path $script:TrackingRoot $Video)
}

function Get-DevMatchId {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Video
    )
    return [System.IO.Path]::GetFileNameWithoutExtension($Video)
}

function Show-DevContext {
    param(
        [string]$Video = ""
    )

    Write-Host "[dev] repo:     $script:RepoRoot" -ForegroundColor DarkCyan
    Write-Host "[dev] tracking: $script:TrackingRoot" -ForegroundColor DarkCyan
    if ($Video) {
        $matchId = Get-DevMatchId -Video $Video
        Write-Host "[dev] video:    $Video" -ForegroundColor DarkCyan
        Write-Host "[dev] outputs:  scripts/tracking/matches/$matchId/" -ForegroundColor DarkCyan
    }
}
