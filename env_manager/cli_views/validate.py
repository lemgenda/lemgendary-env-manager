"""Tables and lines printed by ``lem-env validate``."""

from rich.console import Console
from rich.table import Table

from env_manager.validator import ProjectValidationReport

VIOLATIONS_SHOWN_PER_KIND = 5
"""Violations of each kind printed for a project that fails validation."""


def count_cell(violations: list, noun: str = "issues") -> str:
    """Summary-table cell: green OK, or the violation count in red."""
    return "[green]OK[/green]" if not violations else f"[red]{len(violations)} {noun}[/red]"


def warning_cell(warnings: list) -> str:
    """Summary-table cell for warnings: dim none, or the count in yellow."""
    return "[dim]none[/dim]" if not warnings else f"[yellow]{len(warnings)}[/yellow]"


def summary_table() -> Table:
    """Return the empty validation summary table with its columns."""
    table = Table(title="Validation Summary", header_style="bold magenta")
    table.add_column("Project", style="cyan")
    for column in ("Python", "Lint", "YAML", "JSON", "HTML/WCAG", "Domain", "Warnings"):
        table.add_column(column, style="white")
    table.add_column("Status", style="bold")
    return table


def summary_row(proj_name: str, rep: ProjectValidationReport, is_node: bool) -> list[str]:
    """Return one summary-table row for ``rep``; Node projects show N/A for Python."""
    emoji_issue = f" +{len(rep.emoji_violations)} emoji" if rep.emoji_violations else ""
    py_status = "[dim]N/A[/dim]" if is_node else count_cell(rep.compile_errors, "errors")
    html_status = (
        "[green]OK[/green]"
        if not rep.html_errors and not rep.wcag_violations
        else f"[red]{len(rep.html_errors)}W3C {len(rep.wcag_violations)}WCAG[/red]"
    )
    return [
        proj_name, py_status + emoji_issue, count_cell(rep.lint_errors),
        count_cell(rep.yaml_errors), count_cell(rep.json_errors), html_status,
        count_cell(rep.domain_errors), warning_cell(rep.warnings),
        "[green][PASS][/green]" if rep.passed else "[red][FAIL][/red]",
    ]


def print_violations(console: Console, rep: ProjectValidationReport) -> None:
    """Print the first few violations of each kind in a failed report."""
    sections = (
        ("SyntaxError", rep.compile_errors), ("Emoji", rep.emoji_violations),
        ("Lint", rep.lint_errors), ("YAML", rep.yaml_errors), ("JSON", rep.json_errors),
        ("HTML", rep.html_errors), ("WCAG", rep.wcag_violations), ("Domain", rep.domain_errors),
    )
    for label, violations in sections:
        for err in violations[:VIOLATIONS_SHOWN_PER_KIND]:
            console.print(
                f"  [red]{label} ({err.file_path}:{err.line_number}): {err.message}[/red]"
            )


def print_warnings(console: Console, rep: ProjectValidationReport) -> None:
    """Print the first few warnings of ``rep`` and how many more there are."""
    for warn in rep.warnings[:VIOLATIONS_SHOWN_PER_KIND]:
        console.print(
            f"  [yellow]Warning ({warn.file_path}:{warn.line_number}): {warn.message}[/yellow]"
        )
    hidden = len(rep.warnings) - VIOLATIONS_SHOWN_PER_KIND
    if hidden > 0:
        console.print(f"  [yellow]... {hidden} more warnings in {rep.project_name}[/yellow]")
