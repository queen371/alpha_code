"""
LLM streaming client for Alpha Code.

Handles OpenAI-compatible chat completions with tool calling support.
Streams SSE responses from providers (DeepSeek, OpenAI, Grok, Ollama).
"""

import json
import logging
from collections.abc import AsyncGenerator

import httpx

from .config import LLM_TIMEOUT, get_provider_config

logger = logging.getLogger(__name__)


async def stream_chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    temperature: float = 0.5,
    provider: str = "deepseek",
) -> AsyncGenerator[dict, None]:
    """
    Stream LLM response with tool calling support.

    Yields events:
    - {"type": "content_token", "token": "..."}  — incremental text
    - {"type": "final", "content": "...", "tool_calls": [...], "error": None}

    Uses OpenAI-compatible streaming format (delta.content, delta.tool_calls).
    """
    cfg = get_provider_config(provider)
    base_url = cfg["base_url"]
    api_key = cfg["api_key"]
    model = cfg["model"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools

    accumulated_content = ""
    tool_calls_acc: dict[int, dict] = {}  # index -> {id, name, arguments}

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(LLM_TIMEOUT, connect=10.0)
        ) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    logger.error(
                        f"LLM HTTP {response.status_code}: {error_body[:500]}"
                    )
                    yield {
                        "type": "final",
                        "content": "",
                        "tool_calls": [],
                        "error": f"HTTP error {response.status_code}",
                    }
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {})

                        # Content tokens
                        content = delta.get("content", "")
                        if content:
                            accumulated_content += content
                            yield {"type": "content_token", "token": content}

                        # Tool calls (streamed incrementally)
                        if delta.get("tool_calls"):
                            for tc_delta in delta["tool_calls"]:
                                idx = tc_delta["index"]
                                if idx not in tool_calls_acc:
                                    tool_calls_acc[idx] = {
                                        "id": tc_delta.get("id", ""),
                                        "name": tc_delta.get("function", {}).get(
                                            "name", ""
                                        ),
                                        "arguments": "",
                                    }
                                entry = tool_calls_acc[idx]
                                if tc_delta.get("id"):
                                    entry["id"] = tc_delta["id"]
                                fn = tc_delta.get("function", {})
                                if fn.get("name"):
                                    entry["name"] = fn["name"]
                                if fn.get("arguments"):
                                    entry["arguments"] += fn["arguments"]

                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    except httpx.TimeoutException:
        logger.error(f"LLM timeout ({LLM_TIMEOUT}s)")
        yield {
            "type": "final",
            "content": accumulated_content,
            "tool_calls": [],
            "error": f"LLM timeout ({LLM_TIMEOUT}s)",
        }
        return
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text[:500]
        except (AttributeError, UnicodeDecodeError):
            pass
        logger.error(f"LLM HTTP error: {e.response.status_code} | body: {body}")
        yield {
            "type": "final",
            "content": accumulated_content,
            "tool_calls": [],
            "error": f"HTTP error ({e.response.status_code})",
        }
        return
    except (json.JSONDecodeError, KeyError, ValueError, RuntimeError, OSError) as e:
        logger.error(f"LLM error: {e}")
        yield {
            "type": "final",
            "content": accumulated_content,
            "tool_calls": [],
            "error": str(e),
        }
        return

    # Build final event
    if tool_calls_acc:
        tool_calls = [
            {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
            for tc in tool_calls_acc.values()
        ]
        yield {
            "type": "final",
            "content": accumulated_content,
            "tool_calls": tool_calls,
            "error": None,
        }
    else:
        yield {
            "type": "final",
            "content": accumulated_content,
            "tool_calls": [],
            "error": None,
        }
