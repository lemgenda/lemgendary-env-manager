"""Smart Clean Install Pipeline orchestrator module.

Executes unified environment lifecycle operations across all LemGendary projects
and streams real-time telemetry events to CLI, WebSocket, and GUI clients.
"""

import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

from env_manager.bootstrap import verify_prerequisites
from env_manager.dependency_resolver import find_outdated_packages
from env_manager.health_checker import HealthAuditReport, run_full_health_audit
from env_manager.requirements_manager import sync_all_manifests
from env_manager.system_probe import probe_hardware
from env_manager.validator import validate_project
from env_manager.venv_manager import (
    create_venv,
    discover_projects,
    install_requirements,
    is_venv_valid,
)


@dataclass
class PipelineEvent:
    """Individual telemetry event emitted during pipeline execution."""
    timestamp: str
    step_number: int
    total_steps: int
    step_name: str
    status: str  # "info", "success", "warning", "error"
    message: str
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary."""
        return asdict(self)


class PipelineOrchestrator:
    """Orchestrates end-to-end environment creation, installation, and auditing."""

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            self.base_dir = Path(__file__).resolve().parent.parent.parent
        else:
            self.base_dir = base_dir

        self.current_step = 0
        self.total_steps = 7
        self.is_running = False
        self.last_status = "idle"
        self.last_run_timestamp: Optional[str] = None
        self.last_report: Optional[HealthAuditReport] = None

    def run_clean_install_pipeline(
        self,
        event_callback: Optional[Callable[[PipelineEvent], None]] = None,
        target_project: Optional[str] = None,
    ) -> Generator[PipelineEvent, None, HealthAuditReport]:
        """Execute the 7-step Smart Clean Install Pipeline."""
        self.is_running = True
        self.last_status = "running"
        self.last_run_timestamp = datetime.utcnow().isoformat()

        def emit(
            step_num: int,
            step_name: str,
            status: str,
            msg: str,
            data: Optional[Dict[str, Any]] = None,
        ) -> PipelineEvent:
            ev = PipelineEvent(
                timestamp=datetime.utcnow().isoformat(),
                step_number=step_num,
                total_steps=self.total_steps,
                step_name=step_name,
                status=status,
                message=msg,
                data=data,
            )
            if event_callback:
                event_callback(ev)
            return ev

        try:
            # Step 1: System Probe & Hardware Detection
            yield emit(1, "Hardware Discovery", "info", "Probing system hardware and accelerators...")
            hw = probe_hardware()
            accel_names = [a.name for a in hw.accelerators] or ["None (CPU Fallback)"]
            yield emit(
                1,
                "Hardware Discovery",
                "success",
                f"Detected platform {hw.os_name} ({hw.architecture}) with backend: {hw.primary_backend.upper()} [{', '.join(accel_names)}].",
                hw.to_dict(),
            )

            # Step 2: Global Prerequisites Audit
            yield emit(2, "Toolchain Audit", "info", "Verifying Python, Git, and NPM toolchains...")
            boot = verify_prerequisites()
            if not boot.python_valid or not boot.git_installed:
                yield emit(
                    2,
                    "Toolchain Audit",
                    "error",
                    f"Toolchain prerequisites missing: {', '.join(boot.missing_prerequisites)}",
                    boot.to_dict(),
                )
            else:
                yield emit(
                    2,
                    "Toolchain Audit",
                    "success",
                    f"Python {boot.python_version} and Git verified successfully.",
                    boot.to_dict(),
                )

            # Step 3: Project Discovery & Venv Provisioning
            yield emit(3, "Virtual Environments", "info", "Discovering managed projects and virtual environments...")
            projects = discover_projects(self.base_dir)
            if target_project:
                projects = [p for p in projects if p.name == target_project]

            for p in projects:
                p_dir = Path(p.project_dir)
                if not is_venv_valid(p_dir):
                    yield emit(3, "Virtual Environments", "info", f"Creating virtual environment for {p.name}...")
                    success, msg = create_venv(p_dir)
                    if success:
                        yield emit(3, "Virtual Environments", "success", f"Created .venv for {p.name}.")
                    else:
                        yield emit(3, "Virtual Environments", "error", f"Failed to create .venv for {p.name}: {msg}")
                else:
                    yield emit(3, "Virtual Environments", "info", f"Existing .venv verified for {p.name}.")

            # Step 4: Requirements Manifest Sync & Installation
            yield emit(4, "Requirements Synchronization", "info", "Synchronizing centralized manifests to projects...")
            sync_results = sync_all_manifests(self.base_dir)
            for p_name, (ok, s_msg) in sync_results.items():
                if target_project and p_name != target_project:
                    continue
                if ok:
                    yield emit(4, "Requirements Synchronization", "success", s_msg)
                else:
                    yield emit(4, "Requirements Synchronization", "warning", s_msg)

            yield emit(4, "Package Installation", "info", "Installing dependencies into target environments...")
            for p in projects:
                p_dir = Path(p.project_dir)
                req_file = p_dir / "requirements.txt"
                if req_file.exists():
                    yield emit(4, "Package Installation", "info", f"Installing requirements for {p.name}...")
                    ok, inst_msg = install_requirements(
                        p_dir,
                        req_file,
                        extra_index_url=hw.recommended_torch_index,
                    )
                    if ok:
                        yield emit(4, "Package Installation", "success", f"Dependencies installed successfully for {p.name}.")
                    else:
                        yield emit(4, "Package Installation", "error", f"Failed installing dependencies for {p.name}: {inst_msg[:200]}")

            # Step 5: Safe Dependency Upgrades & Outdated Check
            yield emit(5, "Dependency Audit", "info", "Checking for outdated packages across environments...")
            for p in projects:
                p_dir = Path(p.project_dir)
                outdated = find_outdated_packages(p_dir)
                count = len(outdated)
                if count > 0:
                    yield emit(5, "Dependency Audit", "info", f"{p.name} has {count} outdated packages available for upgrade.")
                else:
                    yield emit(5, "Dependency Audit", "success", f"{p.name} dependencies are up to date.")

            # Step 6: Bytecode Compilation & Zero-Emoji Validation
            yield emit(6, "Codebase Verification", "info", "Running py_compile and zero-emoji verification...")
            for p in projects:
                p_dir = Path(p.project_dir)
                report = validate_project(p_dir)
                if report.passed:
                    yield emit(
                        6,
                        "Codebase Verification",
                        "success",
                        f"{p.name}: Compiled {report.compiled_files_count} files with zero syntax errors and zero emoji violations.",
                    )
                else:
                    err_summary = f"Syntax errors: {len(report.compile_errors)}, Emoji violations: {len(report.emoji_violations)}"
                    yield emit(
                        6,
                        "Codebase Verification",
                        "error",
                        f"{p.name} validation failed: {err_summary}",
                        report.to_dict(),
                    )

            # Step 7: Final Health Report Generation
            yield emit(7, "Health Matrix", "info", "Generating final ecosystem health report...")
            final_report = run_full_health_audit(self.base_dir)
            self.last_report = final_report
            self.last_status = "success" if final_report.overall_healthy else "warning"

            status_type = "success" if final_report.overall_healthy else "warning"
            yield emit(
                7,
                "Health Matrix",
                status_type,
                f"Pipeline execution completed. Ecosystem healthy status: {final_report.overall_healthy}.",
                final_report.to_dict(),
            )

            self.is_running = False
            return final_report

        except Exception as exc:
            self.is_running = False
            self.last_status = "failed"
            yield emit(0, "Pipeline Error", "error", f"Unhandled pipeline exception: {exc}")
            raise
