$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv sync
    uv run novel-workspace serve
} else {
    Write-Host "uv was not found; installing the project into the current Python environment..." -ForegroundColor Yellow
    python -m pip install -e .
    novel-workspace serve
}
