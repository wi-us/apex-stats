param(
    [Parameter(Mandatory = $true)]
    [string]$Video,

    [int]$Coarse = 300,
    [int]$Fine = 10,
    [double]$Threshold = 90,
    [double]$Start = 0,
    [double]$End = -1
)

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "_common.ps1")

Enable-TrackingEnvironment
$videoPath = Resolve-DevVideoPath -Video $Video
Show-DevContext -Video $videoPath

$runner = Join-Path $script:RepoRoot "scripts\tracking\modules\find_cuts\push.ps1"
& $runner `
    -Video $videoPath `
    -Coarse $Coarse `
    -Fine $Fine `
    -Threshold $Threshold `
    -Start $Start `
    -End $End `
    -NoPush
