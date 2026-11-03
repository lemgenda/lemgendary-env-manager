"""Rich tables for the drift matrix, manifest coverage, single-manifest and npm drift."""


from rich.table import Table

from env_manager.health.models import (
    ManifestCoverageEntry,
    NpmDriftEntry,
    PinInfo,
    SingleManifestEntry,
    UpgradeStatus,
    VersionDriftEntry,
)

PIN_GLYPH = {
    "exact":      "=",
    "range":      "~",
    "floating":   "*",
    "transitive": ".",
    "absent":     "",
}


def format_cell(
    version: str | None,
    pin: PinInfo | None,
    upgrade: UpgradeStatus | None,
) -> str:
    """Format one drift-matrix cell."""
    if not version:
        return "-"

    pin_type = pin.pin_type if pin else ""
    glyph = PIN_GLYPH.get(pin_type, "")
    base = f"{version}{glyph}" if glyph else version

    if upgrade and upgrade.is_outdated and upgrade.latest:
        arrow = "^" if upgrade.safe else "!"
        return f"{base} -> {upgrade.latest} {arrow}"

    return base


def render_drift_matrix(entries: list[VersionDriftEntry]) -> Table:
    """Return a Rich Table showing Python drift, pins, and upgrade status."""
    projects: list[str] = []
    for e in entries:
        for name in e.versions.keys():
            if name not in projects:
                projects.append(name)

    table = Table(title="Python Package Drift & Upgrade Status", show_lines=False)
    table.add_column("Package", style="cyan", no_wrap=True)
    for proj in projects:
        table.add_column(proj, min_width=18, overflow="fold")
    table.add_column("Drift", justify="center", no_wrap=True)
    table.add_column("Safe ↑", justify="center", no_wrap=True)

    for entry in entries:
        row: list[str] = [entry.package_name]
        safe_count = 0
        for proj in projects:
            ver = entry.versions.get(proj)
            pin = entry.pins.get(proj)
            status = entry.upgrades.get(proj)
            if status and status.safe and status.is_outdated:
                safe_count += 1
            row.append(format_cell(ver, pin, status))

        drift_marker = "[red]yes[/red]" if entry.has_drift else "[green]no[/green]"
        if entry.has_pin_mismatch:
            drift_marker = f"{drift_marker} [yellow]pin≠[/yellow]"
        row.append(drift_marker)
        row.append(str(safe_count) if safe_count else "-")
        table.add_row(*row)

    return table


def render_manifest_coverage_table(
    entries: list[ManifestCoverageEntry],
) -> Table:
    """Return a Rich Table showing declared-vs-installed coverage per project."""
    table = Table(
        title="Manifest Coverage (Declared vs Installed)",
        show_lines=False,
    )
    table.add_column("Project", style="cyan", no_wrap=True)
    table.add_column("Declared", justify="right")
    table.add_column("Installed", justify="right")
    table.add_column("Missing", justify="right")
    table.add_column("Extra", justify="right", style="yellow")
    table.add_column("Platform-Skipped", justify="right", style="dim")

    for e in entries:
        missing_cell = (
            f"[red]{len(e.missing)}[/red]" if e.missing else "[green]0[/green]"
        )
        extra_cell = (
            f"[yellow]{len(e.extra)}[/yellow]" if e.extra else "0"
        )
        skipped_cell = (
            f"[dim]{len(e.platform_skipped)}[/dim]" if e.platform_skipped else "0"
        )
        table.add_row(
            e.project_name,
            str(e.declared_count),
            str(e.installed_count),
            missing_cell,
            extra_cell,
            skipped_cell,
        )

    return table


def render_single_manifest_table(
    entries: list[SingleManifestEntry],
) -> Table:
    """Return a Rich Table showing packages declared in only one manifest."""
    table = Table(
        title="Packages Declared in Only One Manifest",
        show_lines=False,
    )
    table.add_column("Project", style="cyan", no_wrap=True)
    table.add_column("Package", style="white", no_wrap=True)
    table.add_column("Pin", style="yellow", no_wrap=True)
    table.add_column("Installed", style="green")

    for e in entries:
        glyph = PIN_GLYPH.get(e.pin_type, "")
        spec = e.specifier if e.specifier else "-"
        pin_display = f"{glyph} {spec}".strip() if glyph else spec
        installed = e.installed_version or "[red]MISSING[/red]"
        table.add_row(e.project, e.package_name, pin_display, installed)

    return table


def render_npm_drift_matrix(entries: list[NpmDriftEntry]) -> Table:
    """Return a Rich Table showing npm workspace drift."""
    workspaces: list[str] = []
    for e in entries:
        for name in e.versions.keys():
            if name not in workspaces:
                workspaces.append(name)

    table = Table(title="NPM Package Drift Across Workspaces", show_lines=False)
    table.add_column("Package", style="cyan", no_wrap=True)
    for ws in workspaces:
        table.add_column(ws, min_width=18, overflow="fold")
    table.add_column("Drift", justify="center", no_wrap=True)
    table.add_column("Kind", justify="center", no_wrap=True)

    for entry in entries:
        row: list[str] = [entry.package_name]
        for ws in workspaces:
            ver = entry.versions.get(ws)
            spec = entry.specifiers.get(ws)
            if ver and spec:
                row.append(f"{ver}  ({spec})")
            elif ver:
                row.append(ver)
            elif spec:
                row.append(f"-  ({spec})")
            else:
                row.append("-")
        row.append("[red]yes[/red]" if entry.has_drift else "[green]no[/green]")
        row.append("dev" if entry.is_dev_dependency else "prod")
        table.add_row(*row)

    return table
