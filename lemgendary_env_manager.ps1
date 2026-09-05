# ==============================================================================
# LemGendary Environment Manager Hub (v2.0.0)
# Authoritative PowerShell orchestrator for cross-platform environments.
# ==============================================================================

[CmdletBinding()]
param (
    [Parameter(Position = 0)]
    [ValidateSet("probe", "audit", "install", "sync", "validate", "serve", "menu")]
    [string]$Command = "menu",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"

function Ensure-Environment {
    if (-not (Test-Path $VenvPython)) {
        Write-Host "[INIT] Setting up virtual environment for LemGendary Environment Manager..." -ForegroundColor Cyan
        $GlobalPy = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $GlobalPy) {
            Write-Error "Global Python was not found in PATH. Please install Python 3.10+."
        }
        & $GlobalPy -m venv (Join-Path $ScriptDir ".venv")
        & $VenvPython -m pip install --upgrade pip wheel setuptools
        $ReqPath = Join-Path $ScriptDir "requirements.txt"
        if (Test-Path $ReqPath) {
            & $VenvPython -m pip install -r $ReqPath
        }
    }
}

Ensure-Environment

switch ($Command) {
    "probe" {
        & $VenvPython -m env_manager.cli probe @RemainingArgs
    }
    "audit" {
        & $VenvPython -m env_manager.cli audit @RemainingArgs
    }
    "install" {
        & $VenvPython -m env_manager.cli install @RemainingArgs
    }
    "sync" {
        & $VenvPython -m env_manager.cli sync @RemainingArgs
    }
    "validate" {
        & $VenvPython -m env_manager.cli validate @RemainingArgs
    }
    "serve" {
        & $VenvPython -m env_manager.cli serve @RemainingArgs
    }
    "menu" {
        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host " LEMGENDARY ENVIRONMENT MANAGER - SOTA ECOSYSTEM HUB" -ForegroundColor Cyan
        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host " [1] Probe System & Hardware"
        Write-Host " [2] Run Full Ecosystem Health Audit"
        Write-Host " [3] Execute Smart Clean Install Pipeline"
        Write-Host " [4] Synchronize Requirements Manifests"
        Write-Host " [5] Validate Projects (py_compile & Zero-Emoji)"
        Write-Host " [6] Launch API Sidecar Server (FastAPI/WebSocket)"
        Write-Host " [Q] Quit"
        Write-Host "======================================================================" -ForegroundColor Cyan
        $choice = Read-Host "Select an option"
        switch ($choice) {
            "1" { & $VenvPython -m env_manager.cli probe }
            "2" { & $VenvPython -m env_manager.cli audit }
            "3" { & $VenvPython -m env_manager.cli install }
            "4" { & $VenvPython -m env_manager.cli sync }
            "5" { & $VenvPython -m env_manager.cli validate }
            "6" { & $VenvPython -m env_manager.cli serve }
            default { Write-Host "Exiting." -ForegroundColor Yellow }
        }
    }
}
