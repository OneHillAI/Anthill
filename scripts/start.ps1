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

# This launcher is the person at this machine: it listens on loopback only and marks the server local-only
# (see CONTRIBUTING.md). An ANTHILL_LOCAL_ONLY you already set is honoured, so set it to 0 if this sits behind
# a proxy or tunnel. The variables are set for this run only and put back afterwards.
$previousHost = $env:ANTHILL_HOST
$previousLocal = $env:ANTHILL_LOCAL_ONLY
try {
    $env:ANTHILL_HOST = "127.0.0.1"
    if ($null -eq $env:ANTHILL_LOCAL_ONLY) { $env:ANTHILL_LOCAL_ONLY = "1" }
    python -m uvicorn anthill.web.app:app --host 127.0.0.1 --port 8000
}
finally {
    $env:ANTHILL_HOST = $previousHost
    $env:ANTHILL_LOCAL_ONLY = $previousLocal
}
