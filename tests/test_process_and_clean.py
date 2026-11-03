"""Subprocess timeouts and the clean command (REVIEW_REPORT D-13, D-14)."""

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from env_manager.utils import (
    drain_lines,
    purge_project_cache,
    purge_workspace_caches,
    stream_command,
    stream_process_lines,
)


KILL_DEADLINE_S = 15
"""Upper bound for a 1-second timeout to be enforced, kill included."""
EXIT_CODE = 3


def _popen(code: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_silent_hung_child_is_killed_at_the_deadline() -> None:
    """A child that writes nothing and never exits is killed after ``timeout``."""
    started = time.monotonic()
    with _popen("import time; time.sleep(60)") as proc:
        with pytest.raises(subprocess.TimeoutExpired):
            list(stream_process_lines(proc, timeout=1))
    assert time.monotonic() - started < KILL_DEADLINE_S
    assert proc.returncode is not None


def test_child_that_hangs_after_output_is_killed() -> None:
    """Output followed by a hang still times out instead of blocking on readline."""
    lines: list[tuple[str, str]] = []
    with _popen("import sys, time; print('hi', flush=True); time.sleep(60)") as proc:
        with pytest.raises(subprocess.TimeoutExpired):
            for item in stream_process_lines(proc, timeout=2):
                lines.append(item)
    assert lines == [("stdout", "hi\n")]


def test_streams_are_kept_apart_and_exit_code_returned() -> None:
    """stdout and stderr lines are tagged, and the generator returns the exit code."""
    script = f"import sys; print('out'); print('err', file=sys.stderr); sys.exit({EXIT_CODE})"
    seen: list[tuple[str, str]] = []
    code: int | None = None
    with _popen(script) as proc:
        gen = stream_process_lines(proc, timeout=30)
        while code is None:
            try:
                seen.append(next(gen))
            except StopIteration as stop:
                code = stop.value
    assert sorted(seen) == [("stderr", "err\n"), ("stdout", "out\n")]
    assert code == EXIT_CODE


def test_stream_command_yields_lines_and_keeps_streams_apart() -> None:
    """``stream_command`` yields every line live and returns stdout and stderr separately."""
    script = f"import sys; print('out'); print('err', file=sys.stderr); sys.exit({EXIT_CODE})"
    seen: list[str] = []
    completed = drain_lines(stream_command([sys.executable, "-c", script], 30), seen.append)
    assert sorted(seen) == ["err", "out"]
    assert completed.stdout == "out"
    assert completed.stderr == "err"
    assert completed.returncode == EXIT_CODE


def test_stream_command_raises_when_the_tool_is_missing(tmp_path: Path) -> None:
    """A command that cannot start raises ``OSError`` for the caller to report."""
    with pytest.raises(OSError):
        drain_lines(stream_command([str(tmp_path / "no-such-tool")], 30))


def test_clean_keeps_compiled_extension_modules(tmp_path: Path) -> None:
    """``.pyd`` extension modules survive; ``.pyc`` and ``__pycache__`` go."""
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "pkg" / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"x" * 10)
    (tmp_path / "pkg" / "stray.pyc").write_bytes(b"x" * 5)
    (tmp_path / "pkg" / "_native.cp312-win_amd64.pyd").write_bytes(b"x" * 100)
    count, reclaimed = purge_project_cache(tmp_path)
    assert (tmp_path / "pkg" / "_native.cp312-win_amd64.pyd").exists()
    assert not (tmp_path / "pkg" / "stray.pyc").exists()
    assert not (tmp_path / "pkg" / "__pycache__").exists()
    assert (count, reclaimed) == (2, 15)


def test_workspace_clean_skips_node_projects(tmp_path: Path) -> None:
    """CLI and API share one rule: Node projects are not cleaned."""
    for name in ("py", "node"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "a.pyc").write_bytes(b"x")
    projects = [
        SimpleNamespace(name="py", project_dir=str(tmp_path / "py"), is_node_project=False),
        SimpleNamespace(name="node", project_dir=str(tmp_path / "node"), is_node_project=True),
    ]
    assert purge_workspace_caches(projects) == (1, 1)
    assert (tmp_path / "node" / "a.pyc").exists()
    assert purge_workspace_caches(projects, target="node") == (0, 0)
