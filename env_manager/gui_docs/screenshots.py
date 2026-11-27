"""Screenshot capture and PNG validation service for AI Studio GUI docs."""

import json
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from env_manager.gui_docs.specs import FIGURE_SPECS, FigureSpec
from env_manager.workspace import ENV_MANAGER_DIR, workspace_root

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_HEADER_BYTES = 24


def read_png_dimensions(png_path: Path) -> tuple[int, int]:
    """Verify PNG magic bytes and return ``(width, height)`` from IHDR."""
    data = png_path.read_bytes()
    if len(data) < _PNG_HEADER_BYTES or data[:8] != PNG_SIGNATURE:
        raise ValueError(f"Invalid PNG signature in {png_path}")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid PNG dimensions {width}x{height} in {png_path}")
    return width, height


def validate_captured_screenshots(
    out_dir: Path,
    specs: tuple[FigureSpec, ...] = FIGURE_SPECS,
) -> list[str]:
    """Validate that every FigureSpec has sequential callouts and a valid PNG."""
    errors: list[str] = []
    for spec in specs:
        errors.extend(spec.validate_callouts())
        png_path = out_dir / spec.filename
        if not png_path.exists():
            errors.append(f"Missing screenshot: {png_path}")
            continue
        try:
            read_png_dimensions(png_path)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    return errors


def capture_gui_screenshots(
    url: str = "http://localhost:1420",
    out_dir: Path | None = None,
    audit_out: Path | None = None,
    prune_test_pngs: bool = True,
) -> dict[str, object]:
    """Run ``capture_runner.mjs`` via Node and validate every captured PNG."""
    node_exe = shutil.which("node")
    if not node_exe:
        raise RuntimeError("Node.js executable not found on PATH.")

    target_dir = out_dir or (workspace_root() / "lemgendary-docs" / "assets" / "gui")
    target_dir.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).resolve().parent / "capture_runner.mjs"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8", delete=False
    ) as tmp:
        json.dump([asdict(s) for s in FIGURE_SPECS], tmp, indent=2)
        specs_path = Path(tmp.name)

    try:
        cmd = [
            node_exe,
            str(runner),
            "--url",
            url,
            "--out-dir",
            str(target_dir),
            "--specs-json",
            str(specs_path),
        ]
        if audit_out is not None:
            cmd.extend(["--audit-out", str(audit_out)])
        if not prune_test_pngs:
            cmd.append("--no-prune")

        env = os.environ.copy()
        env["NODE_PATH"] = str(ENV_MANAGER_DIR / "node_modules")
        proc = subprocess.run(
            cmd,
            cwd=str(ENV_MANAGER_DIR),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"capture_runner.mjs failed ({proc.returncode}): {proc.stderr or proc.stdout}"
            )
    finally:
        specs_path.unlink(missing_ok=True)

    errors = validate_captured_screenshots(target_dir, FIGURE_SPECS)
    if errors:
        raise RuntimeError("Screenshot validation failed:\n" + "\n".join(errors))

    return {
        "captured_count": len(FIGURE_SPECS),
        "output_dir": str(target_dir),
        "runner_output": proc.stdout.strip(),
    }
