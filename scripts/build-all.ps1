"""Build every layer image and (optionally) the OS web dist.

Usage (repo root):
  pwsh -File scripts/build-all.ps1
  pwsh -File scripts/build-all.ps1 -LocalUi
  pwsh -File scripts/build-all.ps1 -NoDocker
"""

param(
    [switch]$LocalUi,
    [switch]$NoDocker
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ($LocalUi -or $NoDocker) {
    Write-Host "Building Operating Platform web dist..."
    Push-Location (Join-Path $Root "Enterprise_AI_Platform\web")
    if (-not (Test-Path "node_modules")) { npm ci }
    npm run build
    Pop-Location
}

if (-not $NoDocker) {
    Write-Host "Building Docker images (docker compose build)..."
    docker compose build
    Write-Host "Images:"
    docker compose images
}

Write-Host "Build complete."
