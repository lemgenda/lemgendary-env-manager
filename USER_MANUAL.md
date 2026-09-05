# LemGendary Environment Manager: User Manual

An authoritative operations and reference guide for the LemGendary Environment Manager (`lem-env`) and the LemGendary AI Studio Desktop GUI.

---

## Table of Contents

- [1. Introduction & Architecture](#1-introduction--architecture)
- [2. Installation & Quick Start](#2-installation--quick-start)
  - [2.1 Prerequisites](#21-prerequisites)
  - [2.2 Package Installation](#22-package-installation)
  - [2.3 Launcher Scripts](#23-launcher-scripts)
- [3. Command Line Interface (CLI) Manual](#3-command-line-interface-cli-manual)
  - [3.1 Global Options & Conventions](#31-global-options--conventions)
  - [3.2 Command: `lem-env probe`](#32-command-lem-env-probe)
  - [3.3 Command: `lem-env audit`](#33-command-lem-env-audit)
  - [3.4 Command: `lem-env install`](#34-command-lem-env-install)
  - [3.5 Command: `lem-env sync`](#35-command-lem-env-sync)
  - [3.6 Command: `lem-env validate`](#36-command-lem-env-validate)
  - [3.7 Command: `lem-env clean`](#37-command-lem-env-clean)
  - [3.8 Command: `lem-env serve`](#38-command-lem-env-serve)
- [4. Operational Workflows & Best Practices](#4-operational-workflows--best-practices)
  - [4.1 Initial Machine Provisioning](#41-initial-machine-provisioning)
  - [4.2 Pre-Training Validation Run](#42-pre-training-validation-run)
  - [4.3 Cross-Repository Dependency Reconciliation](#43-cross-repository-dependency-reconciliation)
  - [4.4 Cloud Execution (Kaggle & Colab)](#44-cloud-execution-kaggle--colab)
- [5. LemGendary AI Studio Desktop GUI Manual](#5-lemgendary-ai-studio-desktop-gui-manual)
  - [5.1 GUI Overview & Architecture](#51-gui-overview--architecture)
  - [5.2 Launching the Studio](#52-launching-the-studio)
  - [5.3 Top Navigation & Global Header](#53-top-navigation--global-header)
  - [5.4 Dashboard View](#54-dashboard-view)
  - [5.5 Pipeline View](#55-pipeline-view)
  - [5.6 Projects View](#56-projects-view)
  - [5.7 Health & Drift Matrix View](#57-health--drift-matrix-view)
  - [5.8 Logs & Real-Time Telemetry View](#58-logs--real-time-telemetry-view)
  - [5.9 Status Bar Indicators](#59-status-bar-indicators)
- [6. REST & WebSocket API Reference](#6-rest--websocket-api-reference)
  - [6.1 Server Architecture & Startup](#61-server-architecture--startup)
  - [6.2 Complete REST Endpoints Specification](#62-complete-rest-endpoints-specification)
  - [6.3 WebSocket Real-Time Telemetry Protocol](#63-websocket-real-time-telemetry-protocol)
  - [6.4 Client Integration Examples](#64-client-integration-examples)
- [7. Troubleshooting & Remediation](#7-troubleshooting--remediation)

---

## 1. Introduction & Architecture

The LemGendary Environment Manager establishes an authoritative, centralized control plane for Python virtual environments, hardware accelerator binding, dependency synchronization, and syntax compliance across the entire LemGendary AI ecosystem:

- `lemgendary-training-suite`: Model architectures, progressive curriculum training, optimization engines, and ONNX/PyTorch model exporters.
- `lemgendary-datasets`: Multi-manifold dataset compilers, downloaders, verifiers, and index synthesizers.
- `lemgendary-env-manager`: Core infrastructure service, CLI, and REST/WebSocket sidecar.
- `lemgendary-ai-studio-gui`: Cross-platform desktop interface built on React 18, Vanilla CSS, and Tauri v2.

By decoupling environment logic from individual training and compilation scripts, the system eliminates version skew, prevents wheel resolution conflicts, and guarantees identical execution profiles across local desktop hardware (CUDA, ROCm, DirectML, CPU) and cloud compute nodes (Kaggle, Colab, HPC clusters).

---

## 2. Installation & Quick Start

### 2.1 Prerequisites

Before operating `lem-env`, verify that your host system satisfies the baseline requirements:

- **Python**: Version 3.10 or higher (3.11 or 3.12 recommended).
- **Git**: Version 2.30 or higher in system PATH.
- **Node.js & NPM** (Optional, required for Studio GUI build): Node.js 18+ and NPM 9+.
- **Hardware Acceleration Tools** (Optional, detected automatically):
  - NVIDIA: `nvidia-smi` in PATH.
  - AMD: `rocm-smi` in PATH.
  - Windows DirectML: DirectX runtime.

### 2.2 Package Installation

Install `lemgendary-env-manager` in editable mode within the ecosystem:

```bash
cd lemgendary-env-manager
python -m pip install -e .
```

Confirm that the CLI entrypoint is active:

```bash
lem-env --help
```

### 2.3 Launcher Scripts

On Windows systems, use the authoritative PowerShell launcher:

```powershell
# Interactive menu launcher
.\lemgendary_env_manager.ps1

# Direct CLI execution via launcher
.\lemgendary_env_manager.ps1 probe
.\lemgendary_env_manager.ps1 audit
.\lemgendary_env_manager.ps1 install
.\lemgendary_env_manager.ps1 validate
.\lemgendary_env_manager.ps1 serve
```

---

## 3. Command Line Interface (CLI) Manual

### 3.1 Global Options & Conventions

The CLI adheres to strict professional standards:
- Output is rendered using structured ANSI tables and text-only tags (`[OK]`, `[FAIL]`, `[WARN]`, `[DRIFT]`, `[SYNCED]`).
- Zero emojis are produced in stdout, stderr, or log streams.
- Exit code `0` signals complete success; non-zero codes (`1`, `2`) signal fatal errors or verification failures.

```bash
lem-env [COMMAND] [OPTIONS]
```

To display built-in command assistance:

```bash
lem-env --help
lem-env [COMMAND] --help
```

---

### 3.2 Command: `lem-env probe`

#### Purpose
Probes host hardware topology, operating system details, Python runtime environment, and available hardware accelerator devices (NVIDIA CUDA, AMD ROCm, DirectML, or CPU fallback). Recommends the optimal PyTorch extra-index URL.

#### Syntax
```bash
lem-env probe
```

#### What It Does
1. Queries platform architecture, OS release version, and Python interpreter path.
2. Counts physical and logical CPU cores and calculates total system RAM.
3. Probes for NVIDIA GPUs using `nvidia-smi` and extracts name, VRAM capacity, and driver version.
4. Probes for AMD GPUs using `rocm-smi` and extracts device identifiers and memory.
5. Probes for DirectML support on Windows systems.
6. Computes the primary backend (`cuda`, `rocm`, `directml`, or `cpu`) and outputs the exact PyTorch index URL matching the detected compute capability.

#### Output Table Fields
- **Operating System**: Platform name, kernel release, and CPU architecture.
- **Python Version & Executable**: Active interpreter version and resolved path.
- **Logical / Physical Cores**: Thread and core topology.
- **Total RAM (MB)**: Total installed physical memory.
- **Primary Backend**: Highest-priority detected accelerator.
- **Recommended Torch Index**: Official wheel repository (e.g. `https://download.pytorch.org/whl/cu121`).
- **Accelerator Details**: Device name, memory buffer, driver version, and device index.

#### Recommended Usage
Run `lem-env probe` immediately after configuring a new workstation, installing GPU drivers, or mounting cloud instances to verify accelerator visibility.

---

### 3.3 Command: `lem-env audit`

#### Purpose
Conducts an ecosystem-wide audit across all sibling repositories, evaluating prerequisite toolchains, virtual environment status, installed packages, missing requirements, and cross-project package version drift.

#### Syntax
```bash
lem-env audit
```

#### What It Does
1. Audits host toolchain availability (Python $\ge 3.10$, Git, NPM).
2. Discovers all registered sibling projects (`lemgendary-training-suite`, `lemgendary-datasets`, `lemgendary-env-manager`).
3. Evaluates each project's `.venv` directory, resolving its local Python executable and version.
4. Reads the project's target requirements manifest and compares it against installed site-packages to compute installed vs. missing package counts.
5. Cross-references package versions across all projects to construct a Version Drift Matrix, flagging any package that has divergent versions installed across sibling virtual environments.

#### Output Tables
1. **Prerequisites & Toolchain**: Status of Python, Git, and NPM.
2. **Project Environments Status**: Project name, venv existence (`[YES]`/`[NO]`), installed package count, missing package count, and overall health status (`[OK]`/`[WARN]`).
3. **Package Version Drift Matrix**: Multi-column comparison listing package name, version in each project, and drift verdict (`[SYNCED]` or `[DRIFT]`).

#### Recommended Usage
Execute `lem-env audit` before running training jobs or dataset compilation tasks to ensure no dependencies are missing or out of alignment.

---

### 3.4 Command: `lem-env install`

#### Purpose
Executes the comprehensive Smart Clean Install Pipeline across all projects or targeting a specific project.

#### Syntax
```bash
# Full ecosystem installation
lem-env install

# Target single project
lem-env install --project lemgendary-training-suite
lem-env install -p lemgendary-datasets
```

#### Pipeline Steps Executed
1. **Hardware Discovery**: Executes system hardware audit and determines the PyTorch extra-index URL.
2. **Toolchain Audit**: Verifies host Python version and Git installation.
3. **Venv Provisioning**: Verifies or creates `.venv` virtual environments in targeted projects, upgrading base `pip`, `wheel`, and `setuptools`.
4. **Manifest Synchronization**: Copies centralized requirement manifests from `lemgendary-env-manager/requirements/` to target project directories.
5. **Dependency Installation & Safe Upgrades**: Installs project requirements via `pip install` using the resolved PyTorch wheel index, honoring PEP 508 platform markers.
6. **Codebase Verification**: Executes `py_compile` bytecode compilation across all project Python scripts and runs strict zero-emoji compliance linting.
7. **Health Matrix Generation**: Reruns health audit and outputs the final ecosystem status summary.

#### Options
- `--project`, `-p`: Name of a single project to target (`lemgendary-training-suite`, `lemgendary-datasets`, or `lemgendary-env-manager`). When omitted, all sibling projects are processed sequentially.

#### Recommended Usage
Run `lem-env install` when initially setting up the workspace, when pull requests introduce updated dependencies, or whenever an environment becomes corrupted.

---

### 3.5 Command: `lem-env sync`

#### Purpose
Synchronizes centralized requirement manifests from `lemgendary-env-manager/requirements/` into the local `requirements.txt` of each sibling repository.

#### Syntax
```bash
lem-env sync
```

#### What It Does
1. Reads `requirements/requirements-training.txt` and mirrors it to `../lemgendary-training-suite/requirements.txt`.
2. Reads `requirements/requirements-datasets.txt` and mirrors it to `../lemgendary-datasets/requirements.txt`.
3. Reads `requirements/requirements-env-manager.txt` and mirrors it to `./requirements.txt`.
4. Validates that the target destination files exist and updates them atomically.

#### Recommended Usage
Execute `lem-env sync` after modifying any centralized requirements file to ensure sibling repositories are updated before committing changes.

---

### 3.6 Command: `lem-env validate`

#### Purpose
Enforces code quality standards by compiling every Python file across all sibling repositories with `python -m py_compile` and scanning for any unicode emoji characters.

#### Syntax
```bash
lem-env validate
```

#### What It Does
1. Recursively traverses all Python files (`*.py`) in all sibling repositories (excluding `.venv`, `__pycache__`, and `.git`).
2. Invokes bytecode compilation on each file, catching and reporting any syntax errors with file paths and line numbers.
3. Scans file contents against unicode emoji code point ranges to guarantee 100% text-only code compliance.
4. Returns exit code `0` if all files pass cleanly; returns exit code `1` and lists all violations if any file fails.

#### Recommended Usage
Run `lem-env validate` as a mandatory pre-commit hook or before marking any coding task complete.

---

### 3.7 Command: `lem-env clean`

#### Purpose
Reclaims disk space by purging compiled bytecode files, `__pycache__` directories, and temporary build artifacts across projects.

#### Syntax
```bash
# Clean all projects
lem-env clean

# Clean specific project
lem-env clean --project lemgendary-training-suite
lem-env clean -p lemgendary-datasets
```

#### What It Does
1. Discovers targeted project directories.
2. Identifies and removes all `__pycache__` folders (outside `.venv`).
3. Removes orphaned `*.pyc`, `*.pyo`, and `*.pyd` artifacts.
4. Calculates total bytes reclaimed and displays the count of removed artifacts along with total megabytes freed.

#### Recommended Usage
Run `lem-env clean` prior to creating repository archives, making git commits, or when recovering disk space on resource-constrained storage drives.

---

### 3.8 Command: `lem-env serve`

#### Purpose
Launches the FastAPI sidecar server that provides REST endpoints and a real-time WebSocket log stream for the LemGendary AI Studio Desktop GUI and external automation agents.

#### Syntax
```bash
# Default binding (127.0.0.1:8000)
lem-env serve

# Custom host and port
lem-env serve --host 0.0.0.0 --port 8080
```

#### Available REST Endpoints
- `GET /api/hardware`: Returns JSON payload with system hardware and accelerator profile.
- `GET /api/health`: Returns JSON payload with toolchain status, project environments, and version drift matrix.
- `GET /api/pipeline/status`: Returns current pipeline execution state (`is_running`) and recent event logs.
- `POST /api/pipeline/run`: Triggers the Smart Clean Install Pipeline in a background thread. Accepts optional query parameter `?project=<name>`.
- `WS /ws/logs`: WebSocket endpoint that broadcasts real-time `PipelineEvent` JSON objects to connected desktop clients.

#### Recommended Usage
Run `lem-env serve` in a dedicated terminal or as a background service when operating the LemGendary AI Studio GUI.

---

## 4. Operational Workflows & Best Practices

### 4.1 Initial Machine Provisioning

When provisioning a fresh development workstation:

1. Clone all repositories into a unified root directory:
   ```text
   Development/python/model-training/
   ├── lemgendary-training-suite/
   ├── lemgendary-datasets/
   ├── lemgendary-env-manager/
   └── lemgendary-ai-studio-gui/
   ```
2. Navigate to `lemgendary-env-manager` and install the package:
   ```bash
   cd lemgendary-env-manager
   python -m pip install -e .
   ```
3. Probe system hardware:
   ```bash
   lem-env probe
   ```
4. Execute the automated clean install pipeline:
   ```bash
   lem-env install
   ```
5. Verify compliance:
   ```bash
   lem-env validate
   ```

### 4.2 Pre-Training Validation Run

Before launching lengthy training runs (e.g. `train_all.py` or `train_forex_curriculum.py`):

1. Check ecosystem health and version drift:
   ```bash
   lem-env audit
   ```
2. Verify syntax across modified files:
   ```bash
   lem-env validate
   ```
3. Reclaim stale cache artifacts:
   ```bash
   lem-env clean
   ```

### 4.3 Cross-Repository Dependency Reconciliation

When adding a new library (e.g., a new perceptual loss library):

1. Add the dependency to the centralized manifest in `lemgendary-env-manager/requirements/requirements-training.txt`.
2. Synchronize manifests across repositories:
   ```bash
   lem-env sync
   ```
3. Reconcile the target project's virtual environment:
   ```bash
   lem-env install -p lemgendary-training-suite
   ```
4. Confirm version alignment:
   ```bash
   lem-env audit
   ```

### 4.4 Cloud Execution (Kaggle & Colab)

In cloud environments where `lemgendary-env-manager` is cloned alongside the suite:

1. The generated notebooks automatically clone `lemgendary-env-manager` during Step 3.
2. Step 3 detects the centralized manifest at `/kaggle/working/lemgendary-env-manager/requirements/requirements-training.txt` (or Colab equivalent) and installs wheels directly against the host accelerator.
3. No local `.venv` overhead is created in cloud containers; the system binds dependencies directly to the cloud Python runtime.

---

## 5. LemGendary AI Studio Desktop GUI Manual

### 5.1 GUI Overview & Architecture

The LemGendary AI Studio Desktop GUI provides a reactive visual dashboard for monitoring hardware health, tracking package drift, executing clean installation pipelines, and observing real-time pipeline telemetry.

- **Frontend**: React 18, TypeScript, Vanilla CSS design system (HSL color tokens, dark theme, smooth micro-interactions).
- **Desktop Shell**: Tauri v2, providing a native, lightweight executable window.
- **Backend Communication**: Binds to `lem-env serve` via HTTP REST and bidirectional WebSockets.

---

### 5.2 Launching the Studio

1. Start the backend sidecar server:
   ```bash
   lem-env serve --port 8000
   ```
2. In a separate terminal, navigate to `lemgendary-ai-studio-gui` and launch the dev environment:
   ```bash
   cd lemgendary-ai-studio-gui
   npm run tauri dev
   ```
   Or launch in browser mode:
   ```bash
   npm run dev
   ```

---

### 5.3 Top Navigation & Global Header

The global header persists across all views and contains the following controls:

| Control | Type | Purpose & Action | Recommended Usage |
| :--- | :--- | :--- | :--- |
| **Page Title** | Display Text | Indicates active view (`Ecosystem Control Dashboard`, `Pipeline`, etc.). | Context awareness. |
| **WebSocket Indicator** | Status Pill | Displays green `Connected` or yellow `Offline`. Indicates real-time event streaming state. | Verify backend is reachable. |
| **Last Updated** | Timestamp | Displays timestamp of last successful REST poll. | Audit freshness of displayed telemetry. |
| **Refresh Button** | Action Button | Immediately polls `/api/hardware`, `/api/health`, and `/api/pipeline/status`. | Click after making manual changes to disk. |
| **Run Full Pipeline** | Primary Button | Triggers `/api/pipeline/run`, initiating the 7-step clean install across all projects. | Click to reconcile all virtual environments. |

---

### 5.4 Dashboard View

The Dashboard view provides a top-level telemetry summary of system hardware and project health.

#### Elements & Cards
1. **Hardware Accelerator Card**:
   - Displays host CPU topology and physical memory count.
   - Highlights the active accelerator backend (`CUDA`, `ROCM`, `DIRECTML`, or `CPU`).
   - Shows the active PyTorch extra-index URL.
   - Lists detected GPUs with dedicated VRAM meters, memory gauges, and driver versions.
2. **Projects Overview Grid**:
   - Displays project cards for each repository (`lemgendary-training-suite`, `lemgendary-datasets`, `lemgendary-env-manager`).
   - Displays virtual environment validity pill (`Active` / `Missing`).
   - Shows total installed packages count and missing requirements count.
   - **Reconcile Button**: Triggers the clean install pipeline targeted specifically to that single project.
3. **Quick Health Status**:
   - High-level indicators showing Python, Git, and NPM readiness.

---

### 5.5 Pipeline View

The Pipeline view monitors execution of the 7-stage Smart Clean Install Pipeline.

#### Elements & Controls
1. **Pipeline Header & Trigger**:
   - Displays overall pipeline execution status (`Idle`, `Running`, `Completed`, `Failed`).
   - Primary **Run Clean Install Pipeline** button.
2. **Progress Bar**:
   - Visual progress indicator reflecting steps 1 through 7.
3. **Step Cards**:
   - **Step 1: Hardware Discovery**: System accelerator probing and index resolution.
   - **Step 2: Toolchain Audit**: Verification of host Python and Git versions.
   - **Step 3: Venv Provisioning**: Verification and creation of `.venv` directories.
   - **Step 4: Manifest Sync**: Centralized requirements distribution.
   - **Step 5: Dependency Upgrades**: Package installation and wheel resolution.
   - **Step 6: Codebase Verification**: Bytecode compilation and zero-emoji auditing.
   - **Step 7: Health Matrix**: Aggregated status computation.
   - Each card displays a status tag (`Pending`, `In Progress`, `Success`, `Warning`, `Error`) and the latest event message.

---

### 5.6 Projects View

The Projects view provides detailed management controls for individual project environments.

#### Elements & Controls
- **Project Selection List**: Allows selecting between individual projects.
- **Environment Path Details**: Displays absolute path to project directory, `.venv` path, and resolved Python interpreter.
- **Python Version Badge**: Displays exact version of Python inside the virtual environment.
- **Package Inventory**: Lists installed packages and versions.
- **Missing Dependencies Warning**: Highlights packages present in `requirements.txt` but absent in the virtual environment.
- **Individual Action Buttons**:
  - **Reconcile Environment**: Runs targeted install for the selected project.
  - **Validate Syntax**: Runs `py_compile` checks against project files.
  - **Clean Artifacts**: Purges project `__pycache__` and temporary build files.

---

### 5.7 Health & Drift Matrix View

The Health view provides an in-depth diagnostic audit of toolchains and package version alignment.

#### Elements & Controls
1. **Toolchain Prerequisites Table**:
   - Status rows for Python, Git, and NPM.
   - Displays installed version numbers and pass/fail badges.
2. **Project Health Table**:
   - Comprehensive summary table showing project name, venv status, total packages, missing count, and health grade.
3. **Version Drift Matrix Table**:
   - Lists packages shared across projects.
   - Multi-column display of installed version in each project.
   - Highlights rows with yellow `[DRIFT]` badge if different versions are installed, or green `[SYNCED]` if identical versions are present.

---

### 5.8 Logs & Real-Time Telemetry View

The Logs view displays the live stream of events broadcast over WebSockets from the backend server.

#### Elements & Controls
- **Console Terminal**: High-contrast, dark-mode terminal displaying timestamped pipeline events.
- **Log Level Filter**: Filter events by severity (`All`, `Success`, `Warning`, `Error`).
- **Auto-Scroll Toggle**: Locks or unlocks scroll position to bottom of stream.
- **Clear Console Button**: Clears displayed events from local memory.
- **Copy Logs Button**: Copies entire console log buffer to system clipboard for bug reports.

---

### 5.9 Status Bar Indicators

The persistent footer status bar displays:
- **System State**: Indicates whether the environment is idle or executing a task.
- **WebSocket Connection**: Real-time indicator of connection to `ws://localhost:8000/ws/logs`.
- **Active Projects Count**: Number of detected sibling projects.
- **Engine Version**: Displays LemGendary Environment Manager release version.

---

## 6. REST & WebSocket API Reference

The LemGendary Environment Manager provides an authoritative REST and WebSocket sidecar service built with FastAPI and Uvicorn. It enables external tools, automated orchestrators, and the LemGendary AI Studio Desktop GUI to query ecosystem health, control the clean installation pipeline, synchronize manifests, and consume real-time telemetry streams.

### 6.1 Server Architecture & Startup

The sidecar server operates on `http://127.0.0.1:8000` by default. It includes full Cross-Origin Resource Sharing (CORS) middleware, enabling direct connection from local web clients and desktop shells.

#### Starting the Server
```bash
# Launch server via CLI
lem-env serve --host 127.0.0.1 --port 8000

# Or launch via PowerShell launcher
.\lemgendary_env_manager.ps1 serve
```

#### Base URLs
- **REST API Base**: `http://127.0.0.1:8000/api`
- **WebSocket Base**: `ws://127.0.0.1:8000/ws/log` (or `ws://127.0.0.1:8000/ws/logs`)
- **Interactive OpenAPI Documentation**: `http://127.0.0.1:8000/docs`
- **ReDoc Documentation**: `http://127.0.0.1:8000/redoc`

---

### 6.2 Complete REST Endpoints Specification

#### Endpoint 1: `GET /api/hardware`
Retrieves the comprehensive system hardware topology, CPU specifications, operating system details, detected GPU accelerators, and the recommended PyTorch extra-index URL.

- **HTTP Method**: `GET`
- **Path**: `/api/hardware`
- **Request Headers**: None
- **Query Parameters**: None
- **Request Body**: None
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema
```json
{
  "os_name": "Windows",
  "os_release": "11",
  "architecture": "AMD64",
  "python_version": "3.12.3",
  "python_executable": "C:\\Python312\\python.exe",
  "cpu_count_logical": 16,
  "cpu_count_physical": 8,
  "total_ram_mb": 32768,
  "primary_backend": "cuda",
  "recommended_torch_index": "https://download.pytorch.org/whl/cu121",
  "accelerators": [
    {
      "name": "NVIDIA GeForce RTX 4070",
      "backend": "cuda",
      "total_memory_mb": 12288,
      "driver_version": "551.86",
      "index": 0
    }
  ]
}
```

---

#### Endpoint 2: `GET /api/health`
Executes an immediate, full-ecosystem health audit across all sibling repositories, returning toolchain prerequisites, virtual environment statuses, missing package counts, and cross-project package version drift.

- **HTTP Method**: `GET`
- **Path**: `/api/health`
- **Request Headers**: None
- **Query Parameters**: None
- **Request Body**: None
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema
```json
{
  "bootstrap": {
    "python_valid": true,
    "python_version": "3.12.3",
    "git_installed": true,
    "git_version": "git version 2.44.0.windows.1",
    "npm_installed": true,
    "npm_version": "10.5.0"
  },
  "projects": [
    {
      "name": "lemgendary-training-suite",
      "project_dir": "c:\\Development\\python\\model-training\\lemgendary-training-suite",
      "venv_exists": true,
      "python_version": "Python 3.12.3",
      "total_installed": 142,
      "missing_packages": [],
      "is_healthy": true
    },
    {
      "name": "lemgendary-datasets",
      "project_dir": "c:\\Development\\python\\model-training\\lemgendary-datasets",
      "venv_exists": true,
      "python_version": "Python 3.12.3",
      "total_installed": 98,
      "missing_packages": [],
      "is_healthy": true
    }
  ],
  "version_drift": [
    {
      "package_name": "torch",
      "versions": {
        "lemgendary-training-suite": "2.3.0+cu121",
        "lemgendary-datasets": "2.3.0+cu121"
      },
      "has_drift": false
    }
  ]
}
```

---

#### Endpoint 3: `GET /api/projects`
Returns metadata and status for each registered sibling project in the ecosystem.

- **HTTP Method**: `GET`
- **Path**: `/api/projects`
- **Request Headers**: None
- **Query Parameters**: None
- **Request Body**: None
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema
```json
[
  {
    "name": "lemgendary-training-suite",
    "project_dir": "c:\\Development\\python\\model-training\\lemgendary-training-suite",
    "venv_dir": "c:\\Development\\python\\model-training\\lemgendary-training-suite\\.venv",
    "python_path": "c:\\Development\\python\\model-training\\lemgendary-training-suite\\.venv\\Scripts\\python.exe",
    "is_valid": true,
    "python_version": "Python 3.12.3",
    "installed_packages_count": 142
  }
]
```

---

#### Endpoint 4: `GET /api/npm`
Audits package dependencies and build toolchains in frontend repositories (such as `lemgendary-ai-studio-gui`).

- **HTTP Method**: `GET`
- **Path**: `/api/npm`
- **Request Headers**: None
- **Query Parameters**: None
- **Request Body**: None
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema
```json
[
  {
    "project_name": "lemgendary-ai-studio-gui",
    "package_json_exists": true,
    "node_modules_exists": true,
    "total_dependencies": 24,
    "is_installed": true
  }
]
```

---

#### Endpoint 5: `GET /api/pipeline/status`
Returns the operational state of the Smart Clean Install Pipeline, including whether execution is active, the last status verdict, and the 50 most recent event logs.

- **HTTP Method**: `GET`
- **Path**: `/api/pipeline/status`
- **Request Headers**: None
- **Query Parameters**: None
- **Request Body**: None
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema
```json
{
  "is_running": false,
  "last_status": "success",
  "last_run_timestamp": "2026-09-06T00:15:30.123456",
  "recent_events": [
    {
      "step_number": 7,
      "total_steps": 7,
      "step_name": "Health Matrix",
      "status": "success",
      "message": "Health matrix generated: All projects operational.",
      "timestamp": "2026-09-06T00:15:30.120000"
    }
  ]
}
```

---

#### Endpoint 6: `POST /api/pipeline/run`
Asynchronously triggers the 7-stage Smart Clean Install Pipeline in a background thread. Emits real-time progress events over WebSockets.

- **HTTP Method**: `POST`
- **Path**: `/api/pipeline/run`
- **Request Headers**: `Content-Type: application/json`
- **Request Body**:
  ```json
  {
    "target_project": "lemgendary-training-suite"
  }
  ```
  *(Note: Pass `null` or omit `"target_project"` to execute across all sibling projects).*
- **Response Status**: `200 OK` (or `409 Conflict` if pipeline is already running)
- **Response Content-Type**: `application/json`

##### Response Schema (Success)
```json
{
  "status": "accepted",
  "message": "Pipeline initiated."
}
```

##### Response Schema (Already Running)
```json
{
  "status": "error",
  "message": "Pipeline is already running."
}
```

---

#### Endpoint 7: `GET /api/manifests`
Reads and lists all centralized requirement manifests from `lemgendary-env-manager/requirements/` and returns their raw text definitions.

- **HTTP Method**: `GET`
- **Path**: `/api/manifests`
- **Request Headers**: None
- **Query Parameters**: None
- **Request Body**: None
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema
```json
{
  "manifests": {
    "requirements-training.txt": "--extra-index-url https://download.pytorch.org/whl/cu121\ntorch>=2.3.0\ntorchvision>=0.18.0\n...",
    "requirements-datasets.txt": "opencv-python-headless>=4.8.0\n...",
    "requirements-env-manager.txt": "fastapi>=0.111.0\nuvicorn>=0.30.0\n..."
  }
}
```

---

#### Endpoint 8: `POST /api/manifests/sync`
Synchronizes centralized requirement manifests into the local `requirements.txt` of each respective sibling repository.

- **HTTP Method**: `POST`
- **Path**: `/api/manifests/sync`
- **Request Headers**: None
- **Query Parameters**: None
- **Request Body**: None
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema
```json
{
  "status": "success",
  "results": {
    "lemgendary-training-suite": {
      "success": true,
      "message": "Synchronized requirements-training.txt to .../requirements.txt."
    },
    "lemgendary-datasets": {
      "success": true,
      "message": "Synchronized requirements-datasets.txt to .../requirements.txt."
    },
    "lemgendary-env-manager": {
      "success": true,
      "message": "Synchronized requirements-env-manager.txt to .../requirements.txt."
    }
  }
}
```

---

#### Endpoint 9: `POST /api/clean`
Purges Python bytecode caches (`__pycache__`, `*.pyc`, `*.pyo`, `*.pyd`) and temporary build artifacts across project workspaces to reclaim storage.

- **HTTP Method**: `POST`
- **Path**: `/api/clean`
- **Request Headers**: `Content-Type: application/json`
- **Request Body**:
  ```json
  {
    "project": null
  }
  ```
  *(Pass project name to clean a specific repository, or `null` to clean all).*
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema
```json
{
  "status": "success",
  "cleaned_count": 84,
  "reclaimed_bytes": 14680064,
  "reclaimed_mb": 14.0
}
```

---

#### Endpoint 10: `POST /api/validate`
Executes bytecode syntax compilation (`python -m py_compile`) and strict zero-emoji compliance audits across all Python files in the target project or entire ecosystem.

- **HTTP Method**: `POST`
- **Path**: `/api/validate`
- **Request Headers**: `Content-Type: application/json`
- **Request Body**:
  ```json
  {
    "project": "lemgendary-training-suite"
  }
  ```
- **Response Status**: `200 OK`
- **Response Content-Type**: `application/json`

##### Response Schema (All Passed)
```json
{
  "status": "success",
  "all_passed": true,
  "projects": {
    "lemgendary-training-suite": {
      "passed": true,
      "compiled_files_count": 48,
      "compile_errors": [],
      "emoji_violations": []
    }
  }
}
```

---

### 6.3 WebSocket Real-Time Telemetry Protocol

The WebSocket interface streams typed telemetry messages as the pipeline executes.

- **Connection URLs**:
  - `ws://127.0.0.1:8000/ws/log`
  - `ws://127.0.0.1:8000/ws/logs`
- **Protocol**: Raw JSON framing over standard WebSocket (RFC 6455).
- **Handshake**: Clients initiate connection; upon handshake acceptance, the server immediately emits up to the last 20 cached `PipelineEvent` messages, allowing newly opened tabs to hydrate instantly.

#### WebSocket Event Payload Schema
```json
{
  "step_number": 4,
  "total_steps": 7,
  "step_name": "Manifest Sync",
  "status": "success",
  "message": "Synchronized centralized requirements to 3 projects.",
  "timestamp": "2026-09-06T00:15:22.456789"
}
```

#### Field Definitions
- `step_number` (`int`): Active step sequence index (1 to 7).
- `total_steps` (`int`): Total pipeline stages (always 7).
- `step_name` (`string`): Name of the active stage (`Hardware Discovery`, `Toolchain Audit`, `Venv Provisioning`, `Manifest Sync`, `Dependency Upgrades`, `Codebase Verification`, `Health Matrix`).
- `status` (`string`): Execution state:
  - `"pending"`: Step queued.
  - `"running"`: Step actively executing.
  - `"success"`: Step finished cleanly.
  - `"warning"`: Step finished with non-fatal advisory.
  - `"error"`: Step encountered a fatal error.
- `message` (`string`): Text-only explanation of stage progress or outcome.
- `timestamp` (`string`): ISO-8601 UTC timestamp.

---

### 6.4 Client Integration Examples

#### Example 1: cURL CLI
```bash
# 1. Probe Hardware
curl -X GET http://127.0.0.1:8000/api/hardware

# 2. Query Ecosystem Health
curl -X GET http://127.0.0.1:8000/api/health

# 3. Trigger Clean Install Pipeline
curl -X POST http://127.0.0.1:8000/api/pipeline/run \
     -H "Content-Type: application/json" \
     -d '{"target_project": null}'

# 4. Synchronize Centralized Manifests
curl -X POST http://127.0.0.1:8000/api/manifests/sync

# 5. Clean Cache Artifacts
curl -X POST http://127.0.0.1:8000/api/clean \
     -H "Content-Type: application/json" \
     -d '{"project": "lemgendary-training-suite"}'

# 6. Validate Codebase Syntax & Zero-Emoji Rule
curl -X POST http://127.0.0.1:8000/api/validate \
     -H "Content-Type: application/json" \
     -d '{}'
```

#### Example 2: Python Client (`httpx` + `websockets`)
```python
import httpx
import asyncio
import websockets
import json

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws/log"

async def monitor_pipeline():
    # 1. Trigger pipeline via REST
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        resp = await client.post("/api/pipeline/run", json={"target_project": None})
        print(f"Trigger Status: {resp.json()}")

    # 2. Stream events over WebSocket
    async with websockets.connect(WS_URL) as ws:
        print("Connected to telemetry stream...")
        while True:
            msg = await ws.recv()
            event = json.loads(msg)
            print(f"[{event['step_number']}/{event['total_steps']}] {event['step_name']}: {event['message']}")
            if event['step_name'] == "Health Matrix" and event['status'] in ["success", "error"]:
                print("Pipeline execution finished.")
                break

if __name__ == "__main__":
    asyncio.run(monitor_pipeline())
```

#### Example 3: JavaScript / TypeScript (`fetch` + WebSocket)
```typescript
const API_BASE = "http://127.0.0.1:8000";
const WS_URL = "ws://127.0.0.1:8000/ws/log";

// 1. Fetch Ecosystem Health
async function getHealth() {
  const res = await fetch(`${API_BASE}/api/health`);
  const data = await res.json();
  console.log("Projects:", data.projects);
}

// 2. Trigger Clean Install Pipeline
async function triggerPipeline(targetProject: string | null = null) {
  const res = await fetch(`${API_BASE}/api/pipeline/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_project: targetProject }),
  });
  const data = await res.json();
  console.log("Pipeline initiated:", data);
}

// 3. Connect to WebSocket Log Stream
function listenToLogs() {
  const ws = new WebSocket(WS_URL);

  ws.onopen = () => console.log("Connected to telemetry stream");
  ws.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    console.log(`[${payload.step_name}] ${payload.message}`);
  };
  ws.onclose = () => console.log("WebSocket disconnected");
}
```

---

## 7. Troubleshooting & Remediation

### Problem: `lem-env: command not found`
- **Cause**: The package was not installed in the active Python environment or the script directory is not in system PATH.
- **Remediation**:
  ```bash
  cd lemgendary-env-manager
  python -m pip install -e .
  ```
  Alternatively, run commands via the PowerShell launcher:
  ```powershell
  .\lemgendary_env_manager.ps1 probe
  ```

### Problem: PyTorch Installs CPU Version Instead of CUDA
- **Cause**: Default PyPI index was used instead of the PyTorch hardware wheel repository.
- **Remediation**: Run `lem-env probe` to inspect detected accelerators. If your NVIDIA GPU is recognized, run `lem-env install` which automatically passes `--extra-index-url https://download.pytorch.org/whl/cu121`.

### Problem: `WebSocket Connection Offline` in Desktop GUI
- **Cause**: The FastAPI sidecar server is not running on port 8000.
- **Remediation**: In a separate terminal, start the server:
  ```bash
  lem-env serve --port 8000
  ```
  The GUI will automatically reconnect within 3 seconds.

### Problem: Codebase Validation Fails with `Emoji Violation`
- **Cause**: One or more source files contain unicode emoji characters, violating the strict text-only code standard.
- **Remediation**: Inspect the terminal output of `lem-env validate` to identify the file and line number. Replace the emoji with standard text tokens (e.g. replace emoji with `[OK]`, `[FAIL]`, `[INFO]`, or standard ASCII characters).
