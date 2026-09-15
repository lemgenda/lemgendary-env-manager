# ==============================================================================
# LemGendary Environment Manager Hub (v2.1.0)
# Authoritative PowerShell orchestrator for cross-platform environments.
# ==============================================================================

[CmdletBinding()]
param (
    [Parameter(Position = 0)]
    [ValidateSet("probe", "audit", "install", "update", "sync", "validate", "serve", "menu")]
    [string]$Command = "menu",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvCfg = Join-Path $VenvDir "pyvenv.cfg"

function Initialize-Environment {
    # Check if the baseline filesystem structural locks exist
    $HasPython = Test-Path $VenvPython
    $HasCfg = Test-Path $VenvCfg

    # Core Fix: If the structural anchors are present, skip initialization completely
    # to avoid file locking loops inside the active orchestrator.
    if ($HasPython -and $HasCfg) {
        return
    }

    Write-Host "[INIT] Setting up virtual environment for LemGendary Environment Manager..." -ForegroundColor Cyan

    if (Test-Path $VenvDir) {
        Write-Host "[INIT] Purging broken or incomplete environment folder..." -ForegroundColor Yellow
        Start-Sleep -Seconds 1
        Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
    }

    $GlobalPy = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $GlobalPy) {
        Write-Error "Global Python was not found in PATH. Please install Python 3.10+."
    }

    & $GlobalPy -m venv $VenvDir
    & $VenvPython -m pip install --upgrade pip wheel setuptools

    $ReqPath = Join-Path $ScriptDir "requirements.txt"
    if (Test-Path $ReqPath) {
        & $VenvPython -m pip install -r $ReqPath
    }
}

# Run safe bootstrap pass
Initialize-Environment

# ── Non-interactive mode (CLI passthrough) ────────────────────────────────────
if ($Command -ne "menu") {
    switch ($Command) {
        "probe"    { & $VenvPython -m env_manager.cli probe @RemainingArgs }
        "audit"    { & $VenvPython -m env_manager.cli audit @RemainingArgs }
        "install"  { & $VenvPython -m env_manager.cli install @RemainingArgs }
        "update"   { & $VenvPython -m env_manager.cli update @RemainingArgs }
        "sync"     { & $VenvPython -m env_manager.cli sync @RemainingArgs }
        "validate" { & $VenvPython -m env_manager.cli validate @RemainingArgs }
        "serve"    { & $VenvPython -m env_manager.cli serve @RemainingArgs }
    }
    exit 0
}

# ── Interactive menu loop ─────────────────────────────────────────────────────
$ServerJob = $null

do {
    Write-Host ""
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host " LEMGENDARY ENVIRONMENT MANAGER - SOTA ECOSYSTEM HUB" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host " [1] Probe System & Hardware" -ForegroundColor White
    Write-Host " [2] Run Full Ecosystem Health Audit" -ForegroundColor White
    Write-Host " [3] Execute Smart Clean Install Pipeline" -ForegroundColor White
    Write-Host " [4] Safe Package Update (bottom-up + auto-sync)" -ForegroundColor White
    Write-Host " [5] Validate Projects (py_compile, ESLint, YAML, W3C, WCAG 2.2 AA)" -ForegroundColor White

    if ($null -ne $ServerJob -and (Get-Job -Id $ServerJob.Id -ErrorAction SilentlyContinue)) {
        $jobState = (Get-Job -Id $ServerJob.Id).State
        if ($jobState -eq "Running") {
            Write-Host " [6] Stop API Sidecar Server (currently RUNNING on http://127.0.0.1:8000)" -ForegroundColor Green
        } else {
            Write-Host " [6] Launch API Sidecar Server (FastAPI/WebSocket)" -ForegroundColor White
            $ServerJob = $null
        }
    } else {
        Write-Host " [6] Launch API Sidecar Server (FastAPI/WebSocket)" -ForegroundColor White
        $ServerJob = $null
    }

    Write-Host " [Q] Quit" -ForegroundColor White
    Write-Host "======================================================================" -ForegroundColor Cyan
    $choice = Read-Host "Select an option"

    switch ($choice.ToUpper()) {
        "1" {
            Write-Host ""
            Write-Host "[Option 1] Probing system hardware and checking software..." -ForegroundColor Cyan
            & $VenvPython -m env_manager.cli probe
        }
        "2" {
            Write-Host ""
            Write-Host "[Option 2] Running full ecosystem health audit..." -ForegroundColor Cyan
            & $VenvPython -m env_manager.cli audit
        }
        "3" {
            Write-Host ""
            Write-Host "[Option 3] Starting Smart Clean Install Pipeline..." -ForegroundColor Cyan
            & $VenvPython -m env_manager.cli install
        }
        "4" {
            Write-Host ""
            Write-Host "[Option 4] Running safe bottom-up package upgrade..." -ForegroundColor Cyan
            & $VenvPython -m env_manager.cli update
        }
        "5" {
            Write-Host ""
            Write-Host "[Option 5] Running full validation suite..." -ForegroundColor Cyan
            & $VenvPython -m env_manager.cli validate
        }
        "6" {
            Write-Host ""
            if ($null -ne $ServerJob -and (Get-Job -Id $ServerJob.Id -ErrorAction SilentlyContinue) -and (Get-Job -Id $ServerJob.Id).State -eq "Running") {
                Write-Host "[Option 6] Stopping API Sidecar Server..." -ForegroundColor Yellow
                Stop-Job -Id $ServerJob.Id -ErrorAction SilentlyContinue
                Remove-Job -Id $ServerJob.Id -Force -ErrorAction SilentlyContinue
                $ServerJob = $null
                Write-Host "[OK] Server stopped." -ForegroundColor Green
            } else {
                Write-Host "[Option 6] Launching API Sidecar Server in background..." -ForegroundColor Cyan
                Write-Host "  REST API:  http://127.0.0" -ForegroundColor Gray
                Write-Host "  WebSocket: ws://127.0.0.1:8000/ws/log" -ForegroundColor Gray
                $VenvPythonLocal = $VenvPython
                $ServerJob = Start-Job -ScriptBlock {
                    param($py)
                    & $py -m env_manager.cli serve
                } -ArgumentList $VenvPythonLocal
                Start-Sleep -Seconds 2
                Write-Host "[OK] Server started (Job ID: $($ServerJob.Id)). Select [6] again to stop it." -ForegroundColor Green
            }
        }
        { $_ -in "Q", "QUIT", "EXIT" } {
            if ($null -ne $ServerJob) {
                $jobId = $ServerJob.Id
                if (Get-Job -Id $jobId -ErrorAction SilentlyContinue) {
                    Write-Host "Stopping background API server..." -ForegroundColor Yellow
                    Stop-Job -Id $jobId -ErrorAction SilentlyContinue
                    Remove-Job -Id $jobId -Force -ErrorAction SilentlyContinue
                }
            }
            Write-Host "Goodbye!" -ForegroundColor Yellow
            break
        }
        default {
            Write-Host "Unknown option '$choice'. Please select 1-6 or Q." -ForegroundColor Red
        }
    }

} while ($choice.ToUpper() -notin @("Q", "QUIT", "EXIT"))
