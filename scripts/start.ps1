# Windows dev / terminal launcher for Anthill (source checkout).
#
# This is the Windows equivalent of running `python -m uvicorn` by hand: it creates a venv,
# installs the package editable, and serves the dashboard on http://127.0.0.1:8000. The
# no-terminal, double-click path on every OS is the Tauri desktop app (see the cross-platform
# plan, Phase 2); this script just covers the developer/terminal path on Windows the way the
# bash flows cover it on macOS/Linux.
#
# Usage (PowerShell):  ./scripts/start.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& ".\.venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip | Out-Null
pip install -e . | Out-Null

python -m uvicorn anthill.web.app:app --host 127.0.0.1 --port 8000
