param(
    [Parameter(Mandatory = $true)]
    [string]$Video,

    [string]$Game = "m-test-g1",
    [string]$RunName = "",
    [string]$SeriesId = "01KH2HK109T8T08WHXMJ1B4412",
    [int]$MatchNumber = 5,
    [string]$Device = "cpu",
    [double]$SampleFps = 1.0,
    [string]$Weights = "",
    [string]$Config = "",
    [string]$HsvPresets = "",
    [string]$ColorProfiles = "",
    [string]$PoiPriors = "",
    [switch]$SkipDetection,
    [switch]$SkipTracks,
    [switch]$SkipColorProfiles,
    [switch]$SkipPoiPriors,
    [switch]$SyncToUi,
    [switch]$WriteSlotTags
)

$ErrorActionPreference = "Stop"
$ModuleRoot = $PSScriptRoot
$TrackingRoot = Split-Path (Split-Path $ModuleRoot -Parent) -Parent
$ConfigRoot = Join-Path $TrackingRoot "configs"
$MatchesRoot = Join-Path $TrackingRoot "matches"
$Python = Join-Path $TrackingRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

function Resolve-ModulePath([string]$PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return (Join-Path $ModuleRoot $PathValue)
}

function Resolve-TrackingPath([string]$PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return (Join-Path $TrackingRoot $PathValue)
}

function Get-SafeName([string]$NameValue) {
    $invalid = [System.IO.Path]::GetInvalidFileNameChars()
    $chars = $NameValue.ToCharArray() | ForEach-Object {
        if ($invalid -contains $_) { "_" } else { $_ }
    }
    return (-join $chars).Trim()
}

$Video = Resolve-ModulePath $Video
$MatchId = Get-SafeName ([System.IO.Path]::GetFileNameWithoutExtension($Video))
if ($RunName -eq "") {
    $RunName = $MatchId
}

if ($Config -eq "") {
    $Config = Join-Path $ConfigRoot "plate_detector\$SeriesId.json"
}
if ($HsvPresets -eq "") {
    $HsvPresets = Join-Path $ConfigRoot "hsv_presets.storm-point.json"
}
if ($ColorProfiles -eq "") {
    $ColorProfiles = Join-Path $MatchesRoot "$RunName\plate_detector\color_profiles.json"
}
if ($PoiPriors -eq "") {
    $PoiPriors = Join-Path $MatchesRoot "$RunName\plate_detector\poi_priors.json"
}
if ($Weights -eq "") {
    $candidateWeights = @(
        "runs\detect\runs\team_plate_v2_hardneg_cpu\weights\best.pt",
        "runs\detect\team_plate_v2_hardneg_cpu\weights\best.pt",
        "yolo11n.pt"
    )
    foreach ($candidate in $candidateWeights) {
        if (Test-Path -LiteralPath (Resolve-ModulePath $candidate)) {
            $Weights = $candidate
            break
        }
    }
}
if ($Weights -eq "") {
    throw "Weights not found. Pass -Weights or place a model under runs\...\weights\best.pt."
}

$Config = Resolve-TrackingPath $Config
$HsvPresets = Resolve-TrackingPath $HsvPresets
$ColorProfiles = Resolve-TrackingPath $ColorProfiles
$PoiPriors = Resolve-TrackingPath $PoiPriors
$Weights = Resolve-ModulePath $Weights
$OutDir = Join-Path $MatchesRoot "$RunName\plate_detector"
$Detections = Join-Path $OutDir "detections.csv"
$Tracks = Join-Path $OutDir "tracks.json"

if (-not (Test-Path -LiteralPath $Video)) {
    throw "Video not found: $Video"
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "Config not found: $Config"
}
if ((-not $SkipDetection) -and (-not (Test-Path -LiteralPath $Weights))) {
    throw "Weights not found: $Weights"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Push-Location $ModuleRoot
try {
    if (-not $SkipColorProfiles) {
        & $Python "scripts\build_color_profiles_from_hsv_presets.py" `
            --config $Config `
            --hsv-presets $HsvPresets `
            --out $ColorProfiles
    }

    if (-not $SkipPoiPriors) {
        $DbPath = Join-Path $ModuleRoot "data\algs.sqlite"
        if (Test-Path -LiteralPath $DbPath) {
            & $Python "scripts\export_poi_priors.py" `
                --db $DbPath `
                --series-id $SeriesId `
                --match-number $MatchNumber `
                --config $Config `
                --out $PoiPriors
        }
        else {
            Write-Warning "Skipping POI priors; DB not found: $DbPath"
        }
    }

    if (-not $SkipDetection) {
        & $Python "scripts\identify_match_plates.py" analyze-video `
            --video $Video `
            --weights $Weights `
            --config $Config `
            --out $OutDir `
            --color-profiles $ColorProfiles `
            --hsv-presets $HsvPresets `
            --prefer-presets `
            --sample-fps $SampleFps `
            --device $Device
    }

    if (-not $SkipTracks) {
        $trackArgs = @(
            "scripts\build_tracks_from_detections.py",
            "--detections", $Detections,
            "--out", $Tracks,
            "--config", $Config,
            "--color-profiles", $ColorProfiles,
            "--video-name", (Split-Path $Video -Leaf)
        )
        if (Test-Path -LiteralPath $PoiPriors) {
            $trackArgs += @("--poi-priors", $PoiPriors)
        }
        & $Python @trackArgs
    }

    if ($SyncToUi) {
        $syncArgs = @(
            "sync_to_ui.py",
            "--tracks", $Tracks,
            "--game", $Game,
            "--copy-reports"
        )
        if ($WriteSlotTags) {
            $syncArgs += "--write-slot-tags"
        }
        & $Python @syncArgs
    }
}
finally {
    Pop-Location
}
