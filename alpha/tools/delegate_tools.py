"""Delegate tools — spawn sub-agents to handle tasks independently.

Supports single delegation (delegate_task) and parallel delegation
(delegate_parallel) with concurrency limited by max_parallel_agents.

Apos #082 split: helpers extraidos para _delegate_core.py.
"""

from . import ToolDefinition, ToolSafety, register_tool

from ._delegate_core import (
    SUBAGENT_DESTRUCTIVE_BLOCKLIST,
    GIT_READ_ACTIONS,
    _auto_approve_no_callback,
    _load_subagent_prompt,
    _new_agent_id,
    _create_scratch_dir,
    _snapshot_dir,
)



async def _run_subagent(
    task: str,
    context: str = "",
    tools_filter: str = "",
    provider: str = "",
    label: str = "",
    stream_to_parent: bool = True,
    parent_approval_callback=None,
    parent_workspace: str | None = None,
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
    from ..config import (
        DEFAULT_PROVIDER,
        FEATURES as feat,
        LIMITS,
        get_subagent_allow,
        get_subagent_extra_block,
        get_subagent_policy,
    )
    from . import get_openai_tools, get_tool

    max_iterations = LIMITS.get("subagent_max_iterations", 15)
    agent_provider = provider or DEFAULT_PROVIDER
    workspace_root = parent_workspace or str(AGENT_WORKSPACE)

    agent_id = _new_agent_id()
    try:
        scratch_dir = _create_scratch_dir(workspace_root, agent_id)
    except OSError as e:
        return {"ok": False, "category": "io_error", "error": f"Cannot create scratch dir for sub-agent: {e}"}

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
        task_content += f"Context: {_strip_control_chars(context)}\n\n"
    task_content += _strip_control_chars(task)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_content},
    ]

    # Get tools — filter out delegate tools to prevent recursion
    # Also block destructive tools that could bypass approval if no callback.
    # Policy lives in SUBAGENT_DESTRUCTIVE_BLOCKLIST (module level) so tests
    # can validate the surface independently of an actual run.
    #
    # #D007 (V1.0): policy + extra_block + allow vem de env via getters
    # (AUDIT_V1.2 #014: cache de import-time perdia mudancas runtime).
    # - "strict" (default): bloqueia destructive em modo no-callback (comportamento antigo)
    # - "relaxed": confia no sub-agent, so anti-recursao
    # - extra_block: usuario pode fortalecer
    # - allow: usuario pode aliviar (sobrepoe blocklist)
    _blocked = {"delegate_task", "delegate_parallel"}
    all_tools = get_openai_tools()
    policy = get_subagent_policy()
    if parent_approval_callback is None and policy != "relaxed":
        _blocked = _blocked | SUBAGENT_DESTRUCTIVE_BLOCKLIST
    _blocked = _blocked | get_subagent_extra_block()
    _blocked = _blocked - get_subagent_allow()
    # `delegate_*` continua bloqueado mesmo se usuario incluir em
    # subagent_allow — anti-recursao e invariante, nao policy.
    _blocked = _blocked | {"delegate_task", "delegate_parallel"}
    tools = [t for t in all_tools if t["function"]["name"] not in _blocked]

    if tools_filter:
        allowed = {s.strip() for s in tools_filter.split(",")}
        tools = [t for t in tools if t["function"]["name"] in allowed]
    else:
        allowed = None

    # Safe get_tool wrapper: aplica a politica de blocklist montada acima
    # (delegate_* anti-recursao + SUBAGENT_DESTRUCTIVE_BLOCKLIST quando
    # nao ha approval callback do parent + filtro tools_filter quando
    # explicito). Centraliza o gate para que `run_agent` interno nao
    # consiga "burlar" via lookup direto no TOOL_REGISTRY (#091).
    original_get_tool = get_tool
    _all_blocked = _blocked
    allowed_filter = allowed

    def _safe_get_tool(name: str):
        if name in _all_blocked:
            return None
        if allowed_filter is not None and name not in allowed_filter:
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
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # #056: log full traceback (logger.error sem exc_info perdia o
        # frame onde o bug realmente aconteceu).
        logger.error(f"Sub-agent {agent_id} failed: {e}", exc_info=True)
        return {
            "ok": False,
            "category": "subagent_error",
            "error": f"Sub-agent execution failed: {type(e).__name__}: {e}",
            "agent_id": agent_id,
        }
    finally:
        # Cleanup runs on every exit path (Exception, CancelledError,
        # KeyboardInterrupt). Empty-dir guard: never remove a scratch dir
        # the sub-agent may have written artifacts into.
        try:
            if scratch_dir.exists() and not any(scratch_dir.iterdir()):
                scratch_dir.rmdir()
        except OSError:
            pass

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
        return {"ok": False, "category": "feature_disabled", "error": "Multi-agent system is disabled. Set FEATURES['multi_agent_enabled']=True."}
    if not FEATURES.get("delegate_tool_enabled"):
        return {"ok": False, "category": "feature_disabled", "error": "Delegate tool is disabled. Enable 'delegate_tool_enabled' in config."}
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
        return {"ok": False, "category": "feature_disabled", "error": "Multi-agent system is disabled. Set FEATURES['multi_agent_enabled']=True."}
    if not FEATURES.get("delegate_tool_enabled"):
        return {"ok": False, "category": "feature_disabled", "error": "Delegate tool is disabled. Enable 'delegate_tool_enabled' in config."}

    # Parse tasks JSON array
    try:
        task_list = json.loads(tasks)
        if not isinstance(task_list, list) or not task_list:
            return {"ok": False, "category": "invalid_args", "error": "tasks must be a non-empty JSON array of strings"}
    except json.JSONDecodeError as e:
        return {"ok": False, "category": "invalid_args", "error": f"Invalid JSON in tasks: {e}"}

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
