[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$profileRoot = [Environment]::GetFolderPath("UserProfile")
$configPath = Join-Path $profileRoot ".gemini\config\deep-dev-update.json"
if (-not (Test-Path -LiteralPath $configPath)) { exit 0 }
$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
if (-not $config.auto_update -or [string]::IsNullOrWhiteSpace($config.manifest_url)) { exit 0 }
$updater = Join-Path $profileRoot ".gemini\config\skills\deep-dev\updater\deep_dev_update.py"
& py $updater --manifest-url $config.manifest_url --current-version $config.version --user-profile $profileRoot --python-executable py --auto-update
exit $LASTEXITCODE
