"""
Context management for Alpha Code.

Handles intelligent message compression, token estimation, and context window
optimization. Instead of crude truncation, uses the LLM itself to summarize
old messages when context grows too large.
"""

import json
import logging
import os
from collections.abc import AsyncGenerator

from .attachments import extract_text

logger = logging.getLogger(__name__)

# ─── Token estimation ───
# Rough approximation: ~4 chars per token for English, ~3 for code-heavy content.
# Good enough for budget decisions without requiring tiktoken dependency.

CHARS_PER_TOKEN = 4
# Flat per-image budget for context accounting. Anthropic and OpenAI charge
# roughly 1k–3k tokens per typical screenshot; we use a conservative midpoint
# so the compression trigger fires before the model's real budget is hit.
IMAGE_TOKEN_COST = 1500

# Context window sizes per provider (conservative estimates leaving room for response)
PROVIDER_CONTEXT_LIMITS: dict[str, int] = {
    "deepseek": 60_000,   # 64K context, reserve 4K for response
    "openai": 120_000,    # 128K context, reserve 8K for response
    "anthropic": 180_000, # 200K context, reserve 20K for response
    "grok": 120_000,      # 128K context, reserve 8K
    "ollama": 28_000,     # varies, conservative default
}

# Compression triggers when usage exceeds this fraction of the context window
COMPRESSION_THRESHOLD = 0.70

# Keep the last N messages untouched (system + recent exchanges)
PROTECTED_TAIL_MESSAGES = 8

# Minimum messages before compression is even considered
MIN_MESSAGES_FOR_COMPRESSION = 12


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _estimate_content_tokens(content) -> int:
    if isinstance(content, str):
        return estimate_tokens(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                total += estimate_tokens(block.get("text", ""))
            elif btype in ("image_url", "image"):
                total += IMAGE_TOKEN_COST
        return total
    return 0


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across all messages."""
    total = 0
    for msg in messages:
        total += _estimate_content_tokens(msg.get("content"))
        # Tool calls in assistant messages add tokens too
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                total += estimate_tokens(fn.get("name", ""))
                total += estimate_tokens(fn.get("arguments", ""))
    return total


def get_context_limit(provider: str) -> int:
    """Get the token budget for a provider.

    Honors `ALPHA_CONTEXT_LIMIT` as a global override. For Anthropic, also
    detects 1M extended-context models from the model name (e.g. an env
    `ANTHROPIC_MODEL` containing "1m" or "[1m]") and bumps the budget.
    """
    override = os.environ.get("ALPHA_CONTEXT_LIMIT", "").strip()
    if override:
        try:
            return int(override)
        except ValueError:
            pass

    if provider == "anthropic":
        model = os.environ.get("ANTHROPIC_MODEL", "").lower()
        if "1m" in model:
            # 1M context — leave ~100K headroom for response + tool schemas.
            return 900_000

    return PROVIDER_CONTEXT_LIMITS.get(provider, 28_000)


_CONTEXT_OVERFLOW_MARKERS = (
    "maximum context length",
    "context_length_exceeded",
    "context length",
    "prompt is too long",
    "request_too_large",
    "exceeds the maximum",
    "exceed the maximum",
    "too many tokens",
    "input is too long",
)


def is_context_overflow_error(error_msg: str | None) -> bool:
    """Detect whether an LLM error string indicates a context-window overflow."""
    if not error_msg:
        return False
    lo = error_msg.lower()
    return any(m in lo for m in _CONTEXT_OVERFLOW_MARKERS)


def needs_compression(messages: list[dict], provider: str) -> bool:
    """Check if messages exceed the compression threshold."""
    if len(messages) < MIN_MESSAGES_FOR_COMPRESSION:
        return False
    limit = get_context_limit(provider)
    current = estimate_messages_tokens(messages)
    return current > (limit * COMPRESSION_THRESHOLD)


def _find_compressible_range(
    messages: list[dict],
    tail: int = PROTECTED_TAIL_MESSAGES,
) -> tuple[int, int]:
    """
    Find the range of messages that can be compressed.

    Returns (start, end) indices. Protects:
    - Index 0: system prompt
    - Last `tail` messages (default PROTECTED_TAIL_MESSAGES)
    """
    if len(messages) <= tail + 1:
        return (0, 0)  # nothing to compress

    start = 1  # skip system prompt
    end = len(messages) - tail
    if end <= start:
        return (0, 0)
    return (start, end)


def build_compression_prompt(messages: list[dict], start: int, end: int) -> str:
    """
    Build a prompt asking the LLM to summarize a range of messages.

    Returns a user message containing the conversation segment to compress.
    """
    lines = []
    for msg in messages[start:end]:
        role = msg.get("role", "unknown")
        raw_content = msg.get("content")
        content = extract_text(raw_content) if raw_content else ""
        # Note in the summary that the original message had image attachments,
        # otherwise the model has no idea they ever existed.
        image_count = (
            sum(1 for b in raw_content if isinstance(b, dict)
                and b.get("type") in ("image_url", "image"))
            if isinstance(raw_content, list) else 0
        )
        image_note = f" [+{image_count} image(s)]" if image_count else ""

        if role == "assistant" and msg.get("tool_calls"):
            tc_names = [tc.get("function", {}).get("name", "unknown")
                        for tc in msg["tool_calls"]]
            lines.append(f"[assistant called tools: {', '.join(tc_names)}]")
            if content:
                lines.append(f"[assistant text]: {content[:500]}")
        elif role == "tool":
            preview = content[:800] if content else "(empty)"
            tool_id = msg.get("tool_call_id", "")
            lines.append(f"[tool result {tool_id}]: {preview}")
        else:
            preview = content[:1000] if content else "(empty)"
            lines.append(f"[{role}{image_note}]: {preview}")

    conversation_text = "\n".join(lines)

    return (
        "Summarize the following conversation segment into a concise but complete "
        "summary. Preserve ALL important information: file paths, code snippets, "
        "tool results, decisions made, errors encountered, and key findings. "
        "Format as bullet points. Be thorough but concise.\n\n"
        f"--- CONVERSATION SEGMENT ---\n{conversation_text}\n--- END SEGMENT ---"
    )


async def compress_context(
    messages: list[dict],
    provider: str,
    stream_fn,
    protected_tail: int = PROTECTED_TAIL_MESSAGES,
) -> list[dict]:
    """
    Compress old messages by summarizing them via the LLM.

    Args:
        messages: Full message list (mutated in-place).
        provider: LLM provider for the compression call.
        stream_fn: The stream_chat_with_tools function for making LLM calls.
        protected_tail: Number of trailing messages to leave untouched. Lower
            this to free more tokens when the recent tail itself is huge.

    Returns:
        The compressed messages list.
    """
    start, end = _find_compressible_range(messages, protected_tail)
    if start >= end:
        logger.debug("Nothing to compress — range too small")
        return messages

    tokens_before = estimate_messages_tokens(messages)

    # Build compression request
    compression_prompt = build_compression_prompt(messages, start, end)

    compression_messages = [
        {
            "role": "system",
            "content": (
                "You are a conversation summarizer. Produce a concise bullet-point "
                "summary preserving all key technical details: file paths, function "
                "names, error messages, decisions, and outcomes. Do not add opinions."
            ),
        },
        {"role": "user", "content": compression_prompt},
    ]

    # Call LLM without tools for summarization
    summary = ""
    async for event in stream_fn(compression_messages, [], 0.2, provider=provider):
        if event["type"] == "content_token":
            summary += event["token"]
        elif event["type"] == "final":
            if event.get("content"):
                summary = event["content"]

    if not summary:
        logger.warning("Compression produced empty summary — skipping")
        return messages

    # Replace compressed messages with a single summary message
    summary_message = {
        "role": "user",
        "content": (
            f"[CONTEXT SUMMARY — compressed from {end - start} messages]\n\n"
            f"{summary}\n\n"
            "[End of summary. The conversation continues below.]"
        ),
    }

    # Rebuild: system + summary + protected tail
    new_messages = (
        [messages[0]]          # system prompt
        + [summary_message]    # compressed summary
        + messages[end:]       # recent messages (protected tail)
    )

    tokens_after = estimate_messages_tokens(new_messages)
    logger.info(
        f"Context compressed: {tokens_before} -> {tokens_after} tokens "
        f"({end - start} messages summarized)"
    )

    # Mutate in-place so the caller's reference stays valid
    messages[:] = new_messages
    return messages


async def compress_until_under_budget(
    messages: list[dict],
    provider: str,
    stream_fn,
    target_tokens: int | None = None,
    max_passes: int = 3,
) -> tuple[int, int]:
    """Compress repeatedly, shrinking the protected tail each pass, until the
    estimated token count drops to `target_tokens` (defaults to 60% of the
    provider's context limit) or `max_passes` runs out.

    Returns (tokens_before, tokens_after). Mutates `messages` in place.

    Each pass uses a smaller protected tail so that, when the bulk of tokens
    sits in recent tool results (e.g. huge file dumps), we still claw budget
    back instead of looping at -1% reductions.
    """
    tokens_before = estimate_messages_tokens(messages)

    if target_tokens is None:
        target_tokens = int(get_context_limit(provider) * 0.6)

    if tokens_before <= target_tokens:
        return tokens_before, tokens_before

    # Adaptive tail schedule — first pass keeps the standard buffer; later
    # passes erode it down to a single message if the tail itself is the bloat.
    tail_schedule = [
        PROTECTED_TAIL_MESSAGES,
        max(2, PROTECTED_TAIL_MESSAGES // 2),
        1,
    ]
    passes = min(max_passes, len(tail_schedule))

    current = tokens_before
    for i in range(passes):
        tail = tail_schedule[i]
        try:
            await compress_context(
                messages, provider, stream_fn, protected_tail=tail
            )
        except Exception as e:
            logger.warning(f"Compression pass {i + 1} failed: {e}")
            break

        new_total = estimate_messages_tokens(messages)
        logger.info(
            f"Compression pass {i + 1}/{passes} (tail={tail}): "
            f"{current} -> {new_total} tokens"
        )
        # Stop if the pass made no meaningful progress (avoid infinite churn).
        if new_total >= current * 0.98:
            current = new_total
            break
        current = new_total
        if current <= target_tokens:
            break

    return tokens_before, current
