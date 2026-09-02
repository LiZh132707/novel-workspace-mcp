$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv sync
    uv run python ui/app.py
} else {
    Write-Host "uv 未安装，使用当前 Python 环境安装项目依赖..." -ForegroundColor Yellow
    python -m pip install -e .
    python ui/app.py
}
