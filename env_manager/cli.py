"""Typer CLI interface for LemGendary Environment Manager.

Provides commands for system probing, health auditing, dependency synchronization,
validation, and running the background API server.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from env_manager.bootstrap import verify_prerequisites
from env_manager.health_checker import run_full_health_audit
from env_manager.orchestrator import PipelineOrchestrator
from env_manager.requirements_manager import sync_all_manifests
from env_manager.system_probe import probe_hardware
from env_manager.validator import validate_project
from env_manager.venv_manager import discover_projects

app = typer.Typer(
    name="lem-env",
    help="LemGendary Environment Manager - Authoritative Ecosystem CLI",
    add_completion=False,
)
console = Console()


@app.command()
def probe():
    """Probe system hardware, operating system, and accelerator devices."""
    console.print("[bold cyan]Probing system hardware and accelerators...[/bold cyan]")
    hw = probe_hardware()

    table = Table(title="System Hardware & Accelerator Profile", header_style="bold magenta")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Operating System", f"{hw.os_name} ({hw.os_release}, {hw.architecture})")
    table.add_row("Python Version", hw.python_version)
    table.add_row("Python Executable", hw.python_executable)
    table.add_row("Logical / Physical Cores", f"{hw.cpu_count_logical} / {hw.cpu_count_physical}")
    table.add_row("Total RAM (MB)", f"{hw.total_ram_mb:,}")
    table.add_row("Primary Backend", hw.primary_backend.upper())
    table.add_row("Recommended Torch Index", hw.recommended_torch_index)

    if hw.accelerators:
        for acc in hw.accelerators:
            details = f"Index: {acc.index}, VRAM: {acc.total_memory_mb} MB, Backend: {acc.backend.upper()}"
            if acc.driver_version:
                details += f", Driver: {acc.driver_version}"
            table.add_row(f"Accelerator: {acc.name}", details)
    else:
        table.add_row("Accelerators", "None detected (Using CPU backend)")

    console.print(table)


@app.command()
def audit():
    """Audit health, virtual environments, and version drift across all projects."""
    console.print("[bold cyan]Executing ecosystem health audit...[/bold cyan]")
    report = run_full_health_audit()

    boot_table = Table(title="Prerequisites & Toolchain", header_style="bold magenta")
    boot_table.add_column("Tool", style="cyan")
    boot_table.add_column("Status", style="green")
    boot_table.add_column("Details", style="white")

    boot_table.add_row(
        "Python",
        "[OK]" if report.bootstrap.python_valid else "[FAIL]",
        report.bootstrap.python_version,
    )
    boot_table.add_row(
        "Git",
        "[OK]" if report.bootstrap.git_installed else "[FAIL]",
        report.bootstrap.git_version or "Not found",
    )
    boot_table.add_row(
        "NPM",
        "[OK]" if report.bootstrap.npm_installed else "[FAIL]",
        report.bootstrap.npm_version or "Not found",
    )
    console.print(boot_table)

    proj_table = Table(title="Project Environments Status", header_style="bold magenta")
    proj_table.add_column("Project", style="cyan")
    proj_table.add_column("Venv Exists", style="yellow")
    proj_table.add_column("Installed Pkgs", style="green")
    proj_table.add_column("Missing Pkgs", style="red")
    proj_table.add_column("Healthy", style="bold")

    for p in report.projects:
        proj_table.add_row(
            p.name,
            "[YES]" if p.venv_exists else "[NO]",
            str(p.total_installed),
            str(len(p.missing_packages)),
            "[OK]" if p.is_healthy else "[WARN]",
        )
    console.print(proj_table)

    if report.version_drift:
        drift_table = Table(title="Package Version Drift Matrix", header_style="bold magenta")
        drift_table.add_column("Package", style="cyan")
        proj_names = [p.name for p in report.projects]
        for p_name in proj_names:
            drift_table.add_column(p_name, style="white")
        drift_table.add_column("Drift Detected", style="yellow")

        for d in report.version_drift:
            row_items = [d.package_name]
            for p_name in proj_names:
                row_items.append(d.versions.get(p_name) or "-")
            row_items.append("[DRIFT]" if d.has_drift else "[SYNCED]")
            drift_table.add_row(*row_items)
        console.print(drift_table)


@app.command()
def install(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Target specific project"),
):
    """Run the Smart Clean Install Pipeline."""
    console.print("[bold green]Starting Smart Clean Install Pipeline...[/bold green]")
    orchestrator = PipelineOrchestrator()

    for event in orchestrator.run_clean_install_pipeline(target_project=project):
        color = "cyan"
        if event.status == "success":
            color = "green"
        elif event.status == "error":
            color = "red"
        elif event.status == "warning":
            color = "yellow"

        console.print(f"[{color}][Step {event.step_number}/{event.total_steps}] {event.step_name}: {event.message}[/{color}]")


@app.command()
def sync():
    """Synchronize centralized manifests to all sibling projects."""
    console.print("[bold cyan]Synchronizing requirements manifests...[/bold cyan]")
    results = sync_all_manifests()
    for name, (ok, msg) in results.items():
        tag = "[green][OK][/green]" if ok else "[red][FAIL][/red]"
        console.print(f"{tag} {name}: {msg}")


@app.command()
def validate():
    """Run bytecode compilation and zero-emoji compliance checks across projects."""
    console.print("[bold cyan]Validating code compilation and zero-emoji compliance...[/bold cyan]")
    base_dir = Path(__file__).resolve().parent.parent.parent
    projects = discover_projects(base_dir)

    all_passed = True
    for p in projects:
        rep = validate_project(Path(p.project_dir))
        status_tag = "[green][PASS][/green]" if rep.passed else "[red][FAIL][/red]"
        console.print(f"{status_tag} {p.name}: {rep.compiled_files_count} files compiled.")
        if rep.compile_errors:
            all_passed = False
            for err in rep.compile_errors:
                console.print(f"  [red]Syntax Error ({err.file_path}:{err.line_number}): {err.message}[/red]")
        if rep.emoji_violations:
            all_passed = False
            for viol in rep.emoji_violations:
                console.print(f"  [red]Emoji Violation ({viol.file_path}:{viol.line_number}): {viol.message}[/red]")

    if all_passed:
        console.print("[bold green]All projects successfully passed compilation and zero-emoji compliance.[/bold green]")
    else:
        raise typer.Exit(code=1)


@app.command()
def clean(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Target specific project to clean"),
):
    """Reclaim disk space by purging bytecode caches and temporary artifacts."""
    import shutil
    console.print("[bold cyan]Reclaiming cache and temporary build artifacts...[/bold cyan]")
    base_dir = Path(__file__).resolve().parent.parent.parent
    projects = discover_projects(base_dir)

    total_reclaimed = 0
    cleaned_count = 0

    target_projects = [p for p in projects if project is None or p.name == project]
    for p in target_projects:
        p_path = Path(p.project_dir)
        for item in p_path.rglob("__pycache__"):
            if ".venv" in item.parts:
                continue
            if item.is_dir():
                try:
                    size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                    shutil.rmtree(item)
                    total_reclaimed += size
                    cleaned_count += 1
                except Exception:
                    pass

        for pattern in ["*.pyc", "*.pyo", "*.pyd"]:
            for item in p_path.rglob(pattern):
                if ".venv" in item.parts:
                    continue
                if item.is_file():
                    try:
                        size = item.stat().st_size
                        item.unlink()
                        total_reclaimed += size
                        cleaned_count += 1
                    except Exception:
                        pass

    reclaimed_mb = total_reclaimed / (1024 * 1024)
    console.print(f"[bold green]Reclaimed {cleaned_count} cache artifacts ({reclaimed_mb:.2f} MB freed).[/bold green]")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind"),
    port: int = typer.Option(8000, "--port", help="Port to bind"),
):
    """Start the FastAPI sidecar server."""
    import uvicorn
    console.print(f"[bold green]Starting LemGendary Environment Manager server on http://{host}:{port}...[/bold green]")
    uvicorn.run("env_manager.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
