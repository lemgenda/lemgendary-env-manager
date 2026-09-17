# LemGendary Environment Manager (v2.0.0)

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
