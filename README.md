# LemGendary Environment Manager — Changelog

All notable changes to LemGendary Environment Manager are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## [2.3.0] — 2026-09-15

### Added

- **Reverse-Dependency Safety Classifier**: `dependency_resolver.py` now uses `pip inspect --local` to build a reverse-dependency graph and blocks any upgrade whose target version would violate an already-installed package's requirement. Catches the `astroid`/`pylint` class of coupling that pip's forward-only resolver misses.
- **Constraint-Preserving Dry-Run**: `constraint_preserving_dry_run()` writes every non-target installed package into a temporary pip constraints file, forcing the resolver to find a solution that does not downgrade or remove anything. Used as a second verification pass behind the reverse-dependency check.
- **Corruption-Aware Pip Recovery**: `run_pip_with_recovery()` detects `access violation`, `Retry(total=`, hash-mismatch, and truncated-download signatures in pip's output, purges the HTTP cache, and retries once with `--no-cache-dir`. Any other failure (resolver conflict, missing package, auth) returns immediately without retrying.
- **UTF-8 Subprocess Environment**: `pip_env()` forces `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1` on every pip subprocess, preventing pip's rich formatter (unicorn emoji, non-breaking hyphens, arrows) from crashing the process on Windows cp1252 consoles.
- **CUDA-Aware Torch Upgrade Path**: `_check_torch_cuda_upgrades()` resolves `torch`, `torchvision`, and `torchaudio` against the detected CUDA index (`download.pytorch.org/whl/cu121`), not PyPI. Only packages already installed in each project are considered, preventing accidental installation of `torchaudio` into a project that only uses `torch` and `torchvision`.
- **Torch-Family Rollback Guard**: Each torch-family upgrade snapshots the venv via `pip freeze`, applies the upgrade, runs `pip check`, and rolls back from the snapshot if the environment is left in a broken state.
- **`PROTECTED_FROM_SYNC` Set**: `astroid`, `pylint`, `pydantic`, and `pydantic-core` are no longer overwritten by the manifest sync step. This prevents a transient upgrade-time drift from being permanently encoded into `requirements-*.txt`.
- **OpenCV Variant Normalizer**: `normalize_opencv_variant()` uninstalls every `cv2` provider except the desired one and force-reinstalls the target variant with `--no-deps`. Datasets projects are normalized to `opencv-contrib-python`; training-suite is left alone by design.
- **Manifest Coverage Table**: New `compute_manifest_coverage()` reconciles each manifest's declared set against its installed set, surfacing declared-but-missing, installed-but-undeclared, and platform-skipped packages separately.
- **Single-Manifest Package Inventory**: New `compute_single_manifest_packages()` lists every package declared in exactly one manifest. This is what the drift matrix intentionally filters out; the inventory now closes that visibility gap.
- **NPM Workspace Drift Matrix**: New `compute_npm_drift()` and `render_npm_drift_matrix()` show version drift for every package declared in two or more npm workspaces.
- **`GET /api/drift` Endpoint**: Returns Python drift entries, manifest coverage rows, single-manifest packages, and npm drift entries in one compact payload. Accepts `?include_safety=true` to populate safe-upgrade classification.
- **`include_safety` Query Parameter**: `/api/health` and `/api/drift` now accept a boolean query parameter to opt into per-project pip dry-run classification. Default is `false` for fast polling; `true` adds 15–40 seconds per Python project.
- **`audit --fast` Flag**: Skips the safety classification dry-runs. The audit completes in seconds but the Safe-↑ column is left empty.
- **PowerShell 7 Terminal Profile**: `.vscode/settings.json` defines a `PowerShell 7 (Clean)` terminal profile that launches `pwsh.exe` and relies on the profile to set `$PSNativeCommandUseErrorActionPreference = $true`. This eliminates spurious `NativeCommandError` output from pip's stderr notices.
- **Winget Alias Routing**: `bootstrap.py` invokes winget via `cmd /c winget` when the resolved path lives under `%LOCALAPPDATA%\Microsoft\WindowsApps\`. This avoids `WinError 1920` (`ERROR_CANT_ACCESS_FILE`) when the pipeline runs inside an IDE-spawned job object.
- **Registry-First MT5 Detection**: `system_probe.py` checks four registry keys (`HKCU`/`HKLM` × `SOFTWARE`/`WOW6432Node`) for `MetaQuotes\Terminal` before falling back to well-known paths and `Get-Package`. Detection latency drops from 10–40 seconds to ~5 milliseconds.
- **MT5 File-Version Extraction**: When a registry or filesystem path is available, `system_probe.py` reads `terminal64.exe`'s `FileVersion` via PowerShell's `VersionInfo` property. This gives an authoritative version number even when winget has no record of the installation.
- **Winget Version Lookup**: `bootstrap.py` now runs `winget list --id X --exact` (installed version) and `winget show --id X --exact` (available version) alongside `winget upgrade`. Both commands succeed whether or not an upgrade is available, replacing the `?` placeholder with real numbers.
- **`source_tracked` Field**: `SoftwareUpdateInfo` now carries a boolean indicating whether winget knows about the package. The CLI displays `INSTALLED (not in winget)` instead of collapsing the state to `UNKNOWN`.
- **Type Stubs for Python Projects**: `types-PyYAML`, `types-requests`, `types-psutil`, and `types-beautifulsoup4` added to datasets, training, and env-manager manifests so Pyrefly can resolve imports without warnings.

### Changed

- **`venv_manager.py`**: `create_venv()` and `is_venv_valid()` no longer apply special-case logic that could silently approve a deleted environment. The `or True` branch in `is_venv_valid` and the `sys.executable` fallback in `get_venv_python_path` were removed. The orchestrator's active-venv detection moved into `orchestrator.py` where it belongs.
- **`orchestrator.py`**: Step 3 skips the clean purge for the venv currently executing the pipeline, logging a warning instead of deleting the environment out from under the running process.
- **`updater.py`**: `apply_upgrade_plan()` resolves the installed torch-family subset per project and passes only that subset to `_apply_torch_cuda_upgrade`. When nothing would change, a `VERIFIED` event with current versions replaces the previous misleading `UPGRADED` event.
- **`health_checker.py`**: `compute_version_drift()` discovers shared packages dynamically by counting declarations across manifests. The previous hardcoded 11-item allowlist (`shared_candidate_keys`) was removed.
- **`health_checker.py`**: `PinInfo` now includes a `pin_type` field (`exact` / `range` / `floating` / `transitive` / `absent`) and `UpgradeStatus` records `current`, `latest`, `safe`, and `reason` per project.
- **`health_checker.py`**: `_load_project_manifest()` records platform-skipped entries in a `platform_skipped` list rather than dropping them silently.
- **`validator.py`**: `run_eslint()` uses `--format json` for exact parsing. The previous compact-format regex plus broad `" error "` fallback produced false positives from Browserslist warnings and deprecation notices.
- **`validator.py`**: `run_psscriptanalyzer()` prefers `pwsh` over `powershell`, writes the file list to a temp file instead of concatenating paths into a single command line, and correctly parses Windows drive-letter paths in PSSA output.
- **`validator.py`**: `run_yamllint()` and `run_jsonlint()` use `env=pip_env()` so the env-manager's yamllint subprocess inherits UTF-8 encoding.
- **`npm_manager.py`**: `run_npm_install()` uses `encoding="utf-8"`, `errors="replace"`, `env=pip_env()`, and a 600-second timeout. `check_npm_package()` correctly resolves scoped packages under `node_modules/@scope/name`.
- **`server.py`**: `@app.on_event("startup")` replaced with an `@asynccontextmanager` lifespan handler. The background event-drain task is cancelled cleanly on shutdown instead of being fire-and-forget.
- **`server.py`**: `lifespan()` is annotated as `AsyncGenerator[None, None]` rather than `AsyncIterator[None]`. Python 3.13 deprecated the `AsyncIterator` overload of `@asynccontextmanager` for context-manager use.
- **`bootstrap.py`**: `check_metatrader5()` delegates to `probe_metatrader5()` from `system_probe.py`, eliminating a duplicate detection path.
- **`requirements-datasets.txt`**: `pandas-ta==0.4.71b0` replaced with `pandas-ta-classic==0.6.52`. The former pinned `numba==0.61.2`, which in turn pinned `numpy<2.3`. The maintained fork makes `numba` optional, lifting the constraint.
- **`requirements-datasets.txt`**: `numpy` bounded to `>=2.2.6,<2.6`, matching the last successful build and allowing bug-fix minors without crossing into untested territory.
- **`requirements-datasets.txt`**: `opencv-python` removed from the top-level list. `mediapipe` pulls `opencv-contrib-python` transitively; the normalizer removes `opencv-python` after install.
- **`requirements-training.txt`**: `pandas-ta` replaced with `pandas-ta-classic==0.6.52`, `numpy` bounded to `>=2.2.6,<2.6`.
- **`requirements-env-manager.txt`**: `astroid` reverted to `4.0.4` (pylint 4.0.8 requires `astroid<=4.1.dev0`). Added an explanatory comment and the `PROTECTED_FROM_SYNC` entry in `updater.py` prevents future overwrites.
- **`requirements-datasets.txt` / `requirements-training.txt`**: PyTorch index URL corrected from `https://pytorch.org` to `https://download.pytorch.org/whl/cu121`.
- **`.vscode/settings.json`**: `terminal.integrated.defaultProfile.windows` set to a clean `pwsh.exe` profile; `terminal.integrated.automationProfile.windows` matches, ensuring both interactive terminals and tasks use PowerShell 7.

### Fixed

- **Active venv self-deletion**: The Smart Clean Install Pipeline deleted `lemgendary-env-manager/.venv` while executing from it, producing `No pyvenv.cfg file` on every subsequent pip call. The orchestrator now detects and skips the running venv.
- **`is_venv_valid` false positive**: The `or True` branch in `venv_manager.is_venv_valid` caused the manager to approve its own deleted venv. Removed.
- **PyTorch CUDA index URL**: `--extra-index-url https://pytorch.org` was not a valid pip index. Corrected to the CUDA-specific URL in both GPU-enabled manifests.
- **`pandas-ta` → `numba` → `numpy` resolution lock**: The old `pandas-ta` pinned `numba==0.61.2` which capped `numpy` below 2.3. Migrating to `pandas-ta-classic` broke the chain.
- **`astroid` upgrade corrupting the manifest**: An earlier pipeline run upgraded `astroid` to `4.3.1`, which pylint 4.0.8 rejects, and then `sync_manifests_from_venvs` wrote the incompatible version back into the manifest. Fixed on three levels: reverse-dep check blocks the upgrade at plan time, `PROTECTED_FROM_SYNC` prevents the manifest rewrite, and the manifest now carries the correct pin.
- **Pip cache corruption crash**: `access violation writing 0x0000000000000048` during a torch download is caused by a corrupt wheel in the HTTP cache. `run_pip_with_recovery` now detects the signature, purges the cache, and retries.
- **Windows cp1252 console crash**: pip's rich formatter emits non-ASCII characters that crash the subprocess on cp1252 consoles. `pip_env()` forces UTF-8 on every pip invocation.
- **PowerShell 5.1 `NativeCommandError` on pip notices**: pip writes advisory notices to stderr; PS 5.1 conflates stderr with failure. Fixed by adopting PowerShell 7.4+ and setting `$PSNativeCommandUseErrorActionPreference = $true` in the shell profile.
- **`WinError 1920` when invoking winget**: The App Execution Alias stub in `%LOCALAPPDATA%\Microsoft\WindowsApps\` cannot be resolved by `CreateProcess` inside IDE-spawned job objects. Routed through `cmd /c winget`.
- **Phantom `torchaudio` install in datasets**: The CUDA dry-run was run against all three torch-family names regardless of what was installed, causing `torchaudio` to be added to a project that never declared it. Now filtered to the installed subset.
- **Misleading `[UPGRADED]` events with no versions**: `apply_upgrade_plan` emitted a generic torch-family event even when nothing was upgraded. Now emits `[VERIFIED]` with the current versions when the CUDA index has no newer build.
- **`yamllint` exit code 106 misread as validation failure**: The validator now distinguishes yamllint's exit code `1` (lint findings, expected) from `2+` (tool error, silent).
- **Duplicate `run_jsonlint`**: `validator.py` defined `run_jsonlint` twice. The second definition shadowed the first. Removed the dead copy.
- **PSScriptAnalyzer path parsing**: Windows drive-letter paths (`C:\...`) broke the naive `split(":", 3)` parser. Now re-joins the drive component before splitting severity and message.
- **PSScriptAnalyzer 32K command-line limit**: Concatenating every `.ps1` path into a single command line would eventually exceed the Windows `CreateProcess` limit. Now written to a temp file.
- **FastAPI `on_event` deprecation**: Replaced with lifespan; the drain task is cancelled cleanly on shutdown.
- **`AsyncIterator` deprecated overload**: Changed to `AsyncGenerator[None, None]` in the lifespan signature.
- **`MT5 version: Unknown`**: The `probe` command now shows the file version read from `terminal64.exe`, and `winget list` + `winget show` populate real version numbers for both the installed and available columns.
- **`?` placeholders in the Software Update Availability table**: Replaced with `—` for genuinely-unknown values and `INSTALLED (not in winget)` for MT5, which lives outside winget's package database.
- **MetaTrader 5 `Get-Package` timeout on every probe**: Step 1 of the pipeline no longer spends 10 seconds per run waiting for `Get-Package` to time out. Registry probe runs first.

### Removed

- `venv_manager.py`: `or True` branch in `is_venv_valid` that always approved `lemgendary-env-manager` as valid.
- `venv_manager.py`: `sys.executable` fallback in `get_venv_python_path` that silently redirected pip operations into the wrong interpreter.
- `validator.py`: Duplicate `run_jsonlint` definition (dead code).
- `health_checker.py`: `shared_candidate_keys` hardcoded allowlist.
- `server.py`: `@app.on_event("startup")` decorator.

---

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

- [Technical Whitepaper (Markdown)](../lemgendary-docs/MD-Papers/PAPER_ENV_MANAGER.md)
- [Technical Whitepaper (HTML)](../lemgendary-docs/papers/env_manager.html)
- [REST API Reference (Markdown)](../lemgendary-docs/MD-Papers/MANUAL_API.md)
- [REST API Reference (HTML)](../lemgendary-docs/papers/api-manual.html)
- [CLI Reference (Markdown)](../lemgendary-docs/MD-Papers/MANUAL_CLI.md)
- [CLI Reference (HTML)](../lemgendary-docs/papers/cli-manual.html)
