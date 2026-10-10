"""Start the full Docker sitting.

  pwsh -File scripts/up.ps1
  pwsh -File scripts/up.ps1 -Build
"""

param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if ($Build) {
    docker compose up -d --build
} else {
    docker compose up -d
}

Write-Host ""
Write-Host "Sitting:"
Write-Host "  Operating Platform  http://127.0.0.1:8010"
Write-Host "  Gateway admin       http://127.0.0.1:8000/admin"
Write-Host "  Agent command       http://127.0.0.1:8001/dashboard"
Write-Host "  Eval explorer       http://127.0.0.1:8002/explorer"
Write-Host "  Twin                http://127.0.0.1:8003/"
Write-Host "  GraphRAG search     http://127.0.0.1:8100/search"
Write-Host ""
Write-Host "Optional GraphRAG reindex: docker compose exec graphrag python -m graphrag.cli reindex"
