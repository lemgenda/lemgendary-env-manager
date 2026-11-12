"""Typer command-line interface for the LemGendary Environment Manager (``lem-env``)."""

import sys

import typer
import uvicorn
from rich.console import Console

from env_manager._logging import get_logger
from env_manager.bootstrap import verify_prerequisites
from env_manager.cli_views.audit import print_audit_report
from env_manager.cli_views.probe import (
    hardware_table,
    print_prerequisites,
    print_software_updates,
)
from env_manager.cli_views.update import event_line, plan_table, validation_lines
from env_manager.cli_views.validate import (
    print_violations,
    print_warnings,
    summary_row,
    summary_table,
)
from env_manager.ecosystem import SELF_SERVICE_ID, ecosystem
from env_manager.health_checker import run_full_health_audit
from env_manager.hooks import install_git_hooks
from env_manager.orchestrator import PipelineOrchestrator
from env_manager.requirements_manager import sync_all_manifests
from env_manager.system_probe import probe_hardware
from env_manager.updater import apply_upgrade_plan, build_upgrade_plan
from env_manager.upgrade.finalize import finalize_upgrade
from env_manager.upgrade.verify import UpgradeTally
from env_manager.utils import purge_workspace_caches
from env_manager.validator import validate_workspace
from env_manager.venv_manager import discover_projects
from env_manager.workspace import workspace_root

_log = get_logger(__name__)

INSTALL_STATUS_COLORS = {"success": "green", "error": "red", "warning": "yellow"}
"""Console colour per install pipeline event status; any other status is cyan."""


def _configure_windows_console() -> None:
    """Switch the Windows console streams to UTF-8 so rich output never crashes.

    A stream that cannot be reconfigured (for example a detached or
    non-text stream) keeps its current encoding; the reason is logged.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            _log.warning("Could not switch console stream to UTF-8: %s", exc)


_configure_windows_console()

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
    console.print(hardware_table(hw))
    if boot.software_updates:
        print_software_updates(console, boot.software_updates)
    print_prerequisites(console, boot)


@app.command()
def audit(
    fast: bool = typer.Option(
        False,
        "--fast",
        help="Skip pip safety dry-runs. Faster, but the Safe-↑ column will be empty.",
    ),
):
    """Audit health, virtual environments, npm packages, and version drift across all projects."""
    console.print("[bold cyan]Executing ecosystem health audit...[/bold cyan]")
    if fast:
        console.print("[dim]Fast mode: skipping safety classification dry-runs.[/dim]")

    report = run_full_health_audit(include_safety=not fast)
    print_audit_report(console, report)


@app.command()
def install(
    project: str | None = typer.Option(None, "--project", "-p", help="Target specific project"),
    clean: bool = typer.Option(
        False,
        "--clean",
        help=(
            "Destructive: delete every project .venv and node_modules, stop "
            "processes running from those venvs, then reinstall from scratch. "
            "Without this flag valid environments are kept."
        ),
    ),
):
    """Run the Smart Clean Install Pipeline (creates venvs, npm install, pip install).

    Non-destructive by default; pass --clean for a full wipe and reinstall.
    """
    console.print("[bold green]Starting Smart Clean Install Pipeline...[/bold green]")
    orchestrator = PipelineOrchestrator()

    for event in orchestrator.run_clean_install_pipeline(target_project=project, clean=clean):
        color = INSTALL_STATUS_COLORS.get(event.status, "cyan")
        console.print(
            f"[{color}][Step {event.step_number}/{event.total_steps}] "
            f"{event.step_name}: {event.message}[/{color}]"
        )


@app.command()
def update(
    project: str | None = typer.Option(None, "--project", "-p", help="Target specific project"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be upgraded without applying"
    ),
):
    """Safely upgrade outdated packages (bottom-up) and auto-sync manifests.

    With ``--project`` only that project is planned and upgraded.
    """
    console.print("[bold cyan]Building ecosystem upgrade plan...[/bold cyan]")
    base_dir = workspace_root()
    try:
        plan = build_upgrade_plan(base_dir, project=project)
    except ValueError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(code=1) from exc


    if not plan.has_any_updates:
        console.print("[bold green]All packages are up to date across all projects.[/bold green]")
        return

    console.print(plan_table(plan))
    if dry_run:
        console.print("[bold yellow]Dry run — no packages were upgraded.[/bold yellow]")
        return

    console.print("[bold green]Applying upgrades bottom-up...[/bold green]")
    tally = UpgradeTally()
    for event in apply_upgrade_plan(plan):
        tally.record(event)
        console.print(event_line(event))

    if tally.failed:
        console.print(f"[bold yellow]{tally.summary()}. Check output above.[/bold yellow]")
        raise typer.Exit(code=1)
    console.print(f"[bold green]Upgrade complete. {tally.summary()}.[/bold green]")

    console.print("[bold cyan]Running post-upgrade validation on modified projects...[/bold cyan]")
    finalization = finalize_upgrade(base_dir, project)
    for name, report in finalization.reports:
        for line in validation_lines(name, report):
            console.print(line)

    if not finalization.passed:
        console.print(
            "[bold red]Manifest sync SKIPPED - validation failures detected above. "
            "Fix errors and re-run.[/bold red]"
        )
        raise typer.Exit(code=1)
    console.print("[bold green]All projects passed validation. Syncing manifests...[/bold green]")
    _print_sync_results(finalization.manifest_sync)
    _print_sync_results(sync_all_manifests(base_dir))
    console.print("[bold green]Manifests synced successfully.[/bold green]")


def _print_sync_results(results: dict[str, tuple[bool, str]]) -> None:
    """Print one OK/FAIL line per synchronized manifest."""
    for name, (ok, msg) in results.items():
        tag = "[green][OK][/green]" if ok else "[red][FAIL][/red]"
        console.print(f"{tag} {name}: {msg}")


@app.command()
def sync():
    """Synchronize centralized manifests to all sibling projects (one-way copy)."""
    console.print("[bold cyan]Synchronizing requirements manifests...[/bold cyan]")
    _print_sync_results(sync_all_manifests())


@app.command()
def validate(
    project: str | None = typer.Option(None, "--project", "-p", help="Target specific project"),
):
    """Run full validation.

    Checks: py_compile, zero-emoji, ESLint, TS, markdownlint, yamllint, W3C, WCAG 2.2 AA.
    """
    if project:
        console.print(
            f"[bold cyan]Validating project '{project}' (full compliance suite)...[/bold cyan]"
        )
    else:
        console.print("[bold cyan]Validating all projects (full compliance suite)...[/bold cyan]")
    base_dir = workspace_root()
    node_projects = {p.folder for p in ecosystem().validated_projects() if p.is_node}

    table = summary_table()
    reports = validate_workspace(base_dir, project)
    for proj_name, rep in reports.items():
        table.add_row(*summary_row(proj_name, rep, proj_name in node_projects))
        if not rep.passed:
            print_violations(console, rep)
        print_warnings(console, rep)
    console.print(table)

    if not all(rep.passed for rep in reports.values()):
        raise typer.Exit(code=1)
    console.print("[bold green]All projects passed full compliance validation.[/bold green]")


@app.command("clean")
def clean_command(
    project: str | None = typer.Option(
        None, "--project", "-p", help="Target specific project to clean"
    ),
):
    """Reclaim disk space by purging bytecode caches and temporary artifacts."""
    console.print("[bold cyan]Reclaiming cache and temporary build artifacts...[/bold cyan]")
    base_dir = workspace_root()
    projects = discover_projects(base_dir)

    cleaned_count, total_reclaimed = purge_workspace_caches(projects, project)

    reclaimed_mb = total_reclaimed / (1024 * 1024)
    console.print(
        f"[bold green]Reclaimed {cleaned_count} cache artifacts "
        f"({reclaimed_mb:.2f} MB freed).[/bold green]"
    )


@app.command()
def serve(
    host: str | None = typer.Option(
        None, "--host", help="Host interface to bind (default: identity.bind_host)",
    ),
    port: int | None = typer.Option(
        None, "--port", help="Port to bind (default: the env-manager service port)",
    ),
):
    """Start the FastAPI sidecar server (REST + WebSocket telemetry for AI Studio GUI)."""
    eco = ecosystem()
    service = eco.require_service(SELF_SERVICE_ID)
    host = host or eco.identity.bind_host
    port = port or service.port
    console.print(
        f"[bold green]Starting {eco.display_name(service.project)} server on "
        f"http://{host}:{port}...[/bold green]"
    )
    console.print(f"  REST API:  http://{host}:{port}/docs")
    console.print(f"  WebSocket: ws://{host}:{port}{service.websocket_path}")
    uvicorn.run(service.app_import, host=host, port=port, reload=False)


@app.command(name="setup-hooks")
def setup_hooks(
    project: str | None = typer.Option(None, "--project", "-p", help="Target specific project"),
):
    """Install or repair standardized pre-commit hooks across all ecosystem projects."""
    console.print(
        "[bold cyan]Configuring standardized pre-commit hooks across projects...[/bold cyan]"
    )
    base_dir = workspace_root()
    results = install_git_hooks(base_dir=base_dir, target_project=project)
    all_ok = True
    for proj_name, ok, msg in results:
        tag = "[green][OK][/green]" if ok else "[red][FAIL][/red]"
        console.print(f"  {tag} {proj_name}: {msg}")
        if not ok:
            all_ok = False

    if all_ok:
        console.print(
            "[bold green]All pre-commit hooks installed and verified successfully.[/bold green]"
        )
    else:
        raise typer.Exit(code=1)


@app.command(name="hooks")
def hooks_command(
    project: str | None = typer.Option(None, "--project", "-p", help="Target specific project"),
):
    """Install or repair pre-commit hooks in every ecosystem project (alias of ``setup-hooks``)."""
    setup_hooks(project=project)


if __name__ == "__main__":
    app()
