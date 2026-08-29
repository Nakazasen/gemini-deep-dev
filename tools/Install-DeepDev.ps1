[CmdletBinding()]
param(
    [string]$ManifestUrl = "",
    [switch]$EnableAutoUpdate
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command py -ErrorAction SilentlyContinue)
if ($null -eq $python) { throw "Python Launcher (py) is required." }
$args = @($PSScriptRoot + "\deep_dev_installer.py", "--bundle-root", $projectRoot + "\bundle", "--version", (Get-Content -Raw ($projectRoot + "\VERSION")).Trim(), "--python-executable", "py")
if ($ManifestUrl) { $args += @("--manifest-url", $ManifestUrl) }
if ($EnableAutoUpdate) { $args += "--enable-auto-update" }
& py @args
