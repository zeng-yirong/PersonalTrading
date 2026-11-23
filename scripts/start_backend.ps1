$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = $null
foreach ($candidate in @(".venv\Scripts\python.exe", "venv\Scripts\python.exe")) {
    if (Test-Path $candidate) { $python = $candidate; break }
}
if (-not $python) { $python = "python" }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Host "Starting PersonalTrading backend on http://127.0.0.1:8010"
& $python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8010