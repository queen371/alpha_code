"""
Delegate tools — spawn sub-agents to handle tasks independently.

Supports single delegation (delegate_task) and parallel delegation
(delegate_parallel) with concurrency limited by max_parallel_agents.
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from . import ToolDefinition, ToolSafety, register_tool
from ..config import FEATURES
from ..display import print_subagent_event

logger = logging.getLogger(__name__)

_SUBAGENT_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "subagent.md"


def _load_subagent_prompt() -> str:
    if _SUBAGENT_PROMPT_PATH.exists():
        return _SUBAGENT_PROMPT_PATH.read_text(encoding="utf-8")
    return "You are a focused sub-agent. Complete the delegated task using your tools."


async def _run_subagent(
    task: str,
    context: str = "",
    tools_filter: str = "",
    provider: str = "",
    label: str = "",
    stream_to_parent: bool = True,
    parent_approval_callback=None,
    parent_messages: list[dict] | None = None,
) -> dict:
    """
    Core sub-agent runner with isolated context.

    Sub-agents receive only the task-relevant context, not the full parent
    conversation. This protects the parent's context window and focuses
    the sub-agent on its specific task.

    If parent_messages is provided, a compact summary of relevant context
    is extracted and injected into the sub-agent's system prompt.
    """

    # Lazy imports to avoid circular dependencies
    from ..agent import run_agent
    from ..config import DEFAULT_PROVIDER, FEATURES as feat
    from . import get_openai_tools, get_tool

    max_iterations = feat.get("subagent_max_iterations", 15)
    agent_provider = provider or DEFAULT_PROVIDER
    cwd = os.getcwd()

    # Build isolated context for the sub-agent
    system_prompt = _load_subagent_prompt()

    # Extract relevant context from parent conversation if available
    parent_context = ""
    if parent_messages:
        parent_context = _extract_relevant_context(parent_messages, task)

    task_content = f"[CWD: {cwd}]\n"
    if parent_context:
        task_content += (
            f"[CONTEXT FROM PARENT AGENT]\n{parent_context}\n"
            "[END PARENT CONTEXT]\n\n"
        )
    if context:
        task_content += f"Context: {context}\n\n"
    task_content += task

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_content},
    ]

    # Get tools — filter out delegate tools to prevent recursion
    # Also block destructive tools that could bypass approval if no callback
    _blocked = {"delegate_task", "delegate_parallel"}
    _destructive_without_approval = {
        "execute_shell", "execute_pipeline", "http_request",
        "query_database", "clipboard_write", "install_package",
    }
    all_tools = get_openai_tools()
    if parent_approval_callback is None:
        # No approval callback = read-only mode for sub-agents
        _blocked = _blocked | _destructive_without_approval
    tools = [t for t in all_tools if t["function"]["name"] not in _blocked]

    if tools_filter:
        allowed = {s.strip() for s in tools_filter.split(",")}
        tools = [t for t in tools if t["function"]["name"] in allowed]

    # Safe get_tool that blocks disallowed tools
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

    # Sub-agents use parent's approval callback if available,
    # otherwise auto-approve only safe tools (destructive ones are blocked above)
    effective_approval = parent_approval_callback or (lambda name, args: True)

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
        logger.error(f"Sub-agent failed: {e}")
        return {"error": f"Sub-agent execution failed: {e}"}

    # Return a concise summary instead of raw collected text
    result = {
        "status": "completed",
        "result": collected_text,
        "tools_used": tool_calls_made,
        "iterations": len(tool_calls_made),
    }
    if errors:
        result["errors"] = errors

    return result


def _extract_relevant_context(
    parent_messages: list[dict], task: str, max_chars: int = 2000
) -> str:
    """
    Extract task-relevant context from parent conversation.

    Filters parent messages to include only those relevant to the delegated task.
    Keeps the context compact to avoid polluting the sub-agent's window.
    """
    # Extract keywords from task for relevance matching
    task_lower = task.lower()
    task_words = set(task_lower.split())
    # Remove common stop words
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
                  "to", "for", "of", "and", "or", "not", "this", "that", "with",
                  "o", "a", "os", "as", "de", "do", "da", "em", "no", "na",
                  "um", "uma", "para", "com", "por", "que", "e", "ou", "nao"}
    task_words -= stop_words

    relevant_parts = []
    total_chars = 0

    for msg in parent_messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""

        if role == "system":
            continue

        # Check relevance: does this message contain task-related keywords?
        content_lower = content.lower()
        relevance = sum(1 for w in task_words if w in content_lower)

        if relevance == 0 and role == "tool":
            continue  # Skip irrelevant tool results entirely

        # Include user/assistant messages (they provide conversation flow)
        # and relevant tool results
        if role == "user":
            text = content
            if text.startswith("[CWD:"):
                text = text.split("\n", 1)[-1] if "\n" in text else text
            snippet = f"[user]: {text[:300]}"
        elif role == "assistant":
            if msg.get("tool_calls"):
                tc_names = [
                    tc.get("function", {}).get("name", "?")
                    for tc in msg.get("tool_calls", [])
                ]
                snippet = f"[assistant called: {', '.join(tc_names)}]"
            else:
                snippet = f"[assistant]: {content[:300]}"
        elif role == "tool" and relevance > 0:
            snippet = f"[tool result]: {content[:400]}"
        else:
            continue

        if total_chars + len(snippet) > max_chars:
            break
        relevant_parts.append(snippet)
        total_chars += len(snippet)

    return "\n".join(relevant_parts)


# ── Single delegation ─────────────────────────────────────────

async def _delegate_task(
    task: str,
    context: str = "",
    tools_filter: str = "",
    provider: str = "",
) -> dict:
    """Spawn a single sub-agent to handle a task."""
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
    if not FEATURES.get("delegate_tool_enabled"):
        return {"error": "Delegate tool is disabled. Enable 'delegate_tool_enabled' in config."}

    # Parse tasks JSON array
    try:
        task_list = json.loads(tasks)
        if not isinstance(task_list, list) or not task_list:
            return {"error": "tasks must be a non-empty JSON array of strings"}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in tasks: {e}"}

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
