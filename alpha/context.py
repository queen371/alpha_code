"""
Context management for Alpha Code.

Handles intelligent message compression, token estimation, and context window
optimization. Instead of crude truncation, uses the LLM itself to summarize
old messages when context grows too large.
"""

import json
import logging
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

# ─── Token estimation ───
# Rough approximation: ~4 chars per token for English, ~3 for code-heavy content.
# Good enough for budget decisions without requiring tiktoken dependency.

CHARS_PER_TOKEN = 4

# Context window sizes per provider (conservative estimates leaving room for response)
PROVIDER_CONTEXT_LIMITS: dict[str, int] = {
    "deepseek": 60_000,   # 64K context, reserve 4K for response
    "openai": 120_000,    # 128K context, reserve 8K for response
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


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across all messages."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        total += estimate_tokens(content)
        # Tool calls in assistant messages add tokens too
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                total += estimate_tokens(fn.get("name", ""))
                total += estimate_tokens(fn.get("arguments", ""))
    return total


def get_context_limit(provider: str) -> int:
    """Get the token budget for a provider."""
    return PROVIDER_CONTEXT_LIMITS.get(provider, 28_000)


def needs_compression(messages: list[dict], provider: str) -> bool:
    """Check if messages exceed the compression threshold."""
    if len(messages) < MIN_MESSAGES_FOR_COMPRESSION:
        return False
    limit = get_context_limit(provider)
    current = estimate_messages_tokens(messages)
    return current > (limit * COMPRESSION_THRESHOLD)


def _find_compressible_range(messages: list[dict]) -> tuple[int, int]:
    """
    Find the range of messages that can be compressed.

    Returns (start, end) indices. Protects:
    - Index 0: system prompt
    - Last PROTECTED_TAIL_MESSAGES messages
    """
    if len(messages) <= PROTECTED_TAIL_MESSAGES + 1:
        return (0, 0)  # nothing to compress

    start = 1  # skip system prompt
    end = len(messages) - PROTECTED_TAIL_MESSAGES
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
        content = msg.get("content") or ""

        if role == "assistant" and msg.get("tool_calls"):
            # Summarize tool calls compactly
            tc_names = []
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                tc_names.append(fn.get("name", "unknown"))
            lines.append(f"[assistant called tools: {', '.join(tc_names)}]")
            if content:
                lines.append(f"[assistant text]: {content[:500]}")
        elif role == "tool":
            # Truncate tool results for the summary prompt
            preview = content[:800] if content else "(empty)"
            tool_id = msg.get("tool_call_id", "")
            lines.append(f"[tool result {tool_id}]: {preview}")
        else:
            preview = content[:1000] if content else "(empty)"
            lines.append(f"[{role}]: {preview}")

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
) -> list[dict]:
    """
    Compress old messages by summarizing them via the LLM.

    Args:
        messages: Full message list (mutated in-place).
        provider: LLM provider for the compression call.
        stream_fn: The stream_chat_with_tools function for making LLM calls.

    Returns:
        The compressed messages list.
    """
    start, end = _find_compressible_range(messages)
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
