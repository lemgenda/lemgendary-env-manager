# LemGendary Environment Manager (v16.9.67-STABLE)

> **Autonomous Environment Governance and Sidecar Service for LemGendary AI
> Suite.**
>
> High-performance hardware probing, multi-project virtual environment
> lifecycle management, dependency drift detection, safe upgrade
> orchestration, and automated workspace compliance validation.

---

## Architecture Overview

`lemgendary-env-manager` functions as both a standalone CLI automation utility
and an asynchronous HTTP/WebSocket sidecar service powering developer tooling
and the **LemGendary AI Studio Desktop GUI** (`lemgendary-ai-studio-gui`).

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

- Deep hardware detection across Windows architectures via
  `env_manager.system_probe`.
- Queries NVIDIA driver versions, CUDA toolkit capabilities, cuDNN, TensorRT
  library availability, and PyTorch CUDA build compatibility.
- Recommends tailored PyTorch index URLs (e.g.
  `https://download.pytorch.org/whl/cu128` or `cu124`).

### 2. Multi-Project Virtual Environment Lifecycle

- Automatic discovery of all sub-projects in the workspace
  (`lemgendary-datasets`, `lemgendary-ai-studio-gui`, `lemgendary-docs`, etc.).
- Manifest reconciliation between declared dependency manifests
  (`requirements/*.txt`, `package.json`) and installed packages.
- Detection of virtual environment corruption, broken symlinks, and orphaned
  site-packages.

### 3. Dependency Drift Detection & Safe Upgrades

- Cross-project package version comparison to eliminate version mismatch
  regressions.
- Isolated dry-run dependency resolution via pip to evaluate upgrade safety
  prior to execution.
- Bottom-up upgrade pipeline with automatic rollback snapshots.

### 4. Codebase Compliance & Lint Validation

- Zero emoji policy enforcement across Python source, docstrings, JSON, YAML,
  and documentation.
- Validation suites covering `py_compile`, yamllint, JSON syntax, markdownlint,
  ESLint, `tsc --noEmit`, html-validate (W3C), pa11y (WCAG 2.2 AA),
  PSScriptAnalyzer, and structural metadata integrity.
- Fail-closed gates: a lint tool that is missing, crashes, or exits non-zero
  without a parsable finding is reported as a `tool_error` violation, so a
  broken toolchain can never report `[PASS]`. Node tools are never downloaded at
  validation time (`npx --no`).
- Pylint over every Python project, from the project's own `.venv` when it has
  one and with the project's own Pylint config; a missing Pylint is a
  `tool_error`.
- Suppression gate: inline silencing comments in Python (read with `tokenize`,
  so strings do not count), `warnings` ignore filters (found in the AST),
  TypeScript/JavaScript `@ts-*` and `eslint-disable` comments, and Rust `allow`
  attributes.
- Absolute-path gate: drive-letter paths, file URLs that spell out a path, and
  POSIX home directories in source, config and documentation files.
- Ecosystem SSOT gate (`ecosystem_ssot`, warning severity): code,
  configuration and tests must not spell out service ports, the Kaggle
  owner, the docs URL or project folder lists (test fixtures excepted).
  Documentation (Markdown, HTML), the model and dataset registries and
  asset projects may, but only with the values in `ecosystem.json`. For the
  GUI it also checks the snapshot, CSP ports, pipeline steps, the
  `ProjectHealth` keys against `openapi.json` and `package.json` against
  its central manifest.
- Not implemented: detection of silent `except` blocks.
- Gates are pluggable: each one is a `Gate` in
  `env_manager/validation/registry.py` with the report list it fills and a
  predicate for the projects it applies to. A new gate is one more entry; the
  runner in `env_manager/validator.py` does not change.

### 5. GUI Integration & Ecosystem Observability

- Aggregated state endpoint (`/api/gui/state`) delivering consolidated host
  metrics, hardware profile, discovered projects, and active pipelines in a
  single non-blocking payload.
- Ecosystem status endpoint (`/api/gui/ecosystem`) monitoring both
  `lemgendary-env-manager` (port 8000) and `lemgendary-datasets` (port 8100) for
  GUI header status indicators.
- Contract freezing via exported `openapi.json` for TypeScript type generation.

---

## API Reference

The sidecar service binds to `http://127.0.0.1:8000` by default. Interactive
documentation is available at `http://127.0.0.1:8000/docs`.

### Core Health & Telemetry Endpoints

- `GET /api/ping` (also `HEAD`): Constant-time liveness probe with no I/O.
  Returns `{"status": "ok", "service": "lemgendary-env-manager", "version":
  str}`. Use it to check whether the sidecar is online.
- `GET /api/health?include_safety={bool}`: Full ecosystem health audit (slow:
  runs prerequisite, hardware and `pip list` checks). Includes Python and NPM
  package drift. Not a liveness probe; use `/api/ping` for that.
- `GET /api/drift?include_safety={bool}`: Cross-project version drift,
  declared-vs-installed coverage, and single-manifest inventory.
- `GET /api/hardware`: Host hardware metrics (CPU, RAM, GPU, CUDA, PyTorch
  compatibility).
- `GET /api/projects`: Health of each discovered project, typed as
  `ProjectHealth` (environment present, Python version, required versus
  installed packages, missing packages, healthy flag).
- `GET /api/npm`: Audit results for NPM workspaces.

### Desktop GUI Aggregation Endpoints

- `GET /api/gui/state`: Unified payload aggregating host service information,
  hardware profile, project list, and pipeline status. Designed for rapid
  initial GUI state hydration.
- `GET /api/gui/ecosystem`: Multi-sidecar connectivity monitor: one entry per
  service in `ecosystem/ecosystem.json` (`env_manager`, `dataset_compiler`,
  `training_suite`), probed on its liveness path.
- `GET /api/ecosystem`: The ecosystem registry (`ecosystem/ecosystem.json`)
  as a typed `Ecosystem` response: services, projects, pipeline steps and
  identity.

### Orchestration & Maintenance Endpoints

- `GET /api/pipeline/status`: Current pipeline state and recent log events.
- `POST /api/pipeline/run`: Initiates the asynchronous install pipeline. Body:
  `{"target_project": str | null, "clean": bool}`. Non-destructive by default:
  valid venvs and `node_modules` are kept. Only `"clean": true` deletes every
  `.venv` and `node_modules`, stops processes running from those venvs and
  reinstalls from scratch.
- `POST /api/pipeline/run-step`: Runs one pipeline step (1-7) in isolation.
  Body: `{"step_number": int, "target_project": str | null, "clean": bool}`;
  `clean` defaults to `false` with the same meaning.
- `POST /api/update`: Safe package upgrades according to dependency graphs.
  Body: `{"project": str | null, "dry_run": bool}`; `project` limits the run to
  one project (unknown names return `404`). A dry run returns the plan (`status:
  "dry_run"` or `"up_to_date"`). A real run starts a background job and returns
  at once with `{"status": "accepted", "job_id": str, "project": str | null}`;
  progress is streamed over `/ws/log`. Only one update runs at a time: a second
  request, or a request while the pipeline runs, returns `409`.
- `GET /api/update/status`: `{"is_running": bool, "job": {...} | null}` for the
  current or last update job (`status` is `running`, `up_to_date`, `success`,
  `partial` or `failed`, plus `events` and `error`).
- `POST /api/clean`: Purges build caches, `__pycache__`, and temporary
  artifacts.
- `POST /api/validate`: Runs workspace compliance validation suites.
- `GET /api/manifests`: Retrieves declared manifest contents.
- `POST /api/manifests/sync`: Copies the central manifests to the sibling
  projects. A sibling whose file the copy would downgrade or shrink is left
  unchanged and reported with `success: false` and a `DRIFT:` message.
- `GET /api/secrets`: Lists ecosystem secrets masked: each item has `has_value`
  and `value_hint` (last 4 characters, empty for values shorter than 8) and
  never the secret itself. Read-only; when `.secrets.yaml` does not exist yet,
  items are seeded in memory from legacy credential files (`initialized:
  false`).
- `POST /api/secrets`: Write-only save of the full secrets list. An empty or
  omitted `secret_value` keeps the stored value for the same `id`; a non-empty
  value replaces it; items left out are removed. A Kaggle token (new or kept) is
  mandatory. The response never echoes values and includes a `propagation`
  report (`written`, `skipped`, `failed`) for the legacy Kaggle files.
- `WS /ws/logs`: Real-time streaming log feed for pipeline and background tasks.

### Bug Report Endpoint

- `POST /api/bug-report` (`multipart/form-data`): Saves a bug report ZIP in the
  workspace and opens the mail client. No email is sent by the server and
  `mailto:` cannot attach files, so the message body names the archive and its
  relative path for the user to attach.
  - Fields: `email` (valid address), `module`, `description` (at least 50
    characters after trimming, at most 10000), `app_version`, `current_tab` (all
    required).
  - Files: up to 5 `screenshot` (`image/png` or `image/webp`, 5 MB each),
    optional `telemetry` (`text/csv`, UTF-8, 2 MB), optional `recording`
    (`video/webm` or `video/mp4`, 50 MB). File signatures (magic bytes) must
    match the declared type. Total uploads at most 80 MB.
  - Errors: `422` for invalid fields or uploads, `413` for size limits; `detail`
    lists `{"loc": ["body", field], "msg": str, "type": str}`.
  - Archive `bug_report_<UTC YYYYMMDDTHHMMSSZ>_<8-char id>.zip` contains
    `report.csv` (header row plus one row:
    `timestamp,report_id,email,module,current_tab,app_version,description,screenshot_count,has_recording,has_telemetry`),
    `telemetry.csv`, `screenshot_N.<ext>` and `recording.<ext>`. It is saved to
    `bug-reports/` at the workspace root (outside every repository).
  - Response: `report_id`, `archive_name`, `archive_path` (relative to the
    workspace root, forward slashes), `recipient`, `subject`,
    `mail_client_opened`, `folder_revealed`. If the mail client or file manager
    cannot be opened the flag is `false`; the request still succeeds because the
    archive is saved.

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

# Run the install pipeline (non-destructive: keeps valid venvs and node_modules)
python -m env_manager.cli install -p lemgendary-datasets

# Destructive clean install: deletes every .venv and node_modules, stops
# processes running from those venvs, then reinstalls from scratch
python -m env_manager.cli install --clean

# Clean temporary caches and artifacts
python -m env_manager.cli clean

# Start the sidecar API service (host and port default to ecosystem.json)
python -m env_manager.cli serve
```

---

## Release Artifacts

- `openapi.json`: OpenAPI 3.1 contract exported for client code generation in
  `lemgendary-ai-studio-gui`.
- `requirements/`: Canonical manifest directory governing ecosystem
  dependencies.

### Manifest Single Source of Truth

- The central files in `requirements/` (`requirements-*.txt` and
  `*.package.json`) are the only manifests to edit. Each sibling
  `requirements.txt` or `package.json` is a generated copy written by `lem-env
  sync`, `POST /api/manifests/sync` and pipeline steps 4 and 6.
- Sync is one-way (central to sibling) and never takes anything away from a
  sibling. It refuses to write, leaves both files unchanged and reports drift
  when the central copy would:
  - lower a version floor (`torch==2.14.1` over `torch==2.15.0`),
  - drop a pin (`pillow` over `pillow==13.0.0`), or
  - remove a package the sibling still lists.
- Equal pins and central upgrades are written as before. To resolve drift, raise
  the central pin (or remove the package from both files on purpose), then sync
  again.
- A project's own `package.json` `version` is its release number, not
  dependency information. The central `*.package.json` for the GUI does not
  hold it, drift ignores it and sync keeps the sibling's value.

### Ecosystem Single Source of Truth

- `ecosystem/ecosystem.json` is the one place that defines ecosystem facts;
  `ecosystem/ecosystem.schema.json` (JSON Schema draft 2020-12) describes it
  and a test validates the file against it. Top-level keys:
  - `services[]`: `id`, `aliases`, `project`, `port`, `health_path`,
    `liveness_path`, `websocket_path`, `app_import`, `log_dir`.
  - `projects[]`: `folder`, `display_name`, `kind` (`python`, `node`,
    `asset`), `managed`, `validated`, `upgrade_order`, `git_hooked`,
    `requirements_manifest`, `npm_manifest`, `legacy_folders`.
  - `pipeline_steps[]`: `number`, `id`, `name`, `short_description`,
    `long_description`.
  - `identity`: `docs_url`, `kaggle_owner`, `bind_host`.
- Python code reads it through `env_manager.ecosystem.ecosystem()` (parsed
  once and cached). `LEMGENDARY_ECOSYSTEM_FILE` overrides the location;
  otherwise this repository's own file is used, then the copy the wheel
  installs under `<prefix>/share/lemgendary-env-manager/ecosystem/`. A
  missing or invalid file stops with an error naming the expected path.
- Service ports and URLs, the validated, managed, hooked and asset-store
  project lists, the upgrade order, manifest names, the pipeline step table
  and the documentation URL are all derived from it. Edit the file, not the
  code. Other repositories and the GUI read the same file (or
  `GET /api/ecosystem`).

## Ecosystem SSOT Gate

The `ecosystem_ssot` validation gate fails validation (severity `error`,
`ECOSYSTEM_SSOT_SEVERITY` in `validation/gates/ecosystem_ssot/__init__.py`)
when a repository spells out a fact that belongs to a single source.

Single-source files:

- Env-manager `ecosystem/ecosystem.json`: service ports and paths, the
  Kaggle owner, the docs URL, the bind host, the project list and the
  pipeline steps.
- Datasets `task_taxonomy.json` (task ids and strategies) and
  `unified_data.yaml` (dataset registry records).
- Training `unified_models_v2.yaml` (model registry records).
- GUI snapshot `src/generated/ecosystem.json`: a copy of the env-manager
  file that must equal it.
- GUI `package.json`: must equal the central
  `requirements/lemgendary-ai-studio-gui.package.json` in every field except
  the GUI's own release `version` (finding `ssot_npm_manifest`, skipped with
  a note when either file is absent).

Allowlist (`allowlist.py`): the files above, the ecosystem schema,
`src-tauri/tauri.conf.json` (its CSP ports are checked against the
services), `openapi.json`, lockfiles, `REVIEW_REPORT.md`, `CHANGELOG*`,
README changelog sections and `fixtures` folders below a test folder are
not scanned. Documentation and registry files may name a port, owner or
URL, but only with the registry value. Everything else (code, configuration,
tests) may not.

Skips and failures: a check that cannot run because an optional input is
absent (a sibling project not checked out, a GUI that keeps its own step
table, a missing OpenAPI file) leaves a logged `ssot_skipped` note and does
not fail. A source file that exists but cannot be parsed is an
`ssot_unreadable` finding and fails.

To fix a finding: replace the literal with a read from `ecosystem.json`
(`env_manager.ecosystem.ecosystem()` in Python, the generated snapshot in
the GUI); for a stale snapshot, copy the env-manager file over
`src/generated/ecosystem.json`; for a wrong value in documentation, correct
it to the registry value. Do not add an allowlist entry for a real
duplicate. Run `lem-env validate` to check.

---

## Changelog

### v16.9.68 - Resilient Netstat Port Holder Discovery & Clean Subprocess Teardown

- `env_manager/service_manager.py`: Hardened `_find_port_pids_netstat` to parse
  both standard `0.0.0.0:0` listeners and Windows loopback endpoints (`127.0.0.1:0`
  or `*:*`) so lingering port holder processes on ports 8000, 8100, and 8200
  are discovered and terminated during service stop requests.
- `env_manager/service_manager.py`: Preserved direct PID registration on detached
  server spawns to avoid orphaned daemon trees across test runs and GUI stops.

### v16.9.67 - Resilient Sidecar Process Tree Termination & Autorun Guard

- `env_manager/service_manager.py`: Immediately persist `proc.pid` to
  `server.pid` upon spawning detached background daemons, guaranteeing
  direct process tree termination on `/api/services/{id}/stop`.
- `env_manager/service_manager.py`: Enhanced `_parse_netstat_pids` to match all
  listening TCP connections across modern Windows versions (`LISTENING` state,
  wildcard foreign ports `*:*` or `0.0.0.0:0`), resolving unkillable sidecar
  holder processes.
- `env_manager/server.py`: Introduced `LEM_DISABLE_SIDECAR_AUTORUN` environment
  guard to allow test harnesses and sandboxed environments to skip auto-spawning
  external sidecars during server startup.

### v16.9.66 - Robust Netstat Port Holder Discovery on Windows

- `env_manager/service_manager.py`: Added `_find_port_pids_netstat` fallback in
  `_stop_port_holder` when `psutil.net_connections` encounters Windows permission
  checks (`RuntimeError: GetExtendedTcpTable failed`), ensuring reliable stop of
  all sidecar services across the desktop application.
- `env_manager/service_manager.py`: Named constant `_NETSTAT_TCP_COLUMNS = 5` to
  adhere to Ruff PLR2004 comparison rule.

### v16.9.65 - PSScriptAnalyzer Gate Hardening and WindowsApps pwsh Fallback

- `env_manager/probe/shell.py` and `env_manager/validation/tooling.py`:
  Safely detect and avoid uninstalled WindowsApps execution aliases for `pwsh`,
  falling back gracefully to Windows PowerShell without WinError 1920.
- `env_manager/validation/gates/powershell.py`: Integrated local module import
  for `PSScriptAnalyzer` from isolated virtual environment tools bundle,
  enabling robust fail-closed static analysis in all local environments.

### v16.9.64 - Synchronize Central Tauri 2.12 SSOT Manifest

- `requirements/lemgendary-ai-studio-gui.package.json`: Updated `@tauri-apps/api`
  and `@tauri-apps/cli` to `~2.12.0` and `@tauri-apps/plugin-shell` to `~2.6.0`,
  aligning with the Tauri 2.12 desktop ecosystem release.

### v16.9.63 - CUDA 13.4 Toolchain Support & Centralized PyTorch 2.15.0 Manifests

- `env_manager/probe/models.py`: added driver version `610+` mapping to CUDA
  13.4 index (`https://download.pytorch.org/whl/cu134`) in hardware probe
  `CUDA_INDEX_BY_MIN_DRIVER`.
- `requirements/requirements-datasets.txt` & `requirements-training.txt`:
  updated deep learning toolchain to PyTorch 2.15.0+cu134, TorchVision
  0.29.2+cu134, and TorchAudio 2.11.1+cu134 on CUDA 13.4.
- Synchronized package versioning across `pyproject.toml`, `package.json`,
  `openapi.json`, and PowerShell orchestrator.

### v16.9.62 - Establish .secrets.yaml as SSOT and Drop Repo Dotfile Propagation

- `env_manager/secrets_vault.py`: Removed repository-level propagation of
  `.kaggle_token` and `.kaggle_users` into sibling repositories. Sibling
  repositories read directly from `.secrets.yaml` as SSOT. Synchronization
  of the default Kaggle token to `~/.kaggle/kaggle.json` is preserved for
  native Kaggle CLI support.
- `tests/test_secrets_vault.py`: Updated propagation tests to verify
  `~/.kaggle/kaggle.json` synchronization and failure reporting.

### v16.9.61 - Discover Windows Git sh.exe in Hook Tests

- `tests/test_hooks.py`: `_find_sh` checks the sibling `bin/sh.exe` and
  `usr/bin/sh.exe` directories of the detected `git` executable when `sh` is
  not on the default Windows `PATH`, so all five hook tests run instead of
  skipping.

### v16.9.60 - GUI Release Version Leaves the Central Manifest

- `check_npm_manifest` (`ssot_npm_manifest`) no longer compares the GUI
  `package.json` `version`. The GUI bumps it on every commit, so the check
  always failed on a number that is not dependency information. Every other
  field is still compared.
- `requirements/lemgendary-ai-studio-gui.package.json` drops `version`; the
  central manifest no longer claims to own the GUI release number.
- npm sync: `find_package_json_drift` no longer reports `version`, and
  `_sync_npm_manifest` writes the central manifest with the sibling's own
  `version` kept (`merge_npm_manifest`, sibling key order and line endings
  kept) instead of copying the file byte for byte. `NPM_RELEASE_KEYS` in
  `requirements_manager` names the ignored fields for both.
- Tests: a version-only difference passes the gate and is not sync drift; a
  dependency change still fails; sync keeps the sibling version.

### v16.9.59 - ecosystem_ssot Catches Every Standalone Port Token

- The `ssot_port` rule now flags any service port from `ecosystem.json`
  written as its own number token in code, configuration and tests:
  template literals (`${base}:<port>`), calls (`toBe(<port>)`),
  assignments and list items. It used to need a word character or `]`
  before the colon, or a `port` label, so `}:<port>` and bare numbers
  slipped through. Longer numbers, hex, colours, versions, decimals,
  negative numbers and units still pass. The allowlist is unchanged.
- `tests/test_server_api.py` reads the expected ports from the ecosystem
  file.

### v16.9.58 - Central GUI Manifest Synced; package.json Parity Check

- `requirements/lemgendary-ai-studio-gui.package.json` now equals the GUI
  `package.json` (GUI v2.10.36): ESLint 9 flat config (`eslint`,
  `@eslint/js`, `typescript-eslint`, `globals`, `eslint-plugin-react-hooks`
  5) replaces ESLint 8 and the `@typescript-eslint/*` pair, plus
  `modern-screenshot`, the `sync:ecosystem` / `check:ecosystem` scripts
  and the new `lint` and `typecheck` commands. The central file stays the
  one to edit; the GUI had moved on, so sync would have refused it as drift.
- The `ecosystem_ssot` gate gains `check_npm_manifest`: the GUI
  `package.json` must equal its central manifest (`ssot_npm_manifest`
  finding naming the differing keys). A missing central or GUI file is a
  logged `ssot_skipped` note; an unparseable one is `ssot_unreadable`.
  Tests use a fixture workspace.

### v16.9.57 - Validator-Clean Tests; yamllint Skips Ignored Trees

- `tests/test_apply_plan.py` creates its temporary workspace with
  `tempfile.mkdtemp()` and an `rmtree` cleanup. The validator's Pylint
  (env-manager interpreter) reported `consider-using-with` for the
  `enterContext(TemporaryDirectory())` form.
- `.yamllint.yaml` sets `ignore-from-file: .gitignore`, so `yamllint .`
  no longer lints third-party YAML inside an installed `node_modules` or
  `.venv`.

### v16.9.56 - Central GUI Manifest Gets @types/node

- `requirements/lemgendary-ai-studio-gui.package.json` adds
  `@types/node` `^24.19.2`, which the GUI added in v2.10.36 when it dropped
  `skipLibCheck`, so the downgrade guard does not report it as missing from
  central.

### v16.9.55 - Launcher Script Passes PSScriptAnalyzer

- `lemgendary_env_manager.ps1` had 42 PSScriptAnalyzer warnings, hidden
  until the gate fix in v16.9.53. All are fixed at the source:
  - `Write-Host` (39 calls) is replaced by `Write-Ui`, which writes to the
    information stream (shown by default, capturable with `6>`) and colours
    the line with ANSI codes for the named console colour.
  - `Stop-ServerJob` supports `ShouldProcess` (`-WhatIf`, `-Confirm`).
  - The sidecar job reads the interpreter path with `$using:` instead of a
    job parameter.
  - Comment rules use ASCII dashes, so the file is plain ASCII and needs no
    BOM for Windows PowerShell 5.1.

### v16.9.54 - npx Fallback Test Isolated from Local Tools

- `test_npx_fallback_never_downloads` failed once the env-manager
  `node_modules` was installed, because the local `markdownlint` binary took
  precedence over npx. The test now points the env-manager folder at a
  temporary directory, so it checks the npx fallback on any machine.

### v16.9.53 - PSScriptAnalyzer Gate Runs Again; Test Temp Workspace

- The PSScriptAnalyzer gate piped a `foreach` statement into
  `ForEach-Object`, which PowerShell rejects ("An empty pipe element is not
  allowed"), so the gate never analysed a script and only reported a tool
  failure. The file list is now piped through `Where-Object` and
  `ForEach-Object`, and the gate reports real findings.
- `tests/test_apply_plan.py` used a relative `ws` folder as the workspace,
  which left an empty `ws/` tree in the repository after each run. The tests
  now use a temporary directory.

### v16.9.52 - SSOT Gate at Error Severity (ENV-3)

- `ECOSYSTEM_SSOT_SEVERITY` is now `Severity.ERROR`: findings fail
  validation. The real workspace scans with 0 findings in every present
  project.
- `ssot_skipped` notes (optional inputs absent) are logged and never fail
  (`collect_ssot_findings` still returns them); a file that exists but cannot
  be parsed is a failing `ssot_unreadable` finding.
- README documents the single-source files, the allowlist and how to fix a
  finding.

### v16.9.51 - SSOT Gate Refinement and Typed Projects (ENV-2b)

- `ecosystem_ssot` now checks each file by mode. Code, configuration and
  tests (`STRICT`): any literal port, Kaggle owner, docs URL, project folder
  list or legacy folder name is a finding; test literals are allowed only in
  `fixtures` folders. Documentation (Markdown, HTML, reStructuredText), the
  registries `unified_data.yaml`, `unified_models_v2.yaml` and
  `canonical_capabilities.*`, and every file of a `kind: asset` project
  (`CONSISTENCY`): facts may be written out but must equal `ecosystem.json`.
  New findings `ssot_wrong_port` (a port next to a service name that is not
  its port), `ssot_wrong_kaggle_owner` (an own `LemGendized*` Kaggle
  reference or a `kaggle_username` under another owner), `ssot_wrong_docs_url`
  (a docs-host link outside the docs base URL) and `ssot_legacy_folder`.
- Validated projects that are not checked out (for example
  `LemGendaryDatasets`) are logged and skipped, not reported as findings
  (`absent_projects()`).
- `GET /api/projects` now returns `list[ProjectHealth]` (the GUI contract);
  `ProjectHealth` is a Pydantic dataclass, so `openapi.json` (regenerated)
  carries its schema and the GUI `ProjectHealth` parity check runs instead of
  being skipped.
- The env-manager's own findings fixed: the PowerShell launcher reads the
  sidecar address from `ecosystem.json`; the manifest registry
  (`/api/manifests/registry`, read, validate, save) is one
  `manifest_targets()` list built from `ecosystem.json`; domain gates declare
  their target projects in their own modules and `for_projects` also matches
  legacy folder names; tests derive folder names from the registry.

### v16.9.50 - Ecosystem SSOT Gate (ENV-2)

- New validation gate `ecosystem_ssot` (package
  `env_manager/validation/gates/ecosystem_ssot/`), registered in
  `validation/registry.py` at warning severity. Per project it reports
  literal service ports, the Kaggle owner, the docs URL and lists of three or
  more project folders that are not read from `ecosystem.json`. Allowlisted:
  the ecosystem file and schema, the GUI `src/generated/ecosystem.json` and
  `src-tauri/tauri.conf.json`, README changelog sections, `CHANGELOG*`
  files, `REVIEW_REPORT.md`, `openapi.json`, lockfiles and `fixtures`
  folders under a test folder.
- For the Tauri GUI it also checks: the snapshot equals the env-manager
  source file; the `csp` and `devCsp` `connect-src` ports equal the service
  ports (the dev server port is ignored); the pipeline step count (skipped
  with an `ssot_skipped` note while the GUI keeps its own `PIPELINE_STEPS`
  table); and the `ProjectHealth` TypeScript keys equal the env OpenAPI
  `ProjectHealth` schema (skipped with a note when either is missing).
- Gates now carry a `severity`. Warning gates report into the new
  `warnings` list of `ProjectValidationReport`, which never fails a project;
  `lem-env validate` shows a Warnings column and the first warnings.
  Setting `ECOSYSTEM_SSOT_SEVERITY = Severity.ERROR` makes the gate fail
  validation.
- The shared text file type set moved to `validation/files.py`
  (`TEXT_FILE_EXTENSIONS`); new `tests/test_ecosystem_ssot.py`.

### v16.9.49 - Ecosystem Registry (ENV-1)

- New `ecosystem/ecosystem.json` with `ecosystem/ecosystem.schema.json`
  (JSON Schema draft 2020-12): `services`, `projects`, `pipeline_steps`
  (long descriptions taken from the GUI step cards) and `identity`
  (`docs_url`, `kaggle_owner`, `bind_host`). It ships in the wheel via
  `tool.setuptools.data-files`.
- New cached loader `env_manager.ecosystem` (`LEMGENDARY_ECOSYSTEM_FILE`
  override, then the repository file, then the installed copy; fails loudly
  with the expected path).
- Duplicates removed and derived from the file: `SERVICE_CONFIGS` (ports,
  health URLs, log dirs, app imports, bind host), `VALIDATION_TARGETS`,
  `UPGRADE_ORDER` (now `upgrade_order()`), `discover_projects` project lists,
  `ASSET_STORE_PROJECTS` (now `asset_store_folders()`),
  `PROJECT_TO_MANIFEST` and the requirements and npm manifest maps, the npm
  audit targets, the secrets vault repository names, the `/api/gui/ecosystem`
  and `/api/gui/state` ports, the docs URLs, the `serve` defaults and
  `TOTAL_STEPS` and the step table. Step events use the step names from the
  file.
- New `GET /api/ecosystem` returns the file as a typed `Ecosystem` response;
  `openapi.json` regenerated.
- `jsonschema` and `types-jsonschema` added as dev requirements; new
  `tests/test_ecosystem.py` (schema, loader, derived lists, endpoint).

### v16.9.48 - Strict Markdown and YAML Lint

- `.markdownlint.json` now matches the ecosystem config: MD013 is on at 80
  columns (the blanket `"MD013": false` and the `MD024` sibling exception are
  gone). README.md and REVIEW_REPORT.md were rewrapped; wide tables were turned
  into lists, long diagram lines were shortened, and the duplicate "resolution"
  headings in REVIEW_REPORT.md were renamed. No changelog content was removed.
- `.yamllint.yaml` is now the stock `default` ruleset with one change:
  `line-length` max 120 as an error (the default 80 is too narrow for package
  specifier lines). The `document-start` warning, the 200-column warning,
  `new-lines: disable` and `indent-sequences: whatever` overrides are removed.
- `new-lines` is satisfied by forcing LF for `*.yaml` and `*.yml` in
  `.gitattributes`; the YAML files were converted from CRLF to LF.

### v16.9.47 - One Subprocess Runner

- Audit: the API routes and the update job runner already call the service layer
  in-process (`validate_workspace`, `run_full_health_audit`,
  `build_upgrade_plan`/`apply_upgrade_plan` on a job thread,
  `sync_all_manifests`, `install_git_hooks`, `purge_workspace_caches`). No route
  runs `lem-env`, `python -m env_manager` or a repository script as a
  subprocess, so the HTTP contract is unchanged. The only self-reference is the
  git pre-commit hook, which runs outside any env-manager process.
- External tools now go through two runners in `utils`: `run_command_simple` for
  captured commands and the new `stream_command` for long-running ones with live
  output. `stream_command` keeps stdout and stderr apart, kills the process tree
  on timeout and raises launch errors for the caller to report. pip attempts and
  `npm install` both use it, so the two separate `Popen` blocks are gone.
- The direct `subprocess.run` calls in `venv_manager` (venv creation),
  `service_manager` (taskkill), `hooks` (git config), `upgrade/torch_cuda` (CUDA
  runtime check), `upgrade/npm` (outdated, update), `bug_report/platform` (file
  manager) and the pip cache purge now use `run_command_simple`. taskkill now
  has a timeout.
- Errors that were dropped are now logged: a failed taskkill logs its exit code
  and output, a failed `pip cache purge` logs its exit code, `npm outdated` with
  no output and a non-zero exit logs npm's stderr, and a failed `npm install`
  logs its exit code. The `npm install` result message now lists stdout before
  stderr.
- Tests: two new `stream_command` tests (separate streams, launch error); the
  hook and bug-report tests patch `run_command_simple`.

### v16.9.46 - Size Gates Enforced

- The ruff ignores for `PLR0912`, `PLR0913` and `PLR0915` are removed from
  `pyproject.toml`, and the eight `too-many-*` disables are removed from
  `.pylintrc`. Ruff, pylint and pyright now run with no ignored or disabled
  checks.
- `.pylintrc` sets `max-attributes=16`. The classes above pylint's default of 7
  are DTO dataclasses that mirror the JSON/API contracts field for field
  (`BootstrapStatus` has 15), so splitting them would change the HTTP contract.
- Functions over 15 locals or 12 branches are split into helpers:
  `verify_prerequisites`, `run_full_health_audit`, `sync_all_manifests`
  (`_sync_npm_manifest`), the Kaggle propagation run (`_write_users_file`),
  `compute_npm_drift` (`_installed_workspaces`, `_drift_entry`),
  `audit_project_health` (`_node_project_health`, `_missing_requirements`),
  `audit_docs_synchronization` (`_manifest_doc_violations`,
  `_paper_parity_violations`), `run_eslint` (`_eslint_report_violations`) and
  `stop_service` (`_stop_via_pid_files`, `_stop_port_holder`). Behaviour and
  messages are unchanged.
- Duplicate code: the markdownlint, yamllint and tsc gates parse tool output
  through one helper, `validation.tooling.regex_violations`.
  `install_requirements` uses `utils.drain_lines`. The CORS and origin-guard
  wiring is `security.install_browser_guard`, used by the server and by the
  security tests.
- `pipeline.events.Emit` is now an alias of `StepEmitter`, its only
  implementation, rather than a one-method Protocol; tests build emitters with
  `EventPublisher.step`. `docs_dir` has a docstring, and a long test line is
  wrapped.

### v16.9.45 - Validation and Drift Helpers

- `validation/domain/datasets.audit_dataset_manifests` (21 branches) checks each
  YAML manifest through one helper, `_yaml_mapping_violations` (parse error,
  non-mapping root, missing keys), and each manifold directory through
  `_manifold_violations`. The required files, the Forex manifold name and the
  shard count are module constants. Violation types and messages are unchanged.
- `validation/domain/project_hygiene.audit_git_hooks` (7 nested blocks) is split
  into `_hook_files` (each real hook file once) and `_hook_line_violations`; the
  retired scripts are the constant `RETIRED_HOOK_SCRIPTS`.
- `health/drift.compute_version_drift` (14 branches, 23 locals) builds one entry
  per shared package with `_drift_entry` and `_pin_info`.

### v16.9.44 - Install Step, pip Streaming and Bootstrap Cleanup

- `pipeline/install.install_packages` (17 branches, 57 statements) becomes a
  loop over three sub-generators: `_install_node`, `_install_python` and
  `_normalize_opencv`. A shared `_relay` turns a streamed npm or pip output into
  pipeline events and returns the stream's result. Events and failure handling
  are unchanged.
- `utils.stream_pip_with_recovery` moves one attempt into `_stream_pip_attempt`.
  A timeout or a launch failure is still returned at once without a retry.
- The new `utils.drain_lines(gen, on_line)` replaces the two copies of the
  generator-drain loop in `utils.run_pip_with_recovery` and
  `npm_manager.run_npm_install` (pylint duplicate-code finding).
- `bootstrap.py` runs every subprocess through `utils.run_command_simple`
  instead of repeating the `subprocess.run` keyword arguments (pylint
  duplicate-code finding). `verify_prerequisites` builds its missing list and
  install instructions from the `REMEDIATION` table via `remediation_for`, which
  replaces five per-OS `if`/`elif` chains. The instructions are unchanged.

### v16.9.43 - System Probe Split

- `env_manager/system_probe.py` (540 lines) is split into the
  `env_manager/probe/` package: `models.py` (`HardwareProfile`,
  `AcceleratorDevice`, `MetaTrader5Info`, the CUDA driver-to-index table and the
  ROCm, CPU and default CUDA index constants), `shell.py` (`preferred_shell`,
  `ps_quote`, `run_powershell`), `accelerators.py` (nvidia-smi, rocm-smi and
  DirectML probes and `detect_accelerators`) and `metatrader.py` (registry,
  known-path and `Get-Package` MT5 probes). `system_probe.py` keeps
  `probe_hardware` (78 lines) and re-exports every public name. Behaviour is
  unchanged.
- The three PowerShell calls share `shell.run_powershell`, and every probe
  subprocess goes through `utils.run_command_simple` instead of repeating the
  `subprocess.run` keyword arguments.
- The deep nesting is gone: the PE version read is `_pe_file_version` with early
  returns, one MT5 registry subkey is read by `_read_mt5_subkey`, nvidia-smi
  lines are parsed by `_nvidia_device`, and the backend priority (CUDA, ROCm,
  DirectML, CPU) is `detect_accelerators` instead of nested `if`/`else` in
  `probe_hardware`.

### v16.9.42 - Dependency Resolver Split

- `env_manager/dependency_resolver.py` (585 lines) is split into the
  `env_manager/resolver/` package: `models.py` (`OutdatedPackage`, `normalize`,
  marker checks), `pip_json.py` (pip JSON queries, `PipJsonError`,
  `find_outdated_packages`), `reverse_deps.py` (`pip inspect` reverse
  dependencies and the PyPI pinned-reason diagnosis), `dry_run.py`
  (`constraint_preserving_dry_run`) and `classify.py`
  (`classify_safe_upgrades`). `dependency_resolver.py` is a 29-line facade that
  keeps every public import working. Behaviour is unchanged.
- `classify_safe_upgrades` (27 branches) becomes a short driver over
  `_reverse_dep_survivors`, `_vet_normal` (batch dry-run with per-package
  fallback) and `_vet_torch`; the pydantic lockstep pairs are the module
  constant `CO_DEPENDENT_PAIRS`.
- All resolver pip calls go through one helper, `pip_json.run_pip`, which looks
  up the venv interpreter and logs stderr. The two copies of the "parse
  requirement, skip extras, check marker" loop are one generator,
  `models.active_requirements`. `upgrade.models` reuses
  `resolver.models.normalize` instead of its own copy.
- `constraint_preserving_dry_run` loses its never-used `timeout` argument; the
  limit is the constant `DRY_RUN_TIMEOUT` (300 s, as before).
- Tests patch pip in `env_manager.resolver.pip_json` and assert on the
  `lem_env.resolver` logger.

### v16.9.41 - CLI Split into Views and Services

- `env_manager/cli.py` (632 lines, 292 now) keeps only argument parsing and the
  order of output. The Rich tables move to the new `env_manager/cli_views/`
  package: `probe.py` (hardware table, software-update table and status,
  prerequisites), `audit.py` (toolchain, project, manifest-coverage and npm
  tables, `print_audit_report`), `update.py` (plan table, event line,
  post-upgrade validation lines) and `validate.py` (summary table and rows,
  violation listing).
- The `update` command's logic moves to the new service module
  `env_manager/upgrade/verify.py`: `UpgradeTally` counts the apply events and
  builds the summary, and `validate_upgraded_projects` re-validates the upgraded
  projects before the manifest sync. Status colours are lookup tables
  (`INSTALL_STATUS_COLORS`, `STATUS_COLORS`) instead of `if` chains.
- `probe`, `audit`, `update` and `validate` are now within the default branch,
  statement and local-variable limits. Output and exit codes are unchanged. The
  `update` test patches `validate_project` in `env_manager.upgrade.verify`.

### v16.9.40 - Updater Split

- `env_manager/updater.py` (790 lines) is split into the `env_manager/upgrade/`
  package: `models.py` (plan records, `UPGRADE_ORDER`, name helpers),
  `torch_cuda.py` (GPU index choice, CUDA runtime check, torch-family CUDA
  dry-run), `npm.py` (`npm outdated` / `npm update`), `plan.py`
  (`build_upgrade_plan`), `apply.py` (pinned installs, `pip check`, freeze
  snapshots, rollback, `apply_upgrade_plan`) and `manifest_sync.py`
  (`sync_manifests_from_venvs`). `updater.py` is now a 66-line facade that
  re-exports the public names, so existing imports keep working. Behaviour is
  unchanged.
- The three long functions are broken into single-purpose helpers:
  `build_upgrade_plan` plans each Node or Python project separately,
  `apply_upgrade_plan` applies each project through `_apply_node_project` /
  `_apply_python_project`, and the manifest sync re-pins one line at a time
  (`_synced_line`) from a module-level `PROJECT_TO_MANIFEST` table. None of them
  exceeds the default branch, statement or local-variable limits any more.
- Helpers used across the new modules lost their leading underscore (for example
  `_check_torch_cuda_upgrades` is `check_torch_cuda_upgrades`,
  `_apply_batch_with_rollback` is `apply_batch_with_rollback`). Tests patch them
  in `env_manager.upgrade.apply` and `env_manager.upgrade.plan`.

### v16.9.39 - Health Checker Split

- `env_manager/health_checker.py` (945 lines) is split into the
  `env_manager/health/` package: `models.py` (audit records, package-name
  normalization and the package-family sets), `manifests.py` (reading
  requirements pins with environment markers), `project.py` (per-project audit
  and upgrade classification), `drift.py` (version drift, manifest coverage,
  single-manifest packages), `npm.py` (npm drift) and `render.py` (the Rich
  tables). `health_checker.py` keeps `run_full_health_audit` (111 lines) and
  re-exports the names the CLI and tests import. Behaviour is unchanged.
- Helpers used across the new modules lost their leading underscore (for example
  `normalize_package_name` stays, `_load_project_pins` is `load_project_pins`,
  `_OPENCV_VARIANTS_NORMALIZED` is `OPENCV_VARIANTS_NORMALIZED`).

### v16.9.38 - Server Split into Routers

- `env_manager/server.py` (1012 lines) now only builds the app: lifespan, CORS
  and origin-guard middleware, the documentation-hub mount, and `include_router`
  for each area (109 lines). The routes moved to `env_manager/api/`:
  `routes_status.py` (ping, health, drift, hardware, projects, npm),
  `routes_pipeline.py` (pipeline and safe-update jobs), `routes_manifests.py`,
  `routes_workspace.py` (secrets, bug report, docs status, clean, validate,
  hooks) and `routes_ecosystem.py` (GUI state, sidecar services, shutdown,
  `/ws/log`). The WebSocket clients, the recent-event buffer, the orchestrator
  and the update job manager live in `env_manager/api/state.py`. Paths, methods
  and route order are unchanged.
- `openapi.json` is regenerated: since v16.9.25 the route docstrings appear as
  operation descriptions and the committed file lacked them. The OpenAPI test
  now compares the whole document with `app.openapi()`, not only
  `/api/bug-report`.
- Tests patch the module that owns a name (`api.state.update_jobs`,
  `api.routes_workspace._secrets_vault`, and so on) instead of `server`.

### v16.9.37 - Orchestrator Split, One Implementation per Pipeline Step

- `env_manager/orchestrator.py` (1151 lines) kept two copies of every step: one
  inside `run_clean_install_pipeline` and one per `_single_step_*` method. Both
  runs now execute the same step generators from the new `env_manager/pipeline/`
  package: `events.py` (`PipelineEvent`, the pip progress parser rules,
  `EventPublisher` and the per-step `StepEmitter`), `steps.py` (the seven steps
  and `PipelineRun`, the state they share), `install.py` (step 4 installs),
  `upgrades.py` (step 5 safe upgrades) and `venvs.py` (running-venv and
  `node_modules` helpers). `orchestrator.py` (189 lines) keeps the run state,
  the claim lock and the event wiring, and its public names.
- The full run probes the hardware and discovers projects once and passes them
  to later steps, as before; a single-step run computes only what its step
  needs, as before.
- Message wording that differed between the two copies now follows the full run:
  step 3 says "Purging existing node_modules ... (clean install)" and "will be
  installed freshly", the active-venv warning says "cannot delete the venv
  currently executing this pipeline", and step 7 starts with "Generating final
  ecosystem health report...". The step 7 summary still starts with "Pipeline
  execution completed." for a full run and "Step 7 completed." for a single
  step.
- Step emitters are bound to their step: `parse_pip_line_to_event` and
  `execute_safe_upgrades` take an `Emit` called as `emit(status, msg, *,
  data=None, is_progress=False)` instead of a six-argument callback.

### v16.9.36 - Validator Split into Pluggable Gates, New Gates (R6, D-16)

- `env_manager/validator.py` (1704 lines) is split along its responsibilities
  into the `env_manager/validation/` package: `models.py` (violation and report
  records, fail-closed helpers), `tooling.py` (finding and running lint tools),
  `files.py` (the one project walker and skip list), `gate.py` (the `Gate`
  abstraction), `registry.py` (the default gate list), `gates/` (source files,
  YAML/JSON, Node linters, HTML/WCAG, PowerShell, Pylint, suppressions, absolute
  paths) and `domain/` (docs hub, datasets, models and training suite, README
  changelog and git hooks). No module is over 310 lines. `validator.py` is now
  the runner (about 130 lines) and keeps every name callers import from it.
- `validate_project` runs the registered gates instead of a fixed sequence.
  Adding a gate means adding one `Gate` to `registry.GATES`; the runner does not
  change (OCP). The report shape and the order of checks are unchanged.
- New gates, deferred from D-16: Pylint (from the project's `.venv` when
  present, with the project's own config; fails closed when Pylint is missing),
  forbidden suppressions (Python comment directives via `tokenize`, `warnings`
  ignore filters via the AST, TS/JS `@ts-*` and `eslint-disable` comments, Rust
  `allow` attributes) and hardcoded absolute paths. Their findings go to
  `lint_errors`. They report existing violations in sibling repos (for example
  absolute paths in `lemgendary-datasets` tests and
  `lemgendary-training-suite`), which those repos' owners need to fix.
- Lint tools run through one helper (`tooling.run_tool`, built on
  `utils.run_command_simple`) instead of nine copies of the same
  `subprocess.run` arguments; staging-residue checks for datasets and models
  share `audit_staging_residue`.
- This repository passes the new gates: the PSScriptAnalyzer and pip-warning
  test fixtures and one `REVIEW_REPORT.md` line no longer spell out a drive
  path.
- New tests: `tests/test_validation_gates.py`. `tests/test_cli.py` stubs
  external gates by filtering the registry on `Gate.external`.

### v16.9.35 - Review Remediation Status

- `REVIEW_REPORT.md` has a new "Remediation Status" section. It maps every
  Medium and Low finding (D-10 to D-20) to the commit that fixed it, or says why
  it is N/A or deferred: 41 fixed, 1 N/A, 2 deferred. It also lists the
  cross-repository copies of ports, project lists, model rules and the docs URL
  that step L7 will move into one registry.
- The v16.9.34 entry no longer spells out an absolute Windows path.

### v16.9.34 - Contract and Hygiene Fixes (D-20)

- Dead code removed: the never-fed `_event_queue` and its drain task in
  `server.py`, and the unused `create_safe_upgrade_plan`, `apply_safe_upgrades`,
  `_pip_dry_run` and `DependencyResolutionPlan` in `dependency_resolver.py`.
- The startup launch of the sidecars is no longer fire-and-forget: its result,
  or its exception, is logged when it finishes.
- The server logger is `lem_env.server`, inside the configured `lem_env`
  hierarchy, so its records reach the configured handler and level.
- `stop_service` handles `psutil.AccessDenied` from `net_connections` (macOS
  without elevated rights) instead of raising.
- `discover_projects` runs each venv interpreter once (it ran `python --version`
  three times per project); validity and version come from one call.
- The MetaTrader 5 path probe uses only `ProgramFiles`, `ProgramFiles(x86)` and
  `LOCALAPPDATA`; the hardcoded drive-letter fallbacks are gone. The PowerShell
  version query quotes the path with `system_probe.ps_quote`, which escapes `'`.
- New tests: `tests/test_hygiene.py`.

### v16.9.33 - Launcher Completes D-19

- `lemgendary_env_manager.ps1` accepts every `lem-env` command (`clean`,
  `setup-hooks` and `hooks` were missing from its `ValidateSet`) and passes the
  command straight through, so the list cannot drift from the CLI again. A test
  compares the `ValidateSet` with the Typer commands.
- The venv interpreter is `.venv\Scripts\python.exe` on Windows and
  `.venv/bin/python` under PowerShell 7 on Linux and macOS; the bootstrap looks
  for `python` and then `python3`.
- The sidecar banner prints the full URL `http://127.0.0.1:8000` (it was cut to
  `http://127.0.0`).
- New tests: `tests/test_launcher.py`.

### v16.9.32 - Python 3.12 Contract and Cross-Platform Git Hooks (D-17, D-18)

- `requires-python` is now `>=3.12`, matching the code (`Path.walk`,
  `shutil.rmtree(onexc=...)`, `datetime.UTC`), Ruff's `target-version`, and
  ECOSYSTEM_ARCHITECTURE.md. `bootstrap.MIN_PYTHON` is `(3, 12)` and the
  launcher asks for Python 3.12+. A test keeps the three values equal.
- The pre-commit hook finds the project with `git rev-parse --show-toplevel`, so
  the copy in `.git/hooks` validates the project instead of a project named
  `.git`. It finds `lem-env` or `python` under `.venv/Scripts` (Windows) or
  `.venv/bin` (Linux, macOS), so commits no longer fail on POSIX hosts. The
  Python fallback runs from the env-manager directory instead of building a
  `PYTHONPATH` with a platform-specific separator.
- `install_git_hooks` reports a failed `git config core.hooksPath` as a failed
  install, marks the hook files executable, and takes its project list from
  `validator.VALIDATION_TARGETS`.
- `.githooks/pre-commit` is regenerated from the template, and `.gitattributes`
  keeps `.githooks/*` at LF line endings so `sh` can run the hook after a
  Windows checkout.
- New tests: `tests/test_hooks.py`, `tests/test_python_contract.py`.

### v16.9.31 - One Validation Set, Fail-Closed Gates (D-15, D-16)

- `lem-env validate` and `POST /api/validate` share
  `validator.validate_workspace` and `validator.VALIDATION_TARGETS`, so both
  check the same 7 projects with the same `is_node_project` flags. The API runs
  validation off the event loop.
- Validation fails closed. When yamllint, markdownlint, ESLint, `tsc`,
  html-validate, pa11y or PSScriptAnalyzer is missing, crashes, or exits
  non-zero without a parsable finding, the gate adds a `tool_error` violation,
  so the project reports `[FAIL]` instead of `[PASS]`. Missing YAML interpreter,
  yamllint exit code 2, ESLint exit code 2, ESLint output without a JSON report,
  and a missing PSScriptAnalyzer module are all reported.
- The npx fallback runs `npx --no <package>`, so validation never downloads
  packages; install the tools locally or globally.
- README: the validation list now names the gates that exist. Suppression
  detection, Pylint and an absolute-path gate are listed as not implemented.
- Tests that run `validate` stub the external linters, so they do not depend on
  installed tools. New tests: `tests/test_validator_fail_closed.py`.

### v16.9.30 - Enforced Subprocess Timeouts and a Safe Clean (D-13, D-14)

- New `utils.stream_process_lines(proc, timeout)` drains stdout and stderr on
  reader threads against one deadline. A child that hangs, with or without
  output, is now killed when the timeout expires, together with its child
  processes, instead of blocking on `readline()` until it closes its pipes. pip
  (`stream_pip_with_recovery`) and `npm install` (`stream_npm_install`) both use
  it, which also removes their duplicated drain loop.
- `clean` no longer deletes `.pyd` files: they are compiled extension modules,
  not bytecode. It removes `__pycache__`, `.pyc` and `.pyo` only.
- `lem-env clean` and `POST /api/clean` share `utils.purge_workspace_caches`, so
  both skip Node projects. The API now runs the purge off the event loop.
- New tests: `tests/test_process_and_clean.py`.

### v16.9.29 - Pipeline State and Drift Matrix Fixes (D-11, D-12)

- Every entry in `recent_events` (`GET /api/pipeline/status`, WebSocket replay)
  now carries a `timestamp`. Events go through `server.record_event`, which adds
  the current UTC time when an event has none and stores the same dict that is
  broadcast, so the GUI's timestamp-based de-duplication sees one identity per
  event. Update-job events are stamped the same way before broadcast.
- `recent_events` is read and written under a lock; worker threads no longer
  mutate it while a request copies it.
- The pipeline is claimed before its worker thread starts
  (`PipelineOrchestrator.try_claim`), under one lock shared with `POST
  /api/update`, so two fast `POST /api/pipeline/run` requests start one pipeline
  and a pipeline and an update can no longer start together.
- `is_running` is cleared in a `finally` block, so a run whose consumer stops
  early (`GeneratorExit`) no longer leaves the orchestrator busy. `current_step`
  now follows the full pipeline's progress.
- The drift matrix normalizes pip's raw package names (`pydantic_core`) before
  looking them up, so packages with `_` or `.` in their names show their
  installed versions.
- New tests: `tests/test_pipeline_state.py`,
  `tests/test_drift_normalization.py`.

### v16.9.28 - Apply Exactly the Vetted Plan (D-10)

- Upgrades install `name==latest_version` for each vetted candidate instead of
  an unpinned `pip install --upgrade`, so the apply phase can no longer pick up
  a newer release, or unvetted transitive upgrades, that the dry-run never
  checked. This holds for `lem-env update`, `POST /api/update` and pipeline Step
  5.
- The torch CUDA install runs only when the plan holds torch-family candidates
  from the CUDA index, and installs exactly those versions. It no longer
  reinstalls torch from the CUDA index on every run. On a host without a GPU
  index, vetted torch candidates go in the normal pinned batch.
- A pip install that fails part-way now restores the `pip freeze` snapshot
  (before, only a failed `pip check` did). The torch CUDA path rolls back the
  same way.
- New `updater.torch_upgrade_index(hw)` is the one definition of the GPU torch
  index (None on CPU-only hosts). The health audit, the plan builder and Step 5
  all use it; `apply_upgrade_plan` reuses the index stored in the plan instead
  of probing the hardware again.
- New tests: `tests/test_apply_plan.py`.

### v16.9.27 - Ruff Clean

- `ruff check .` reports 0 diagnostics (it reported 360). The only Ruff ignores
  left are the size checks `PLR0912`, `PLR0913` and `PLR0915`, which go with the
  module splits in step L8.
- Annotations use built-in generics and `X | None` (`UP006`, `UP045`);
  `Callable`, `Generator` and friends come from `collections.abc` (`UP035`);
  redundant `"r"` modes are gone (`UP015`).
- Command lists are built with unpacking (`[*prefix, "install", ...]`) instead
  of list concatenation (`RUF005`); `__all__` is sorted; an `else: if` in the
  drift matrix is an `elif`; the pa11y parser no longer rebinds its loop
  variable and checks prefixes with one `startswith` tuple.
- The test stub for the pipeline `emit` callback takes `data` and `is_progress`
  as keyword-only arguments, like the real callback.

### v16.9.26 - Pyright Covers the Tests

- Pyright 1.1.414 (basic mode, no overrides) reports 0 errors on `env_manager/`.
  `[tool.pyright] include` now also lists `tests`, and the 6 errors found there
  are fixed: the README version test fails explicitly when a regex does not
  match instead of calling `.group` on a possible `None`, the `_kaggle` test
  helper is annotated (`username: str | None`), and the CORS wiring test narrows
  `allow_origins` to a list before using it.

### v16.9.25 - Docstrings Required

- `missing-docstring` is removed from the `.pylintrc` disable list. The 195
  public functions, methods and classes that lacked one (62 in `env_manager/`,
  133 in `tests/`) now have a one-line docstring saying what they return or
  check. `.pylintrc` now disables only the eight `too-many-*` size checks, which
  go with the module splits in step L8; pylint reports nothing else except the
  existing `duplicate-code` notes.
- `tests/test_downgrade_guard.py::TestRealCentralManifestsHaveNoDrift` is
  removed: it read the real sibling repositories, so its result depended on
  their state. The same drift check runs on every `lem-env sync`, which refuses
  to write a manifest that would downgrade a sibling.

### v16.9.24 - Line Length Enforced

- `line-too-long` is removed from the `.pylintrc` disable list; all 66 lines
  over 100 characters (pylint `C0301`, Ruff `E501`) are wrapped.
- The validate command's repeated status cells and per-kind violation prints are
  now `_count_cell` and `_print_violations` in `cli.py`
  (`VIOLATIONS_SHOWN_PER_KIND = 5`).
- `validator.py`: the asset-store exemption list is `_ASSET_STORE_PROJECTS` and
  the git-hook script pattern is the precompiled `_HOOK_PY_SCRIPT_RE`.
- Over-long docstrings in `validator.py` and `cli.py` are rewritten to say what
  the function checks.

### v16.9.23 - Magic Numbers Named (Ruff PLR2004)

- `PLR2004` is removed from the Ruff `ignore` list; its 27 hits are fixed.
  Thresholds are named module constants with a one-line docstring (`MIN_PYTHON`,
  `PLAN_PREVIEW_SIZE`, `MIN_SHARED_DECLARATIONS`, `UPGRADE_SUMMARY_SIZE`,
  `STARTUP_TIMEOUT_S`, `ESLINT_SEVERITY_ERROR`, `DOC_LENGTH_RATIO_MIN/MAX`,
  `MANIFOLD_PARQUET_COUNT`, ...). HTTP checks compare against `HTTPStatus.OK`;
  the pipeline step check uses `orchestrator.total_steps`.
- `select_cuda_torch_index` reads its driver-to-wheel mapping from
  `CUDA_INDEX_BY_MIN_DRIVER`, and the `nvidia-smi` query is built from
  `NVIDIA_SMI_FIELDS`.
- The server keeps recent pipeline events in a `deque(maxlen=MAX_RECENT_EVENTS)`
  instead of trimming a list by hand.
- PSScriptAnalyzer results are emitted tab-separated and parsed by the new
  `validator.parse_pssa_line`, which replaces the colon-splitting code that had
  to special-case Windows drive letters.
- Still ignored until the module splits in step L8: `PLR0912`, `PLR0913`,
  `PLR0915` (size checks).

### v16.9.22 - Launcher Error Handling (D-19)

- **`lemgendary_env_manager.ps1`** no longer uses `-ErrorAction
  SilentlyContinue`. Removing a broken `.venv` and finding the global Python
  stop with a clear error and exit code 1 when they fail. Server-job checks use
  `Get-LiveServerJob`, which filters `Get-Job` output instead of hiding a lookup
  error, and `Stop-ServerJob` reports a job that cannot be stopped with
  `Write-Warning`.
- `python -m venv` and the `pip install` calls run through `Invoke-Checked`,
  which stops on a non-zero exit code; the non-interactive passthrough exits
  with the CLI's exit code instead of always `0`.

### v16.9.21 - Hermetic Tests and One Workspace Root

- **`env_manager/workspace.py`** is now the only place that derives the
  workspace root (`workspace_root()`, `ENV_MANAGER_DIR`). The 19 copies of
  `Path(__file__).resolve().parent.parent.parent` (CLI, server, orchestrator,
  updater, hooks, npm, venv, service and requirements modules) and the 5 of
  `.parent.parent` (validator, server) use it instead. Setting
  `LEMGENDARY_WORKSPACE_ROOT` overrides the root.
- Nothing resolves the root at import time any more:
  `PipelineOrchestrator.base_dir` is a property, service configs store the
  project name and resolve the folder per call, and `GET /api/docs/status` looks
  up the docs folder per request.
- `lem-env update` passes its workspace to the post-upgrade manifest sync
  instead of letting it fall back to the default root.
- **Tests**: `tests/conftest.py` builds a minimal fake workspace under
  `tmp_path` for every test and points `LEMGENDARY_WORKSPACE_ROOT` and
  `GIT_CEILING_DIRECTORIES` at it, so no test reads or writes the real sibling
  repositories. `test_cli_validate_command_single_project` and
  `test_project_option_scopes_plan_and_apply` now pass whatever state the
  sibling repos are in. New `tests/test_workspace.py` covers root resolution.

### v16.9.20 - Lint Configuration Suppressions Removed

- **`.pylintrc` (ROBOTS.md zero configuration-suppression rule)**: 38 of the 48
  disabled checks are enabled again, and the code they hid is fixed. Still
  disabled, as remaining follow-up work: `missing-docstring`, `line-too-long`
  and the eight size/complexity checks (`too-many-branches`, `-locals`,
  `-statements`, `-lines`, `-arguments`, `-positional-arguments`,
  `-instance-attributes`, `-nested-blocks`). With this configuration pylint
  reports only the existing `duplicate-code` notes.
  - Unused imports, variables and arguments removed (`compute_version_drift`
    lost its unused `base_dir`, `_execute_package_installation` its unused
    `target_project`); the FastAPI `lifespan` argument is `_app`.
  - Function-local imports and re-imports in `cli.py`, `server.py` and the tests
    moved to module level; the CLI `clean` command function is `clean_command`
    (command name unchanged) so it no longer shadows the `install --clean`
    option. The duplicate `CleanRequest`/`ValidateRequest` models in `server.py`
    (pylint `E0102`) are removed. `venv_manager` no longer edits `sys.path`
    before its imports.
  - Windows-only modules (`ctypes.wintypes`, `winreg`) are loaded with
    `importlib.import_module` inside their platform guards; the MT5 exe-version
    reader reads the two version DWORDs directly instead of declaring a nested
    `ctypes.Structure`.
  - `consider-using-with`: the pip and npm streamers use `with
    subprocess.Popen(...)` and the new `utils.wait_or_kill`, which kills and
    reaps the child on timeout instead of leaking it. Resources that must
    outlive their creator are returned by small factories (`_spawn_detached` for
    sidecar daemons, `_new_worker_pool` for the update-job executor). Tests use
    `tempfile.mkdtemp` with `addCleanup(shutil.rmtree, ...)`.
  - The update-job manager tracks "busy" with a flag under its state lock
    instead of a `Lock` acquired in `start` and released on the worker thread.
  - `protected-access` in tests: the tested helpers are public
    (`installed_package_versions`, `execute_safe_upgrades`,
    `parse_pip_line_to_event`), and log assertions use the logger name
    (`lem_env.dependency_resolver`) instead of the module's `_log`.
  - `create_venv` is split into `_remove_existing_venv` and `_build_venv`
    (`too-many-return-statements`); `select_cuda_torch_index` drops an `elif`
    after `return`.
- **Pyright**: `reportGeneralTypeIssues = "none"`, `reportPrivateImportUsage =
  "none"` and `reportMissingTypeStubs = false` removed. `pyrightconfig.json`
  duplicated the `[tool.pyright]` table in `pyproject.toml` and is deleted, so
  `pyproject.toml` is the single pyright configuration.

### v16.9.19 - Narrow, Logged Exception Handling and One Version

- **No silent exception handlers (ROBOTS.md zero-swallow rule)**: every `except
  Exception`, `except ...: pass` and silent `continue`/default in `env_manager/`
  now catches the specific errors the call can raise (`OSError`,
  `subprocess.SubprocessError`, `ValueError`/`json.JSONDecodeError`,
  `yaml.YAMLError`, `psutil.Error`, `packaging` marker and requirement errors)
  and logs them with context, re-raises with `from`, or returns a typed error.
  Conditions that are expected (service offline, process already gone,
  unparsable third-party metadata) stay non-fatal but are logged.
  - Calls that already report failures through their return value
    (`run_pip_with_recovery`, `find_outdated_packages`,
    `classify_safe_upgrades`, `_check_torch_cuda_upgrades`, `probe_hardware`)
    are no longer wrapped in catch-all handlers.
  - `on_line` callbacks of `run_pip_with_recovery`, `run_npm_install` and
    `install_requirements` are no longer wrapped: a failing callback now
    propagates instead of being hidden.
  - `rmtree` read-only hook re-raises when the retry fails, so a half-deleted
    venv is reported.
  - Safe-update jobs run on a `ThreadPoolExecutor`; the `Future` records a
    runner failure on the job, so no catch-all handler is needed. Interpreter
    shutdown now waits for a running upgrade instead of killing pip mid-write.
  - WebSocket broadcast and `/ws/log` catch only disconnect-class errors
    (`WebSocketDisconnect`, `RuntimeError`, `OSError`) and always unregister the
    client.
  - Manifest validation and save catch only `yaml.YAMLError`/`ValueError`; the
    save handler chains the cause (`raise ... from exc`).
  - Validator manifest audits use one documented error tuple,
    `_MANIFEST_READ_ERRORS`.
  - `Get-Package` MT5 probe no longer uses `-ErrorAction SilentlyContinue`; a
    non-zero exit (no package) is logged.
- **Required dependencies imported directly**: the `ImportError` fallbacks for
  `packaging`, `rich` and `psutil` (all declared dependencies) are removed
  together with their dead branches. Function-local imports of `yaml`, `os`,
  `signal`, `time`, `stat`, `psutil`, `platform` and `unittest` moved to module
  level; only the Windows-only `ctypes.windll` and `winreg` imports stay inside
  their platform guards.
- **Inline suppressions removed** (all five `# noqa`):
  - `run_pipeline_single_step` dispatches to one generator method per step
    through a handler table (`_single_step_*`) instead of a seven-branch `if`
    chain.
  - `_parse_pip_line_to_event` takes a `PipProgress` value and applies a rule
    table (`_PIP_PROGRESS_RULES`), one small function per pip line kind.
  - The pipeline `emit` helpers take `data` and `is_progress` as keyword-only
    arguments.
  - `start_service` is split into `_preflight_service`,
    `_resolve_service_python`, `_launch_service` and `_await_service_health`.
- **Cleanup**: unused `_version_lt` removed; `service_manager` uses the shared
  `lem_env` logger; console UTF-8 reconfiguration in the CLI moved into
  `_configure_windows_console()` after the imports.
- **One version string**: `env_manager.__version__`, `pyproject.toml`, both
  `package.json` copies, the FastAPI `app.version` (now read from `__version__`,
  also used by `/api/ping` and `/api/gui/state`), `openapi.json` and the
  PowerShell banner all read `16.9.19`. `tests/test_readme_version.py` checks
  every one against the README title.
- Tests: `tests/test_orchestrator.py` updated for the `PipProgress` signature.

### v16.9.18 - Bug Report Endpoint

- **`POST /api/bug-report` (FIX_PLAN Phase 4 backend)**: the bug reporter
  backend now lives in the env manager. It validates the multipart form with
  Pydantic, checks upload count, size, declared type and magic bytes, writes
  `report.csv` with the `csv` module (formula-like cells are prefixed with `'`),
  and saves the ZIP to `bug-reports/` at the workspace root. No SMTP: the
  endpoint opens the user's mail client (`mailto:`) and reveals the archive in
  the file manager, and reports whether each step worked.
- **Single configuration place**: recipient (`lemgenda.obrt@gmail.com`), subject
  (`LemGendary AI Studio Bug report`), reports folder and all limits are defined
  in `env_manager/bug_report/config.py` (`BugReportSettings`). A reports folder
  outside the workspace root is refused; one inside a git repository gets its
  own `.gitignore`.
- **Platform adapter**: `PlatformAdapter` protocol with `SystemPlatformAdapter`
  (Windows `explorer /select,`, macOS `open -R`, Linux `xdg-open`; mail through
  `webbrowser`). The service depends only on the protocol.
- **Dependencies**: `python-multipart==0.0.33` added to
  `requirements/requirements-env-manager.txt`, its generated copy
  `requirements.txt`, and `pyproject.toml`.
- **OpenAPI**: `openapi.json` regenerated; adds `/api/bug-report` with
  `BugReportFields` and `BugReportResponse`.
- Tests: `tests/test_bug_report.py` (fake adapter, temporary workspace): archive
  contents, CSV escaping of quotes, commas and newlines, each validation error,
  size limits, magic-byte mismatch, adapter failures, no absolute path in the
  response or mail body.

### v16.9.17 - Correct Upgrade Routing and Scoped Updates

- **Pipeline Step 5 routing (review finding D-08)**:
  - Project discovery now records `has_npm_manifest` (a `package.json` is
    present) next to `is_node_project`. Step 5 dispatches from this metadata:
    every Python project gets the pip safe-upgrade path, and every project with
    a `package.json` gets the npm path. A dual-stack project such as
    `lemgendary-env-manager` (Python plus npm lint tooling) now gets both.
  - The npm path uses `npm outdated` (`_find_npm_outdated`) instead of the pip
    query, so outdated npm packages are actually found and updated.
  - Step 5 is split into `_upgrade_npm_dependencies` and
    `_upgrade_python_dependencies`.
- **Scoped `lem-env update --project X` (review finding D-09)**: the project is
  passed to `build_upgrade_plan`, so only X is planned and applied. An unknown
  project prints a clear error and exits with code 1.
- **README header**: the title version now matches the latest changelog entry;
  `tests/test_readme_version.py` keeps them in sync.
- Tests: `tests/test_upgrade_routing.py` (dual-stack, Python-only and npm-only
  routing with mocked upgrade functions; `update --project` scope; unknown
  project) and `tests/test_readme_version.py`.

### v16.9.16 - Manifest Downgrade Guard and Clean pip JSON

- **Manifest Single Source of Truth (review finding D-06)**:
  - Decision: the central `requirements/` files are the single source of truth
    and the siblings are generated copies (see "Manifest Single Source of Truth"
    above). The central files were brought up to the siblings first:
    `pytest==9.2.0` and `httpx2==2.16.0` added to `requirements-training.txt`
    and `requirements-datasets.txt`, `jsdom` `25.0.1` added to
    `lemgendary-ai-studio-gui.package.json`. The central and sibling files are
    now identical; no sibling file was edited.
  - Downgrade guard: `sync_manifest_to_project` and the npm step of
    `sync_all_manifests` compare both files first (`find_requirement_drift`,
    `find_package_json_drift`). Lower floors, dropped pins and packages missing
    from central are drift: nothing is written, a warning is logged and the
    result is `(False, "DRIFT: ...")`, which the pipeline shows as a warning.
    Requirements are compared per package and environment marker.
  - `tests/test_cli.py` runs `lem-env sync` against an empty temporary
    workspace, so the test suite can no longer rewrite sibling repositories.
- **Clean pip JSON and Fail-Closed Classifier (review finding D-07)**:
  - `stream_pip_with_recovery` captures stdout and stderr on separate pipes (two
    reader threads). Both still stream live, but `CompletedProcess.stdout` holds
    only pip's stdout and `stderr` holds its warnings and errors. Before, stderr
    was merged into stdout and `stderr` was always empty, so a warning such as
    `WARNING: Ignoring invalid distribution ~orch` broke every `--format=json`
    and `--report -` parse.
  - `dependency_resolver`: new `_run_pip_json` logs each stderr line as a
    warning and parses only stdout. A failed or unparseable `pip list` / `pip
    inspect` raises `PipJsonError`; an empty `pip list` in a valid venv is also
    an error.
  - Fail closed: `classify_safe_upgrades` blocks every candidate when the
    installed state or reverse dependencies cannot be read;
    `constraint_preserving_dry_run` aborts when the installed state is empty (an
    empty constraints file would make the dry-run unconstrained) or when the
    `--report` JSON cannot be parsed. The text fallback that guessed from "Would
    install" lines is gone.
  - `venv_manager.get_installed_packages` logs pip stderr and catches only parse
    errors.
- **Tests**: `tests/test_downgrade_guard.py` (11, temporary directories only,
  plus a read-only check that the committed central files are not behind the
  siblings) and `tests/test_pip_json_parsing.py` (9, including a real child
  process writing JSON to stdout and warnings to stderr). 95 pass.

### v16.9.15 - Non-Blocking Updates and Liveness Probe

- **Background Safe Updates (review finding D-04)**:
  - `POST /api/update` no longer waits for the upgrade inside the request
    handler (`thread.join(timeout=1200)` froze the event loop, every request and
    every WebSocket broadcast for up to 20 minutes). A real run starts a
    background job (`env_manager/update_jobs.py`, `UpdateJobManager`) and
    returns at once with `status: "accepted"` and a `job_id`.
  - One update at a time: the job holds a lock; a second request returns `409`
    with the running job id. Updates also return `409` while the pipeline runs,
    and the pipeline refuses to start while an update runs.
  - The `project` field is honoured: `build_upgrade_plan(base_dir, project=...)`
    plans only that project. Unknown projects return `404`.
  - The dry-run plan is built in the thread pool instead of on the event loop.
  - Progress: upgrade events and job start/finish events are streamed over
    `/ws/log`; the new `GET /api/update/status` returns the current or last job.
    Runner failures are logged and recorded as `status: "failed"` with `error`.
- **Liveness Probe (review finding D-05)**: new `GET`/`HEAD /api/ping` returns
  `{"status": "ok", "service", "version"}` without any I/O. `/api/health` stays
  the full audit for the Health panel.
- **OpenAPI**: `openapi.json` regenerated from the app; adds `/api/ping`,
  `/api/update/status`, and the previously missing `/api/pipeline/run-step`,
  `/api/hooks/install` and `/api/shutdown`.
- **Tests (`tests/test_update_jobs.py`)**: 14 tests with a fake runner (the real
  updater is never called): the handler returns before the job finishes, lock
  gives `409`, `project` reaches the plan builder, failures release the lock,
  `/api/ping` never calls the audit.

### v16.9.14 - Non-Destructive Pipeline by Default

- **Pipeline `clean` Opt-In (review finding D-03)**:
  - `PipelineOrchestrator.run_clean_install_pipeline` now defaults to
    `clean=False`. A default run keeps valid venvs and `node_modules` and stops
    no processes; only invalid or missing environments are created.
  - `RunPipelineRequest` gains `clean: bool = False` and `POST
    /api/pipeline/run` forwards it. Requests without `clean` stay valid and are
    now non-destructive (previously every GUI "Run Pipeline" click deleted all
    venvs and `node_modules` and killed running sidecars).
  - CLI `install`: replaced `--clean/--no-clean` (default clean) with an
    explicit `--clean` flag whose help text states what it deletes.
  - `node_modules` purge no longer uses `shutil.rmtree(...,
    ignore_errors=True)`; a failed purge is reported as a Step 3 error event.
- **OpenAPI**: `openapi.json` `RunPipelineRequest` schema gains the optional
  `clean` field (default `false`).
- **Tests**: default run never calls `create_venv` or the `node_modules` purge,
  `clean=True` does (all destructive functions mocked); `RunPipelineRequest`
  defaults and `/api/pipeline/run` forwarding of `clean`.

### v16.9.13 - Masked, Write-Only Secrets Vault

- **Masked Secrets API (`env_manager.secrets_vault`)**:
  - `GET /api/secrets` no longer returns secret values. Each item carries
    `has_value` and `value_hint` (last 4 characters, empty when the value is
    shorter than 8). Fixes the secrets exposure part of review finding D-01.
  - `GET /api/secrets` has no side effects: it no longer creates
    `.secrets.yaml`. Legacy credential files are read in memory and persisted on
    the first save.
  - `POST /api/secrets` is write-only: an empty or omitted `secret_value` keeps
    the stored value for the same `id`, a non-empty value replaces it. The
    mandatory Kaggle check counts kept values. The vault file is written
    atomically with a `.secrets.yaml.bak` backup.
- **Legacy Credential Propagation (review finding D-02)**:
  - The default Kaggle token is written only to repositories that read it and
    ignore it in git (`lemgendary-datasets/.kaggle_token`,
    `lemgendary-training-suite/.kaggle_token` and `.kaggle_users`); the env
    manager no longer writes its own `.kaggle_token`.
  - No hardcoded usernames: entries without a username are skipped (and
    `kaggle.json` is not written) instead of falling back to a personal account.
    Seeding no longer invents usernames or an MT5 server.
  - Write failures are logged and reported in the new `propagation` field
    (`written`, `skipped`, `failed`) instead of being swallowed by `except
    OSError: pass`.
- **Git Hygiene**: `.gitignore` now ignores `.secrets.yaml`, its backup,
  `.kaggle_token`, `.kaggle_users`, `kaggle.json`, `.mt5_credentials` and the
  other secret dotfiles.
- **OpenAPI**: `openapi.json` updated for the masked `GET /api/secrets` and
  write-only `POST /api/secrets` schemas.
- **Tests (`tests/test_secrets_vault.py`)**: 15 tests on a temporary workspace
  covering masking, no GET side effect, keep-on-empty, replace-on-value, Kaggle
  validation with kept values, no plaintext in responses and propagation
  reporting.

### v16.9.12 - Browser Origin Guard & Closed CORS Allow-List

- **Origin Guard Middleware (`env_manager.security`)**:
  - New `origin_guard_middleware` refuses (HTTP 403) any request whose `Origin`
    is not an AI Studio GUI origin (`tauri://localhost`,
    `http://tauri.localhost`, `https://tauri.localhost`,
    `http://localhost:1420`, `http://127.0.0.1:1420`).
  - Browser requests using `POST`, `PUT`, `PATCH` or `DELETE` must send
    `X-LemGendary-Client: ai-studio-gui`; the custom header forces a CORS
    preflight and blocks cross-site form posts.
  - WebSocket handshakes from foreign origins are refused. Requests without
    `Origin` (CLI, scripts, tests) are unaffected.
- **Closed CORS Policy (`env_manager.server`)**:
  - Replaced `allow_origins=["*"]` with credentials by the allow-list above,
    explicit methods, explicit headers and `allow_credentials=False`. Fixes
    review finding D-01 (any web page could read `/api/secrets` or start
    destructive runs).
- **Test Dependency**: added `httpx2==2.16.0` (required by the FastAPI test
  client) to both requirement files.
- **Tests (`tests/test_security.py`)**: 16 tests covering allowed and foreign
  origins, write header rules, CORS preflight, WebSocket handshakes and the real
  app wiring.

### v16.9.11 - Graceful Server Shutdown & Stop Service Lifecycle Unlocking

- **Graceful Orchestrator Shutdown Endpoint (`env_manager.server`)**:
  - Implemented `POST /api/shutdown` endpoint to allow operators to trigger
    programmatic graceful self-shutdown with zero-zombie process cleanup.
- **Sidecar Stop Service Handler Unlocking (`env_manager.service_manager`)**:
  - Removed internal self-stop block from `stop_service('env-manager')`.
    Terminating port 8000 from the desktop GUI now gracefully dispatches
    `_kill_process_tree` with a 0.5s grace window, allowing full lifecycle stop
    control over all 3 sidecar ports (8000, 8100, 8200).
- **Strict Lint & Zero-Warning Pass (`env_manager.orchestrator`,
  `env_manager.service_manager`)**:
  - Resolved all 82 Ruff lint and style warnings across `orchestrator.py` and
    `service_manager.py` (import placement, line lengths, positional argument
    limits, and exception handling), restoring clean zero-warning compliance.

### v16.9.10 - Pipeline Step 5 Safe Dependency Upgrade Execution

- **Automated Bottom-Up Safe Upgrades in Pipeline Step 5
  (`env_manager.orchestrator`)**:
  - Upgraded Pipeline Step 5 from a dry-run audit report to actively apply
    verified safe dependency upgrades bottom-up.
  - Automatically applies safe python packages with snapshot rollback protection
    and runs npm updates for Node projects.
  - Synchronizes centralized requirement manifests with updated dependency
    versions following successful upgrades.

### v16.9.9 - Validation Suite Hardening & Cross-Project Changelog Audit

- **Automated Changelog Enforcement
  (env_manager.validator._audit_readme_changelog)**:
  - Validates that README.md includes an updated changelog section for any
    project with modified codebase files.
- **Bijective Documentation & Capabilities Synchronization Gate
  (_audit_docs_synchronization, _audit_manifest_synchronization)**:
  - Full bidirectional parity audit across all 48 Markdown whitepapers and HTML
    publications in lemgendary-docs/.
  - Enforces 100% alignment between documents.json, canonical_capabilities.json,
    and canonical_capabilities.yaml.
  - Ensures clean markdownlint formatting and W3C/WCAG accessibility compliance.

### v16.9.8 — Dependency Resolver Diagnostic Precision and Extras-Marker Guard

- **Actionable Blocker Diagnostics
  (`env_manager.dependency_resolver._diagnose_pinned_reason`)** — Replaced the
  generic `"kept pinned by another installed package's requirement"` blocker
  message with precise root-cause diagnostics. When a candidate version is
  blocked by the constraint-preserving dry-run, `_diagnose_pinned_reason`
  fetches the candidate's `Requires-Dist` metadata from PyPI and compares each
  dependency specifier against the currently installed versions, surfacing the
  exact conflicting pin (e.g., `diffusers 0.41.0 requires huggingface-hub<2.0
  but 2.2.0 is installed`).
- **Extras-Conditional Requirement Exclusion (`_get_reverse_dependencies`)** —
  Hardened the reverse-dependency graph builder to explicitly skip
  `Requires-Dist` entries whose marker expression contains the `extra`
  environment key. This prevents optional dependency groups (e.g.,
  `diffusers==0.35.2; extra == "testing"` declared by `transformers`) from being
  counted as active runtime constraints and generating false-positive blocker
  reports.
- **Top-Level Import Consolidation (`env_manager.updater`)** — Eliminated two
  `PLC0415`-violating function-level lazy imports (`probe_hardware` from
  `system_probe`, `constraint_preserving_dry_run` from `dependency_resolver`) by
  promoting them to the module top-level import block. No circular import risk
  exists between these modules.
- **Zero Lint Diagnostics** — All changes resolve to zero ruff/pylint/pyright
  diagnostics across `dependency_resolver.py` and `updater.py`.

---

### v16.9.7 — Centralized Desktop GUI Manifest Governance & Telemetry Progress Provisioning

- **NPM Package Manifest Synchronization
  (`requirements/lemgendary-ai-studio-gui.package.json`)** — Added
  `@radix-ui/react-progress` (`^1.1.2`) to the centralized GUI package manifest
  under `env_manager.requirements_manager`.
- **Automated Manifest Propagation (`lem-env sync`)** — Synchronized centralized
  package definitions to `lemgendary-ai-studio-gui/package.json` ensuring 100%
  manifest parity and deterministic dependency audits across desktop UI
  projects.

---

### v16.9.6 — Git Hook Installation API & CLI Alias Alignment, Cache Purge Endpoint

- **Git Hook Installation Endpoint (`POST /api/hooks/install`)** — Implemented
  dedicated REST endpoint executing `install_git_hooks()` asynchronously,
  allowing desktop GUI and automated scripts to install or repair standardized
  pre-commit hooks across all 7 ecosystem repositories without terminal
  commands.
- **CLI Subcommand Alias (`lem-env hooks`)** — Added concise alias
  `@app.command(name="hooks")` in `env_manager/cli.py` for `lem-env
  setup-hooks`.
- **Ecosystem Maintenance & Governance Alignment** — Fully integrated with
  `lemgendary-ai-studio-gui` HealthPanel to power one-click Git Hook
  installations, deep cache purges (`POST /api/clean`), safe package upgrades,
  and multi-gate ecosystem validations.

### v16.9.5 — Universal Zero Child Console Flashing & In-Process PE Inspection

- **Elimination of Child Process Console Flashing** — Configured
  `WINDOWS_NO_WINDOW` (`0x08000000`) across all `subprocess.run` invocations in
  `utils.py`, `system_probe.py`, `bootstrap.py`, `venv_manager.py`,
  `npm_manager.py`, `updater.py`, `hooks.py`, and `validator.py`. Completely
  prevents Windows from allocating temporary console or PowerShell windows
  during background hardware probing, health audits, package discovery, and
  toolchain checks.
- **In-Process PE FileVersion Reader** — Replaced external PowerShell PE version
  queries with native in-process `ctypes.windll.version.GetFileVersionInfoW`
  inspection for MetaTrader 5 executables, reducing probe latency to
  sub-millisecond with zero process allocations.
- **Unified Subprocess Flag Exporter** — Added `get_subprocess_creation_flags()`
  and `WINDOWS_NO_WINDOW` in `env_manager.utils` for consistent cross-module
  child process execution standards.

### v16.9.4 — Documentation Test Battery & Pre-Commit Hook Hardening

- **Automated Documentation Test Suite Gate** — Integrated
  `_audit_documentation_suite` directly into `run_domain_verification` in
  `env_manager/validator.py` for `lemgendary-docs`. Automatically executes the
  20-rule documentation test suite (`tests/test_documentation.py`) covering
  LaTeX notation, zero emojis, category uniqueness, HTML structural integrity,
  and SSOT manifest alignment as a mandatory gate under `lem-env validate`.
- **Git Hook Root Directory Discovery Hardening** — Hardened pre-commit hook
  scripts across `.githooks/pre-commit` and `.git/hooks/pre-commit` with dynamic
  directory inspection (`HOOK_DIR`) ensuring proper relative resolution of
  `WORKSPACE_ROOT` and `lem-env` regardless of whether git executes the hook
  from `.githooks` or `.git/hooks`.

### v16.9.9 — Orchestrator Hybrid Python-Node Project Upgrade Dispatch

- **Hybrid Project Python Pipeline Upgrade Alignment** — Refactored
  `_execute_package_installation` and `_execute_safe_upgrades` in
  `env_manager/orchestrator.py` to condition npm-only execution strictly on
  `p.is_node_project` instead of checking for `package.json`. Ensures that
  hybrid projects containing dev-time JavaScript linters
  (`lemgendary-env-manager`) have their Python virtual environments and pip
  dependencies fully upgraded and audited during Step 4 and Step 5 execution.

### v16.9.8 — Real-Time Streaming Requirements Progress & Live Telemetry

- **Real-Time Streaming Pip Execution** — Implemented `stream_pip_with_recovery`
  in `env_manager.utils` and `stream_install_requirements` in
  `env_manager.venv_manager` to yield stdout/stderr lines from pip in real time
  during requirement installation instead of blocking silently.
- **Dynamic Package Installation Telemetry** — Added `is_progress: bool` to
  `PipelineEvent` and implemented live pip stdout parsing in
  `PipelineOrchestrator._parse_pip_line_to_event` and
  `_execute_package_installation`, broadcasting structured progress ticks
  (`INSTALLING`, `DOWNLOADING`, package counts, transfer rates, and ETA) over
  local WebSocket to the desktop GUI.
- **Node.js NPM Streaming Execution** — Implemented `stream_npm_install` in
  `env_manager.npm_manager` with real-time process stdout line streaming and
  deterministic lifecycle completion.
- **Zero Resource Leaks & Cleanup** — Enforced strict `try/finally` resource
  cleanup closing subprocess pipe wrappers across `stream_pip_with_recovery` and
  `stream_npm_install`, eliminating all Python `ResourceWarning` diagnostics.

### v16.9.3 — Validator Manifest Directory Exclusion & Artifact Protection

- **Export and Cache Directory Masking** — Hardened `run_yamllint`,
  `run_jsonlint`, and `validate_project` in `env_manager/validator.py` with
  explicit exclusion of generator artifact folders (`export`, `runs`,
  `.lemtrain_server`, `.lgd_server`, `.env_server`). Eliminates false-positive
  lint failures caused by third-party training run configurations while
  preserving rigorous compliance enforcement across all source manifests.

### v16.9.2 — Windowless Cross-Platform Daemon Execution & Startup Polling Stabilization

- **Headless Windowless Daemon Execution** — Updated `service_manager.py` to
  launch background sidecars without spawning console or terminal windows. On
  Windows, preferentially selects `.venv/Scripts/pythonw.exe`
  (`IMAGE_SUBSYSTEM_WINDOWS_GUI`) and sets `CREATE_NO_WINDOW` (`0x08000000`)
  with `stdin=subprocess.DEVNULL`, preventing Windows 11 Windows Terminal from
  opening new windows. On Linux and macOS, sets `start_new_session=True`
  (`os.setsid()`) and runs `.venv/bin/python`.
- **Extended Startup Verification Window** — Increased sidecar health check
  timeout from 4.0s to 20.0s to accommodate heavy PyTorch/CUDA model import
  times on Windows without false timeout returns.
- **Automated Ecosystem Sidecar Startup on Boot** — Wired background
  `start_all_services()` directly into `lifespan` in `server.py`, ensuring all
  tripartite sidecars initialize on Environment Manager daemon startup.
- **Port Collision & Concurrency Protection** — Added `is_port_in_use()` guard
  to prevent duplicate process spawning during daemon startup transitions.
- **Dual GET & HEAD Health Endpoint Support** — Added `@app.head("/api/health")`
  decorator to ensure universal HTTP probe compatibility with desktop frontends.

### v16.9.1 — Ecosystem Sidecar Process Lifecycle Management & On-Demand Dispatch

- **On-Demand Sidecar Process Supervisor** — Added `env_manager.service_manager`
  to programmatically spawn, monitor, and gracefully terminate background
  sidecar daemons (`lemgendary-datasets` on port 8100,
  `lemgendary-training-suite` on port 8200) directly from the centralized
  orchestrator.
- **Service Control REST Endpoints** — Implemented `POST
  /api/services/{service_id}/start`, `POST /api/services/{service_id}/stop`, and
  `POST /api/services/start-all` on port 8000 with sub-second health
  verification, automated virtual environment resolution, and process PID
  tracking.
- **GUI Tripartite Integration** — Connected `lemgendary-ai-studio-gui` service
  cards to on-demand daemon startup, replacing pipeline re-installation triggers
  with non-destructive, isolated process dispatch.

### v16.9.0 — Server API Coverage, CLI Isolation & Comprehensive Testing Battery

- **Comprehensive 17-Test Quality Battery** — Implemented and verified full
  automated test suite (`tests/test_server_api.py`, `tests/test_cli.py`,
  `tests/test_bootstrap.py`, `tests/test_requirements_manager.py`,
  `tests/test_system_probe.py`, `tests/test_validator.py`) with 17/17 tests
  passing and 100% compliance under `lem-env validate`.
- **Dependency-Free Asynchronous Server API Testing** — Tested all FastAPI route
  handlers directly via coroutine invocation (`asyncio.run()`), eliminating
  external testing dependencies while achieving sub-second verification across
  hardware, project status, pipeline execution, and ecosystem mesh endpoints.
- **CLI Test Isolation & Performance Optimization** — Intercepted network-bound
  `winget` queries during CLI unit tests, reducing suite execution duration from
  265s down to 15s with zero flakiness.
- **Tripartite Mesh Orchestration** — Integrated sidecar discovery and health
  status reporting across the three ecosystem daemons (8000, 8100, 8200)
  powering `lemgendary-ai-studio-gui`.

### v16.8.0 — Container Runtime Dependencies Synchronization & SSOT Alignment

- **Container Runtime Dependency Matrix Synchronization** — Upgraded centralized
  requirements manifests (`requirements-datasets.txt`,
  `requirements-training.txt`) with complete runtime support for unified
  streaming and columnar container engines: `mosaicml-streaming>=0.9.0,<1.0.0`,
  `litdata>=0.2.0,<1.0.0`, `webdataset==1.0.2`, `pyarrow==25.0.1`, and
  `zstandard>=0.23.0`.
- **Runtime Environment Specification Upgrade** — Updated
  `requirements/runtime_env.yaml` to Version 1.1, explicitly registering
  `container_runtime_dependencies` with package specifiers, target container
  formats (`mds`, `litdata`, `webdataset`, `parquet`, `zstd`), and architectural
  descriptions.
- **Cross-Project Manifest Synchronization** — Executed `sync_all_manifests()`
  from `env_manager.requirements_manager` to propagate sanitized, deterministic
  dependency manifests into `lemgendary-datasets/requirements.txt` and
  `lemgendary-training-suite/requirements.txt`.
- **Web Service Dependencies Alignment** — Reconciled training server sidecar
  requirements in `requirements-training.txt` including `fastapi`, `uvicorn`,
  `pydantic`, and `httpx`.
