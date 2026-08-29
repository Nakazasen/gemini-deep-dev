[CmdletBinding()]
param(
    [string]$ManifestUrl = "",
    [switch]$EnableAutoUpdate
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command py -ErrorAction SilentlyContinue)
if ($null -eq $python) { throw "Python Launcher (py) is required." }
$version = (Get-Content -Raw ($projectRoot + "\VERSION")).Trim()
$installer = Join-Path $PSScriptRoot "deep_dev_installer.py"
$bundle = Join-Path $projectRoot "bundle"

if ($ManifestUrl -and $EnableAutoUpdate) {
    & py $installer --bundle-root $bundle --version $version --python-executable py --manifest-url $ManifestUrl --enable-auto-update
} elseif ($ManifestUrl) {
    & py $installer --bundle-root $bundle --version $version --python-executable py --manifest-url $ManifestUrl
} elseif ($EnableAutoUpdate) {
    & py $installer --bundle-root $bundle --version $version --python-executable py --enable-auto-update
} else {
    & py $installer --bundle-root $bundle --version $version --python-executable py
}
