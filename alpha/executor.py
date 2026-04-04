"""
Tool executor for Alpha Code.

Executes tool calls, handles approval flow, formats results.
Simplified from CORA34's tool_executor.py — sequential only, no audit logging.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable

from .config import TOOL_RESULT_MAX_CHARS

logger = logging.getLogger(__name__)

TOOL_EXECUTION_TIMEOUT = 120  # seconds per individual tool
_SLOW_TOOL_TIMEOUT = 300  # 5 minutes for investigation pipelines
_SLOW_TOOLS = frozenset({"investigate_person", "deploy_check", "run_tests"})


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


async def execute_tool_calls(
    tool_calls: list[dict],
    messages: list[dict],
    needs_approval_fn: Callable[[str, dict], bool],
    approval_callback: Callable[[str, dict], bool] | None = None,
    get_tool_fn: Callable | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Execute tool calls sequentially, yielding events for each step.

    Args:
        tool_calls: List of {id, name, arguments} dicts from the LLM.
        messages: Conversation messages list (mutated in-place with tool results).
        needs_approval_fn: Function(tool_name, args) -> bool.
        approval_callback: Sync function(tool_name, args) -> bool for user approval.
            If None, destructive tools are auto-rejected.
        get_tool_fn: Function(name) -> tool_definition. Must have .executor attribute.

    Yields:
        {"type": "tool_call", "name": ..., "args": ..., "safety": ...}
        {"type": "approval_needed", "name": ..., "args": ...}
        {"type": "tool_result", "name": ..., "result": ...}
    """
    for tc in tool_calls:
        tool_name = tc["name"]

        # Parse arguments
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {}

        # Look up tool
        tool_def = None
        if get_tool_fn:
            tool_def = get_tool_fn(tool_name)

        if tool_def is None:
            result = {"error": f"Unknown tool: {tool_name}"}
            yield {
                "type": "tool_call",
                "name": tool_name,
                "args": args,
                "safety": "unknown",
            }
            yield {"type": "tool_result", "name": tool_name, "result": result}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            continue

        # Determine safety level
        safety = getattr(tool_def, "safety", None)
        safety_str = safety.value if hasattr(safety, "value") else "safe"

        yield {
            "type": "tool_call",
            "name": tool_name,
            "args": args,
            "safety": safety_str,
        }

        # Approval gate
        if needs_approval_fn(tool_name, args):
            yield {
                "type": "approval_needed",
                "name": tool_name,
                "args": args,
            }

            approved = False
            if approval_callback:
                # Run sync callback in thread to avoid blocking the event loop
                approved = await asyncio.to_thread(
                    approval_callback, tool_name, args
                )

            if not approved:
                result = {"skipped": True, "reason": "User denied this action"}
                yield {
                    "type": "tool_result",
                    "name": tool_name,
                    "result": result,
                    "denied": True,
                }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                continue

        # Execute tool with timeout
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
        except (RuntimeError, ValueError, TypeError, KeyError, OSError) as e:
            logger.error(f"Tool error ({tool_name}): {e}")
            result = {"error": str(e)}

        yield {"type": "tool_result", "name": tool_name, "result": result}

        # Add tool result to messages (truncated)
        result_str = _format_result(result, tool_name)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_str,
            }
        )
