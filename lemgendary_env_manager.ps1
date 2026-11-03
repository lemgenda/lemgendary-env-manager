# ==============================================================================
# LemGendary Environment Manager Hub (v16.9.39)
# Authoritative PowerShell orchestrator for cross-platform environments.
# ==============================================================================

[CmdletBinding()]
param (
    [Parameter(Position = 0)]
    [ValidateSet("probe", "audit", "install", "update", "sync", "validate", "clean", "setup-hooks", "hooks", "serve", "menu")]
    [string]$Command = "menu",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ScriptDir ".venv"
# Windows venvs keep the interpreter in Scripts\python.exe; Linux and macOS
# (PowerShell 7) use bin/python.
if ($IsWindows -or $env:OS -eq "Windows_NT") {
    $VenvPython = Join-Path (Join-Path $VenvDir "Scripts") "python.exe"
} else {
    $VenvPython = Join-Path (Join-Path $VenvDir "bin") "python"
}
$VenvCfg = Join-Path $VenvDir "pyvenv.cfg"

function Invoke-Checked {
    # Run a native command and stop with its exit code if it fails.
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] '$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

function Get-LiveServerJob {
    # Return the server job while it still exists, else $null.
    param($Job)
    if ($null -eq $Job) {
        return $null
    }
    return Get-Job | Where-Object { $_.Id -eq $Job.Id }
}

function Stop-ServerJob {
    # Stop and remove the server job; report a failure instead of hiding it.
    param($Job)
    $live = Get-LiveServerJob $Job
    if ($null -eq $live) {
        return
    }
    try {
        Stop-Job -Job $live -ErrorAction Stop
        Remove-Job -Job $live -Force -ErrorAction Stop
    } catch {
        Write-Warning "Could not stop server job $($live.Id): $($_.Exception.Message)"
    }
}

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
        try {
            Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop
        } catch {
            Write-Host "[ERROR] Cannot remove '$VenvDir': $($_.Exception.Message). Close programs using it and retry." -ForegroundColor Red
            exit 1
        }
    }

    # Linux and macOS often install the interpreter only as python3.
    $GlobalPy = $null
    foreach ($PyName in @("python", "python3")) {
        try {
            $GlobalPy = (Get-Command $PyName -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
            break
        } catch [System.Management.Automation.CommandNotFoundException] {
            Write-Verbose "'$PyName' is not on PATH."
        }
    }
    if ($null -eq $GlobalPy) {
        Write-Host "[ERROR] Global Python was not found in PATH. Please install Python 3.12+." -ForegroundColor Red
        exit 1
    }

    Invoke-Checked $GlobalPy @("-m", "venv", $VenvDir)
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools")

    $ReqPath = Join-Path $ScriptDir "requirements.txt"
    if (Test-Path $ReqPath) {
        Invoke-Checked $VenvPython @("-m", "pip", "install", "-r", $ReqPath)
    }
}

# Run safe bootstrap pass
Initialize-Environment

# ── Non-interactive mode (CLI passthrough) ────────────────────────────────────
if ($Command -ne "menu") {
    # Every ValidateSet value except "menu" is a lem-env command of the same name.
    & $VenvPython -m env_manager.cli $Command @RemainingArgs
    exit $LASTEXITCODE
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

    $liveJob = Get-LiveServerJob $ServerJob
    if ($null -ne $liveJob) {
        if ($liveJob.State -eq "Running") {
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
            $liveJob = Get-LiveServerJob $ServerJob
            if ($null -ne $liveJob -and $liveJob.State -eq "Running") {
                Write-Host "[Option 6] Stopping API Sidecar Server..." -ForegroundColor Yellow
                Stop-ServerJob $ServerJob
                $ServerJob = $null
                Write-Host "[OK] Server stopped." -ForegroundColor Green
            } else {
                Write-Host "[Option 6] Launching API Sidecar Server in background..." -ForegroundColor Cyan
                Write-Host "  REST API:  http://127.0.0.1:8000" -ForegroundColor Gray
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
            if ($null -ne (Get-LiveServerJob $ServerJob)) {
                Write-Host "Stopping background API server..." -ForegroundColor Yellow
                Stop-ServerJob $ServerJob
            }
            Write-Host "Goodbye!" -ForegroundColor Yellow
            break
        }
        default {
            Write-Host "Unknown option '$choice'. Please select 1-6 or Q." -ForegroundColor Red
        }
    }

} while ($choice.ToUpper() -notin @("Q", "QUIT", "EXIT"))
