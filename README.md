# LemGendary Environment Manager — Changelog

All notable changes to LemGendary Environment Manager are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## [2.2.0] — 2026-09-13

### Added

- **Ecosystem Authority Centralization**: Retired legacy per-project `verify_*.py` files and absorbed specialized domain verification logic into `env_manager.validator` (`run_domain_verification`).
- **Local NPM Toolchain**: Added `package.json` with devDependencies (`eslint`, `html-validate`, `markdownlint-cli`, `pa11y`, `typescript`) installed locally in `node_modules/.bin` so all audits execute with zero external download overhead.
- **Option [2] NPM Package Dependency Matrix**: Extended `audit` command and API endpoint (`GET /api/v1/npm`) to inspect and display declared vs installed versions for each npm package with health status.
- **Option [3] Clean Install Pipeline Overhaul**: Added `--clean / --no-clean` flag to completely purge `.venv` and `node_modules` before fresh environment recreation.
- **Standardized Git Hook Management (`lem-env setup-hooks`)**: Added `env_manager/hooks.py` and `lem-env setup-hooks` command to automatically install and repair pre-commit hooks across all ecosystem projects, delegating pre-commit validation to `lem-env validate` and preventing commits on failures.
- **Git Hook Integrity Audit Gate**: Proactively audits `.githooks/` and `.git/hooks/` in `validator.py` (`_audit_git_hooks`), catching references to retired or missing scripts before commits run.
- **Purged Duplicate Manifests**: Cleaned obsolete duplicate requirements manifests in `lemgendary-env-manager/requirements/`, standardizing on canonical `requirements-training.txt`, `requirements-datasets.txt`, and `requirements-env-manager.txt`.
- **Dynamic System Path Discovery**: Replaced all hardcoded system drive paths (`C:\`) with dynamic environment variables (`ProgramFiles`, `ProgramFiles(x86)`) in `bootstrap.py` and `system_probe.py`.
- **RFC 8259 JSON Validation Gate**: Added `run_jsonlint()` to `validator.py` ensuring strict JSON syntax across all ecosystem configuration and metadata files.
- **Dedicated Colab & Kaggle Notebook Copy Exports**: Enhanced `lemgendary-training-suite/training/notebook_generator.py` to automatically save synchronized copies of `*_colab_training.ipynb` to `colab/` and `*_training.ipynb` to `kaggle/`.
- **Master Ecosystem Manuals**: Created consolidated `MANUAL_CLI.md` (`cli-manual.html`) and `MANUAL_API.md` (`api-manual.html`) in `lemgendary-docs` covering all seven repositories.

### Changed

- `validator.py`: Binary resolution `_resolve_tool_cmd()` now checks project-local `node_modules/.bin`, global PATH, `lemgendary-env-manager`, and `npx`.
- Centralized `.markdownlint.json` at root level, enabling sibling header deduplication and purging per-project duplicate configurations.
- `.gitignore`: Placed `node_modules/`, `package-lock.json`, and `.w3c_cache.json` at the top of the file to guarantee clean git tracking across all tools.

---

## [2.1.0] — 2026-09-12

### Added

- `env_manager/_logging.py`: Centralized structured logging module (`get_logger()`).
  All probe and linter functions now emit `WARNING`/`DEBUG` via named loggers instead
  of silently swallowing exceptions.
- `env_manager/updater.py`: New Safe Package Update module (`build_upgrade_plan`,
  `apply_upgrade_plan`). Option [4] in the PS1 menu is now a bottom-up safe upgrader
  instead of a manifest sync shortcut.
- `run_psscriptanalyzer()` function in `validator.py`: PSScriptAnalyzer integration
  for linting all `.ps1` scripts in managed projects. Requires
  `Install-Module PSScriptAnalyzer -Scope CurrentUser -Force`.
- Post-install validation gate in `orchestrator.py`: Manifest sync now only runs
  after `validate_project()` passes for all modified projects in the pipeline.
- Post-update validation gate in `cli.py` `update` command: After `apply_upgrade_plan`
  completes, `validate_project()` runs on all modified projects; sync is gated on pass.
- MetaTrader 5 detection in `system_probe.py` and `bootstrap.py` using
  `Get-Package -Name '*MetaTrader*'` (primary) with well-known path and registry
  fallbacks.
- Software update availability check via `winget upgrade --id` for Python and MT5.
- `npm_manager.py`: Node.js and NPM workspace audit module. Tracks `package.json`,
  `node_modules` presence, and dependency counts for `lemgendary-ai-studio-gui`.
- `venv_manager.py`: `discover_projects()` now returns `lemgendary-ai-studio-gui` as
  `is_node_project=True`.
- `health_checker.py`: `audit_npm_workspaces()` call integrated into
  `run_full_health_audit()`. New **Node.js & NPM Workspace Status** table in CLI output.
- MetaTrader 5 row added to the **Prerequisites & Toolchain** table in `lem-env audit`.
- `orchestrator.py`: Step 4 now runs `npm install` for `lemgendary-ai-studio-gui`
  alongside `pip install` for Python projects.
- `server.py`: New `POST /api/update` endpoint to trigger safe package upgrades
  via the REST API.
- `requirements/requirements-env-manager.txt`: Added `yamllint` dependency so
  YAML linting works from the env-manager venv across all managed repos.
- `lemgendary_env_manager.ps1`: Server (Option [6]) now runs as a background
  PowerShell job — menu remains usable while server is active.

### Changed

- `lemgendary_env_manager.ps1`: Rewrote main loop as `do { } while` — all menu
  options now return to the menu instead of exiting.
- `validator.py`: Expanded validation suite to cover all 7 repositories:
  ESLint + TypeScript for GUI, markdownlint via npx for all repos,
  yamllint via env-manager venv for all YAML files, W3C `html-validate`
  and WCAG 2.2 AA `pa11y` for `lemgendary-docs` static HTML.
- Option [4] in PS1 menu renamed from "Synchronize Requirements Manifests" to
  "Execute Safe Package Update Pipeline".
- `server.py`: Fixed critical crash — replaced `asyncio.run()` inside thread pool
  with `asyncio.run_coroutine_threadsafe()` to prevent "Event loop already running"
  `RuntimeError`.
- All `except Exception: pass` blocks replaced with structured logging:
  WARNING for tool/network failures, DEBUG for expected parse misses.

### Fixed

- Silent crash in `asyncio` WebSocket broadcast when pipeline ran in background thread.
- Silent swallowing of `nvidia-smi`, `rocm-smi`, `winget`, `Get-Package`, `npm`,
  `yamllint`, `ESLint`, `tsc`, `html-validate`, and `pa11y` failures.
- PEP 508 marker evaluation failures in `health_checker.py` now logged as DEBUG
  instead of silently keeping wrong counts.
- `requirements_manager.py`: `packaging.Requirement` parse failures now logged at
  DEBUG so unexpected line formats are surfaced.

---

## [2.0.0] — 2026-07-15

### Added

- Full Python package extraction: `env_manager/` package with CLI (`typer`),
  FastAPI sidecar server, and modular architecture.
- `lem-env` CLI entrypoint via `pyproject.toml`.
- 7-stage Smart Clean Install Pipeline in `orchestrator.py`.
- `system_probe.py`: Hardware discovery (CUDA, ROCm, DirectML, CPU).
- `health_checker.py`: Cross-project dependency drift matrix.
- `requirements_manager.py`: Centralized manifest sync.
- `validator.py`: py_compile + zero-emoji validation.
- `server.py`: FastAPI + WebSocket telemetry server.
- Centralized manifests in `requirements/` directory.

### Changed

- Replaced monolithic `lemgendary_env_manager.ps1` with Python-native engine.
- Sibling projects (`lemgendary-training-suite`, `lemgendary-datasets`) no longer
  maintain their own environment management scripts.

---

## [1.0.0] — 2026-01-01

### Added

- Initial monolithic PowerShell script-based environment management.
- Manual pip install and venv creation for training-suite and datasets.

---

## Documentation

- [Technical Whitepaper & Operations Manual (Markdown)](../lemgendary-docs/MD-Papers/PAPER_ENV_MANAGER.md)
- [Technical Whitepaper & Operations Manual (HTML)](../lemgendary-docs/papers/env_manager.html)
- [REST API Reference (Markdown)](../lemgendary-docs/MD-Papers/API_ENV_MANAGER.md)
- [REST API Reference (HTML)](../lemgendary-docs/papers/api_env_manager.html)
- [CLI Reference (Markdown)](../lemgendary-docs/MD-Papers/CLI_ENV_MANAGER.md)
- [CLI Reference (HTML)](../lemgendary-docs/papers/cli_env_manager.html)
