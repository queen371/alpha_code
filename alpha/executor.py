"""
Tool executor for Alpha Code.

Executes tool calls with parallel support, handles approval flow, formats results.
When multiple tools are called in the same turn, independent tools run in parallel.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable

from .config import TOOL_RESULT_MAX_CHARS

logger = logging.getLogger(__name__)

TOOL_EXECUTION_TIMEOUT = 120  # seconds per individual tool
_SLOW_TOOL_TIMEOUT = 300  # 5 minutes for investigation pipelines
_SLOW_TOOLS = frozenset({
    "investigate_person", "deploy_check", "run_tests",
    "delegate_task", "delegate_parallel",
    "browser_open", "browser_navigate", "browser_get_content",
    "browser_describe_page", "browser_screenshot",
    "browser_click", "browser_fill", "browser_wait_for",
    "browser_new_tab",
})


def build_assistant_tool_message(
    content: str | None, tool_calls: list[dict]
) -> dict:
    """Build an OpenAI-compatible assistant message containing tool_calls."""
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                },
            }
            for tc in tool_calls
        ],
    }


def _format_result(result: dict, tool_name: str) -> str:
    """Truncate and format a tool result for inclusion in messages."""
    result_str = json.dumps(result, ensure_ascii=False)
    if len(result_str) > TOOL_RESULT_MAX_CHARS:
        result_str = json.dumps(
            {
                "truncated": True,
                "partial": result_str[: TOOL_RESULT_MAX_CHARS - 100],
                "message": (
                    f"Result truncated from {len(result_str)} "
                    f"to {TOOL_RESULT_MAX_CHARS} chars"
                ),
            },
            ensure_ascii=False,
        )
    return result_str


async def _execute_single_tool(tool_def, tool_name: str, args: dict) -> dict:
    """Execute a single tool with timeout. Returns result dict."""
    tool_timeout = (
        _SLOW_TOOL_TIMEOUT if tool_name in _SLOW_TOOLS else TOOL_EXECUTION_TIMEOUT
    )
    try:
        result = await asyncio.wait_for(
            tool_def.executor(**args),
            timeout=tool_timeout,
        )
    except TimeoutError:
        logger.error(f"Tool timeout ({tool_name}): {tool_timeout}s")
        result = {"error": f"Execution timed out ({tool_timeout}s)"}
    except Exception as e:
        logger.error(f"Tool error ({tool_name}): {type(e).__name__}: {e}")
        result = {"error": f"{type(e).__name__}: {e}"}
    return result


async def execute_tool_calls(
    tool_calls: list[dict],
    messages: list[dict],
    needs_approval_fn: Callable[[str, dict], bool],
    approval_callback: Callable[[str, dict], bool] | None = None,
    get_tool_fn: Callable | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Execute tool calls, yielding events for each step.

    When multiple tools are called, auto-approved tools run in PARALLEL.
    Approval-requiring tools are handled sequentially first.

    Args:
        tool_calls: List of {id, name, arguments} dicts from the LLM.
        messages: Conversation messages list (mutated in-place with tool results).
        needs_approval_fn: Function(tool_name, args) -> bool.
        approval_callback: Sync function(tool_name, args) -> bool for user approval.
        get_tool_fn: Function(name) -> tool_definition.

    Yields:
        {"type": "tool_call", "name": ..., "args": ..., "safety": ...}
        {"type": "approval_needed", "name": ..., "args": ...}
        {"type": "tool_result", "name": ..., "result": ...}
    """
    # Single tool call — fast path (no parallelization overhead)
    if len(tool_calls) == 1:
        async for event in _execute_sequential(
            tool_calls, messages, needs_approval_fn, approval_callback, get_tool_fn
        ):
            yield event
        return

    # Multiple tool calls — pre-process, then run approved ones in parallel
    prepared = []  # list of (tc, tool_name, args, tool_def, safety_str)

    for tc in tool_calls:
        tool_name = tc["name"]
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {}

        tool_def = get_tool_fn(tool_name) if get_tool_fn else None

        if tool_def is None:
            # Unknown tool — handle immediately
            result = {"error": f"Unknown tool: {tool_name}"}
            yield {"type": "tool_call", "name": tool_name, "args": args, "safety": "unknown"}
            yield {"type": "tool_result", "name": tool_name, "result": result}
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
            continue

        safety = getattr(tool_def, "safety", None)
        safety_str = safety.value if hasattr(safety, "value") else "safe"
        prepared.append((tc, tool_name, args, tool_def, safety_str))

    # Phase 1: Yield all tool_call events
    for tc, tool_name, args, tool_def, safety_str in prepared:
        yield {"type": "tool_call", "name": tool_name, "args": args, "safety": safety_str}

    # Phase 2: Handle approvals (sequential — needs user input)
    approved = []  # (tc, tool_name, args, tool_def)
    for tc, tool_name, args, tool_def, safety_str in prepared:
        if needs_approval_fn(tool_name, args):
            yield {"type": "approval_needed", "name": tool_name, "args": args}

            user_approved = False
            if approval_callback:
                user_approved = await asyncio.to_thread(approval_callback, tool_name, args)

            if not user_approved:
                result = {"skipped": True, "reason": "User denied this action"}
                yield {"type": "tool_result", "name": tool_name, "result": result, "denied": True}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })
                continue

        approved.append((tc, tool_name, args, tool_def))

    if not approved:
        return

    # Phase 3: Execute approved tools in PARALLEL
    async def _run(item):
        tc, tool_name, args, tool_def = item
        result = await _execute_single_tool(tool_def, tool_name, args)
        return tc, tool_name, result

    results = await asyncio.gather(*[_run(item) for item in approved], return_exceptions=True)

    # Phase 4: Yield results and append to messages (in original order)
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            tc, tool_name, args, tool_def = approved[i]
            logger.error(f"Parallel tool execution error ({tool_name}): {type(r).__name__}: {r}")
            result = {"error": f"{type(r).__name__}: {r}"}
            yield {"type": "tool_result", "name": tool_name, "result": result}
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
            continue

        tc, tool_name, result = r
        yield {"type": "tool_result", "name": tool_name, "result": result}

        result_str = _format_result(result, tool_name)
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result_str,
        })


async def _execute_sequential(
    tool_calls: list[dict],
    messages: list[dict],
    needs_approval_fn: Callable[[str, dict], bool],
    approval_callback: Callable[[str, dict], bool] | None = None,
    get_tool_fn: Callable | None = None,
) -> AsyncGenerator[dict, None]:
    """Original sequential execution for single tool calls."""
    for tc in tool_calls:
        tool_name = tc["name"]

        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {}

        tool_def = None
        if get_tool_fn:
            tool_def = get_tool_fn(tool_name)

        if tool_def is None:
            result = {"error": f"Unknown tool: {tool_name}"}
            yield {"type": "tool_call", "name": tool_name, "args": args, "safety": "unknown"}
            yield {"type": "tool_result", "name": tool_name, "result": result}
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
            continue

        safety = getattr(tool_def, "safety", None)
        safety_str = safety.value if hasattr(safety, "value") else "safe"

        yield {"type": "tool_call", "name": tool_name, "args": args, "safety": safety_str}

        # Approval gate
        if needs_approval_fn(tool_name, args):
            yield {"type": "approval_needed", "name": tool_name, "args": args}

            approved = False
            if approval_callback:
                approved = await asyncio.to_thread(approval_callback, tool_name, args)

            if not approved:
                result = {"skipped": True, "reason": "User denied this action"}
                yield {"type": "tool_result", "name": tool_name, "result": result, "denied": True}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })
                continue

        result = await _execute_single_tool(tool_def, tool_name, args)
        yield {"type": "tool_result", "name": tool_name, "result": result}

        result_str = _format_result(result, tool_name)
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result_str,
        })
