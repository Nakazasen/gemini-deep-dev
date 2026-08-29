[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$ManifestUrl
)

$ErrorActionPreference = "Stop"
if ($ManifestUrl -notmatch '^https://') { throw "Auto-update accepts HTTPS manifests only." }
$profileRoot = [Environment]::GetFolderPath("UserProfile")
$configPath = Join-Path $profileRoot ".gemini\config\deep-dev-update.json"
if (-not (Test-Path -LiteralPath $configPath)) { throw "Install Deep Dev first." }
$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$config.manifest_url = $ManifestUrl
$config.auto_update = $true
$config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding utf8
$scriptPath = Join-Path $profileRoot ".gemini\config\skills\deep-dev\updater\AutoUpdate-DeepDev.ps1"
$startup = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startup "Gemini Deep Dev Auto Update.cmd"
@"
@echo off
start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$scriptPath" >nul 2>&1
"@ | Set-Content -LiteralPath $launcher -Encoding ascii
Write-Output "Auto-update enabled. It checks after Windows sign-in without requiring administrator permission. Restart Antigravity after an installed update."
