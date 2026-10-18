$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

Write-Host "Setup complete. Run scripts\run.ps1" -ForegroundColor Green
