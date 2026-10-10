# LemGendary Environment Manager (v16.8.0-STABLE)

> **Autonomous Environment Governance and Sidecar Service for LemGendary AI Suite.**
>
> High-performance hardware probing, multi-project virtual environment lifecycle management, dependency drift detection, safe upgrade orchestration, and automated workspace compliance validation.

---

## Architecture Overview

`lemgendary-env-manager` functions as both a standalone CLI automation utility and an asynchronous HTTP/WebSocket sidecar service powering developer tooling and the **LemGendary AI Studio Desktop GUI** (`lemgendary-ai-studio-gui`).

```text
+-------------------------------------------------------------------------+
|                  LemGendary AI Studio Desktop GUI                       |
+------------------------------------+------------------------------------+
                                     |
                                     v HTTP / WebSocket (Port 8000)
+------------------------------------+------------------------------------+
|                   lemgendary-env-manager Sidecar                        |
|                                                                         |
|  +-------------------+  +--------------------+  +--------------------+  |
|  |   System Probe    |  |    Venv Manager    |  |   Health Checker   |  |
|  |  CUDA / TensorRT  |  | Project Discovery  |  | Python / NPM Drift |  |
|  +-------------------+  +--------------------+  +--------------------+  |
|  +-------------------+  +--------------------+  +--------------------+  |
|  |  Updater Engine   |  |   Orchestrator     |  | Codebase Validator |  |
|  | Bottom-Up Upgrade |  | Clean Installation |  | Lints / Compliance |  |
|  +-------------------+  +--------------------+  +--------------------+  |
|  +-------------------------------------------------------------------+  |
|  |                    GUI Aggregation Endpoints                      |  |
|  |         /api/gui/state             /api/gui/ecosystem             |  |
|  +-------------------------------------------------------------------+  |
+------------------------------------+------------------------------------+
                                     | Probe (Port 8100)
                                     v
+------------------------------------+------------------------------------+
|                  lemgendary-datasets Sidecar                           |
+-------------------------------------------------------------------------+
```

---

## Key Capabilities

### 1. Hardware Probing & Platform Intelligence

- Deep hardware detection across Windows architectures via `env_manager.system_probe`.
- Queries NVIDIA driver versions, CUDA toolkit capabilities, cuDNN, TensorRT library availability, and PyTorch CUDA build compatibility.
- Recommends tailored PyTorch index URLs (e.g. `https://download.pytorch.org/whl/cu128` or `cu124`).

### 2. Multi-Project Virtual Environment Lifecycle

- Automatic discovery of all sub-projects in the workspace (`lemgendary-datasets`, `lemgendary-ai-studio-gui`, `lemgendary-docs`, etc.).
- Manifest reconciliation between declared dependency manifests (`requirements/*.txt`, `package.json`) and installed packages.
- Detection of virtual environment corruption, broken symlinks, and orphaned site-packages.

### 3. Dependency Drift Detection & Safe Upgrades

- Cross-project package version comparison to eliminate version mismatch regressions.
- Isolated dry-run dependency resolution via pip to evaluate upgrade safety prior to execution.
- Bottom-up upgrade pipeline with automatic rollback snapshots.

### 4. Codebase Compliance & Lint Validation

- Zero emoji policy enforcement across Python source, docstrings, JSON, YAML, and documentation.
- Zero suppression policy: detects and flags `# noqa`, `# type: ignore`, bare `except: pass`, and silent error swallows.
- Validation suites covering `py_compile`, ESLint, Pylint, W3C HTML, and structural metadata integrity.

### 5. GUI Integration & Ecosystem Observability

- Aggregated state endpoint (`/api/gui/state`) delivering consolidated host metrics, hardware profile, discovered projects, and active pipelines in a single non-blocking payload.
- Ecosystem status endpoint (`/api/gui/ecosystem`) monitoring both `lemgendary-env-manager` (port 8000) and `lemgendary-datasets` (port 8100) for GUI header status indicators.
- Contract freezing via exported `openapi.json` for TypeScript type generation.

---

## API Reference

The sidecar service binds to `http://127.0.0.1:8000` by default. Interactive documentation is available at `http://127.0.0.1:8000/docs`.

### Core Health & Telemetry Endpoints

- `GET /api/health?include_safety={bool}`: Full ecosystem health report including Python and NPM package drift.
- `GET /api/drift?include_safety={bool}`: Cross-project version drift, declared-vs-installed coverage, and single-manifest inventory.
- `GET /api/hardware`: Host hardware metrics (CPU, RAM, GPU, CUDA, PyTorch compatibility).
- `GET /api/projects`: Discovered project list with manifest and venv status.
- `GET /api/npm`: Audit results for NPM workspaces.

### Desktop GUI Aggregation Endpoints

- `GET /api/gui/state`: Unified payload aggregating host service information, hardware profile, project list, and pipeline status. Designed for rapid initial GUI state hydration.
- `GET /api/gui/ecosystem`: Multi-sidecar connectivity monitor probing the environment manager and dataset compiler sidecars.

### Orchestration & Maintenance Endpoints

- `GET /api/pipeline/status`: Current pipeline state and recent log events.
- `POST /api/pipeline/run`: Initiates an asynchronous clean install pipeline.
- `POST /api/update`: Executes safe package upgrades according to dependency graphs.
- `POST /api/clean`: Purges build caches, `__pycache__`, and temporary artifacts.
- `POST /api/validate`: Runs workspace compliance validation suites.
- `GET /api/manifests`: Retrieves declared manifest contents.
- `POST /api/manifests/sync`: Synchronizes requirements manifests across projects.
- `WS /ws/logs`: Real-time streaming log feed for pipeline and background tasks.

---

## CLI Usage

The environment manager can be operated directly from the terminal:

```bash
# Display general help and available commands
python -m env_manager.cli --help

# Run hardware probe
python -m env_manager.cli probe

# Discover workspace projects
python -m env_manager.cli discover

# Run ecosystem health and drift audit
python -m env_manager.cli audit

# Run validation suite on a specific project or the entire workspace
python -m env_manager.cli validate -p lemgendary-env-manager
python -m env_manager.cli validate -p lemgendary-datasets

# Clean temporary caches and artifacts
python -m env_manager.cli clean

# Start the sidecar API service
python -m env_manager.cli serve --host 127.0.0.1 --port 8000
```

---

## Release Artifacts

- `openapi.json`: OpenAPI 3.1 contract exported for client code generation in `lemgendary-ai-studio-gui`.
- `requirements/`: Canonical manifest directory governing ecosystem dependencies.

---

## Changelog

### v16.9.5 — Universal Zero Child Console Flashing & In-Process PE Inspection

- **Elimination of Child Process Console Flashing** — Configured `WINDOWS_NO_WINDOW` (`0x08000000`) across all `subprocess.run` invocations in `utils.py`, `system_probe.py`, `bootstrap.py`, `venv_manager.py`, `npm_manager.py`, `updater.py`, `hooks.py`, and `validator.py`. Completely prevents Windows from allocating temporary console or PowerShell windows during background hardware probing, health audits, package discovery, and toolchain checks.
- **In-Process PE FileVersion Reader** — Replaced external PowerShell PE version queries with native in-process `ctypes.windll.version.GetFileVersionInfoW` inspection for MetaTrader 5 executables, reducing probe latency to sub-millisecond with zero process allocations.
- **Unified Subprocess Flag Exporter** — Added `get_subprocess_creation_flags()` and `WINDOWS_NO_WINDOW` in `env_manager.utils` for consistent cross-module child process execution standards.

### v16.9.4 — Automated Documentation Test Battery Integration & Pre-Commit Hook Hardening

- **Automated Documentation Test Suite Gate** — Integrated `_audit_documentation_suite` directly into `run_domain_verification` in `env_manager/validator.py` for `lemgendary-docs`. Automatically executes the 20-rule documentation test suite (`tests/test_documentation.py`) covering LaTeX notation, zero emojis, category uniqueness, HTML structural integrity, and SSOT manifest alignment as a mandatory gate under `lem-env validate`.
- **Git Hook Root Directory Discovery Hardening** — Hardened pre-commit hook scripts across `.githooks/pre-commit` and `.git/hooks/pre-commit` with dynamic directory inspection (`HOOK_DIR`) ensuring proper relative resolution of `WORKSPACE_ROOT` and `lem-env` regardless of whether git executes the hook from `.githooks` or `.git/hooks`.

### v16.9.3 — Validator Manifest Directory Exclusion & Artifact Protection

- **Export and Cache Directory Masking** — Hardened `run_yamllint`, `run_jsonlint`, and `validate_project` in `env_manager/validator.py` with explicit exclusion of generator artifact folders (`export`, `runs`, `.lemtrain_server`, `.lgd_server`, `.env_server`). Eliminates false-positive lint failures caused by third-party training run configurations while preserving rigorous compliance enforcement across all source manifests.

### v16.9.2 — Windowless Cross-Platform Daemon Execution & Startup Polling Stabilization

- **Headless Windowless Daemon Execution** — Updated `service_manager.py` to launch background sidecars without spawning console or terminal windows. On Windows, preferentially selects `.venv/Scripts/pythonw.exe` (`IMAGE_SUBSYSTEM_WINDOWS_GUI`) and sets `CREATE_NO_WINDOW` (`0x08000000`) with `stdin=subprocess.DEVNULL`, preventing Windows 11 Windows Terminal from opening new windows. On Linux and macOS, sets `start_new_session=True` (`os.setsid()`) and runs `.venv/bin/python`.
- **Extended Startup Verification Window** — Increased sidecar health check timeout from 4.0s to 20.0s to accommodate heavy PyTorch/CUDA model import times on Windows without false timeout returns.
- **Automated Ecosystem Sidecar Startup on Boot** — Wired background `start_all_services()` directly into `lifespan` in `server.py`, ensuring all tripartite sidecars initialize on Environment Manager daemon startup.
- **Port Collision & Concurrency Protection** — Added `is_port_in_use()` guard to prevent duplicate process spawning during daemon startup transitions.
- **Dual GET & HEAD Health Endpoint Support** — Added `@app.head("/api/health")` decorator to ensure universal HTTP probe compatibility with desktop frontends.

### v16.9.1 — Ecosystem Sidecar Process Lifecycle Management & On-Demand Dispatch

- **On-Demand Sidecar Process Supervisor** — Added `env_manager.service_manager` to programmatically spawn, monitor, and gracefully terminate background sidecar daemons (`lemgendary-datasets` on port 8100, `lemgendary-training-suite` on port 8200) directly from the centralized orchestrator.
- **Service Control REST Endpoints** — Implemented `POST /api/services/{service_id}/start`, `POST /api/services/{service_id}/stop`, and `POST /api/services/start-all` on port 8000 with sub-second health verification, automated virtual environment resolution, and process PID tracking.
- **GUI Tripartite Integration** — Connected `lemgendary-ai-studio-gui` service cards to on-demand daemon startup, replacing pipeline re-installation triggers with non-destructive, isolated process dispatch.

### v16.9.0 — Server API Coverage, CLI Isolation & Comprehensive Testing Battery

- **Comprehensive 17-Test Quality Battery** — Implemented and verified full automated test suite (`tests/test_server_api.py`, `tests/test_cli.py`, `tests/test_bootstrap.py`, `tests/test_requirements_manager.py`, `tests/test_system_probe.py`, `tests/test_validator.py`) with 17/17 tests passing and 100% compliance under `lem-env validate`.
- **Dependency-Free Asynchronous Server API Testing** — Tested all FastAPI route handlers directly via coroutine invocation (`asyncio.run()`), eliminating external testing dependencies while achieving sub-second verification across hardware, project status, pipeline execution, and ecosystem mesh endpoints.
- **CLI Test Isolation & Performance Optimization** — Intercepted network-bound `winget` queries during CLI unit tests, reducing suite execution duration from 265s down to 15s with zero flakiness.
- **Tripartite Mesh Orchestration** — Integrated sidecar discovery and health status reporting across the three ecosystem daemons (8000, 8100, 8200) powering `lemgendary-ai-studio-gui`.

### v16.8.0 — Container Runtime Dependencies Synchronization & SSOT Alignment

- **Container Runtime Dependency Matrix Synchronization** — Upgraded centralized requirements manifests (`requirements-datasets.txt`, `requirements-training.txt`) with complete runtime support for unified streaming and columnar container engines: `mosaicml-streaming>=0.9.0,<1.0.0`, `litdata>=0.2.0,<1.0.0`, `webdataset==1.0.2`, `pyarrow==25.0.1`, and `zstandard>=0.23.0`.
- **Runtime Environment Specification Upgrade** — Updated `requirements/runtime_env.yaml` to Version 1.1, explicitly registering `container_runtime_dependencies` with package specifiers, target container formats (`mds`, `litdata`, `webdataset`, `parquet`, `zstd`), and architectural descriptions.
- **Cross-Project Manifest Synchronization** — Executed `sync_all_manifests()` from `env_manager.requirements_manager` to propagate sanitized, deterministic dependency manifests into `lemgendary-datasets/requirements.txt` and `lemgendary-training-suite/requirements.txt`.
- **Web Service Dependencies Alignment** — Reconciled training server sidecar requirements in `requirements-training.txt` including `fastapi`, `uvicorn`, `pydantic`, and `httpx`.
