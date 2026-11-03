"""Checks that the Tauri GUI agrees with ``ecosystem.json``, the env OpenAPI schema
and its central npm manifest.

Each check takes a :class:`GuiContext` and returns violations. A check
that cannot run yet (the GUI still keeps its own table, or a file cannot be
parsed) returns one ``ssot_skipped`` note naming the reason.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from env_manager._logging import get_logger
from env_manager.ecosystem import Ecosystem
from env_manager.validation.files import iter_project_files
from env_manager.validation.models import MANIFEST_READ_ERRORS, FileValidationViolation

_log = get_logger(__name__)

SNAPSHOT_MISMATCH = "ssot_gui_snapshot"
CSP_PORTS = "ssot_csp_ports"
PIPELINE_STEPS = "ssot_pipeline_steps"
PROJECT_HEALTH = "ssot_project_health"
NPM_MANIFEST = "ssot_npm_manifest"
SKIPPED = "ssot_skipped"
UNREADABLE = "ssot_unreadable"

MANIFESTS_DIR = "requirements"
"""The env-manager folder holding the central manifests."""

PACKAGE_JSON = "package.json"

SNAPSHOT_PATH = Path("src") / "generated" / "ecosystem.json"
"""The GUI's committed copy of the ecosystem file."""

TAURI_CONFIG_PATH = Path("src-tauri") / "tauri.conf.json"
"""The Tauri app configuration holding the content security policies."""

CSP_KEYS = ("csp", "devCsp")
"""Security policies in the Tauri configuration whose ``connect-src`` is checked."""

SOURCE_DIR = "src"
"""The GUI's TypeScript source folder."""

TS_SUFFIXES = frozenset({".ts", ".tsx"})

_LOCAL_PORT = re.compile(r"\b(?:https?|wss?)://(?:127\.0\.0\.1|localhost):(\d+)")
_STEPS_TABLE = re.compile(r"export\s+const\s+PIPELINE_STEPS\b[^=]*=\s*\[")
_STEP_ENTRY = re.compile(r"^\s*num\s*:\s*\d+", re.MULTILINE)
_PROJECT_HEALTH = re.compile(r"interface\s+ProjectHealth\s*\{(?P<body>[^{}]*)\}")
_TS_KEY = re.compile(r"^\s*(\w+)\??\s*:", re.MULTILINE)


@dataclass(frozen=True)
class GuiContext:
    """The GUI checkout, the env-manager checkout beside it and the parsed registry."""

    gui_dir: Path
    env_dir: Path
    eco: Ecosystem

    @property
    def source_file(self) -> Path:
        """The env-manager's ``ecosystem.json`` in this workspace."""
        return self.env_dir / "ecosystem" / "ecosystem.json"

    @property
    def openapi_file(self) -> Path:
        """The env-manager's committed ``openapi.json`` in this workspace."""
        return self.env_dir / "openapi.json"


GuiCheck = Callable[[GuiContext], list[FileValidationViolation]]


def is_tauri_gui(project_dir: Path) -> bool:
    """True for a project that holds a Tauri configuration."""
    return (project_dir / TAURI_CONFIG_PATH).is_file()


def _finding(path: Path, kind: str, message: str, line: int = 0) -> FileValidationViolation:
    return FileValidationViolation(str(path), line, kind, message)


def _skip(path: Path, reason: str) -> list[FileValidationViolation]:
    return [_finding(path, SKIPPED, f"Check skipped: {reason}")]


def _unreadable(path: Path, exc: Exception) -> list[FileValidationViolation]:
    """A source file that exists but cannot be parsed is a finding; a missing one is a skip."""
    if isinstance(exc, FileNotFoundError):
        return _skip(path, f"{path.name} is not present")
    return [_finding(path, UNREADABLE, f"Cannot parse {path.name}: {exc}")]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _line_of(text: str, needle: str) -> int:
    index = text.find(needle)
    return 0 if index < 0 else text.count("\n", 0, index) + 1


def find_source(gui_dir: Path, pattern: re.Pattern[str]) -> tuple[Path, str] | None:
    """Return the first TypeScript file under ``src`` matching ``pattern``, with its text."""
    for path in iter_project_files(gui_dir / SOURCE_DIR):
        if path.suffix not in TS_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _log.warning("Cannot read %s, skipping: %s", path, exc)
            continue
        if pattern.search(text):
            return path, text
    return None


def check_snapshot(ctx: GuiContext) -> list[FileValidationViolation]:
    """The GUI snapshot equals the source file (when both exist)."""
    snapshot = ctx.gui_dir / SNAPSHOT_PATH
    if not (snapshot.is_file() and ctx.source_file.is_file()):
        return []
    try:
        same = _read_json(snapshot) == _read_json(ctx.source_file)
    except MANIFEST_READ_ERRORS as exc:
        return [_finding(snapshot, SNAPSHOT_MISMATCH, f"Cannot compare snapshot: {exc}")]
    if same:
        return []
    return [_finding(snapshot, SNAPSHOT_MISMATCH, "Snapshot differs from ecosystem.json")]


def connect_src(policy: Any) -> str:
    """The ``connect-src`` sources of a Tauri policy (object or string form)."""
    if isinstance(policy, dict):
        value = policy.get("connect-src", "")
        return value if isinstance(value, str) else " ".join(value)
    if isinstance(policy, str):
        match = re.search(r"connect-src([^;]*)", policy)
        return match.group(1) if match else ""
    return ""


def dev_server_ports(config: dict[str, Any]) -> set[int]:
    """The port of the GUI's own dev server (``build.devUrl``), if any."""
    port = urlparse(str(config.get("build", {}).get("devUrl", ""))).port
    return set() if port is None else {port}


def check_csp_ports(ctx: GuiContext) -> list[FileValidationViolation]:
    """Local ports in each ``connect-src`` equal the service ports."""
    path = ctx.gui_dir / TAURI_CONFIG_PATH
    try:
        text = path.read_text(encoding="utf-8")
        config = json.loads(text)
        security = config.get("app", {}).get("security", {})
    except MANIFEST_READ_ERRORS as exc:
        return _unreadable(path, exc)
    expected = {svc.port for svc in ctx.eco.services}
    ignored = dev_server_ports(config)
    found: list[FileValidationViolation] = []
    for key in CSP_KEYS:
        if key not in security:
            continue
        ports = {int(p) for p in _LOCAL_PORT.findall(connect_src(security[key]))} - ignored
        if ports != expected:
            found.append(_finding(
                path, CSP_PORTS,
                f"{key} connect-src ports {sorted(ports)} differ from service ports "
                f"{sorted(expected)}",
                _line_of(text, f'"{key}"'),
            ))
    return found


def check_pipeline_steps(ctx: GuiContext) -> list[FileValidationViolation]:
    """The GUI's pipeline steps come from the snapshot and match its step count.

    While the GUI still keeps its own ``PIPELINE_STEPS`` table the check is
    skipped and reported.
    """
    expected = len(ctx.eco.pipeline_steps)
    located = find_source(ctx.gui_dir, _STEPS_TABLE)
    if located is not None:
        path, text = located
        own = len(_STEP_ENTRY.findall(text))
        return _skip(path, (
            f"GUI keeps its own PIPELINE_STEPS table ({own} steps; ecosystem.json has "
            f"{expected}); the count check runs once the GUI derives the steps"
        ))
    snapshot = ctx.gui_dir / SNAPSHOT_PATH
    try:
        steps = _read_json(snapshot)["pipeline_steps"]
    except (*MANIFEST_READ_ERRORS, KeyError) as exc:
        return _unreadable(snapshot, exc)
    if len(steps) == expected:
        return []
    return [_finding(
        snapshot, PIPELINE_STEPS,
        f"GUI has {len(steps)} pipeline steps; ecosystem.json has {expected}",
    )]


def ts_interface_keys(body: str) -> set[str]:
    """Property names in a flat TypeScript interface body."""
    return set(_TS_KEY.findall(body))


def openapi_schema_keys(openapi: Any, name: str) -> set[str] | None:
    """Property names of component schema ``name``, or ``None`` when absent."""
    schema = openapi.get("components", {}).get("schemas", {}).get(name)
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        return None
    return set(schema["properties"])


def check_project_health(ctx: GuiContext) -> list[FileValidationViolation]:
    """The GUI ``ProjectHealth`` keys equal the env OpenAPI ``ProjectHealth`` schema."""
    located = find_source(ctx.gui_dir, _PROJECT_HEALTH)
    if located is None:
        return _skip(ctx.gui_dir / SOURCE_DIR, "no parseable ProjectHealth interface")
    path, text = located
    match = _PROJECT_HEALTH.search(text)
    gui_keys = ts_interface_keys(match.group("body")) if match else set()
    try:
        api_keys = openapi_schema_keys(_read_json(ctx.openapi_file), "ProjectHealth")
    except MANIFEST_READ_ERRORS as exc:
        return _unreadable(ctx.openapi_file, exc)
    if api_keys is None:
        return _skip(ctx.openapi_file, "no ProjectHealth schema in the env OpenAPI document")
    if gui_keys == api_keys:
        return []
    return [_finding(
        path, PROJECT_HEALTH,
        f"ProjectHealth keys differ from OpenAPI: GUI only {sorted(gui_keys - api_keys)}, "
        f"OpenAPI only {sorted(api_keys - gui_keys)}",
        _line_of(text, "interface ProjectHealth"),
    )]


def check_npm_manifest(ctx: GuiContext) -> list[FileValidationViolation]:
    """The GUI ``package.json`` equals its central manifest in the env-manager.

    The central ``requirements/<npm_manifest>`` is the file to edit; a GUI
    copy that has moved on means the central one is stale. Skipped with a
    note when the registry names no manifest or either file is absent.
    """
    try:
        manifest = ctx.eco.project(ctx.gui_dir.name).npm_manifest
    except KeyError:
        manifest = None
    if manifest is None:
        return _skip(ctx.gui_dir, "no central npm manifest is registered for this project")
    central = ctx.env_dir / MANIFESTS_DIR / manifest
    sibling = ctx.gui_dir / PACKAGE_JSON
    for path in (central, sibling):
        if not path.is_file():
            return _skip(path, f"{path.name} is not present")
    try:
        central_data, sibling_data = _read_json(central), _read_json(sibling)
    except MANIFEST_READ_ERRORS as exc:
        return [_finding(sibling, UNREADABLE, f"Cannot compare {sibling.name}: {exc}")]
    if not (isinstance(central_data, dict) and isinstance(sibling_data, dict)):
        return [_finding(sibling, UNREADABLE, "package.json is not a JSON object")]
    keys = sorted(
        key for key in central_data.keys() | sibling_data.keys()
        if central_data.get(key) != sibling_data.get(key)
    )
    if not keys:
        return []
    return [_finding(
        sibling, NPM_MANIFEST,
        f"{sibling.name} differs from the central {manifest} in {keys}; "
        "update the central manifest, then sync",
    )]


GUI_CHECKS: tuple[GuiCheck, ...] = (
    check_snapshot, check_csp_ports, check_pipeline_steps, check_project_health,
    check_npm_manifest,
)
"""Every GUI parity check, in report order."""


def run_gui_checks(ctx: GuiContext) -> list[FileValidationViolation]:
    """Run every GUI parity check on ``ctx``."""
    return [finding for check in GUI_CHECKS for finding in check(ctx)]
