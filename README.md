# LemGendary Environment Manager

A centralized, cross-platform environment and dependency manager for the LemGendary AI ecosystem.

## Overview

LemGendary Environment Manager (`lem-env`) replaces fragmented environment management logic across individual repositories with a unified, cross-platform Python package, CLI, and REST/WebSocket API sidecar service.

### Core Capabilities

- **Automated Hardware Discovery**: Probes host system hardware across NVIDIA CUDA, AMD ROCm, DirectML (Windows), and CPU fallbacks. Automatically resolves matching PyTorch extra-index URLs.
- **Smart Clean Install Pipeline**: End-to-end multi-step orchestration that verifies global toolchains, provisions isolated `.venv` environments, synchronizes centralized requirement manifests, applies safe dependency upgrades, and performs bytecode compilation.
- **Ecosystem Health Matrix**: Real-time auditing of installed packages, missing dependencies, and package version drift across sibling projects.
- **Validation Engine**: Performs bytecode compilation checks (`py_compile`) and strict zero-emoji compliance audits across source files.
- **Sidecar API & WebSockets**: Built-in FastAPI server offering REST endpoints and real-time WebSocket log streaming for the LemGendary AI Studio desktop application.

## Directory Structure

```text
lemgendary-env-manager/
├── env_manager/                # Core Python package
│   ├── bootstrap.py            # Toolchain prerequisites audit
│   ├── cli.py                  # Typer CLI commands
│   ├── dependency_resolver.py  # Outdated package detection and safe upgrades
│   ├── health_checker.py       # Ecosystem health and drift matrix
│   ├── npm_manager.py          # NPM package and documentation audits
│   ├── orchestrator.py         # Smart Clean Install Pipeline
│   ├── requirements_manager.py # Manifest synchronization
│   ├── server.py               # FastAPI and WebSocket sidecar
│   ├── system_probe.py         # Hardware and accelerator detection
│   └── validator.py            # py_compile and zero-emoji verification
├── requirements/               # Centralized requirements manifests
│   ├── requirements-datasets.txt
│   ├── requirements-env-manager.txt
│   └── requirements-training.txt
├── tests/                      # Automated test suite
├── lemgendary_env_manager.ps1  # Authoritative PowerShell launcher
├── pyproject.toml              # Build system and CLI entrypoint
└── requirements.txt            # Package dependencies
```

## Quick Start

### PowerShell Hub

Launch the interactive management hub:

```powershell
.\lemgendary_env_manager.ps1
```

Or execute commands directly:

```powershell
.\lemgendary_env_manager.ps1 probe
.\lemgendary_env_manager.ps1 audit
.\lemgendary_env_manager.ps1 install
.\lemgendary_env_manager.ps1 validate
.\lemgendary_env_manager.ps1 serve
```

### Command Line Interface

```bash
# Probe system hardware and accelerators
lem-env probe

# Audit health matrix and package version drift
lem-env audit

# Execute Smart Clean Install Pipeline
lem-env install

# Synchronize centralized requirement manifests
lem-env sync

# Validate all files for syntax and zero-emoji compliance
lem-env validate

# Reclaim disk space by purging bytecode caches and temporary artifacts
lem-env clean

# Run FastAPI and WebSocket server
lem-env serve --port 8000
```

## Documentation

For the complete architectural whitepaper, operational recipes, and full API specifications, please consult the authoritative documentation in the `lemgendary-docs` repository:

- [Technical Whitepaper & Operations Manual (Markdown)](../lemgendary-docs/MD-Papers/PAPER_ENV_MANAGER.md): Scientific architecture, mathematical complexity bounds, Sibling Surgery migration analysis, CLI reference, and API specifications.
- [Technical Whitepaper & Operations Manual (HTML)](../lemgendary-docs/papers/env_manager.html): Synchronized scientific whitepaper and operations manual formatted for webview.
