"""Shared loader for `.alpha/settings.json` and friends.

Resolves config files in this priority order (first match wins):
  1. ./.alpha/<file>           — project-local override
  2. <project_root>/.alpha/<file>  — bundled with the install
  3. ~/.alpha/<file>           — user-global

Two helpers:
  * `find_config_file(name)`  — returns the resolved Path, or None
  * `read_json(path, default)` — load JSON, log + return default on failure
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import _PROJECT_ROOT

logger = logging.getLogger(__name__)


def alpha_config_paths(filename: str) -> list[Path]:
    """Candidate locations for an `.alpha/<filename>` config file."""
    return [
        Path.cwd() / ".alpha" / filename,
        _PROJECT_ROOT / ".alpha" / filename,
        Path.home() / ".alpha" / filename,
    ]


def find_config_file(filename: str) -> Path | None:
    for path in alpha_config_paths(filename):
        if path.is_file():
            return path
    return None


def read_json(path: Path | None, default: Any = None) -> Any:
    """Read a JSON file. Returns `default` if the path is None, missing, or invalid."""
    if path is None:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return default
