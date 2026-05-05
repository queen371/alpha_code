"""
Tool executor for Alpha Code.

Executes tool calls with parallel support, handles approval flow, formats results.
When multiple tools are called in the same turn, independent tools run in parallel.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable

from . import hooks
from .config import TOOL_RESULT_MAX_CHARS

logger = logging.getLogger(__name__)

TOOL_EXECUTION_TIMEOUT = 120  # seconds per individual tool
_SLOW_TOOL_TIMEOUT = 300  # 5 minutes for investigation pipelines
_SLOW_TOOLS = frozenset({
    "investigate_person", "deploy_check", "run_tests",
    "delegate_task", "delegate_parallel",
    "execute_shell", "execute_pipeline",
    "browser_open", "browser_navigate", "browser_get_content",
    "browser_describe_page", "browser_screenshot",
    "browser_click", "browser_fill", "browser_wait_for",
    "browser_new_tab",
})


def _no_deny(_tool: str, _args: dict) -> tuple[bool, str]:
    return False, ""


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


def _record_skip(tc: dict, tool_name: str, result: dict, messages: list[dict]) -> dict:
    """Append a denied/skipped result to messages and return the event dict."""
    messages.append({
        "role": "tool",
        "tool_call_id": tc["id"],
        "content": json.dumps(result, ensure_ascii=False),
    })
    return {"type": "tool_result", "name": tool_name, "result": result, "denied": True}


def _record_result(tc: dict, tool_name: str, result: dict, messages: list[dict]) -> None:
    messages.append({
        "role": "tool",
        "tool_call_id": tc["id"],
        "content": _format_result(result, tool_name),
    })


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


async def _fire_pre_tool(
    tool_name: str, args: dict, workspace: str | None
) -> hooks.HookOutcome:
    # Fast path: skip the thread-hop entirely when no hooks are configured.
    if not hooks.has_event("pre_tool"):
        return hooks.HookOutcome()
    return await asyncio.to_thread(
        hooks.fire,
        "pre_tool",
        tool_name=tool_name,
        tool_args=args,
        workspace=workspace,
    )


async def _fire_post_tool(
    tool_name: str, args: dict, result: dict, workspace: str | None
) -> None:
    if not hooks.has_event("post_tool"):
        return
    await asyncio.to_thread(
        hooks.fire,
        "post_tool",
        tool_name=tool_name,
        tool_args=args,
        workspace=workspace,
        extra={"tool_result": result},
    )


def _enforce_workspace(
    workspace: str | None, tool_name: str, args: dict
) -> tuple[bool, dict, str]:
    """Apply workspace validation to args. Returns (ok, new_args, error_msg)."""
    if not workspace:
        return True, args, ""
    from .agents import validate_workspace_args

    return validate_workspace_args(workspace, tool_name, args)


async def execute_tool_calls(
    tool_calls: list[dict],
    messages: list[dict],
    needs_approval_fn: Callable[[str, dict], bool],
    approval_callback: Callable[[str, dict], bool] | None = None,
    get_tool_fn: Callable | None = None,
    workspace: str | None = None,
    is_denied_fn: Callable[[str, dict], tuple[bool, str]] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Execute tool calls, yielding events for each step.

    When multiple tools are called, auto-approved tools run in PARALLEL.
    Approval-requiring tools are handled sequentially first.

    Yields:
        {"type": "tool_call", ...}
        {"type": "approval_needed", ...}
        {"type": "tool_result", ...}
    """
    is_denied_fn = is_denied_fn or _no_deny

    if len(tool_calls) == 1:
        async for event in _execute_sequential(
            tool_calls, messages, needs_approval_fn, approval_callback, get_tool_fn,
            workspace=workspace, is_denied_fn=is_denied_fn,
        ):
            yield event
        return

    prepared = []  # (tc, tool_name, args, tool_def, safety_str)

    for tc in tool_calls:
        tool_name = tc["name"]
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {}

        tool_def = get_tool_fn(tool_name) if get_tool_fn else None

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

        ok, args, err = _enforce_workspace(workspace, tool_name, args)
        if not ok:
            result = {"error": err, "workspace_violation": True}
            yield {"type": "tool_call", "name": tool_name, "args": args, "safety": "denied"}
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

    for tc, tool_name, args, tool_def, safety_str in prepared:
        yield {"type": "tool_call", "name": tool_name, "args": args, "safety": safety_str}

    approved = []
    for tc, tool_name, args, tool_def, _ in prepared:
        denied, reason = is_denied_fn(tool_name, args)
        if denied:
            result = {"skipped": True, "reason": reason, "denied_by_rule": True}
            yield _record_skip(tc, tool_name, result, messages)
            continue

        if needs_approval_fn(tool_name, args):
            yield {"type": "approval_needed", "name": tool_name, "args": args}
            user_approved = (
                await asyncio.to_thread(approval_callback, tool_name, args)
                if approval_callback else False
            )
            if not user_approved:
                result = {"skipped": True, "reason": "User denied this action"}
                yield _record_skip(tc, tool_name, result, messages)
                continue

        approved.append((tc, tool_name, args, tool_def))

    if not approved:
        return

    # pre_tool hooks fire in parallel — they're independent shell processes.
    outcomes = await asyncio.gather(
        *[_fire_pre_tool(name, args, workspace) for _, name, args, _ in approved]
    )
    runnable = []
    for (tc, tool_name, args, tool_def), outcome in zip(approved, outcomes):
        if outcome.blocked:
            result = {
                "skipped": True,
                "reason": f"Hook blocked: {outcome.block_reason}",
                "hook_blocked": True,
            }
            yield _record_skip(tc, tool_name, result, messages)
            continue
        runnable.append((tc, tool_name, args, tool_def))

    if not runnable:
        return

    async def _run(item):
        tc, tool_name, args, tool_def = item
        result = await _execute_single_tool(tool_def, tool_name, args)
        return tc, tool_name, args, result

    results = await asyncio.gather(*[_run(item) for item in runnable], return_exceptions=True)

    post_tasks = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            tc, tool_name, args, _ = runnable[i]
            logger.error(f"Parallel tool execution error ({tool_name}): {type(r).__name__}: {r}")
            result = {"error": f"{type(r).__name__}: {r}"}
            yield {"type": "tool_result", "name": tool_name, "result": result}
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
            continue

        tc, tool_name, args, result = r
        post_tasks.append(_fire_post_tool(tool_name, args, result, workspace))
        yield {"type": "tool_result", "name": tool_name, "result": result}
        _record_result(tc, tool_name, result, messages)

    if post_tasks:
        await asyncio.gather(*post_tasks, return_exceptions=True)


async def _execute_sequential(
    tool_calls: list[dict],
    messages: list[dict],
    needs_approval_fn: Callable[[str, dict], bool],
    approval_callback: Callable[[str, dict], bool] | None = None,
    get_tool_fn: Callable | None = None,
    workspace: str | None = None,
    is_denied_fn: Callable[[str, dict], tuple[bool, str]] = _no_deny,
) -> AsyncGenerator[dict, None]:
    for tc in tool_calls:
        tool_name = tc["name"]

        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {}

        tool_def = get_tool_fn(tool_name) if get_tool_fn else None

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

        ok, args, err = _enforce_workspace(workspace, tool_name, args)
        if not ok:
            result = {"error": err, "workspace_violation": True}
            yield {"type": "tool_call", "name": tool_name, "args": args, "safety": "denied"}
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

        denied, reason = is_denied_fn(tool_name, args)
        if denied:
            result = {"skipped": True, "reason": reason, "denied_by_rule": True}
            yield _record_skip(tc, tool_name, result, messages)
            continue

        if needs_approval_fn(tool_name, args):
            yield {"type": "approval_needed", "name": tool_name, "args": args}
            approved = (
                await asyncio.to_thread(approval_callback, tool_name, args)
                if approval_callback else False
            )
            if not approved:
                result = {"skipped": True, "reason": "User denied this action"}
                yield _record_skip(tc, tool_name, result, messages)
                continue

        outcome = await _fire_pre_tool(tool_name, args, workspace)
        if outcome.blocked:
            result = {
                "skipped": True,
                "reason": f"Hook blocked: {outcome.block_reason}",
                "hook_blocked": True,
            }
            yield _record_skip(tc, tool_name, result, messages)
            continue

        result = await _execute_single_tool(tool_def, tool_name, args)
        await _fire_post_tool(tool_name, args, result, workspace)
        yield {"type": "tool_result", "name": tool_name, "result": result}
        _record_result(tc, tool_name, result, messages)
