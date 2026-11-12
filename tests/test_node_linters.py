"""ESLint flat configs, warnings and the node tsconfig are handled (audit 4.2.2)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from env_manager.validation.gates import node_linters
from env_manager.validation.gates.node_linters import (
    eslint_config_args,
    run_eslint,
    run_typescript_check,
)

ESLINT_BIN = ["eslint"]
TSC_BIN = ["tsc"]


def _report(*severities: int) -> str:
    messages = [
        {"line": i + 1, "severity": s, "message": f"m{i}"} for i, s in enumerate(severities)
    ]
    return json.dumps([{"filePath": "src/a.ts", "messages": messages}])


def _eslint(project: Path, stdout: str, code: int = 1) -> tuple[list[str], list[str]]:
    done = subprocess.CompletedProcess([], code, stdout=stdout, stderr="")
    with patch.object(node_linters, "resolve_tool_cmd", return_value=list(ESLINT_BIN)), \
            patch.object(node_linters, "run_tool", return_value=done) as run:
        found = run_eslint(project)
    return [v.violation_type for v in found], run.call_args.args[0]


def test_flat_config_gets_no_ext_flag(tmp_path: Path) -> None:
    """ESLint v9 rejects ``--ext`` with a flat config."""
    (tmp_path / "eslint.config.js").write_text("export default [];\n", encoding="utf-8")
    assert not eslint_config_args(tmp_path)
    _types, cmd = _eslint(tmp_path, "[]", code=0)
    assert "--ext" not in cmd


def test_legacy_config_keeps_ext_flag(tmp_path: Path) -> None:
    """Legacy eslintrc still needs ``--ext`` to reach TypeScript files."""
    (tmp_path / ".eslintrc.json").write_text("{}\n", encoding="utf-8")
    assert eslint_config_args(tmp_path) == ["--ext", ".js,.jsx,.ts,.tsx"]


def test_no_config_skips_eslint(tmp_path: Path) -> None:
    """Without any ESLint config the gate does not run."""
    assert eslint_config_args(tmp_path) is None
    assert not run_eslint(tmp_path)


def test_warnings_are_violations(tmp_path: Path) -> None:
    """Severity 1 warnings fail validation, matching ``--max-warnings 0``."""
    (tmp_path / "eslint.config.js").write_text("export default [];\n", encoding="utf-8")
    types, _cmd = _eslint(tmp_path, _report(1, 2, 0))
    assert types == ["eslint_warning", "eslint_error"]


def test_node_tsconfig_is_type_checked(tmp_path: Path) -> None:
    """``tsconfig.node.json`` gets its own ``tsc -p`` run."""
    for name in ("tsconfig.json", "tsconfig.node.json"):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    failed = subprocess.CompletedProcess(
        [], 2, stdout="vite.config.ts(3,1): error TS2304: Cannot find name 'x'.\n", stderr="",
    )
    passed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    def tsc_cmd(*_args: object, **_kwargs: object) -> list[str]:
        return list(TSC_BIN)

    with patch.object(node_linters, "resolve_tool_cmd", side_effect=tsc_cmd), \
            patch.object(node_linters, "run_tool", side_effect=[passed, failed]) as run:
        found = run_typescript_check(tmp_path)
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["tsc", "-p", "tsconfig.json", "--noEmit"],
        ["tsc", "-p", "tsconfig.node.json", "--noEmit"],
    ]
    assert [(v.file_path, v.line_number) for v in found] == [("vite.config.ts", 3)]
