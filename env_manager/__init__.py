"""LemGendary Environment Manager.

Authoritative cross-platform environment, dependency, and health management system.
"""

from env_manager.health_checker import run_full_health_audit
from env_manager.orchestrator import PipelineOrchestrator
from env_manager.system_probe import probe_hardware
from env_manager.validator import validate_project

__version__ = "2.2.0"
__author__ = "LemGendary AI"

__all__ = [
    "PipelineOrchestrator",
    "probe_hardware",
    "run_full_health_audit",
    "validate_project",
    "__version__",
]
