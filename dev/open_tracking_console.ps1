$ErrorActionPreference = "Stop"

$DevDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $DevDir
$TrackingRoot = Join-Path $RepoRoot "scripts\tracking"
$Activate = Join-Path $TrackingRoot ".venv\Scripts\Activate.ps1"

$ps = Get-Command pwsh -ErrorAction SilentlyContinue
if ($ps) {
    $PowerShellExe = $ps.Source
}
else {
    $PowerShellExe = "powershell.exe"
}

$trackingEsc = $TrackingRoot.Replace("'", "''")
$activateEsc = $Activate.Replace("'", "''")

$command = @"
`$ErrorActionPreference = 'Stop'
chcp 65001 > `$null
Set-Location -LiteralPath '$trackingEsc'
if (Test-Path -LiteralPath '$activateEsc') {
    & '$activateEsc'
    Write-Host '[dev] tracking venv activated' -ForegroundColor Green
} else {
    Write-Warning 'Tracking venv not found: $activateEsc'
}
Write-Host '[dev] cwd:' (Get-Location).Path -ForegroundColor Cyan
Write-Host '[dev] try: .\run_plate_detector.ps1 -Video modules\plate_detector\videos\<vod>.mp4 -SyncToUi' -ForegroundColor DarkGray
"@

Start-Process -FilePath $PowerShellExe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", $command
)
