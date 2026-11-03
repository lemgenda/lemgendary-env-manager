"""Tables and lines printed by ``lem-env audit``."""

from rich.console import Console
from rich.table import Table

from env_manager.bootstrap import BootstrapStatus
from env_manager.health_checker import (
    HealthAuditReport,
    ManifestCoverageEntry,
    ProjectHealth,
    render_drift_matrix,
    render_manifest_coverage_table,
    render_npm_drift_matrix,
    render_single_manifest_table,
)
from env_manager.npm_manager import NpmPackageStatus

OK = "[green][OK][/green]"
FAIL = "[red][FAIL][/red]"
MISSING = "[red][MISSING][/red]"


def bootstrap_table(boot: BootstrapStatus) -> Table:
    """Return the prerequisites and toolchain table."""
    table = Table(title="Prerequisites & Toolchain", header_style="bold magenta")
    table.add_column("Tool", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Details", style="white")
    table.add_row("Python", OK if boot.python_valid else FAIL, boot.python_version)
    table.add_row("Git", OK if boot.git_installed else FAIL, boot.git_version or "Not found")
    table.add_row("NPM", OK if boot.npm_installed else FAIL, boot.npm_version or "Not found")
    table.add_row(
        "MetaTrader 5",
        OK if boot.mt5_installed else "[yellow][MISSING][/yellow]",
        boot.mt5_version or boot.mt5_path or "Not found",
    )
    return table


def projects_table(projects: list[ProjectHealth]) -> Table:
    """Return the per-project virtual environment status table."""
    table = Table(title="Python Project Environments Status", header_style="bold magenta")
    table.add_column("Project", style="cyan")
    table.add_column("Venv Exists", style="yellow")
    table.add_column("Installed Pkgs", style="green")
    table.add_column("Missing Pkgs", style="red")
    table.add_column("Healthy", style="bold")
    for p in projects:
        table.add_row(
            p.name,
            "[YES]" if p.venv_exists else "[NO]",
            str(p.total_installed),
            str(len(p.missing_packages)),
            OK if p.is_healthy else "[red][WARN][/red]",
        )
    return table


def print_manifest_coverage(console: Console, coverage: list[ManifestCoverageEntry]) -> None:
    """Print the coverage table and each project's missing and platform-skipped pins."""
    table = render_manifest_coverage_table(coverage)
    if table is None:
        return
    console.print(table)
    for entry in coverage:
        if entry.missing:
            console.print(f"[red]{entry.project_name} missing:[/red] {', '.join(entry.missing)}")
        if entry.platform_skipped:
            console.print(
                f"[dim]{entry.project_name} platform-skipped:[/dim] "
                f"{', '.join(entry.platform_skipped)}"
            )


def npm_status_table(npm_packages: list[NpmPackageStatus]) -> Table:
    """Return the Node workspace table: package.json, node_modules, dependency count."""
    table = Table(title="Node.js & NPM Workspace Status", header_style="bold magenta")
    table.add_column("Project", style="cyan")
    table.add_column("package.json", style="yellow")
    table.add_column("node_modules", style="green")
    table.add_column("Dependencies", style="white")
    for npm in npm_packages:
        table.add_row(
            npm.location,
            "[YES]",
            OK if npm.node_modules_present else MISSING,
            str(len(npm.dependencies) + len(npm.dev_dependencies)),
        )
    return table


def npm_detail_table(npm_packages: list[NpmPackageStatus]) -> Table:
    """Return one row per declared npm package with its installed version."""
    table = Table(title="NPM Package Dependency Matrix", header_style="bold magenta")
    table.add_column("Project", style="cyan")
    table.add_column("Package", style="white")
    table.add_column("Type", style="yellow")
    table.add_column("Declared Version", style="white")
    table.add_column("Installed Version", style="white")
    table.add_column("Status", style="bold")
    for npm in npm_packages:
        for pkg in getattr(npm, "detailed_packages", []):
            table.add_row(
                npm.location,
                pkg.name,
                "dev" if pkg.is_dev else "prod",
                pkg.declared_version,
                pkg.installed_version or "[dim]None[/dim]",
                OK if pkg.is_installed else MISSING,
            )
    return table


def print_audit_report(console: Console, report: HealthAuditReport) -> None:
    """Print every section of the health audit in order, skipping empty ones."""
    console.print(bootstrap_table(report.bootstrap))
    console.print(projects_table(report.projects))
    print_manifest_coverage(console, report.manifest_coverage)
    optional_tables = [
        render_drift_matrix(report.version_drift) if report.version_drift else None,
        render_single_manifest_table(report.single_manifest_packages)
        if report.single_manifest_packages else None,
    ]
    if report.npm_packages:
        optional_tables += [
            npm_status_table(report.npm_packages),
            npm_detail_table(report.npm_packages),
        ]
    if report.npm_drift:
        optional_tables.append(render_npm_drift_matrix(report.npm_drift))
    for table in optional_tables:
        if table is not None:
            console.print(table)
