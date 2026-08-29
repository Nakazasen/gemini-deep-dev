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
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At 09:00
Register-ScheduledTask -TaskName "Gemini Deep Dev Auto Update" -Action $action -Trigger $trigger -Force | Out-Null
Write-Output "Auto-update enabled. A restart of Antigravity is still required after an installed update."
