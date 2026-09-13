"""Typer CLI interface for LemGendary Environment Manager.

Provides commands for system probing, health auditing, dependency synchronization,
safe package updates, validation, and running the background API server.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from env_manager.bootstrap import verify_prerequisites
from env_manager.health_checker import run_full_health_audit
from env_manager.hooks import install_git_hooks
from env_manager.orchestrator import PipelineOrchestrator
from env_manager.requirements_manager import sync_all_manifests
from env_manager.system_probe import probe_hardware
from env_manager.updater import build_upgrade_plan, apply_upgrade_plan
from env_manager.utils import purge_project_cache
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
    """Probe system hardware, OS, accelerators, MetaTrader 5, and check for software updates."""
    console.print("[bold cyan]Probing system hardware and accelerators...[/bold cyan]")
    hw = probe_hardware()
    boot = verify_prerequisites()

    # ── Hardware Table ────────────────────────────────────────────────────────
    hw_table = Table(title="System Hardware & Accelerator Profile", header_style="bold magenta")
    hw_table.add_column("Property", style="cyan")
    hw_table.add_column("Value", style="green")

    hw_table.add_row("Operating System", f"{hw.os_name} ({hw.os_release}, {hw.architecture})")
    hw_table.add_row("Python Version", hw.python_version)
    hw_table.add_row("Python Executable", hw.python_executable)
    hw_table.add_row("Logical / Physical Cores", f"{hw.cpu_count_logical} / {hw.cpu_count_physical}")
    hw_table.add_row("Total RAM (MB)", f"{hw.total_ram_mb:,}")
    hw_table.add_row("Primary Backend", hw.primary_backend.upper())
    hw_table.add_row("Recommended Torch Index", hw.recommended_torch_index)

    if hw.accelerators:
        for acc in hw.accelerators:
            details = f"Index: {acc.index}, VRAM: {acc.total_memory_mb} MB, Backend: {acc.backend.upper()}"
            if acc.driver_version:
                details += f", Driver: {acc.driver_version}"
            hw_table.add_row(f"Accelerator: {acc.name}", details)
    else:
        hw_table.add_row("Accelerators", "None detected (Using CPU backend)")

    # ── MetaTrader 5 status ───────────────────────────────────────────────────
    if hw.metatrader5 is not None:
        mt5 = hw.metatrader5
        if mt5.installed:
            mt5_str = f"[green]INSTALLED[/green]"
            if mt5.version:
                mt5_str += f" (v{mt5.version})"
            if mt5.install_path:
                mt5_str += f" — {mt5.install_path}"
        else:
            mt5_str = "[red]NOT FOUND[/red]"
        hw_table.add_row("MetaTrader 5", mt5_str)

    console.print(hw_table)

    # ── Software Update Availability ─────────────────────────────────────────
    if boot.software_updates:
        update_table = Table(title="Software Update Availability", header_style="bold magenta")
        update_table.add_column("Software", style="cyan")
        update_table.add_column("Current", style="white")
        update_table.add_column("Latest", style="white")
        update_table.add_column("Status", style="bold")

        for upd in boot.software_updates:
            status_str = "[yellow]UPDATE AVAILABLE[/yellow]" if upd.update_available else "[green]UP TO DATE[/green]"
            update_table.add_row(
                upd.name,
                upd.current_version or "?",
                upd.latest_version or "?",
                status_str,
            )
            if upd.update_available and upd.install_command:
                console.print(f"  [yellow]Run to update:[/yellow] {upd.install_command}")

        console.print(update_table)

    # ── Missing prerequisites + remediation ──────────────────────────────────
    if boot.missing_prerequisites:
        console.print("\n[bold red]Missing Prerequisites:[/bold red]")
        for prereq in boot.missing_prerequisites:
            console.print(f"  [red]• {prereq}[/red]")
        console.print("\n[bold yellow]Remediation:[/bold yellow]")
        for instruction in boot.remediation_instructions:
            console.print(f"  [yellow]→ {instruction}[/yellow]")
    else:
        console.print("[bold green]All software prerequisites satisfied.[/bold green]")


@app.command()
def audit():
    """Audit health, virtual environments, npm packages, and version drift across all projects."""
    console.print("[bold cyan]Executing ecosystem health audit...[/bold cyan]")
    report = run_full_health_audit()

    # ── Bootstrap / Toolchain Table ───────────────────────────────────────────
    boot_table = Table(title="Prerequisites & Toolchain", header_style="bold magenta")
    boot_table.add_column("Tool", style="cyan")
    boot_table.add_column("Status", style="green")
    boot_table.add_column("Details", style="white")

    boot_table.add_row(
        "Python",
        "[green][OK][/green]" if report.bootstrap.python_valid else "[red][FAIL][/red]",
        report.bootstrap.python_version,
    )
    boot_table.add_row(
        "Git",
        "[green][OK][/green]" if report.bootstrap.git_installed else "[red][FAIL][/red]",
        report.bootstrap.git_version or "Not found",
    )
    boot_table.add_row(
        "NPM",
        "[green][OK][/green]" if report.bootstrap.npm_installed else "[red][FAIL][/red]",
        report.bootstrap.npm_version or "Not found",
    )
    boot_table.add_row(
        "MetaTrader 5",
        "[green][OK][/green]" if report.bootstrap.mt5_installed else "[yellow][MISSING][/yellow]",
        (report.bootstrap.mt5_version or report.bootstrap.mt5_path or "Not found"),
    )
    console.print(boot_table)

    # ── Python Project Environments ───────────────────────────────────────────
    proj_table = Table(title="Python Project Environments Status", header_style="bold magenta")
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
            "[green][OK][/green]" if p.is_healthy else "[red][WARN][/red]",
        )
    console.print(proj_table)

    # ── NPM / Node.js Workspace Status ───────────────────────────────────────
    if report.npm_packages:
        npm_table = Table(title="Node.js & NPM Workspace Status", header_style="bold magenta")
        npm_table.add_column("Project", style="cyan")
        npm_table.add_column("package.json", style="yellow")
        npm_table.add_column("node_modules", style="green")
        npm_table.add_column("Dependencies", style="white")

        for npm in report.npm_packages:
            dep_count = len(npm.dependencies) + len(npm.dev_dependencies)
            npm_table.add_row(
                npm.location,
                "[YES]",
                "[green][OK][/green]" if npm.node_modules_present else "[red][MISSING][/red]",
                str(dep_count),
            )
        console.print(npm_table)

        # Detailed NPM dependencies matrix
        npm_detail_table = Table(title="NPM Package Dependency Matrix", header_style="bold magenta")
        npm_detail_table.add_column("Project", style="cyan")
        npm_detail_table.add_column("Package", style="white")
        npm_detail_table.add_column("Type", style="yellow")
        npm_detail_table.add_column("Declared Version", style="white")
        npm_detail_table.add_column("Installed Version", style="white")
        npm_detail_table.add_column("Status", style="bold")

        for npm in report.npm_packages:
            for pkg in getattr(npm, "detailed_packages", []):
                status_str = "[green][OK][/green]" if pkg.is_installed else "[red][MISSING][/red]"
                type_str = "dev" if pkg.is_dev else "prod"
                npm_detail_table.add_row(
                    npm.location,
                    pkg.name,
                    type_str,
                    pkg.declared_version,
                    pkg.installed_version or "[dim]None[/dim]",
                    status_str,
                )
        console.print(npm_detail_table)


    # ── Version Drift Matrix ──────────────────────────────────────────────────
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
            row_items.append("[red][DRIFT][/red]" if d.has_drift else "[green][SYNCED][/green]")
            drift_table.add_row(*row_items)
        console.print(drift_table)


@app.command()
def install(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Target specific project"),
    clean: bool = typer.Option(True, "--clean/--no-clean", help="Purge existing environment and recreate cleanly"),
):
    """Run the Smart Clean Install Pipeline (creates venvs, npm install, pip install)."""
    console.print("[bold green]Starting Smart Clean Install Pipeline...[/bold green]")
    orchestrator = PipelineOrchestrator()

    for event in orchestrator.run_clean_install_pipeline(target_project=project, clean=clean):
        color = "cyan"
        if event.status == "success":
            color = "green"
        elif event.status == "error":
            color = "red"
        elif event.status == "warning":
            color = "yellow"

        console.print(f"[{color}][Step {event.step_number}/{event.total_steps}] {event.step_name}: {event.message}[/{color}]")


@app.command()
def update(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Target specific project"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be upgraded without applying"),
):
    """Safely upgrade all outdated packages (bottom-up) and auto-sync manifests.

    Upgrade order: lemgendary-env-manager -> lemgendary-datasets ->
    lemgendary-training-suite -> lemgendary-ai-studio-gui (npm).
    Manifests in lemgendary-env-manager/requirements/ are re-written after upgrade.
    """
    console.print("[bold cyan]Building ecosystem upgrade plan...[/bold cyan]")
    base_dir = Path(__file__).resolve().parent.parent.parent
    plan = build_upgrade_plan(base_dir)

    if not plan.has_any_updates:
        console.print("[bold green]All packages are up to date across all projects.[/bold green]")
        return

    # Show plan table
    plan_table = Table(title=f"Upgrade Plan ({plan.total_outdated} packages outdated)", header_style="bold magenta")
    plan_table.add_column("Project", style="cyan")
    plan_table.add_column("Type", style="white")
    plan_table.add_column("Outdated", style="yellow")
    plan_table.add_column("Packages", style="white")

    for proj_plan in plan.projects:
        if project and proj_plan.name != project:
            continue
        pkg_summary = ", ".join(
            f"{p.name} ({p.current_version} -> {p.latest_version})"
            for p in proj_plan.outdated_packages[:5]
        )
        if len(proj_plan.outdated_packages) > 5:
            pkg_summary += f" ... (+{len(proj_plan.outdated_packages) - 5} more)"
        plan_table.add_row(
            proj_plan.name,
            "npm" if proj_plan.is_node_project else "pip",
            str(len(proj_plan.outdated_packages)),
            pkg_summary or "(up to date)",
        )
    console.print(plan_table)

    if dry_run:
        console.print("[bold yellow]Dry run — no packages were upgraded.[/bold yellow]")
        return

    # Apply upgrades
    console.print("[bold green]Applying upgrades bottom-up...[/bold green]")
    from env_manager.system_probe import probe_hardware
    hw = probe_hardware()

    success_count = 0
    fail_count = 0

    for event in apply_upgrade_plan(plan, base_dir, extra_index_url=hw.recommended_torch_index):
        if project and event.project != project and "(manifest sync)" not in event.package:
            continue
        color = "green" if event.status == "upgraded" else ("red" if event.status == "failed" else "yellow")
        pkg_info = f"{event.package}"
        if event.old_version and event.new_version:
            pkg_info += f" ({event.old_version} -> {event.new_version})"
        console.print(f"[{color}][{event.status.upper()}] {event.project}: {pkg_info}[/{color}]")
        if event.status == "upgraded":
            success_count += 1
        elif event.status == "failed":
            fail_count += 1

    if fail_count == 0:
        console.print(f"[bold green]Upgrade complete. {success_count} packages upgraded.[/bold green]")
    else:
        console.print(f"[bold yellow]{success_count} upgraded, {fail_count} failed. Check output above.[/bold yellow]")
        raise typer.Exit(code=1)

    # Post-upgrade: validate all modified projects before syncing manifests
    console.print("[bold cyan]Running post-upgrade validation on modified projects...[/bold cyan]")
    all_valid = True
    from env_manager.validator import validate_project as _validate
    from env_manager.venv_manager import discover_projects
    projects = discover_projects(base_dir)
    for p in projects:
        if project and p.name != project:
            continue
        p_dir = Path(p.project_dir)
        report = _validate(p_dir, is_node_project=getattr(p, "is_node_project", False))
        tag = "[bold green][PASS][/bold green]" if report.passed else "[bold red][FAIL][/bold red]"
        console.print(f"{tag} {p.name}: compiled={report.compiled_files_count}, "
                      f"lint={len(report.lint_errors)}, yaml={len(report.yaml_errors)}")
        if not report.passed:
            all_valid = False
            for err in report.compile_errors[:3]:
                console.print(f"  [red]{err.file_path}:{err.line_number}: {err.message}[/red]")

    if all_valid:
        console.print("[bold green]All projects passed validation. Syncing manifests...[/bold green]")
        sync_results = sync_all_manifests()
        for name, (ok, msg) in sync_results.items():
            tag = "[green][OK][/green]" if ok else "[red][FAIL][/red]"
            console.print(f"{tag} {name}: {msg}")
        console.print("[bold green]Manifests synced successfully.[/bold green]")
    else:
        console.print("[bold red]Manifest sync SKIPPED — validation failures detected above. Fix errors and re-run.[/bold red]")
        raise typer.Exit(code=1)



@app.command()
def sync():
    """Synchronize centralized manifests to all sibling projects (one-way copy)."""
    console.print("[bold cyan]Synchronizing requirements manifests...[/bold cyan]")
    results = sync_all_manifests()
    for name, (ok, msg) in results.items():
        tag = "[green][OK][/green]" if ok else "[red][FAIL][/red]"
        console.print(f"{tag} {name}: {msg}")


@app.command()
def validate(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Target specific project"),
):
    """Run full validation: py_compile, zero-emoji, ESLint, TS, markdownlint, yamllint, W3C, WCAG 2.2 AA."""
    if project:
        console.print(f"[bold cyan]Validating project '{project}' (full compliance suite)...[/bold cyan]")
    else:
        console.print("[bold cyan]Validating all projects (full compliance suite)...[/bold cyan]")
    base_dir = Path(__file__).resolve().parent.parent.parent

    # All LemGendary projects to validate
    all_project_dirs = [
        ("lemgendary-training-suite", False),
        ("lemgendary-datasets", False),
        ("lemgendary-env-manager", False),
        ("lemgendary-ai-studio-gui", True),   # Node.js project
        ("lemgendary-docs", False),            # Static HTML/CSS — gets W3C + WCAG
        ("LemGendaryDatasets", False),
        ("LemGendaryModels", False),
    ]

    all_passed = True
    summary_table = Table(title="Validation Summary", header_style="bold magenta")
    summary_table.add_column("Project", style="cyan")
    summary_table.add_column("Python", style="white")
    summary_table.add_column("Lint", style="white")
    summary_table.add_column("YAML", style="white")
    summary_table.add_column("JSON", style="white")
    summary_table.add_column("HTML/WCAG", style="white")
    summary_table.add_column("Domain", style="white")
    summary_table.add_column("Status", style="bold")

    for proj_name, is_node in all_project_dirs:
        if project and proj_name != project:
            continue
        proj_dir = base_dir / proj_name
        if not proj_dir.exists():
            continue

        rep = validate_project(proj_dir, is_node_project=is_node)

        py_status = "[green]OK[/green]" if not rep.compile_errors else f"[red]{len(rep.compile_errors)} errors[/red]"
        emoji_issue = f" +{len(rep.emoji_violations)} emoji" if rep.emoji_violations else ""
        lint_status = "[green]OK[/green]" if not rep.lint_errors else f"[red]{len(rep.lint_errors)} issues[/red]"
        yaml_status = "[green]OK[/green]" if not rep.yaml_errors else f"[red]{len(rep.yaml_errors)} issues[/red]"
        json_status = "[green]OK[/green]" if not rep.json_errors else f"[red]{len(rep.json_errors)} issues[/red]"
        html_status = (
            "[green]OK[/green]"
            if not rep.html_errors and not rep.wcag_violations
            else f"[red]{len(rep.html_errors)}W3C {len(rep.wcag_violations)}WCAG[/red]"
        )
        domain_status = "[green]OK[/green]" if not rep.domain_errors else f"[red]{len(rep.domain_errors)} issues[/red]"
        if is_node:
            py_status = "[dim]N/A[/dim]"
        overall = "[green][PASS][/green]" if rep.passed else "[red][FAIL][/red]"

        summary_table.add_row(
            proj_name,
            py_status + emoji_issue,
            lint_status,
            yaml_status,
            json_status,
            html_status,
            domain_status,
            overall,
        )

        if not rep.passed:
            all_passed = False
            for err in rep.compile_errors[:5]:
                console.print(f"  [red]SyntaxError ({err.file_path}:{err.line_number}): {err.message}[/red]")
            for err in rep.emoji_violations[:5]:
                console.print(f"  [red]Emoji ({err.file_path}:{err.line_number}): {err.message}[/red]")
            for err in rep.lint_errors[:5]:
                console.print(f"  [red]Lint ({err.file_path}:{err.line_number}): {err.message}[/red]")
            for err in rep.yaml_errors[:5]:
                console.print(f"  [red]YAML ({err.file_path}:{err.line_number}): {err.message}[/red]")
            for err in rep.json_errors[:5]:
                console.print(f"  [red]JSON ({err.file_path}:{err.line_number}): {err.message}[/red]")
            for err in rep.html_errors[:5]:
                console.print(f"  [red]HTML ({err.file_path}:{err.line_number}): {err.message}[/red]")
            for err in rep.wcag_violations[:5]:
                console.print(f"  [red]WCAG ({err.file_path}:{err.line_number}): {err.message}[/red]")
            for err in rep.domain_errors[:5]:
                console.print(f"  [red]Domain ({err.file_path}:{err.line_number}): {err.message}[/red]")

    console.print(summary_table)

    if all_passed:
        console.print("[bold green]All projects passed full compliance validation.[/bold green]")
    else:
        raise typer.Exit(code=1)


@app.command()
def clean(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Target specific project to clean"),
):
    """Reclaim disk space by purging bytecode caches and temporary artifacts."""
    console.print("[bold cyan]Reclaiming cache and temporary build artifacts...[/bold cyan]")
    base_dir = Path(__file__).resolve().parent.parent.parent
    projects = discover_projects(base_dir)

    total_reclaimed = 0
    cleaned_count = 0

    target_projects = [p for p in projects if project is None or p.name == project]
    for p in target_projects:
        if p.is_node_project:
            continue  # Don't purge node_modules
        cnt, reclaimed = purge_project_cache(Path(p.project_dir))
        cleaned_count += cnt
        total_reclaimed += reclaimed

    reclaimed_mb = total_reclaimed / (1024 * 1024)
    console.print(f"[bold green]Reclaimed {cleaned_count} cache artifacts ({reclaimed_mb:.2f} MB freed).[/bold green]")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind"),
    port: int = typer.Option(8000, "--port", help="Port to bind"),
):
    """Start the FastAPI sidecar server (REST + WebSocket telemetry for AI Studio GUI)."""
    import uvicorn
    console.print(f"[bold green]Starting LemGendary Environment Manager server on http://{host}:{port}...[/bold green]")
    console.print(f"  REST API:  http://{host}:{port}/docs")
    console.print(f"  WebSocket: ws://{host}:{port}/ws/log")
    uvicorn.run("env_manager.server:app", host=host, port=port, reload=False)


@app.command(name="setup-hooks")
def setup_hooks(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Target specific project"),
):
    """Install or repair standardized pre-commit hooks across all ecosystem projects."""
    console.print("[bold cyan]Configuring standardized pre-commit hooks across projects...[/bold cyan]")
    base_dir = Path(__file__).resolve().parent.parent.parent
    results = install_git_hooks(base_dir=base_dir, target_project=project)
    all_ok = True
    for proj_name, ok, msg in results:
        tag = "[green][OK][/green]" if ok else "[red][FAIL][/red]"
        console.print(f"  {tag} {proj_name}: {msg}")
        if not ok:
            all_ok = False

    if all_ok:
        console.print("[bold green]All pre-commit hooks installed and verified successfully.[/bold green]")
    else:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
