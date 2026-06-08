param(
    [Parameter(Mandatory = $true)]
    [string]$Video,

    [double]$SampleFps = 1.0,
    [int]$MaxFrames = 0,
    [switch]$SaveDebug,
    [switch]$NoRecovery
)

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "_common.ps1")

Enable-TrackingEnvironment
$videoPath = Resolve-DevVideoPath -Video $Video
Show-DevContext -Video $videoPath

$runner = Join-Path $script:RepoRoot "scripts\tracking\modules\detect_plates\run.ps1"
$argsList = @(
    "-Video", $videoPath,
    "-SampleFps", $SampleFps,
    "-MaxFrames", $MaxFrames
)
if ($SaveDebug) { $argsList += "-SaveDebug" }
if ($NoRecovery) { $argsList += "-NoRecovery" }

& $runner @argsList
