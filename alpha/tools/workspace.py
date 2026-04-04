"""Shared workspace path for all agent tools (single source of truth).

Defaults to current working directory (like Claude Code).
Set AGENT_WORKSPACE env var to override.
"""

import os
from pathlib import Path

AGENT_WORKSPACE = Path(os.getenv("AGENT_WORKSPACE", os.getcwd())).resolve()
AGENT_WORKSPACE.mkdir(parents=True, exist_ok=True)
