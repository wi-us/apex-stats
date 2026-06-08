param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ViteArgs
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

if ($ViteArgs.Count -gt 0) {
    & bun run dev:vite -- @ViteArgs
} else {
    & bun run dev:vite
}

exit $LASTEXITCODE
