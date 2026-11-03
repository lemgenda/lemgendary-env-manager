# LemGendary Environment Manager - Lead Architect Review Report

Review date: 2026-11-02. Scope: `./` (package `env_manager`, CLI, FastAPI sidecar, PowerShell launcher, tests, manifests), measured against `../ROBOTS.md`, `../Instructions.md`, `../lemgendary-docs/MD-Papers/PAPER_ENV_MANAGER.md`, `../lemgendary-docs/MD-Papers/ECOSYSTEM_ARCHITECTURE.md`, `../lemgendary-docs/papers/env_manager.html`, and the GUI client `../lemgendary-ai-studio-gui/src/api/client.ts`.

This is a read-only review. No source, config, or existing document was modified. All `path:line` references are relative to this repository unless prefixed with `../`.

## Tooling Evidence

All tools ran from a throwaway venv outside the repository (Python 3.12.10, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`).

| Tool | Configuration | Result |
| :--- | :--- | :--- |
| `ruff 0.17.3` | project `pyproject.toml` rules | 588 diagnostics (251 UP006, 112 UP045, 95 E501, 39 PLC0415, 10 F401, 2 F811, 1 B904, 1 F841, others). README claims zero (README.md:168). |
| `pylint 4.1.3` | project `.pylintrc` (57 checks disabled) | 9.92/10; still reports `E0102 function-redefined` at env_manager/server.py:390 and :394, `W0706`, `W0707`. |
| `pylint 4.1.3` | default rc, only checks that `.pylintrc` hides | 102 `broad-exception-caught`, 10 `unused-import`, 8 `redefined-outer-name`, 3 `consider-using-with`, 2 `function-redefined`. |
| `pyright 1.1.414` | not run | `pip install pyright` fails at the mandated scratch path with a Windows MAX_PATH `OSError` on bundled typeshed stubs. Not a project defect; result unknown. |
| `pytest 9.1.1` | `-k "not sync and not clean and not validate_command"` | 23 passed, 4 deselected, 36.95 s. The 4 deselected tests mutate or network-touch the real workspace (see D-06). README still advertises "17/17 tests" (README.md:223). |

## Executive Findings

### Strengths

- Clear functional decomposition by concern at module level: `system_probe`, `venv_manager`, `dependency_resolver`, `updater`, `health_checker`, `validator`, `service_manager`.
- Upgrade safety model is conceptually strong: reverse-dependency analysis via `pip inspect`, constraint-preserving dry-runs, snapshot/rollback via `pip freeze` (env_manager/dependency_resolver.py:317-387, env_manager/updater.py:499-520).
- Corruption-aware pip retry with cache purge (env_manager/utils.py:128-240) and real-time streaming of pip/npm output into typed `PipelineEvent`s.
- Registry-first MetaTrader 5 detection with in-process PE version reading (env_manager/system_probe.py:213-345).
- Consistent use of `WINDOWS_NO_WINDOW` creation flags and a named logger hierarchy (env_manager/_logging.py).

### Weaknesses

- The HTTP sidecar has no authentication and a wildcard CORS policy with credentials, while exposing plaintext secrets and destructive operations.
- Four god modules: env_manager/validator.py (1584 lines), env_manager/server.py (985), env_manager/health_checker.py (960), env_manager/orchestrator.py (923, with ~250 lines of copy-pasted step logic).
- The workspace project inventory is hardcoded in at least 11 places, ports in at least 6, and the CLI and API each embed their own orchestration logic, so they diverge.
- Diagnostics are suppressed by configuration (`.pylintrc`, pyright `reportGeneralTypeIssues = none`, ruff ignores) and inline (`except ...: pass`, `-ErrorAction SilentlyContinue`, `ignore_errors=True`), in direct violation of ROBOTS.md.
- Seven different version strings are in circulation for the same product.

### Risks

- Any web page open in the user's browser can read `GET /api/secrets` (Kaggle, GitHub PAT, Hugging Face, MT5 password) and trigger `POST /api/pipeline/run`, which deletes every virtual environment.
- Central manifests in `requirements/` are older than sibling `requirements.txt` files. Every pipeline run (Step 4) and `lem-env sync`, including the unit test `tests/test_cli.py:52`, silently downgrades sibling repositories.
- Stderr is merged into stdout for all pip invocations, so any pip warning corrupts JSON parsing. The safety classifier then runs unconstrained and can mark unsafe upgrades as safe.
- `/api/health` is used by the GUI as a 2-second liveness probe but performs a full multi-minute audit (winget, PowerShell, `pip list` per venv).

### Opportunities

- Introduce one `workspace_registry` module, loaded from `../lemgendary-docs/canonical_capabilities.yaml` (which already declares port 8000 at line 12), as the single source for projects, ports, manifests, and service entry points.
- Extract an application-service layer (`services/pipeline.py`, `services/upgrade.py`, `services/validation.py`) that both `cli.py` and `server.py` call.
- Turn validator gates into pluggable strategies registered per project type (OCP), and move domain gates that belong to sibling repos into those repos.
- Generate `openapi.json` in CI from the running app and type-check the GUI client against it.

## Architecture Findings

### Current Architecture Assessment

```text
cli.py ──────────┐                         server.py (985 lines: pipeline, upgrade, manifests editor,
  (own inventory,│                           secrets vault, docs hosting, sidecar control, websockets)
   own validate, │                                   │
   own update)   ▼                                   ▼
        orchestrator.py ──(private import)──> updater._apply_batch_with_rollback
           │   (7 steps duplicated twice)           │
           ├─ venv_manager (inventory #1)            ├─ dependency_resolver
           ├─ requirements_manager (inventory #2)    ├─ system_probe
           ├─ health_checker ──(private import)──> updater._check_torch_cuda_upgrades
           └─ validator (1584 lines, all gates, hardcoded per-repo rules)
```

Key structural problems:

1. No service layer. Business logic lives in the entry points: `cli.update` (env_manager/cli.py:285-414), `cli.validate` (env_manager/cli.py:427-512), `server.run_update` (env_manager/server.py:331-367), `server.validate_codebase` (env_manager/server.py:806-827). The two paths already behave differently (see D-15). This violates ROBOTS.md "CLI and In-Process Unification".
2. Two upgrade engines. `updater.apply_upgrade_plan` (env_manager/updater.py:523-675) and `PipelineOrchestrator._execute_safe_upgrades` (env_manager/orchestrator.py:813-923) implement the same policy differently and disagree on CUDA index handling.
3. Layering inversions through private imports. env_manager/health_checker.py:261 imports `updater._check_torch_cuda_upgrades`, and env_manager/orchestrator.py:820-824 imports `updater._apply_batch_with_rollback` and `_apply_npm_update`.
4. Branching on hardcoded names instead of strategies: env_manager/validator.py:1461-1474 (per-repo `if p_name == ...`), env_manager/orchestrator.py:381-611 (`if step_number == 1 ... elif ... 7`), env_manager/validator.py:1231 (hardcoded `LemGendizedForexUniverseLarge` expecting 8 parquet files).
5. No abstraction over external tools (DIP). `subprocess.run` is called directly in 9 modules. Only `utils.run_command_simple` and `run_pip_with_recovery` approximate an adapter, and most callers bypass them.
6. `server.py` mixes six responsibilities: health/drift API, pipeline control, manifest editor, secrets vault (env_manager/server.py:523-762), static documentation hosting (env_manager/server.py:767-780), and sidecar lifecycle. The secrets vault and documentation hosting fall outside the stated purpose in PAPER_ENV_MANAGER.md section 2.

### Proposed Architecture

```text
env_manager/
  domain/            dataclasses only (HardwareProfile, ProjectInfo, PipelineEvent, UpgradePlan ...)
  registry/
    workspace.py     loads projects/ports/manifests/services from canonical_capabilities.yaml (SSOT)
    packages.py      TORCH_FAMILY, OPENCV_VARIANTS, BOOTSTRAP, CO_DEPENDENT, canonicalize_name()
  adapters/
    process.py       ProcessRunner protocol (run, stream, timeout+kill, separate stdout/stderr)
    pip.py           PipClient (list/inspect/freeze/install/dry-run) on ProcessRunner
    npm.py, winget.py, git.py, powershell.py
  services/
    probe.py, venvs.py, manifests.py (one direction: central -> project, with drift guard)
    upgrade.py       single engine used by CLI, API and pipeline step 5
    pipeline.py      Step protocol + registry of 7 Step classes; run_all == run_steps(1..7)
    validation/      Gate protocol; gates/{python,yaml,json,markdown,eslint,tsc,html,wcag,pssa}.py
                     domain gates loaded from each sibling repo (or a plugin entry point)
    services_mgr.py  sidecar lifecycle from registry
  api/
    app.py           FastAPI factory, auth token, strict CORS
    routes/{health,pipeline,upgrade,manifests,services}.py (thin, async-safe via run_in_executor)
  cli/
    main.py          argument parsing + rich rendering only
```

The secrets vault should move to a dedicated component, either an OS keyring adapter or the GUI's Tauri secure store, and must never return secret values over HTTP. Documentation hosting should belong to `lemgendary-docs` tooling.

## Refactoring Plan

These changes are proposed only. None were applied.

| # | Files to change | Rationale | SOLID / SSOT improvement |
| :--- | :--- | :--- | :--- |
| R1 | env_manager/server.py, new `api/` package | Remove `allow_origins=["*"]` with credentials (server.py:106-112). Add a per-session bearer token like the training suite's `.lemtrain_server/token`. Stop returning `secret_value` (server.py:674-692). | Security; SRP (split routes) |
| R2 | new `registry/workspace.py`; consumers in venv_manager.py:346-352, updater.py:62-68 and :696-701, requirements_manager.py:152-172, npm_manager.py:357-360, hooks.py:434-442, cli.py:438-446, server.py:414-443 and :726, service_manager.py:27-58, validator.py:1461-1474 | One canonical inventory of projects, ports, manifests, and entry points | SSOT; OCP (adding a project means editing data, not code) |
| R3 | new `services/pipeline.py` replacing orchestrator.py:95-619 | `run_clean_install_pipeline` and `run_pipeline_single_step` duplicate each step. Use a `Step` protocol and `run_steps(range)`. | SRP, OCP, DRY |
| R4 | merge orchestrator.py:813-923 into updater.py | One upgrade engine. Remove private cross-module imports. | SSOT, DIP |
| R5 | env_manager/utils.py:165-240 into `adapters/process.py` | Keep stderr separate, enforce timeouts with `kill()`, return typed results | DIP, reliability |
| R6 | split env_manager/validator.py into `validation/gates/*` | Pluggable `Gate` protocol. Move repo-specific gates (validator.py:1105-1377) to the owning repos. | SRP, OCP, ISP |
| R7 | env_manager/cli.py, env_manager/server.py | Thin adapters calling the same services (validate, clean, update) | ROBOTS.md CLI/API unification |
| R8 | `registry/packages.py` | Single definition of TORCH_FAMILY (updater.py:60, health_checker.py:49, dependency_resolver.py:481), OpenCV variants (venv_manager.py:28, health_checker.py:52), bootstrap set (dependency_resolver.py:21, health_checker.py:45), pydantic pairs (updater.py:71, dependency_resolver.py:418). Replace three `_normalize` implementations with `packaging.utils.canonicalize_name`. | SSOT |
| R9 | `.pylintrc`, pyproject.toml:37-39 and :56-61, pyrightconfig.json | Remove configuration-based suppressions and fix the underlying findings | ROBOTS.md zero-suppression |
| R10 | single `__version__` source (`importlib.metadata`) for pyproject, `__init__`, FastAPI `version`, `/api/gui/state`, PS1 banner, README title | One version | SSOT |

## Debug Report

Severity scale: Critical, High, Medium, Low. Every root cause was verified by reading the code. Items marked "inferred" were not reproduced at runtime.

### D-01 Critical: unauthenticated cross-origin access to secrets and destructive operations

- Location: env_manager/server.py:106-112 (CORS `*` with `allow_credentials=True`), :674-692 (`GET /api/secrets` returns plaintext tokens), :492-520 (`POST /api/manifests/save` writes arbitrary content), :275-287 (`POST /api/pipeline/run`).
- Root cause: the sidecar assumes localhost is a trust boundary. Browsers allow any origin to call `127.0.0.1:8000`, and Starlette's CORS middleware then lets that origin read the responses.
- Proposed fix: strict origin allow-list (the Tauri origin and the Vite dev origin from configuration), a bearer token generated at startup and shared through a `0600` file, no secret values in responses, and a confirmation token for destructive routes.

### D-02 Critical: plaintext credentials written into repositories and hardcoded identities

- Location: env_manager/server.py:726-731 writes `.kaggle_token` into three repositories. `.gitignore` does not ignore `.kaggle_token`: `git check-ignore` returned no match. Lines :569, :602, :620, :647, :736, :751 hardcode personal usernames and an MT5 server name. The `except OSError: pass` handlers at :730-731, :742-743, and :755-756 hide write failures.
- Root cause: legacy credential propagation added to a general-purpose API.
- Proposed fix: remove propagation, use an OS keyring adapter, and add `.kaggle_token`, `.secrets.yaml`, and `.env_server/` to `.gitignore` in every repository. Never echo secrets.

### D-03 High: a GUI "Run Pipeline" click deletes every venv and node_modules and kills running sidecars

- Location: `PipelineOrchestrator.run_clean_install_pipeline(clean=True)` default (env_manager/orchestrator.py:99), invoked without `clean` by env_manager/server.py:267. `RunPipelineRequest` has no `clean` field (server.py:115-116). Step 3 calls `create_venv`, which calls `terminate_processes_in_venv` (env_manager/venv_manager.py:171), and `shutil.rmtree(node_modules, ignore_errors=True)` (orchestrator.py:183). The GUI sends only `target_project` (../lemgendary-ai-studio-gui/src/api/client.ts:122-131).
- Root cause: a destructive default with no API parameter to change it.
- Proposed fix: default to `clean=False`, add `clean` to `RunPipelineRequest` and to `openapi.json`, require explicit confirmation, and remove `ignore_errors=True`.

### D-04 High: `/api/update` blocks the event loop for up to 20 minutes

- Location: env_manager/server.py:359-361 (`thread.join(timeout=1200)` inside `async def`). env_manager/server.py:338 calls the synchronous `build_upgrade_plan` on the loop. `/api/validate` (:806-827) and `/api/clean` (:783-803) also do synchronous heavy work on the loop.
- Root cause: synchronous calls inside coroutines. While they run, every other request and every WebSocket broadcast is frozen. `/api/update` also ignores `request.project` and is not guarded by `orchestrator.is_running`, so it can run pip concurrently with the pipeline on the same venv.
- Proposed fix: run the work as a background job with an id, stream progress over the WebSocket, use a global operation lock, and honour `project`.

### D-05 High: `/api/health` is far too heavy for a liveness probe

- Location: env_manager/server.py:139-157 runs `run_full_health_audit`. That calls `verify_prerequisites`, which calls `check_winget_updates` (env_manager/bootstrap.py:248-262, up to 60 s + 60 s + 30 s per package), the DirectML PowerShell probe (system_probe.py:182-187, up to 20 s), and `pip list` in every venv. The GUI probes it with a 2 s abort (../lemgendary-ai-studio-gui/src/api/client.ts:161-175, called from ../lemgendary-ai-studio-gui/src/App.tsx:84). `HEAD /api/health` (server.py:140) runs the same audit.
- Root cause: liveness and audit are conflated. Inferred, not measured: the env manager will routinely show as offline, and each aborted probe still occupies an executor thread.
- Proposed fix: make `/api/health` an O(1) liveness response and move the audit to `/api/audit`. Update the GUI and `openapi.json`.

### D-06 High: manifest SSOT is broken, and sync downgrades sibling repositories

- Evidence: comparing `requirements/requirements-*.txt` with the sibling `requirements.txt` files shows the central files are older. Examples: `torch==2.14.1+cu126` vs `torch==2.15.0`, `fastapi==0.141.1` vs `0.162.0`, `pyarrow==25.0.1` vs `26.0.0`, `transformers>=5.0.0,<6.0.0` vs `==5.22.1`.
- Locations: `sync_all_manifests` (env_manager/requirements_manager.py:144-185) copies central to sibling with no drift guard. It runs in pipeline Step 4 (orchestrator.py:227), in Step 6 (:286), in `lem-env update` (cli.py:407), and in the unit test tests/test_cli.py:52-55, so running the test suite rewrites sibling repositories. `/api/manifests/save` writes the sibling copies directly (server.py:420-423) under names that suggest the central files, so GUI edits are reverted at the next sync. `sync_manifests_from_venvs` (updater.py:680-768) also collapses ranges to `==` pins.
- Root cause: two writable copies and three writers.
- Proposed fix: make the central file the only writable copy. The editor writes central, and sync refuses to downgrade or warns on drift. Mark the sibling files as generated. Make `test_cli` use a temporary workspace.

### D-07 High: merged stderr corrupts JSON, and the safety classifier can run unconstrained

- Location: env_manager/utils.py:181-206 (`stderr=subprocess.STDOUT`; the returned `stderr` is always `""`). Consumers parse `stdout` as JSON: dependency_resolver.py:74, :95, :116; venv_manager.py:229; health is affected indirectly.
- Root cause: any pip warning on stderr (for example `WARNING: Ignoring invalid distribution ~orch`, which is common on Windows after an interrupted upgrade) makes `json.loads` fail. `_installed_package_versions` then returns `{}` (dependency_resolver.py:85-99), `constraint_pins` is empty (:334-338), and the "constraint-preserving" dry-run becomes unconstrained. Candidates can then be classified as safe. Error messages are also lost, because callers read `proc.stderr`, which is empty (venv_manager.py:212, updater.py:242).
- Proposed fix: capture stdout and stderr separately (stream stdout, collect stderr), and fail closed: if the installed state is empty, block all candidates.

### D-08 High: pipeline Step 5 misroutes any project that has a `package.json`

- Location: env_manager/orchestrator.py:829-853. The condition `p.is_node_project or (p_dir / "package.json").exists()` is true for `lemgendary-env-manager` (it has `package.json`). The branch calls the pip function `find_outdated_packages`, runs `npm update` in the Python repository, then `continue`s, so the env manager's Python upgrades are never applied. For the real Node project, `find_outdated_packages` returns `[]` because there is no venv, so npm upgrades are never applied either. env_manager/orchestrator.py:703 has the same condition in Step 4.
- Proposed fix: dispatch on a registry-declared project type, use `_find_npm_outdated` for npm, and do not `continue` for dual-stack projects.

### D-09 High: `lem-env update --project X` upgrades every project

- Location: env_manager/cli.py:347-349 filters only the printed events. `apply_upgrade_plan` (env_manager/updater.py:534) iterates every project in the plan. The API ignores `project` entirely (server.py:331-367).
- Proposed fix: filter the plan before applying.

### D-10 Medium: the apply phase does not honour the vetted plan

- `pip install --upgrade <name>` is unpinned (env_manager/updater.py:367), so it can install a newer version than the vetted `latest_version` and upgrade unvetted transitive dependencies. Pin to `name==latest_version`.
- The torch CUDA reinstall runs whenever CUDA is present and torch is installed, even when the plan had no torch candidates (updater.py:601-605). That is a 2400 s pip run that bypasses classification.
- When `_apply_pip_upgrade` fails mid-way, no rollback happens even though a snapshot exists (updater.py:504-508).
- CUDA detection disagrees between components. `updater._detect_cuda` returns `None` on CPU hosts (updater.py:151-166). The health checker and orchestrator pass `recommended_torch_index`, which is always set, including the CPU index (health_checker.py:925, orchestrator.py:759, :874, :899; cli.py:347). So "safe" means different things in audit and apply.

### D-11 Medium: the drift matrix misses packages with `_` or `.` in their names

- Location: env_manager/health_checker.py:483 looks up the PEP 503-normalized name (`pydantic-core`) in `p.installed_packages`, whose keys are raw pip names (`pydantic_core`). Installed versions show as `None` and drift is under-reported. The coverage path normalizes correctly (health_checker.py:546-548).

### D-12 Medium: orchestrator state races

- `is_running` stays `True` forever if the generator is closed early. `GeneratorExit` is not an `Exception`, and the full pipeline has no `finally` (orchestrator.py:102, :332-340).
- Check-then-start race: `is_running` is set only when the worker thread first iterates (server.py:277-286), so two fast POSTs start two pipelines.
- `current_step` is never updated during the full pipeline (orchestrator.py:88 and :351), so `get_status` is wrong.
- `recent_events` is mutated from worker threads without a lock (server.py:268-271, :302-304).

### D-13 Medium: `clean` deletes compiled extension modules

- Location: env_manager/utils.py:303 treats `.pyd` (Windows native extension DLLs) as bytecode. It only skips `.venv`, `.git`, `node_modules`, and `.cache`, so vendored or in-tree extensions are destroyed. `/api/clean` also cleans Node projects while the CLI skips them (server.py:793 vs cli.py:529).

### D-14 Medium: subprocess timeouts do not stop hung processes

- Location: env_manager/utils.py:193-200 and env_manager/npm_manager.py:317-324. `readline()` blocks until EOF, so `proc.wait(timeout)` applies only after output ends. On `TimeoutExpired`, the child is never killed (utils.py:207-212, npm_manager.py:328-329).

### D-15 Medium: CLI and API validate different sets

- `lem-env validate` covers 7 projects with explicit `is_node` flags (cli.py:438-466). `POST /api/validate` covers only the 5 discovered projects and never passes `is_node_project` (server.py:810-819). Results for the same request differ between CLI, API, and GUI.

### D-16 Medium: the validator fails open

- Tool crashes or a missing binary return zero violations: yamllint (validator.py:241-250, exit code 2 only logged at debug), markdownlint (:386-387), ESLint (:437-441, :462-463), tsc (:500-501), html-validate (:547-548), pa11y (:602-603), PSScriptAnalyzer (:677-685). A broken toolchain therefore reports `[PASS]`.
- `npx --yes` (validator.py:325) downloads packages at validation time, which is non-deterministic.
- README.md:67-68 advertises suppression detection and Pylint. Neither is implemented, and there is no absolute-path gate.

### D-17 Medium: Python version contract is inconsistent

- `pyproject.toml:10` declares `>=3.10` and env_manager/bootstrap.py:406 accepts 3.10. However, `Path.walk` (validator.py:265, :1504) and `shutil.rmtree(onexc=...)` (venv_manager.py:178, :188) require 3.12 and raise `AttributeError` or `TypeError` on 3.10 and 3.11. ECOSYSTEM_ARCHITECTURE.md:109 says 3.12+.

### D-18 Low: git hook template problems

- Windows-only paths (env_manager/hooks.py:392-393, .githooks/pre-commit:11-12) make every commit fail with exit 1 on Linux and macOS.
- The copy written to `.git/hooks` (hooks.py:474-477) resolves `PROJECT_NAME` to `.git`.
- The exit code of `git config core.hooksPath` is ignored (hooks.py:465-471).
- The `HOOK_DIR` hardening claimed in README.md:194 is absent.

### D-19 Low: launcher swallows errors and exit codes

- lemgendary_env_manager.ps1:69 always calls `exit 0`, discarding CLI failures.
- `-ErrorAction SilentlyContinue` appears at :38, :41, :86, :131, :133-134, :153-157.
- `ValidateSet` (:9) omits `clean` and `hooks`.
- The URL printed at :139 is truncated (`http://127.0.0`).
- Paths are hardcoded to `Scripts\python.exe`.

### D-20 Low: contract and hygiene defects

- `openapi.json` is stale. It is missing `POST /api/pipeline/run-step`, `POST /api/hooks/install`, `HEAD /api/health`, and the WebSocket routes, all of which the GUI uses (../lemgendary-ai-studio-gui/src/api/client.ts:137, :304).
- The GUI types `/api/projects` as `ProjectHealth[]` (../lemgendary-ai-studio-gui/src/api/types.ts:43-53), but the server returns `ProjectVenvInfo` (env_manager/venv_manager.py:36-50). `fetchProjects` has no current callers, so the mismatch is latent.
- `CleanRequest` and `ValidateRequest` are defined twice (server.py:126-131 and :390-395).
- Dead code: `_event_queue` is never fed (server.py:69-80); `create_safe_upgrade_plan`, `apply_safe_upgrades`, and `_pip_dry_run` are unused (dependency_resolver.py:515-576); there are unused imports (orchestrator.py:22, :29; validator.py:23-24; service_manager.py:11).
- `start_all_services` is fire-and-forget in `lifespan`, so its exceptions are lost (server.py:88).
- `server` and `service_manager` loggers sit outside the configured `lem_env` hierarchy (server.py:41, service_manager.py:23).
- `psutil.net_connections` can raise `AccessDenied` on macOS without a handler (service_manager.py:331).
- `venv_manager.py:11` mutates `sys.path`.
- `discover_projects` spawns `python --version` three times per project (venv_manager.py:360-363).
- Hardcoded absolute fallbacks `C:\Program Files` appear at system_probe.py:350-351.
- A path is interpolated unquoted into PowerShell (system_probe.py:264).

### ROBOTS.md Suppression and Swallow Inventory

- Configuration suppressions: `.pylintrc:9-57` disables `bare-except`, `broad-exception-caught`, `used-before-assignment`, `duplicate-except`, `subprocess-run-check`, `unused-import`, and others. `pyproject.toml:37-39` and `pyrightconfig.json` set `reportGeneralTypeIssues = none`. `pyproject.toml:56-61` adds ruff ignores.
- `except ...: pass`: cli.py:11-12, health_checker.py:386-387, server.py:730-731, :742-743, :755-756, service_manager.py:222-223, :274-275, :281-282, venv_manager.py:144-145, :151-152.
- Silent `except Exception: return`, `continue`, or default: bootstrap.py:112-113, dependency_resolver.py:77-78, :145-146, :224-225, health_checker.py:633-634, :643-644, npm_manager.py:263-264, orchestrator.py:54-55, :198-199, :454-455, :747-748, service_manager.py:86-87, validator.py:1403-1404, :1513-1514.
- `ignore_errors=True`: orchestrator.py:183, :440.

## Documentation Findings

### README.md

- Seven version strings for one product: title `v16.9.8-STABLE` (README.md:1); newest changelog entry `v16.9.10` (:147); `pyproject.toml:7`, `env_manager/__init__.py:11`, and `package.json:3` say `16.2.0`; FastAPI `version="2.0.0"` (server.py:101, :895); the PS1 banner says `v2.1.0` (lemgendary_env_manager.ps1:2); the paper says `v16.8.0-STABLE`.
- The changelog is out of order and duplicated: `v16.9.8` appears at README.md:163 and again at :196, placed after `v16.9.4`.
- The CLI section documents a non-existent `discover` command (README.md:120) and omits `install`, `update`, `sync`, `setup-hooks`, and `hooks`.
- The API reference omits `run-step`, `hooks/install`, `manifests/registry|read|validate|save`, `secrets`, `docs/status`, and `services/*`. The diagram shows only the 8100 sidecar (README.md:35-39).
- README.md:50 lists `cu128`/`cu124`; the code selects `cu126`/`cu121`/`cu118` (system_probe.py:456-468).
- Claims of "zero ruff/pylint/pyright diagnostics" (README.md:168) and "17/17 tests" (:223) are false: ruff reports 588 and there are 27 tests.

### PAPER_ENV_MANAGER.md and papers/env_manager.html

- The paper says `v16.8.0-STABLE` (PAPER_ENV_MANAGER.md:1). `manifest/documents.json` records `baseline_version` `v16.9.27-STABLE` and category `Category 01.4 ENV`, while the paper says `Category 01.1` (PAPER_ENV_MANAGER.md:3). The HTML `h1` drops the version (../lemgendary-docs/papers/env_manager.html:59).
- The `IndexURL` formula (PAPER_ENV_MANAGER.md:76) gives only `cu121`; the code is driver-dependent.
- Section 5 claims manifests are "bidirectionally mirrored" (PAPER_ENV_MANAGER.md:100). The code is one-way plus venv write-back, and the list omits `requirements-documentation.txt` and `lemgendary-env-manager.package.json`.
- Section 6 lists "Linux, Windows, macOS: Universal". This is contradicted by the Windows-only hooks and launcher (D-18, D-19). "Multi-Cloud Notebook Sync" is not implemented in this repository. The benchmark figures cannot be reproduced from the repository; status uncertain.
- Section 7 step 6 claims "documentation word-for-word synchronization" and "Tauri build structure" checks. The validator only compares H2 counts, table counts, and a length ratio (validator.py:1000-1053) and has no Tauri gate.
- Section 7.1 describes a lifespan "drain task" that is dead code. Section 7.2 says sidecars are probed "concurrently"; they are probed sequentially (server.py:913-920), and the 8200 sidecar is not mentioned.
- The section layout (inserted "2. System Architecture & Entrypoints", 9 sections) deviates from the ROBOTS.md "Dataset Compilers & Frameworks" ordering. Whether the env manager falls under that category is uncertain and needs an owner decision.

### ECOSYSTEM_ARCHITECTURE.md

- Line 109 says "Pylint and astroid eliminated", yet `.pylintrc` exists and README.md:68 advertises Pylint. It also says "Python 3.12+ required", which conflicts with `requires-python >=3.10`.
- Line 110 says MosaicML MDS is "deprecated and fully transcoded", while requirements/runtime_env.yaml:30-33 and README.md:230 still register `mosaicml-streaming`. `requirements-datasets.txt` does not contain it.
- Line 111 pins `torch 2.14.1+cu126`; the training repo's `requirements.txt` has `torch==2.15.0`.

## Cross-Project Findings

| Item | This repo | Sibling(s) | Issue |
| :--- | :--- | :--- | :--- |
| Ports 8000/8100/8200 | service_manager.py:32-53, server.py:778, :896, :915-936, cli.py:542 | ../lemgendary-ai-studio-gui/src/api/config.ts:38-40, ../lemgendary-training-suite/training/utils/env_delegate.py:19, ../lemgendary-docs/canonical_capabilities.yaml:12 | At least four sources of truth. Make canonical_capabilities.yaml authoritative. |
| Project inventory | 11 copies (see R2) | ../lemgendary-ai-studio-gui/src/api/types.ts:55 (`CANONICAL_PROJECT_NAMES`), config.ts:42-66 | Duplicated inventory |
| Python manifests | requirements/requirements-*.txt | sibling `requirements.txt` | Drifted; central is older (D-06) |
| GUI `package.json` | requirements/lemgendary-ai-studio-gui.package.json | ../lemgendary-ai-studio-gui/package.json | Identical today, but the env manager overwrites another repo's tracked file (requirements_manager.py:169-183) and does not manage `package-lock.json` |
| ESLint major | `package.json` `eslint ^9.22.0` | GUI `eslint ^8.57.1` with `.eslintrc.json` | npm drift. `--ext` (validator.py:417) is invalid under ESLint 9 flat config, which is latent if a project adopts ESLint 9. |
| Sidecar entry points | service_manager.py:35-46 (`api.server:app`, `training.server.app:app`, `.lgd_server`, `.lemtrain_server`) | Verified to exist in both siblings | Hardcoded cross-repo knowledge; sibling-owned |
| Domain gates | validator.py:1105-1377 (datasets, models, training schema rules, 8-parquet rule) | Owned by sibling repos | Logic for another repo's invariants lives here; SSOT violation |
| Online docs URL | server.py:779 | ../lemgendary-ai-studio-gui/src/api/client.ts:398 | Duplicated literal |
| WebSocket path | `/ws/log` and `/ws/logs` (server.py:970-971) | GUI uses `/ws/log` (config.ts:48); README.md:104 says `/ws/logs` | Two aliases; docs disagree |
| `/api/health` semantics | Full audit | GUI liveness probe with 2 s timeout | Contract mismatch (D-05) |
| `runtime_env.yaml` | Consumed by ../lemgendary-training-suite/training/notebooks/cells/env.py:35-38 and ../lemgendary-datasets/runtime/environment.py:65 | - | Correct SSOT pattern, but `container_runtime_dependencies` duplicates pins from requirements-datasets.txt (pyarrow, webdataset) |
| Subprocess delegation | - | ../lemgendary-training-suite/training/utils/env_delegate.py:11 and :49-52 shell out to `lem-env` | Acceptable across separate venvs; flag for an owner decision against ROBOTS.md SSOT rule 3 (uncertain) |

## Proposed Changelog Entries

These entries are ready to merge once the corresponding fixes land. They are not applied.

```text
### v16.10.0 - Security Hardening, SSOT Registry & Service-Layer Unification

- Security: replaced wildcard CORS with an origin allow-list and per-session bearer token; `GET /api/secrets` no longer returns secret values; removed plaintext token propagation into repositories; `.kaggle_token`, `.secrets.yaml`, `.env_server/` added to `.gitignore`.
- Pipeline: `POST /api/pipeline/run` now defaults to `clean=false` and accepts an explicit `clean` flag; added a global operation lock preventing concurrent pipeline/update runs.
- API: `/api/health` is now an O(1) liveness probe; full audit moved to `GET /api/audit`; `/api/update`, `/api/validate`, `/api/clean` run as background jobs and no longer block the event loop; `openapi.json` regenerated (adds run-step, hooks/install, audit).
- Upgrades: single upgrade engine shared by CLI, API and pipeline Step 5; applies pinned `name==version` from the vetted plan; rollback on partial apply failure; `--project` filter now restricts what is applied; npm projects routed by registry type.
- Reliability: pip stdout/stderr captured separately; classifier fails closed on empty installed-state; subprocess timeouts now kill the child process; `clean` no longer deletes `.pyd` extension modules.
- SSOT: new `env_manager.registry.workspace` (projects, ports, manifests, sidecars) sourced from `lemgendary-docs/canonical_capabilities.yaml`; package-family constants consolidated; manifest sync refuses downgrades and the manifest editor writes central manifests only.
- Governance: removed configuration-based lint suppressions and all `except ...: pass` handlers; single `__version__` source.
- Docs: README CLI/API reference, version, and changelog order corrected; PAPER_ENV_MANAGER.md (MD + HTML) synchronized.
```

## Remaining Recommendations

Ranked by priority.

1. P0: fix D-01 and D-02 (CORS, auth, secrets exposure, plaintext token files), and audit sibling repositories for already-committed `.kaggle_token` files.
2. P0: stop destructive defaults and the sibling downgrade (D-03, D-06). Make `tests/test_cli.py` hermetic (temporary workspace) so the suite cannot mutate sibling repositories.
3. P1: fix the async blocking and health semantics (D-04, D-05), then regenerate `openapi.json` and the GUI types in the same change.
4. P1: correct the upgrade engine (D-07, D-08, D-09, D-10) and add regression tests for each: stderr noise, a dual-stack project, `--project` scope, pinned apply.
5. P1: introduce `registry/workspace.py` and the service layer (R2, R3, R4, R7). This removes 11 inventory copies and the CLI/API divergence (D-15).
6. P2: remove configuration and inline suppressions (R9) and drive ruff from 588 diagnostics to zero by fixing root causes; restore pyright with `reportGeneralTypeIssues` enabled.
7. P2: decompose `validator.py` into pluggable gates, make gates fail closed (D-16), and move repo-specific domain gates to their owning repositories.
8. P2: unify versioning (R10) and fix the README, paper, HTML, and `documents.json` drift listed above, keeping MD and HTML synchronized word for word.
9. P3: cross-platform hooks and launcher (D-18, D-19), Python version contract (D-17), and the remaining Low items in D-20.
10. P3: decide ownership of the secrets vault and documentation hosting, and move them out of the env manager sidecar.

## Remediation Status

Status of every Medium and Low finding on branch `fix/review-remediation`
as of v16.9.35. Each item is marked Fixed (with the commit), N/A (with
the reason), or Deferred (with the reason and the step that owns it).
Every finding was checked against the current code before it was marked,
because the code changed a lot after the review.

Totals: 41 fixed, 1 N/A, 2 deferred.

### D-10 Medium: the apply phase does not honour the vetted plan

- Unpinned `pip install --upgrade`: Fixed in `4d9a0d8` (v16.9.28). Each
  candidate installs as `name==latest_version` in the CLI, the API and
  pipeline Step 5.
- Torch CUDA reinstall with no torch candidates: Fixed in `4d9a0d8`. It
  runs only for torch-family candidates in the plan, pinned.
- No rollback when the install fails part-way: Fixed in `4d9a0d8`. A
  failed install restores the `pip freeze` snapshot, on the normal and
  the torch CUDA path.
- CUDA detection differs between audit and apply: Fixed in `4d9a0d8`.
  `updater.torch_upgrade_index` is the one definition, and
  `apply_upgrade_plan` reuses the index stored in the plan.

### D-11 Medium: the drift matrix misses names with `_` or `.`

- Fixed in `ccbe8b9` (v16.9.29). Installed names are normalized before
  the lookup.

### D-12 Medium: orchestrator state races

- `is_running` stuck after `GeneratorExit`: Fixed in `ccbe8b9`. It is
  cleared in a `finally` block.
- Check-then-start race: Fixed in `ccbe8b9`. The run is claimed with
  `try_claim` before the thread starts, under a lock shared with
  `POST /api/update`.
- `current_step` never updated: Fixed in `ccbe8b9`.
- `recent_events` mutated without a lock: Fixed in `ccbe8b9`.
- GUI requirement, every `recent_events` entry carries a `timestamp`:
  Fixed in `ccbe8b9`. `record_event` stamps any event without one and
  stores the same dict it broadcasts.

### D-13 Medium: `clean` deletes compiled extension modules

- `.pyd` treated as bytecode: Fixed in `a3599fa` (v16.9.30).
- `/api/clean` cleans Node projects, the CLI does not: Fixed in
  `a3599fa`. Both call `utils.purge_workspace_caches`.

### D-14 Medium: subprocess timeouts do not stop hung processes

- Child not killed on timeout: Fixed in `6f09c15` (v16.9.20,
  `wait_or_kill`).
- Timeout only starts after output ends: Fixed in `a3599fa`.
  `utils.stream_process_lines` reads both pipes against one deadline and
  kills the process tree. pip and npm streaming both use it.

### D-15 Medium: CLI and API validate different sets

- Fixed in `6e013fc` (v16.9.31). Both call
  `validator.validate_workspace` over `VALIDATION_TARGETS`.

### D-16 Medium: the validator fails open

- Tool crash or missing binary reports a pass: Fixed in `6e013fc`. Every
  external gate adds a `tool_error` violation instead.
- `npx --yes` downloads at validation time: Fixed in `6e013fc`
  (`npx --no`).
- README advertises suppression detection and Pylint: Fixed in
  `6e013fc`. The README now lists them as not implemented.
- Missing suppression, Pylint and absolute-path gates: Deferred to step
  L8. They are new gates and belong in the pluggable gate split (R6).

### D-17 Medium: Python version contract is inconsistent

- Fixed in `e84a463` (v16.9.32). `requires-python`, `MIN_PYTHON`, the
  Ruff target and the launcher all require 3.12, and a test keeps them
  equal.

### D-18 Low: git hook template problems

- Windows-only paths: Fixed in `e84a463`. The hook also looks in
  `.venv/bin`.
- The `.git/hooks` copy names the project `.git`: Fixed in `e84a463`.
  The hook uses `git rev-parse --show-toplevel`.
- Exit code of `git config core.hooksPath` ignored: Fixed in `e84a463`.
- `HOOK_DIR` hardening claimed but absent: Fixed in `e84a463`. The
  `git rev-parse` lookup works from either hook directory.

### D-19 Low: launcher swallows errors and exit codes

- `exit 0` after CLI calls: Fixed in `7f9b678` (v16.9.22).
- `-ErrorAction SilentlyContinue`: Fixed in `7f9b678`.
- `ValidateSet` omits `clean` and `hooks`: Fixed in `aa3758b`
  (v16.9.33). A test compares it with the Typer commands.
- Truncated URL `http://127.0.0`: Fixed in `aa3758b`.
- Hardcoded `Scripts\python.exe`: Fixed in `aa3758b`. POSIX venvs use
  `bin/python`.

### D-20 Low: contract and hygiene defects

- Stale `openapi.json`: Fixed in `b2c88e1` (v16.9.15). Since `0c30aad` a
  test keeps it equal to `app.openapi()`.
- WebSocket routes missing from `openapi.json`: N/A. OpenAPI has no
  WebSocket operations; the README documents `/ws/log`.
- GUI types `/api/projects` as `ProjectHealth[]`: Deferred. The type
  lives in `../lemgendary-ai-studio-gui/src/api/envManager.ts:34`, which
  this step may not edit; it is listed for the GUI owner below.
- `CleanRequest` and `ValidateRequest` defined twice: Fixed in `f71f83e`
  (v16.9.19).
- Dead `_event_queue`: Fixed in `0085e7a` (v16.9.34).
- Unused `create_safe_upgrade_plan`, `apply_safe_upgrades`,
  `_pip_dry_run`: Fixed in `0085e7a`.
- Unused imports: Fixed in `f71f83e`; Ruff reports none since `0a1b824`.
- `start_all_services` fire-and-forget: Fixed in `0085e7a`. Its result
  or exception is logged.
- `server` logger outside `lem_env`: Fixed in `0085e7a`.
- `service_manager` logger outside `lem_env`: Fixed in `f71f83e`.
- `net_connections` `AccessDenied` on macOS: Fixed in `0085e7a`.
- `venv_manager` mutates `sys.path`: Fixed in `6f09c15`.
- `discover_projects` runs `python --version` three times: Fixed in
  `0085e7a`. One run per project.
- Hardcoded `Program Files` fallbacks: Fixed in `0085e7a`.
- Unquoted path in PowerShell: Fixed in `0085e7a`. `ps_quote` escapes
  `'`.

### Other Remediation in This Step

- R9 Ruff: `ruff check .` reports 0 diagnostics since `0a1b824`
  (v16.9.27). Only `PLR0912`, `PLR0913` and `PLR0915` stay ignored, for
  the module splits in step L8.

### Cross-Repository Items for Step L7

Left unchanged here by design. Each one is a copy that should come from
one registry (R2).

- Ports 8000, 8100 and 8200: `service_manager.SERVICE_CONFIGS`, the
  `serve` default in `cli.py`, the launcher banner, the GUI
  `src/api/config.ts`, the training suite `env_delegate.py`, and
  `canonical_capabilities.yaml`.
- Project inventory: `validator.VALIDATION_TARGETS` (now shared by
  `validate`, `/api/validate` and the hook installer),
  `venv_manager.discover_projects`, `updater.UPGRADE_ORDER`,
  `requirements_manager.sync_all_manifests`, the server manifest
  registry, `service_manager`, and the GUI `CANONICAL_PROJECT_NAMES`.
- Model and dataset lists: the domain gates in `validator.py` (for
  example `LemGendizedForexUniverseLarge` and `MANIFOLD_PARQUET_COUNT`)
  encode sibling-repo rules.
- Online docs URL: `server.py` and the GUI `src/api/client.ts`.
- GUI `/api/projects` type: `ProjectHealth[]` in the GUI against
  `ProjectVenvInfo` here (D-20, deferred above).
