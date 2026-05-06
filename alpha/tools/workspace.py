"""Shared workspace path for all agent tools (single source of truth).

Defaults to current working directory (like Claude Code).
Set AGENT_WORKSPACE env var to override.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FORBIDDEN_WORKSPACES = frozenset({
    Path("/"), Path("/etc"), Path("/usr"), Path("/var"),
    Path("/home"), Path("/root"), Path("/tmp"), Path("/bin"),
    Path("/sbin"), Path("/lib"), Path("/opt"), Path("/dev"),
    Path("/proc"), Path("/sys"),
})

_raw_workspace = Path(os.getenv("AGENT_WORKSPACE", os.getcwd())).resolve()

if _raw_workspace in _FORBIDDEN_WORKSPACES:
    logger.warning(
        f"AGENT_WORKSPACE={_raw_workspace} is a system directory — falling back to CWD"
    )
    AGENT_WORKSPACE = Path.cwd().resolve()
else:
    AGENT_WORKSPACE = _raw_workspace

AGENT_WORKSPACE.mkdir(parents=True, exist_ok=True)


def assert_within_workspace(path: Path | str) -> str | None:
    """Validate that `path` is inside AGENT_WORKSPACE.

    Centraliza o check `path.relative_to(AGENT_WORKSPACE)` que estava
    duplicado em ~10 sites de tools (#DL015 DEEP_LOGIC). Single source
    para a Camada B do enforcement de workspace; reduz risco de drift
    quando o check muda em uma tool e nao em outras.

    Returns None se OK, ou mensagem de erro se path estiver fora.
    """
    p = Path(path).resolve()
    try:
        p.relative_to(AGENT_WORKSPACE)
        return None
    except ValueError:
        return f"Path fora do workspace permitido ({AGENT_WORKSPACE}): {p}"
