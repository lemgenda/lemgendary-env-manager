"""Tables and lines printed by ``lem-env update``."""

from rich.table import Table

from env_manager.updater import EcosystemUpgradePlan, ProjectUpgradePlan, UpgradeEvent
from env_manager.validator import ProjectValidationReport

PLAN_PREVIEW_SIZE = 3
"""Safe packages listed per project in the upgrade-plan table."""

STATUS_COLORS = {"upgraded": "green", "failed": "red", "blocked": "yellow", "verified": "blue"}
"""Console colour per upgrade event status; any other status is yellow."""


def _preview(proj_plan: ProjectUpgradePlan) -> str:
    """Return the first few safe packages with their version change."""
    safe = proj_plan.safe_packages
    preview = ", ".join(
        f"{p.name} ({p.current_version}->{p.latest_version})" for p in safe[:PLAN_PREVIEW_SIZE]
    )
    if len(safe) > PLAN_PREVIEW_SIZE:
        preview += f" ... (+{len(safe) - PLAN_PREVIEW_SIZE} more)"
    return preview or "[dim](none)[/dim]"


def plan_table(plan: EcosystemUpgradePlan) -> Table:
    """Return the per-project upgrade plan table."""
    table = Table(
        title=f"Upgrade Plan ({plan.total_outdated} packages outdated, "
              f"{plan.total_safe} safe, {plan.total_blocked} blocked)",
        header_style="bold magenta",
    )
    table.add_column("Project", style="cyan")
    table.add_column("Type", style="white")
    table.add_column("Safe", style="green", justify="right")
    table.add_column("Blocked", style="red", justify="right")
    table.add_column("Packages (safe only)", style="white")
    for proj_plan in plan.projects:
        table.add_row(
            proj_plan.name,
            "npm" if proj_plan.is_node_project else "pip",
            str(len(proj_plan.safe_packages)),
            str(len(proj_plan.blocked_packages)),
            _preview(proj_plan),
        )
    return table


def event_line(event: UpgradeEvent) -> str:
    """Return the coloured console line for one upgrade event."""
    color = STATUS_COLORS.get(event.status, "yellow")
    pkg_info = event.package
    if event.old_version and event.new_version:
        pkg_info += f" ({event.old_version} -> {event.new_version})"
    if event.status == "blocked" and event.message:
        return f"[{color}][BLOCKED] {event.project}: {pkg_info} — {event.message}[/{color}]"
    return f"[{color}][{event.status.upper()}] {event.project}: {pkg_info}[/{color}]"


def validation_lines(name: str, report: ProjectValidationReport) -> list[str]:
    """Return the post-upgrade validation line and, on failure, its first three findings."""
    tag = "[bold green][PASS][/bold green]" if report.passed else "[bold red][FAIL][/bold red]"
    lines = [
        f"{tag} {name}: compiled={report.compiled_files_count}, {report.failure_summary()}"
    ]
    if not report.passed:
        lines += [
            f"  [red]{err.file_path}:{err.line_number}: {err.message}[/red]"
            for err in report.failing_violations()[:3]
        ]
    return lines
