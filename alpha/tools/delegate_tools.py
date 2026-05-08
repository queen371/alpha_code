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

from . import ToolDefinition, ToolSafety, register_tool
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


async def _run_subagent(
    task: str,
    context: str = "",
    tools_filter: str = "",
    provider: str = "",
    label: str = "",
    stream_to_parent: bool = True,
    parent_approval_callback=None,
) -> dict:
    """
    Core sub-agent runner with isolated context.

    Sub-agents receive only the task and an optional explicit `context`
    string from the caller — never raw parent messages or tool results.
    Esse isolamento e proposital: messages do parent podem conter saida de
    URLs/arquivos controlados por atacante, e injeta-las no prompt do
    sub-agent vira vetor de prompt-injection cross-agent.
    """

    # Lazy imports to avoid circular dependencies
    from ..agent import run_agent
    from ..config import DEFAULT_PROVIDER, FEATURES as feat
    from . import get_openai_tools, get_tool

    max_iterations = feat.get("subagent_max_iterations", 15)
    agent_provider = provider or DEFAULT_PROVIDER
    workspace_root = str(AGENT_WORKSPACE)

    agent_id = _new_agent_id()
    try:
        scratch_dir = _create_scratch_dir(workspace_root, agent_id)
    except OSError as e:
        return {"error": f"Cannot create scratch dir for sub-agent: {e}"}

    # Build isolated context for the sub-agent
    system_prompt = _load_subagent_prompt()

    # Paths relativos no contexto do sub-agent (#022): o workspace absoluto
    # nao precisa estar no prompt — `validate_workspace_args` ja resolve
    # paths relativos contra o workspace real. Vazar o absoluto no
    # `task_content` deixava ele acessivel via tool results e logs, e
    # convidava o modelo a hard-codar paths em vez de manter portabilidade.
    scratch_rel = scratch_dir.relative_to(workspace_root)
    task_content = (
        f"[AGENT_ID: {agent_id}]\n"
        f"[SCRATCH_DIR: {scratch_rel}]  (relative to workspace)\n"
        "Write any artifacts, logs, or intermediate files to SCRATCH_DIR. "
        "You may read anything under the workspace using relative paths.\n\n"
    )
    if context:
        task_content += f"Context: {context}\n\n"
    task_content += task

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_content},
    ]

    # Get tools — filter out delegate tools to prevent recursion
    # Also block destructive tools that could bypass approval if no callback.
    # Policy lives in SUBAGENT_DESTRUCTIVE_BLOCKLIST (module level) so tests
    # can validate the surface independently of an actual run.
    #
    # #D007 (V1.0): policy + extra_block + allow vem de FEATURES (env-driven).
    # - "strict" (default): bloqueia destructive em modo no-callback (comportamento antigo)
    # - "relaxed": confia no sub-agent, so anti-recursao
    # - extra_block: usuario pode fortalecer
    # - allow: usuario pode aliviar (sobrepoe blocklist)
    _blocked = {"delegate_task", "delegate_parallel"}
    all_tools = get_openai_tools()
    policy = feat.get("subagent_policy", "strict")
    if parent_approval_callback is None and policy != "relaxed":
        _blocked = _blocked | SUBAGENT_DESTRUCTIVE_BLOCKLIST
    _blocked = _blocked | feat.get("subagent_extra_block", frozenset())
    _blocked = _blocked - feat.get("subagent_allow", frozenset())
    # `delegate_*` continua bloqueado mesmo se usuario incluir em
    # subagent_allow — anti-recursao e invariante, nao policy.
    _blocked = _blocked | {"delegate_task", "delegate_parallel"}
    tools = [t for t in all_tools if t["function"]["name"] not in _blocked]

    if tools_filter:
        allowed = {s.strip() for s in tools_filter.split(",")}
        tools = [t for t in tools if t["function"]["name"] in allowed]

    # Safe get_tool wrapper: aplica a politica de blocklist montada acima
    # (delegate_* anti-recursao + SUBAGENT_DESTRUCTIVE_BLOCKLIST quando
    # nao ha approval callback do parent + filtro tools_filter quando
    # explicito). Centraliza o gate para que `run_agent` interno nao
    # consiga "burlar" via lookup direto no TOOL_REGISTRY (#091).
    original_get_tool = get_tool
    _all_blocked = _blocked

    def _safe_get_tool(name: str):
        if name in _all_blocked:
            return None
        return original_get_tool(name)

    # Stream events to terminal if interactive
    is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    should_stream = stream_to_parent and is_tty

    # Run sub-agent loop
    collected_text = ""
    tool_calls_made = []
    errors = []

    # Sub-agents use parent's approval callback if available, otherwise the
    # module-level _auto_approve_no_callback gate (handles git_operation
    # read/write distinction).
    effective_approval = parent_approval_callback or _auto_approve_no_callback

    try:
        async for event in run_agent(
            messages=messages,
            user_message=task,
            temperature=0.3,
            provider=agent_provider,
            get_tool_fn=_safe_get_tool,
            tools=tools,
            approval_callback=effective_approval,
            max_iterations=max_iterations,
            workspace=workspace_root,
        ):
            if event["type"] == "token":
                collected_text += event.get("text", "")
            elif event["type"] == "tool_call":
                tool_calls_made.append(event["name"])
                if should_stream:
                    print_subagent_event(event, label)
            elif event["type"] == "done":
                collected_text = event.get("reply", collected_text)
                if should_stream:
                    print_subagent_event(event, label)
            elif event["type"] == "error":
                errors.append(event.get("message", "unknown error"))
    except Exception as e:
        # #056: log full traceback (logger.error sem exc_info perdia o
        # frame onde o bug realmente aconteceu). #061: limpar scratch dir
        # se ficou vazio para nao acumular diretorios orfaos por falha.
        logger.error(f"Sub-agent {agent_id} failed: {e}", exc_info=True)
        try:
            if scratch_dir.exists() and not any(scratch_dir.iterdir()):
                scratch_dir.rmdir()
        except OSError:
            pass
        return {
            "error": f"Sub-agent execution failed: {type(e).__name__}: {e}",
            "agent_id": agent_id,
        }

    scratch_files = await asyncio.to_thread(_snapshot_dir, scratch_dir)

    result = {
        "status": "completed",
        "result": collected_text,
        "tools_used": tool_calls_made,
        "iterations": len(tool_calls_made),
        "agent_id": agent_id,
        "scratch_dir": str(scratch_dir),
        "scratch_files": scratch_files,
    }
    if errors:
        result["errors"] = errors

    return result


# ── Single delegation ─────────────────────────────────────────

async def _delegate_task(
    task: str,
    context: str = "",
    tools_filter: str = "",
    provider: str = "",
) -> dict:
    """Spawn a single sub-agent to handle a task."""
    if not FEATURES.get("multi_agent_enabled"):
        return {"error": "Multi-agent system is disabled. Set FEATURES['multi_agent_enabled']=True."}
    if not FEATURES.get("delegate_tool_enabled"):
        return {"error": "Delegate tool is disabled. Enable 'delegate_tool_enabled' in config."}
    return await _run_subagent(task, context, tools_filter, provider)


# ── Parallel delegation ───────────────────────────────────────

async def _delegate_parallel(
    tasks: str,
    context: str = "",
    tools_filter: str = "",
    provider: str = "",
) -> dict:
    """Spawn multiple sub-agents in parallel, each handling one task."""
    if not FEATURES.get("multi_agent_enabled"):
        return {"error": "Multi-agent system is disabled. Set FEATURES['multi_agent_enabled']=True."}
    if not FEATURES.get("delegate_tool_enabled"):
        return {"error": "Delegate tool is disabled. Enable 'delegate_tool_enabled' in config."}

    # Parse tasks JSON array
    try:
        task_list = json.loads(tasks)
        if not isinstance(task_list, list) or not task_list:
            return {"error": "tasks must be a non-empty JSON array of strings"}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in tasks: {e}"}

    # Cap total: max_parallel_agents so controla concorrencia, nao total.
    # Sem cap, modelo pode submeter array de 100 tarefas — runaway de custo
    # e disco (cada sub-agent gasta 15 iteracoes + scratch dir).
    max_total = FEATURES.get("max_delegate_total_tasks", 10)
    if len(task_list) > max_total:
        return {
            "error": (
                f"Too many tasks ({len(task_list)}). Maximum is {max_total}. "
                "Split into smaller batches or reconsider scope."
            ),
            "submitted": len(task_list),
            "limit": max_total,
        }

    max_parallel = FEATURES.get("max_parallel_agents", 3)
    semaphore = asyncio.Semaphore(max_parallel)

    async def _run_with_limit(idx: int, task_desc: str) -> dict:
        async with semaphore:
            logger.info(f"Sub-agent #{idx + 1} starting: {task_desc[:60]}")
            result = await _run_subagent(
                task_desc, context, tools_filter, provider,
                label=f"#{idx + 1}",
            )
            result["task_index"] = idx
            result["task"] = task_desc
            return result

    # Launch all sub-agents concurrently (limited by semaphore)
    coros = [_run_with_limit(i, t) for i, t in enumerate(task_list)]
    results = await asyncio.gather(*coros, return_exceptions=True)

    # Format results
    formatted = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            formatted.append({
                "task_index": i,
                "task": task_list[i],
                "status": "failed",
                "error": str(r),
            })
        else:
            formatted.append(r)

    succeeded = sum(1 for r in formatted if r.get("status") == "completed")
    failed = len(formatted) - succeeded

    return {
        "total_tasks": len(task_list),
        "succeeded": succeeded,
        "failed": failed,
        "results": formatted,
    }


# ── Registration ──────────────────────────────────────────────

register_tool(
    ToolDefinition(
        name="delegate_task",
        description=(
            "Delegate a task to a sub-agent that runs independently with its own tool loop. "
            "Use for focused investigation tasks that don't need the main conversation context. "
            "The sub-agent has access to read-only and safe tools only (destructive tools are blocked). "
            "Requires user approval. For multiple independent tasks, use delegate_parallel instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Clear description of what the sub-agent should do. "
                        "Be specific — the sub-agent has no context from the current conversation."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": "Optional context (file paths, constraints, what you've tried).",
                    "default": "",
                },
                "tools_filter": {
                    "type": "string",
                    "description": (
                        "Optional comma-separated tool names the sub-agent can use. "
                        "Empty = all tools. Example: 'read_file,search_files,glob_files'"
                    ),
                    "default": "",
                },
                "provider": {
                    "type": "string",
                    "description": "LLM provider override. Defaults to main agent's provider.",
                    "default": "",
                },
            },
            "required": ["task"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        executor=_delegate_task,
        category="agent",
    )
)

register_tool(
    ToolDefinition(
        name="delegate_parallel",
        description=(
            "Run multiple sub-agents in PARALLEL, each handling one independent task. "
            "Much faster than sequential delegate_task calls for independent work. "
            "Concurrency is limited to max_parallel_agents (default: 3). "
            "Example: analyze 3 different modules simultaneously."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "string",
                    "description": (
                        'JSON array of task descriptions. Each task runs in its own sub-agent. '
                        'Example: \'["analyze alpha/agent.py", "analyze alpha/llm.py", "analyze alpha/executor.py"]\''
                    ),
                },
                "context": {
                    "type": "string",
                    "description": "Shared context passed to ALL sub-agents.",
                    "default": "",
                },
                "tools_filter": {
                    "type": "string",
                    "description": "Comma-separated tool names available to ALL sub-agents.",
                    "default": "",
                },
                "provider": {
                    "type": "string",
                    "description": "LLM provider override for all sub-agents.",
                    "default": "",
                },
            },
            "required": ["tasks"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        executor=_delegate_parallel,
        category="agent",
    )
)
