"""Dataclass models for GUI documentation figure and callout specifications."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CalloutSpec:
    """Numbered callout badge anchored to a live DOM selector."""

    number: int
    selector: str
    label: str
    description: str


@dataclass(frozen=True)
class FigureSpec:
    """Capture specification for a single GUI documentation screenshot."""

    filename: str
    title: str
    tab_id: str
    callouts: tuple[CalloutSpec, ...]
    setup_steps: tuple[dict[str, str], ...] = ()
    clip_selector: str | None = None
    viewport: tuple[int, int] = (1440, 900)

    def validate_callouts(self) -> list[str]:
        """Verify callouts are strictly sequential 1..K."""
        expected = list(range(1, len(self.callouts) + 1))
        actual = [c.number for c in self.callouts]
        if actual != expected:
            return [
                f"{self.filename}: callout numbers {actual} != expected {expected}"
            ]
        return []
