[CmdletBinding()]
param(
    [string]$PackageUrl = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$version = (Get-Content -Raw (Join-Path $projectRoot "VERSION")).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "VERSION must use semantic versioning." }
& py (Join-Path $PSScriptRoot "generate_integrity.py")
if ($LASTEXITCODE -ne 0) { throw "Could not generate integrity registry." }
$dist = Join-Path $projectRoot "dist"
$stage = Join-Path $dist "gemini-deep-dev-v$version"
$archive = Join-Path $dist "gemini-deep-dev-v$version.zip"
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "bundle") -Destination $stage -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "tools") -Destination $stage -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "VERSION"), (Join-Path $projectRoot "README.md") -Destination $stage -Force
Compress-Archive -Path $stage -DestinationPath $archive -CompressionLevel Optimal
$sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLower()
if (-not $PackageUrl) { $PackageUrl = "https://github.com/Nakazasen/gemini-deep-dev/releases/download/v$version/gemini-deep-dev-v$version.zip" }
$manifest = [ordered]@{
    schema_version = 1
    latest_version = $version
    package_url = $PackageUrl
    sha256 = $sha256
    release_notes = "Release v$version"
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot "release\update.json") -Encoding utf8
Write-Output "Built $archive"
Write-Output "SHA-256: $sha256"
