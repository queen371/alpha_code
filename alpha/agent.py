"""
Core agent loop for Alpha Code.

Simplified autonomous engine: LLM call -> tool detection -> approval -> execution.
Includes intelligent context compression, token tracking, and smart loop detection.
"""

import json
import logging
from collections import Counter
from collections.abc import AsyncGenerator
from difflib import SequenceMatcher

from .approval import is_denied, needs_approval
from .config import MAX_ITERATIONS
from .context import (
    compress_until_under_budget,
    estimate_messages_tokens,
    get_context_limit,
    is_context_overflow_error,
    needs_compression,
)
from .executor import build_assistant_tool_message, execute_tool_calls
from .llm import stream_chat_with_tools

logger = logging.getLogger(__name__)

# ─── Loop detection config ───
_MAX_REPEAT_CALLS = 3        # exact same call N times → loop
_SIMILAR_REPEAT_CALLS = 5    # similar calls threshold (higher to avoid false positives)
_SIMILARITY_THRESHOLD = 0.92  # fuzzy match threshold for "similar" calls
_CYCLE_WINDOW = 20            # look-back window for cycle detection
_STALE_WINDOW = 6             # if last N tool calls produced no new info → stale


def _call_signature(tc: dict) -> str:
    """Create a comparable signature from a tool call."""
    return f"{tc['name']}:{tc['arguments']}"


def _parse_args_values(args_str: str) -> list[str]:
    """Extract individual argument values from JSON args for comparison."""
    try:
        args = json.loads(args_str)
        if isinstance(args, dict):
            return [str(v) for v in args.values()]
    except (json.JSONDecodeError, TypeError):
        pass
    return [args_str]


def _are_similar(sig_a: str, sig_b: str) -> bool:
    """Check if two call signatures are similar (same tool, same effective args).

    Compares individual argument values instead of the raw JSON string,
    which avoids false positives from long shared path prefixes.
    """
    name_a, _, args_a = sig_a.partition(":")
    name_b, _, args_b = sig_b.partition(":")
    if name_a != name_b:
        return False
    if args_a == args_b:
        return True

    # Parse and compare individual argument values
    vals_a = _parse_args_values(args_a)
    vals_b = _parse_args_values(args_b)

    if len(vals_a) != len(vals_b):
        return False

    # All values must be similar for calls to be considered similar
    for va, vb in zip(vals_a, vals_b):
        if va == vb:
            continue
        ratio = SequenceMatcher(None, va[:300], vb[:300]).ratio()
        if ratio < _SIMILARITY_THRESHOLD:
            return False
    return True


def _detect_cycle(calls: list[str]) -> bool:
    """Detect A→B→A→B style cycles in recent calls.

    Uses EXACT match only (not fuzzy) to avoid false positives with tools
    like execute_shell where different commands share similar structure.
    Requires at least 3 full cycle repetitions to confirm.
    """
    if len(calls) < 6:
        return False
    # Check for cycles of length 2 and 3, requiring 3 repetitions
    for cycle_len in (2, 3):
        needed = cycle_len * 3  # 3 full cycles
        if len(calls) < needed:
            continue
        recent = calls[-needed:]
        # Check if all 3 cycles are identical
        cycle = recent[:cycle_len]
        is_cycle = True
        for rep in range(1, 3):
            segment = recent[rep * cycle_len : (rep + 1) * cycle_len]
            if segment != cycle:
                is_cycle = False
                break
        if is_cycle:
            return True
    return False


def _detect_stale_progress(
    recent_results: list[str], window: int = _STALE_WINDOW
) -> bool:
    """Check if recent tool results are all very similar (no new info)."""
    if len(recent_results) < window:
        return False
    last_n = recent_results[-window:]
    # If all results are very similar to the first one, we're stale
    base = last_n[0][:500]
    similar_count = sum(
        1 for r in last_n[1:]
        if SequenceMatcher(None, base, r[:500]).ratio() > 0.90
    )
    return similar_count >= window - 2  # allow 1 different result


def _detect_loop(
    call_sigs: list[str],
    recent_calls: list[str],
    recent_results: list[str],
) -> str | None:
    """
    Smart loop detection. Returns a reason string if loop detected, None otherwise.

    Detects:
    1. Exact repetition (same call N times)
    2. Similar calls (same tool, similar args N times)
    3. A→B→A→B cycles
    4. Stale progress (results not changing)
    """
    # 1. Exact repetition — Counter em vez de N x list.count() (O(N) vs O(N*M))
    counts = Counter(recent_calls)
    for sig in call_sigs:
        c = counts.get(sig, 0)
        if c >= _MAX_REPEAT_CALLS:
            return f"exact repeat: '{sig[:60]}' called {c}x"

    # 2. Similar calls (same tool with slightly different args) — indexa por
    # tool name primeiro para evitar SequenceMatcher quando os nomes diferem.
    # Em sessoes ativas com ~60 recent_calls e 5+ tools diferentes, isso
    # corta ~80% das comparacoes caras.
    by_name: dict[str, list[str]] = {}
    for s in recent_calls:
        by_name.setdefault(s.partition(":")[0], []).append(s)
    for sig in call_sigs:
        candidates = by_name.get(sig.partition(":")[0])
        if not candidates or len(candidates) < _SIMILAR_REPEAT_CALLS:
            continue
        similar_count = sum(1 for s in candidates if _are_similar(sig, s))
        if similar_count >= _SIMILAR_REPEAT_CALLS:
            return f"similar calls: '{sig[:60]}' ~{similar_count}x"

    # 3. Cycle detection (A→B→A→B)
    if _detect_cycle(recent_calls):
        return "cycle detected in recent calls"

    # 4. Stale progress
    if _detect_stale_progress(recent_results):
        return "stale progress — tool results not changing"

    return None


async def run_agent(
    messages: list[dict],
    user_message: str,
    temperature: float = 0.5,
    provider: str = "deepseek",
    get_tool_fn=None,
    tools: list[dict] | None = None,
    approval_callback=None,
    max_iterations: int | None = None,
    workspace: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Run the agent loop. Async generator yielding display events.

    Features:
    - Intelligent context compression via LLM summarization
    - Token budget tracking per provider
    - Smart loop detection (exact, fuzzy, cycle, stale)

    Args:
        messages: Full conversation messages (system + history + new user msg).
        user_message: The current user message text.
        temperature: LLM temperature.
        provider: LLM provider name.
        get_tool_fn: Function(name) -> ToolDefinition for looking up tools.
        tools: OpenAI-format tool definitions list.
        approval_callback: Sync function(tool_name, args) -> bool for approval.
        max_iterations: Override iteration limit (defaults to MAX_ITERATIONS).

    Yields:
        {"type": "token", "text": "..."}
        {"type": "tool_call", "name": ..., "args": ...}
        {"type": "tool_result", "name": ..., "result": ...}
        {"type": "approval_needed", "name": ..., "args": ...}
        {"type": "context_compressed", "before": int, "after": int}
        {"type": "done", "reply": "full text"}
        {"type": "error", "message": "..."}
    """
    if tools is None:
        tools = []

    iteration_limit = max_iterations if max_iterations is not None else MAX_ITERATIONS
    full_response = ""

    # Track tool calls for smart loop detection
    _recent_calls: list[str] = []
    _recent_results: list[str] = []

    for iteration in range(iteration_limit):
        logger.info(f"Agent iteration {iteration + 1}/{iteration_limit}")

        # ── Pre-call adaptive compression ──
        if needs_compression(messages, provider):
            tokens_before = estimate_messages_tokens(messages)
            try:
                _, tokens_after = await compress_until_under_budget(
                    messages, provider, stream_chat_with_tools
                )
                if tokens_after != tokens_before:
                    yield {
                        "type": "context_compressed",
                        "before": tokens_before,
                        "after": tokens_after,
                    }
            except Exception as e:
                logger.warning(f"Context compression failed: {e} — continuing")

        # ── Stream LLM call (with one overflow retry) ──
        final_event = None
        overflow_retried = False

        while True:
            final_event = None
            async for event in stream_chat_with_tools(
                messages, tools, temperature, provider=provider
            ):
                if event["type"] == "content_token":
                    yield {"type": "token", "text": event["token"]}
                elif event["type"] == "stream_reset":
                    # llm.py vai retentar; tokens ja yieldados sao da
                    # tentativa abortada. Caller (REPL/main) pode limpar UI.
                    yield event
                elif event["type"] == "final":
                    final_event = event

            if final_event is None:
                yield {"type": "error", "message": "No response from LLM"}
                return

            err = final_event.get("error")
            if err and is_context_overflow_error(err) and not overflow_retried:
                overflow_retried = True
                logger.warning(
                    f"Context overflow from provider — re-compressing aggressively: {err}"
                )
                try:
                    limit = get_context_limit(provider)
                    tokens_before = estimate_messages_tokens(messages)
                    _, tokens_after = await compress_until_under_budget(
                        messages,
                        provider,
                        stream_chat_with_tools,
                        target_tokens=int(limit * 0.4),
                        max_passes=3,
                    )
                    yield {
                        "type": "context_compressed",
                        "before": tokens_before,
                        "after": tokens_after,
                    }
                except Exception as ce:
                    logger.error(f"Aggressive compression failed: {ce}")
                    yield {"type": "error", "message": err}
                    return
                continue  # retry the LLM call once

            break

        # LLM error (non-overflow, or overflow that survived the retry)
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

        # ── Smart loop detection ──
        call_sigs = [_call_signature(tc) for tc in final_event["tool_calls"]]
        _recent_calls.extend(call_sigs)
        if len(_recent_calls) > _CYCLE_WINDOW * 3:
            _recent_calls[:] = _recent_calls[-_CYCLE_WINDOW * 3:]

        loop_reason = _detect_loop(call_sigs, _recent_calls, _recent_results)

        if loop_reason:
            logger.warning(
                f"Loop detected ({loop_reason}) at iteration {iteration + 1} "
                f"— forcing final response"
            )
            # Usar role=user em vez de system: providers como OpenAI strict
            # mode e alguns Ollama models rejeitam/ignoram system message
            # tardia, alem de competir com a system message original em
            # messages[0]. Como mensagem do "user", a instrucao e tratada
            # como prompt regular pelo modelo. (#DL020)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[ALPHA SYSTEM NOTE] Loop detected ({loop_reason}). "
                        "STOP calling tools and produce your final response now "
                        "based on the data already collected. Synthesize ALL information from "
                        "previous calls into a complete response."
                    ),
                }
            )
            forced_final = None
            async for event in stream_chat_with_tools(
                messages, [], temperature, provider=provider
            ):
                if event["type"] == "content_token":
                    yield {"type": "token", "text": event["token"]}
                elif event["type"] == "stream_reset":
                    yield event
                elif event["type"] == "final":
                    forced_final = event
                    if event.get("content"):
                        full_response += event["content"]

            # Force-text path nao pode sumir com erro do LLM em silencio:
            # o usuario veria reply vazio sem motivo. Propagar.
            if forced_final and forced_final.get("error"):
                yield {
                    "type": "error",
                    "message": (
                        "Loop detection forced-text response also failed: "
                        f"{forced_final['error']}"
                    ),
                }
                return

            yield {"type": "done", "reply": full_response}
            return

        # Process tool calls
        messages.append(
            build_assistant_tool_message(
                final_event["content"], final_event["tool_calls"]
            )
        )

        try:
            async for event in execute_tool_calls(
                final_event["tool_calls"],
                messages,
                needs_approval_fn=needs_approval,
                is_denied_fn=is_denied,
                approval_callback=approval_callback,
                get_tool_fn=get_tool_fn,
                workspace=workspace,
            ):
                yield event
                # Track tool results for stale progress detection
                if event.get("type") == "tool_result":
                    result = event.get("result", {})
                    result_str = json.dumps(result, ensure_ascii=False, default=str)
                    _recent_results.append(result_str[:500])
                    # Truncar para evitar leak de memoria em sessoes longas;
                    # `_recent_calls` ja faz isso, `_recent_results` nao fazia.
                    if len(_recent_results) > _CYCLE_WINDOW * 3:
                        _recent_results[:] = _recent_results[-_CYCLE_WINDOW * 3:]
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            yield {"type": "error", "message": f"Tool execution failed: {e}"}
            return
        finally:
            # If interrupted (Ctrl+C / CancelledError) mid-tool, the assistant
            # tool_calls may have no matching tool responses, which makes the
            # provider reject the next request with HTTP 400. Backfill missing
            # tool messages so the conversation stays well-formed.
            last_assistant = None
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    last_assistant = msg
                    break
            if last_assistant:
                responded = {
                    m.get("tool_call_id")
                    for m in messages
                    if m.get("role") == "tool"
                }
                for tc in last_assistant["tool_calls"]:
                    if tc["id"] not in responded:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps({"error": "interrupted"}),
                        })

    # Max iterations reached
    yield {
        "type": "token",
        "text": "\n\n[Maximum iterations reached]",
    }
    yield {"type": "done", "reply": full_response or "[Max iterations reached]"}
