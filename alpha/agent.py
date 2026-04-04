"""
Core agent loop for Alpha Code.

Simplified autonomous engine: LLM call -> tool detection -> approval -> execution.
No perception, planning, delegation, or self-reflection phases.
"""

import json
import logging
from collections.abc import AsyncGenerator

from .approval import needs_approval
from .config import MAX_ITERATIONS
from .executor import build_assistant_tool_message, execute_tool_calls
from .llm import stream_chat_with_tools

logger = logging.getLogger(__name__)

# Loop detection: break if same call repeated N times
_MAX_REPEAT_CALLS = 3


async def run_agent(
    messages: list[dict],
    user_message: str,
    temperature: float = 0.5,
    provider: str = "deepseek",
    get_tool_fn=None,
    tools: list[dict] | None = None,
    approval_callback=None,
) -> AsyncGenerator[dict, None]:
    """
    Run the agent loop. Async generator yielding display events.

    Args:
        messages: Full conversation messages (system + history + new user msg).
        user_message: The current user message text.
        temperature: LLM temperature.
        provider: LLM provider name.
        get_tool_fn: Function(name) -> ToolDefinition for looking up tools.
        tools: OpenAI-format tool definitions list.
        approval_callback: Sync function(tool_name, args) -> bool for approval.

    Yields:
        {"type": "token", "text": "..."}
        {"type": "tool_call", "name": ..., "args": ...}
        {"type": "tool_result", "name": ..., "result": ...}
        {"type": "approval_needed", "name": ..., "args": ...}
        {"type": "done", "reply": "full text"}
        {"type": "error", "message": "..."}
    """
    if tools is None:
        tools = []

    full_response = ""

    # Track repeated tool calls to detect infinite loops
    _recent_calls: list[str] = []

    for iteration in range(MAX_ITERATIONS):
        logger.info(f"Agent iteration {iteration + 1}/{MAX_ITERATIONS}")

        # Truncate old tool results every 5 iterations to prevent unbounded growth
        if iteration > 0 and iteration % 5 == 0:
            cutoff = len(messages) - 6
            for i, msg in enumerate(messages):
                if i >= cutoff:
                    break
                if msg.get("role") == "tool" and len(msg.get("content", "")) > 500:
                    msg["content"] = msg["content"][:500] + "\n... [truncated]"

        # Stream LLM call
        final_event = None
        async for event in stream_chat_with_tools(
            messages, tools, temperature, provider=provider
        ):
            if event["type"] == "content_token":
                yield {"type": "token", "text": event["token"]}
            elif event["type"] == "final":
                final_event = event

        if final_event is None:
            yield {"type": "error", "message": "No response from LLM"}
            return

        # LLM error
        if final_event.get("error"):
            yield {"type": "error", "message": final_event["error"]}
            return

        # Accumulate text
        if final_event.get("content"):
            full_response += final_event["content"]

        # No tool calls = final text response
        if not final_event.get("tool_calls"):
            yield {"type": "done", "reply": full_response}
            return

        # ── Loop detection ──
        call_sigs = [
            f"{tc['name']}:{tc['arguments']}" for tc in final_event["tool_calls"]
        ]
        for sig in call_sigs:
            _recent_calls.append(sig)
        if len(_recent_calls) > 50:
            _recent_calls[:] = _recent_calls[-50:]

        # Check if same call appears N times in recent history
        loop_detected = False
        if len(_recent_calls) >= _MAX_REPEAT_CALLS:
            for sig in call_sigs:
                if _recent_calls.count(sig) >= _MAX_REPEAT_CALLS:
                    logger.warning(
                        f"Loop detected: '{sig[:80]}' called {_MAX_REPEAT_CALLS}x "
                        f"— forcing text response at iteration {iteration + 1}"
                    )
                    loop_detected = True
                    break

        if loop_detected:
            # Inject nudge and do one more LLM call without tools
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "ATTENTION: You have called the same tool multiple times with the same "
                        "arguments. STOP calling tools and produce your final response now "
                        "based on the data already collected. Synthesize ALL information from "
                        "previous calls into a complete response."
                    ),
                }
            )
            async for event in stream_chat_with_tools(
                messages, [], temperature, provider=provider
            ):
                if event["type"] == "content_token":
                    yield {"type": "token", "text": event["token"]}
                elif event["type"] == "final":
                    full_response = event.get("content", "")

            yield {"type": "done", "reply": full_response}
            return

        # Process tool calls
        messages.append(
            build_assistant_tool_message(
                final_event["content"], final_event["tool_calls"]
            )
        )

        async for event in execute_tool_calls(
            final_event["tool_calls"],
            messages,
            needs_approval_fn=needs_approval,
            approval_callback=approval_callback,
            get_tool_fn=get_tool_fn,
        ):
            yield event

    # Max iterations reached
    yield {
        "type": "token",
        "text": "\n\n[Maximum iterations reached]",
    }
    yield {"type": "done", "reply": full_response or "[Max iterations reached]"}
