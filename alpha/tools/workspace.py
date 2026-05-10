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
    # Windows system paths
    Path("C:/Windows"), Path("C:/Windows/System32"),
    Path("C:/Program Files"), Path("C:/Program Files (x86)"),
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

    Caller deve fazer `resolve()` ANTES se necessario (ex: symlink-aware
    checks). Esta funcao faz apenas `expanduser()` para manter o
    comportamento consistente com os sites originais onde alguns usam
    resolve e outros usam normpath.

    Returns None se OK, ou mensagem de erro se path estiver fora.
    """
    p = Path(path).expanduser()
    try:
        p.relative_to(AGENT_WORKSPACE)
        return None
    except ValueError:
        return f"Path fora do workspace permitido ({AGENT_WORKSPACE}): {p}"
