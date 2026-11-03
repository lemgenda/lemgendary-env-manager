# ==============================================================================
# LemGendary Environment Manager Hub (v16.9.58)
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

# The sidecar address comes from ecosystem/ecosystem.json (the single source
# of service ports and the bind host).
$Ecosystem = Get-Content -Raw -Path (Join-Path (Join-Path $ScriptDir "ecosystem") "ecosystem.json") | ConvertFrom-Json
$EnvService = $Ecosystem.services | Where-Object { $_.id -eq "env-manager" }
$EnvAddress = "$($Ecosystem.identity.bind_host):$($EnvService.port)"

function Write-Ui {
    # Show a line to the user on the information stream (visible by default,
    # capturable with 6>), coloured with ANSI codes when a colour is named.
    param([string]$Message = "", [string]$Color = "")
    $codes = @{
        Black = 30; DarkRed = 31; DarkGreen = 32; DarkYellow = 33; DarkBlue = 34; DarkMagenta = 35
        DarkCyan = 36; Gray = 37; DarkGray = 90; Red = 91; Green = 92; Yellow = 93; Blue = 94
        Magenta = 95; Cyan = 96; White = 97
    }
    if ($Color -and $codes.ContainsKey($Color)) {
        $esc = [char]27
        $Message = "$esc[$($codes[$Color])m$Message$esc[0m"
    }
    Write-Information -MessageData $Message -InformationAction Continue
}

function Invoke-Checked {
    # Run a native command and stop with its exit code if it fails.
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Ui "[ERROR] '$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE." -Color Red
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
    [CmdletBinding(SupportsShouldProcess)]
    param($Job)
    $live = Get-LiveServerJob $Job
    if ($null -eq $live -or -not $PSCmdlet.ShouldProcess("server job $($live.Id)", "Stop")) {
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

    Write-Ui "[INIT] Setting up virtual environment for LemGendary Environment Manager..." -Color Cyan
    if (Test-Path $VenvDir) {
        Write-Ui "[INIT] Purging broken or incomplete environment folder..." -Color Yellow
        Start-Sleep -Seconds 1
        try {
            Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop
        } catch {
            Write-Ui "[ERROR] Cannot remove '$VenvDir': $($_.Exception.Message). Close programs using it and retry." -Color Red
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
        Write-Ui "[ERROR] Global Python was not found in PATH. Please install Python 3.12+." -Color Red
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

# -- Non-interactive mode (CLI passthrough) ------------------------------------
if ($Command -ne "menu") {
    # Every ValidateSet value except "menu" is a lem-env command of the same name.
    & $VenvPython -m env_manager.cli $Command @RemainingArgs
    exit $LASTEXITCODE
}

# -- Interactive menu loop -----------------------------------------------------
$ServerJob = $null

do {
    Write-Ui ""
    Write-Ui "======================================================================" -Color Cyan
    Write-Ui " LEMGENDARY ENVIRONMENT MANAGER - SOTA ECOSYSTEM HUB" -Color Cyan
    Write-Ui "======================================================================" -Color Cyan
    Write-Ui " [1] Probe System & Hardware" -Color White
    Write-Ui " [2] Run Full Ecosystem Health Audit" -Color White
    Write-Ui " [3] Execute Smart Clean Install Pipeline" -Color White
    Write-Ui " [4] Safe Package Update (bottom-up + auto-sync)" -Color White
    Write-Ui " [5] Validate Projects (py_compile, ESLint, YAML, W3C, WCAG 2.2 AA)" -Color White
    $liveJob = Get-LiveServerJob $ServerJob
    if ($null -ne $liveJob) {
        if ($liveJob.State -eq "Running") {
            Write-Ui " [6] Stop API Sidecar Server (currently RUNNING on http://$EnvAddress)" -Color Green
        } else {
            Write-Ui " [6] Launch API Sidecar Server (FastAPI/WebSocket)" -Color White
            $ServerJob = $null
        }
    } else {
        Write-Ui " [6] Launch API Sidecar Server (FastAPI/WebSocket)" -Color White
        $ServerJob = $null
    }

    Write-Ui " [Q] Quit" -Color White
    Write-Ui "======================================================================" -Color Cyan
    $choice = Read-Host "Select an option"

    switch ($choice.ToUpper()) {
        "1" {
            Write-Ui ""
            Write-Ui "[Option 1] Probing system hardware and checking software..." -Color Cyan
            & $VenvPython -m env_manager.cli probe
        }
        "2" {
            Write-Ui ""
            Write-Ui "[Option 2] Running full ecosystem health audit..." -Color Cyan
            & $VenvPython -m env_manager.cli audit
        }
        "3" {
            Write-Ui ""
            Write-Ui "[Option 3] Starting Smart Clean Install Pipeline..." -Color Cyan
            & $VenvPython -m env_manager.cli install
        }
        "4" {
            Write-Ui ""
            Write-Ui "[Option 4] Running safe bottom-up package upgrade..." -Color Cyan
            & $VenvPython -m env_manager.cli update
        }
        "5" {
            Write-Ui ""
            Write-Ui "[Option 5] Running full validation suite..." -Color Cyan
            & $VenvPython -m env_manager.cli validate
        }
        "6" {
            Write-Ui ""
            $liveJob = Get-LiveServerJob $ServerJob
            if ($null -ne $liveJob -and $liveJob.State -eq "Running") {
                Write-Ui "[Option 6] Stopping API Sidecar Server..." -Color Yellow
                Stop-ServerJob $ServerJob
                $ServerJob = $null
                Write-Ui "[OK] Server stopped." -Color Green
            } else {
                Write-Ui "[Option 6] Launching API Sidecar Server in background..." -Color Cyan
                Write-Ui "  REST API:  http://$EnvAddress" -Color Gray
                Write-Ui "  WebSocket: ws://$EnvAddress$($EnvService.websocket_path)" -Color Gray
                $VenvPythonLocal = $VenvPython
                $ServerJob = Start-Job -ScriptBlock {
                    & $using:VenvPythonLocal -m env_manager.cli serve
                }
                Start-Sleep -Seconds 2
                Write-Ui "[OK] Server started (Job ID: $($ServerJob.Id)). Select [6] again to stop it." -Color Green
            }
        }
        { $_ -in "Q", "QUIT", "EXIT" } {
            if ($null -ne (Get-LiveServerJob $ServerJob)) {
                Write-Ui "Stopping background API server..." -Color Yellow
                Stop-ServerJob $ServerJob
            }
            Write-Ui "Goodbye!" -Color Yellow
            break
        }
        default {
            Write-Ui "Unknown option '$choice'. Please select 1-6 or Q." -Color Red
        }
    }

} while ($choice.ToUpper() -notin @("Q", "QUIT", "EXIT"))
