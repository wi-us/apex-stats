param(
  [switch]$Force,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "scripts\algs_api\optimize_player_images.py"

$argsList = @($script)
if ($Force) {
  $argsList += "--force"
}
if ($ExtraArgs) {
  $argsList += $ExtraArgs
}

python @argsList
