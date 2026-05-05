"""Anthropic Messages API adapter.

Translates between the OpenAI chat-completions shape used by the rest of
the agent and Anthropic's `/v1/messages` endpoint:

- Header: `x-api-key` instead of `Authorization: Bearer`
- System message: separate top-level `system` field, not a message
- Tool result: `{"role": "user", "content": [{"type": "tool_result", ...}]}`
- Tool definition: `input_schema` instead of `parameters`
- Streaming: `content_block_delta` events with `text_delta` or `input_json_delta`

The streaming generator yields the same `content_token` / `final` events
the rest of the loop already consumes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 8192


# ── OpenAI → Anthropic conversion ──


def _convert_tools(openai_tools: list[dict]) -> list[dict]:
    """Convert OpenAI function-tool schema to Anthropic tool schema."""
    out = []
    for t in openai_tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if t.get("type") == "function" else t
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _convert_messages(openai_messages: list[dict]) -> tuple[str, list[dict]]:
    """Split system messages out and convert the rest to Anthropic shape.

    Adjacent tool-result messages are coalesced into a single `user` turn
    with multiple `tool_result` content blocks (Anthropic requires this).
    """
    system_parts: list[str] = []
    converted: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush_tool_results():
        if pending_tool_results:
            converted.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for msg in openai_messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
            continue

        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                }
            )
            continue

        flush_tool_results()

        if role == "user":
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            converted.append({"role": "user", "content": text})
            continue

        if role == "assistant":
            blocks: list[dict] = []
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            for tc in msg.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    }
                )
            if not blocks:
                blocks.append({"type": "text", "text": ""})
            converted.append({"role": "assistant", "content": blocks})
            continue

    flush_tool_results()

    return "\n\n".join(system_parts), converted


# ── Streaming ──


async def stream_anthropic(
    messages: list[dict],
    tools: list[dict],
    temperature: float,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
) -> AsyncGenerator[dict, None]:
    """Stream from Anthropic's /v1/messages endpoint, yielding the same event
    shape as the OpenAI streaming path: `content_token` and a single `final`.
    """
    system_text, anthropic_messages = _convert_messages(messages)
    anthropic_tools = _convert_tools(tools)

    payload: dict = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "messages": anthropic_messages,
        "stream": True,
        "temperature": temperature,
    }
    if system_text:
        payload["system"] = system_text
    if anthropic_tools:
        payload["tools"] = anthropic_tools

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    accumulated_content = ""
    blocks: dict[int, dict] = {}  # index → {"type": "text"|"tool_use", ...}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        async with client.stream(
            "POST", f"{base_url}/messages", json=payload, headers=headers
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                logger.error(f"Anthropic HTTP {response.status_code}: {body[:500]}")
                yield {
                    "type": "final",
                    "content": "",
                    "tool_calls": [],
                    "error": f"HTTP {response.status_code}: {body[:200].decode('utf-8', errors='replace')}",
                }
                return

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")

                if etype == "content_block_start":
                    idx = event["index"]
                    block = event["content_block"]
                    if block["type"] == "text":
                        blocks[idx] = {"type": "text", "text": ""}
                    elif block["type"] == "tool_use":
                        blocks[idx] = {
                            "type": "tool_use",
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input_json": "",
                        }

                elif etype == "content_block_delta":
                    idx = event["index"]
                    delta = event.get("delta", {})
                    block = blocks.get(idx)
                    if block is None:
                        continue
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            block["text"] = block.get("text", "") + text
                            accumulated_content += text
                            yield {"type": "content_token", "token": text}
                    elif delta.get("type") == "input_json_delta":
                        block["input_json"] = block.get("input_json", "") + delta.get("partial_json", "")

                elif etype == "message_stop":
                    break

    tool_calls = []
    for _, block in sorted(blocks.items()):
        if block["type"] != "tool_use":
            continue
        args_json = block.get("input_json", "") or "{}"
        # Validate the accumulated JSON; keep raw on failure so callers see what arrived.
        try:
            json.loads(args_json)
        except json.JSONDecodeError:
            logger.warning(
                "Anthropic tool '%s' returned malformed JSON args (kept verbatim)",
                block.get("name"),
            )
        tool_calls.append(
            {
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "arguments": args_json,
            }
        )

    yield {
        "type": "final",
        "content": accumulated_content,
        "tool_calls": tool_calls,
        "error": None,
    }
