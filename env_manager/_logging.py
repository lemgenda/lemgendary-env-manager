"""Structured logging configuration for LemGendary Environment Manager.

All internal modules use named child loggers under the 'lem_env' root logger.
The root logger is configured at INFO level by default. Set the LEM_ENV_LOG_LEVEL
environment variable to override (DEBUG, INFO, WARNING, ERROR).

Usage in modules:
    from env_manager._logging import get_logger
    _log = get_logger(__name__)
    ...
    _log.warning("nvidia-smi probe failed: %s", exc)
"""

import logging
import os
import sys

_ROOT_LOGGER_NAME = "lem_env"
_DEFAULT_LOG_LEVEL = os.environ.get("LEM_ENV_LOG_LEVEL", "INFO").upper()
_LOG_FORMAT = "%(levelname)s [%(name)s] %(message)s"

# Configure root logger once
_root = logging.getLogger(_ROOT_LOGGER_NAME)
if not _root.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _root.addHandler(_handler)

_level = getattr(logging, _DEFAULT_LOG_LEVEL, logging.INFO)
_root.setLevel(_level)


def get_logger(module_name: str) -> logging.Logger:
    """Return a child logger namespaced under 'lem_env'.

    Args:
        module_name: Typically pass __name__ from the calling module.

    Returns:
        A Logger instance named 'lem_env.<module_name>'.
    """
    # Strip the 'env_manager.' prefix to keep names short
    short_name = module_name.replace("env_manager.", "").replace("env_manager", "core")
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{short_name}")
