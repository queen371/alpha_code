"""
Delegate tools — spawn sub-agents to handle tasks independently.

Supports single delegation (delegate_task) and parallel delegation
(delegate_parallel) with concurrency limited by max_parallel_agents.
"""

import asyncio
import json
import logging
import secrets
import sys
from datetime import datetime
from pathlib import Path

from . import ToolCategory, ToolDefinition, ToolSafety, register_tool
from ..config import FEATURES
from ..display import print_subagent_event
from .workspace import AGENT_WORKSPACE

logger = logging.getLogger(__name__)

_SUBAGENT_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "subagent.md"
_SCRATCH_SUBDIR = Path(".alpha") / "runs"

# ─── Sub-agent safety policy (referenciado pelos testes em test_subagent_blocked.py) ───
#
# DESTRUCTIVE tools que sao removidas do toolset do sub-agent quando nao
# existe approval callback do parent. Cobre:
# - shell/pipeline/http/db/clipboard/install: side effects fora do workspace
# - browser_*: JS arbitrario, click/fill em sessao logada (cookie/form exfil)
#
# Nao listadas aqui (mas tambem DESTRUCTIVE):
# - write_file, edit_file, execute_python, search_and_replace, run_tests:
#   auto-aprovadas por politica geral (AUTO_APPROVE_TOOLS) — comportamento
#   intencional do system.md
# - delegate_task, delegate_parallel: bloqueadas separadamente para evitar
#   recursao
# - present_plan: ferramenta de planejamento, nao tem efeito real
# - git_operation: gating dinamico via _auto_approve_no_callback abaixo
SUBAGENT_DESTRUCTIVE_BLOCKLIST = frozenset({
    "execute_shell", "execute_pipeline", "http_request",
    "query_database", "clipboard_read", "clipboard_write", "install_package",
    "browser_click", "browser_fill", "browser_select_option",
    "browser_press_key", "browser_execute_js",
    # apify_run_actor executa actor arbitrario com input arbitrario —
    # vetor de exfil via actors maliciosos. Sub-agent sem callback nao
    # pode chamar (#034).
    "apify_run_actor",
})

# Read-only git actions que sub-agents podem chamar sem callback.
# Write actions (push/merge/rebase/reset/clean/...) sao rejeitadas.
GIT_READ_ACTIONS = frozenset({
    "status", "diff", "log", "branch", "show", "blame",
    "stash_list", "remote", "tag",
})


def _auto_approve_no_callback(name: str, args: dict) -> bool:
    """Approval default quando sub-agent nao tem callback humano.

    Aprova qualquer tool por default (ja que tools perigosas estao removidas
    via SUBAGENT_DESTRUCTIVE_BLOCKLIST), exceto git_operation onde precisamos
    distinguir read de write actions.
    """
    if name == "git_operation":
        return (args or {}).get("action") in GIT_READ_ACTIONS
    return True


def _load_subagent_prompt() -> str:
    if _SUBAGENT_PROMPT_PATH.exists():
        raw = _SUBAGENT_PROMPT_PATH.read_text(encoding="utf-8")
        return _strip_control_chars(raw)
    return "You are a focused sub-agent. Complete the delegated task using your tools."


def _strip_control_chars(text: str) -> str:
    """Remove control chars que sequestrariam o prompt do sub-agent.

    Cobre NUL (`\\x00`), ANSI escape (`\\x1b`), e Unicode bidi overrides
    (RLO/LRO/RLI/LRI/PDI). Sem isso, um arquivo subagent.md modificado
    por atacante poderia esconder instrucoes via reordering visual ou
    quebrar prompts via NUL byte.
    """
    # ASCII control: tudo abaixo de 0x20 exceto \t \n \r
    forbidden = set(chr(c) for c in range(32) if c not in (9, 10, 13))
    forbidden |= {"\x7f"}
    # Unicode bidi/format overrides
    forbidden |= {
        "‪", "‫", "‬", "‭", "‮",  # LRE/RLE/PDF/LRO/RLO
        "⁦", "⁧", "⁨", "⁩",            # LRI/RLI/FSI/PDI
        "‎", "‏",                                # LRM/RLM
    }
    return "".join(c for c in text if c not in forbidden)


def _new_agent_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"


def _create_scratch_dir(parent_workspace: str, agent_id: str) -> Path:
    # exist_ok=False — a same-id collision means two agents would share state;
    # fail loudly instead of silently merging.
    scratch = Path(parent_workspace) / _SCRATCH_SUBDIR / agent_id
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(exist_ok=False)
    return scratch


def _snapshot_dir(path: Path) -> list[str]:
    if not path.exists():
        return []
    files = []
    for p in path.rglob("*"):
        if p.is_file():
            try:
                p.stat()
            except OSError:
                continue
            files.append(str(p.relative_to(path)))
    return sorted(files)

