param(
    [Parameter(Mandatory = $true)]
    [string]$Video,

    [ValidateSet("forward", "scout", "two-pass")]
    [string]$Mode = "forward",
    [int]$FrameStep = 600,
    [double]$StartSec = 0,
    [double]$EndSec = 0,
    [int]$Workers = 0,
    [switch]$RingsOnly,
    [switch]$SyncToUi
)

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "_common.ps1")

Enable-TrackingEnvironment
$videoPath = Resolve-DevVideoPath -Video $Video
Show-DevContext -Video $videoPath

$runner = Join-Path $script:RepoRoot "scripts\tracking\modules\hud_read\push.ps1"
$argsList = @(
    "-Video", $videoPath,
    "-Mode", $Mode,
    "-FrameStep", $FrameStep,
    "-StartSec", $StartSec,
    "-EndSec", $EndSec,
    "-Workers", $Workers,
    "-NoPush"
)
if ($RingsOnly) { $argsList += "-RingsOnly" }
if ($SyncToUi) { $argsList += "-SyncUI" }

& $runner @argsList
