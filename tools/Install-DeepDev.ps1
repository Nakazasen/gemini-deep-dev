[CmdletBinding()]
param(
    [string]$ManifestUrl = "https://raw.githubusercontent.com/Nakazasen/gemini-deep-dev/main/release/update.json",
    [switch]$DisableAutoUpdate
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command py -ErrorAction SilentlyContinue)
if ($null -eq $python) { throw "Python Launcher (py) is required." }
$version = (Get-Content -Raw ($projectRoot + "\VERSION")).Trim()
$installer = Join-Path $PSScriptRoot "deep_dev_installer.py"
$bundle = Join-Path $projectRoot "bundle"

if ($DisableAutoUpdate) {
    & py $installer --bundle-root $bundle --version $version --python-executable py --manifest-url $ManifestUrl --disable-auto-update
} else {
    & py $installer --bundle-root $bundle --version $version --python-executable py --manifest-url $ManifestUrl
    if ($LASTEXITCODE -eq 0) {
        & (Join-Path $PSScriptRoot "Enable-AutoUpdate.ps1") -ManifestUrl $ManifestUrl
    }
}
