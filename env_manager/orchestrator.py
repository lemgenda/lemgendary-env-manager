"""Smart Clean Install Pipeline orchestrator module.

Executes unified environment lifecycle operations across all LemGendary projects
and streams real-time telemetry events to CLI, WebSocket, and GUI clients.
"""

import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

from env_manager.bootstrap import verify_prerequisites
from env_manager.dependency_resolver import (
    classify_safe_upgrades,
    find_outdated_packages,
    venv_package_count,
)
from env_manager.health_checker import HealthAuditReport, run_full_health_audit
from env_manager.npm_manager import run_npm_install
from env_manager.requirements_manager import sync_all_manifests
from env_manager.system_probe import probe_hardware
from env_manager.validator import validate_project
from env_manager.venv_manager import (
    create_venv,
    discover_projects,
    install_requirements,
    is_venv_valid,
    normalize_opencv_variant,
)


# Which OpenCV provider each project should end up with.
# The datasets project needs mediapipe's contrib modules (SIFT, aruco,
# etc.), and mediapipe ships opencv-contrib-python. Ultralytics also pulls
# opencv-python transitively. Pinning contrib as the definitive provider
# gives us a single, deterministic cv2 with all the modules we need.
#
# Training-suite is intentionally absent: albumentations (headless) and
# ultralytics (full) coexist there for legitimate training-side reasons,
# and training code does not call GUI functions like imshow.
PREFERRED_OPENCV_VARIANT: Dict[str, str] = {
    "lemgendary-datasets": "opencv-contrib-python",
}


def _get_active_venv() -> Optional[Path]:
    """Return the .venv directory that the current process is running from."""
    try:
        exe = Path(sys.executable).resolve()
    except Exception:
        return None
    for parent in exe.parents:
        if parent.name == ".venv":
            return parent
    return None


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
        clean: bool = True,
    ) -> Generator[PipelineEvent, None, HealthAuditReport]:
        """Execute the 7-step Smart Clean Install Pipeline."""
        self.is_running = True
        self.last_status = "running"
        self.last_run_timestamp = datetime.now(timezone.utc).isoformat()

        active_venv = _get_active_venv()
        install_failures: List[str] = []

        def emit(
            step_num: int,
            step_name: str,
            status: str,
            msg: str,
            data: Optional[Dict[str, Any]] = None,
        ) -> PipelineEvent:
            ev = PipelineEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
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
            yield emit(1, "Hardware Discovery", "info",
                       "Probing system hardware and accelerators...")
            hw = probe_hardware()
            accel_names = [a.name for a in hw.accelerators] or ["None (CPU Fallback)"]
            yield emit(
                1,
                "Hardware Discovery",
                "success",
                f"Detected platform {hw.os_name} ({hw.architecture}) with backend: "
                f"{hw.primary_backend.upper()} [{', '.join(accel_names)}].",
                hw.to_dict(),
            )

            # Step 2: Global Prerequisites Audit
            yield emit(2, "Toolchain Audit", "info",
                       "Verifying Python, Git, and NPM toolchains...")
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
            yield emit(3, "Virtual Environments", "info",
                       "Discovering managed projects and virtual environments...")
            projects = discover_projects(self.base_dir)
            if target_project:
                projects = [p for p in projects if p.name == target_project]

            for p in projects:
                p_dir = Path(p.project_dir)

                if p.is_node_project:
                    if clean:
                        nm_dir = p_dir / "node_modules"
                        if nm_dir.exists():
                            yield emit(3, "Virtual Environments", "info",
                                       f"Purging existing node_modules for {p.name} (clean install)...")
                            shutil.rmtree(nm_dir, ignore_errors=True)
                    if p.node_modules_present and not clean:
                        yield emit(3, "Virtual Environments", "info",
                                   f"Existing node_modules verified for {p.name}.")
                    else:
                        yield emit(3, "Virtual Environments", "info",
                                   f"node_modules will be installed freshly for {p.name} in Step 4.")
                    continue

                venv_dir = p_dir / ".venv"

                is_active_venv = False
                if active_venv is not None:
                    try:
                        is_active_venv = venv_dir.resolve() == active_venv
                    except Exception:
                        is_active_venv = False

                if clean and is_active_venv:
                    yield emit(
                        3,
                        "Virtual Environments",
                        "warning",
                        f"Skipping clean purge for active environment {p.name} "
                        f"(cannot delete the venv currently executing this pipeline).",
                    )
                elif clean:
                    if venv_dir.exists():
                        yield emit(3, "Virtual Environments", "info",
                                   f"Purging existing .venv for {p.name} (clean install)...")
                        shutil.rmtree(venv_dir, ignore_errors=True)

                if not is_venv_valid(p_dir):
                    yield emit(3, "Virtual Environments", "info",
                               f"Creating fresh virtual environment for {p.name}...")
                    success, msg = create_venv(p_dir)
                    if success:
                        yield emit(3, "Virtual Environments", "success",
                                   f"Created fresh .venv for {p.name}.")
                    else:
                        yield emit(3, "Virtual Environments", "error",
                                   f"Failed to create .venv for {p.name}: {msg}")
                else:
                    yield emit(3, "Virtual Environments", "info",
                               f"Existing .venv verified for {p.name}.")

            # Step 4: Requirements Manifest Sync & Installation
            yield emit(4, "Requirements Synchronization", "info",
                       "Synchronizing centralized manifests to projects...")
            sync_results = sync_all_manifests(self.base_dir)
            for p_name, (ok, s_msg) in sync_results.items():
                if target_project and p_name != target_project:
                    continue
                if ok:
                    yield emit(4, "Requirements Synchronization", "success", s_msg)
                else:
                    yield emit(4, "Requirements Synchronization", "warning", s_msg)

            yield emit(4, "Package Installation", "info",
                       "Installing dependencies into target environments...")
            for p in projects:
                p_dir = Path(p.project_dir)
                if p.is_node_project:
                    yield emit(4, "Package Installation", "info",
                               f"Running npm install for {p.name}...")
                    ok, inst_msg = run_npm_install(p_dir)
                    if ok:
                        yield emit(4, "Package Installation", "success",
                                   f"npm install completed successfully for {p.name}.")
                    else:
                        install_failures.append(p.name)
                        yield emit(4, "Package Installation", "error",
                                   f"npm install failed for {p.name}: {inst_msg[:300]}")
                else:
                    req_file = p_dir / "requirements.txt"
                    if not req_file.exists():
                        continue

                    yield emit(4, "Package Installation", "info",
                               f"Installing requirements for {p.name}...")
                    ok, inst_msg = install_requirements(
                        p_dir,
                        req_file,
                        extra_index_url=hw.recommended_torch_index,
                    )
                    if ok:
                        yield emit(4, "Package Installation", "success",
                                   f"Dependencies installed successfully for {p.name}.")
                    else:
                        install_failures.append(p.name)
                        yield emit(4, "Package Installation", "error",
                                   f"Failed installing dependencies for {p.name}: "
                                   f"{inst_msg[:300]}")
                        # Skip OpenCV normalization if install failed; there
                        # is nothing sane to normalize against.
                        continue

                    # ── Post-install OpenCV normalization ─────────────
                    desired = PREFERRED_OPENCV_VARIANT.get(p.name)
                    if desired:
                        yield emit(
                            4,
                            "Package Installation",
                            "info",
                            f"Normalizing OpenCV provider for {p.name} to {desired}...",
                        )
                        norm_ok, norm_msg = normalize_opencv_variant(p_dir, desired)
                        if norm_ok:
                            yield emit(4, "Package Installation", "success", norm_msg)
                        else:
                            yield emit(
                                4,
                                "Package Installation",
                                "warning",
                                f"OpenCV normalization failed for {p.name}: {norm_msg}",
                            )

            # Step 5: Safe Dependency Audit
            yield emit(
                5, "Dependency Audit", "info",
                "Auditing outdated packages (safety dry-run per candidate)...",
            )
            for p in projects:
                p_dir = Path(p.project_dir)

                if p.is_node_project:
                    raw_npm = find_outdated_packages(p_dir)
                    if raw_npm:
                        yield emit(
                            5, "Dependency Audit", "info",
                            f"{p.name}: {len(raw_npm)} npm packages can be safely updated.",
                        )
                    else:
                        yield emit(
                            5, "Dependency Audit", "success",
                            f"{p.name} npm dependencies are up to date.",
                        )
                    continue

                pkg_count = venv_package_count(p_dir)
                if pkg_count == 0:
                    yield emit(
                        5,
                        "Dependency Audit",
                        "warning",
                        f"{p.name} environment has no installed packages. "
                        f"Step 4 dependency install likely failed - see errors above.",
                    )
                    continue

                raw = find_outdated_packages(p_dir)
                if not raw:
                    yield emit(
                        5, "Dependency Audit", "success",
                        f"{p.name} dependencies are up to date ({pkg_count} packages).",
                    )
                    continue

                safe, blocked = classify_safe_upgrades(
                    p_dir,
                    raw,
                    cuda_index_url=hw.recommended_torch_index,
                )

                yield emit(
                    5, "Dependency Audit", "info",
                    f"{p.name}: {len(safe)} safe update(s), "
                    f"{len(blocked)} blocked by dependency constraints.",
                )
                for pkg, reason in blocked:
                    yield emit(
                        5, "Dependency Audit", "warning",
                        f"  Blocked: {pkg.name} "
                        f"{pkg.current_version} -> {pkg.latest_version} ({reason})",
                    )

            # Step 6: Bytecode Compilation, Zero-Emoji & Full Linting Validation
            yield emit(6, "Codebase Verification", "info",
                       "Running full validation suite (py_compile, emoji, lint, PS1)...")
            all_validation_passed = True
            for p in projects:
                p_dir = Path(p.project_dir)
                report = validate_project(
                    p_dir,
                    is_node_project=p.is_node_project if hasattr(p, "is_node_project") else False,
                )
                if report.passed:
                    yield emit(
                        6,
                        "Codebase Verification",
                        "success",
                        f"{p.name}: Compiled {report.compiled_files_count} files, "
                        f"0 syntax errors, 0 emoji violations.",
                    )
                else:
                    all_validation_passed = False
                    err_summary = (
                        f"Syntax: {len(report.compile_errors)}, "
                        f"Emoji: {len(report.emoji_violations)}, "
                        f"Lint: {len(report.lint_errors)}, "
                        f"YAML: {len(report.yaml_errors)}, "
                        f"HTML: {len(report.html_errors)}, "
                        f"WCAG: {len(report.wcag_violations)}"
                    )
                    yield emit(
                        6,
                        "Codebase Verification",
                        "error",
                        f"{p.name} validation failed: {err_summary}",
                        report.to_dict(),
                    )

            if all_validation_passed:
                yield emit(6, "Codebase Verification", "info",
                           "All projects passed validation. Syncing manifests...")
                sync_result = sync_all_manifests(self.base_dir)
                sync_ok = all(ok for ok, _ in sync_result.values()) if sync_result else False
                sync_msg = "; ".join(
                    f"{name}: {msg}" for name, (_, msg) in sync_result.items()
                ) if sync_result else "no manifests processed"
                yield emit(
                    6,
                    "Codebase Verification",
                    "success" if sync_ok else "warning",
                    f"Manifest sync completed: {sync_msg}",
                )
            else:
                yield emit(
                    6,
                    "Codebase Verification",
                    "warning",
                    "Manifest sync skipped due to validation failures. Fix errors and re-run.",
                )

            # Step 7: Final Health Report Generation
            yield emit(7, "Health Matrix", "info",
                       "Generating final ecosystem health report...")

            if install_failures:
                yield emit(
                    7,
                    "Health Matrix",
                    "warning",
                    f"Dependency installation failed for: {', '.join(install_failures)}. "
                    f"Fix pip resolver conflicts and re-run.",
                )

            final_report = run_full_health_audit(self.base_dir)
            self.last_report = final_report
            self.last_status = "success" if final_report.overall_healthy else "warning"

            status_type = "success" if final_report.overall_healthy else "warning"
            yield emit(
                7,
                "Health Matrix",
                status_type,
                f"Pipeline execution completed. "
                f"Ecosystem healthy status: {final_report.overall_healthy}.",
                final_report.to_dict(),
            )

            self.is_running = False
            return final_report

        except Exception as exc:
            self.is_running = False
            self.last_status = "failed"
            yield emit(0, "Pipeline Error", "error",
                       f"Unhandled pipeline exception: {exc}")
            raise

    def get_status(self) -> Dict[str, Any]:
        """Return current status and telemetry of the pipeline."""
        return {
            "is_running": self.is_running,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "last_status": self.last_status,
            "last_run_timestamp": self.last_run_timestamp,
            "has_report": self.last_report is not None,
        }

    def reset(self) -> None:
        """Reset internal execution state."""
        self.current_step = 0
        self.is_running = False
        self.last_status = "idle"