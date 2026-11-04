"""Tables and lines printed by ``lem-env probe``."""

from rich.console import Console
from rich.table import Table

from env_manager.bootstrap import BootstrapStatus, SoftwareUpdateInfo
from env_manager.probe.models import HardwareProfile, MetaTrader5Info


def _mt5_cell(mt5: MetaTrader5Info) -> str:
    """Return the MetaTrader 5 cell: install state, version and path."""
    if not mt5.installed:
        return "[red]NOT FOUND[/red]"
    cell = "[green]INSTALLED[/green]"
    if mt5.version:
        cell += f" (v{mt5.version})"
    if mt5.install_path:
        cell += f" — {mt5.install_path}"
    return cell


def hardware_table(hw: HardwareProfile) -> Table:
    """Return the hardware, accelerator and MetaTrader 5 profile table."""
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

    for acc in hw.accelerators:
        details = (
            f"Index: {acc.index}, VRAM: {acc.total_memory_mb} MB, "
            f"Backend: {acc.backend.upper()}"
        )
        if acc.driver_version:
            details += f", Driver: {acc.driver_version}"
        table.add_row(f"Accelerator: {acc.name}", details)
    if not hw.accelerators:
        table.add_row("Accelerators", "None detected (Using CPU backend)")

    if hw.metatrader5 is not None:
        table.add_row("MetaTrader 5", _mt5_cell(hw.metatrader5))
    return table


def update_status(upd: SoftwareUpdateInfo) -> str:
    """Return the status cell for one tracked piece of software.

    Versions come from ``winget list`` (installed) and ``winget show``
    (available in source). For MT5, if winget doesn't track the install,
    the version is read from terminal64.exe's file metadata instead, and
    the status reflects that winget has no source data.
    """
    if upd.update_available:
        return "[yellow]UPDATE AVAILABLE[/yellow]"
    if upd.current_version and upd.latest_version:
        return "[green]UP TO DATE[/green]"
    if upd.current_version:
        # Installed, but the source doesn't have a version to compare.
        return "[dim]INSTALLED (not in winget)[/dim]"
    if upd.latest_version:
        return "[dim]NOT INSTALLED[/dim]"
    return "[dim]NOT TRACKED[/dim]"


def print_software_updates(console: Console, updates: list[SoftwareUpdateInfo]) -> None:
    """Print the update command for each outdated tool, then the availability table."""
    table = Table(title="Software Update Availability", header_style="bold magenta")
    table.add_column("Software", style="cyan")
    table.add_column("Current", style="white")
    table.add_column("Latest", style="white")
    table.add_column("Status", style="bold")
    for upd in updates:
        table.add_row(
            upd.name,
            upd.current_version or "[dim]—[/dim]",
            upd.latest_version or "[dim]—[/dim]",
            update_status(upd),
        )
        if upd.update_available and upd.install_command:
            console.print(f"  [yellow]Run to update:[/yellow] {upd.install_command}")
    console.print(table)


def print_prerequisites(console: Console, boot: BootstrapStatus) -> None:
    """Print missing prerequisites with their remediation, or that all are satisfied."""
    if not boot.missing_prerequisites:
        console.print("[bold green]All software prerequisites satisfied.[/bold green]")
        return
    console.print("\n[bold red]Missing Prerequisites:[/bold red]")
    for prereq in boot.missing_prerequisites:
        console.print(f"  [red]• {prereq}[/red]")
    console.print("\n[bold yellow]Remediation:[/bold yellow]")
    for instruction in boot.remediation_instructions:
        console.print(f"  [yellow]→ {instruction}[/yellow]")
