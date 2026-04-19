"""
LLM streaming client for Alpha Code.

Handles OpenAI-compatible chat completions with tool calling support.
Streams SSE responses from providers (DeepSeek, OpenAI, Grok, Ollama).
Includes retry with exponential backoff, jitter, and rate-limit handling.
"""

import asyncio
import json
import logging
import random
from collections.abc import AsyncGenerator

import httpx

from .config import LLM_TIMEOUT, get_provider_config

logger = logging.getLogger(__name__)

# ─── Retry / Rate-limit config ───

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds
MAX_BACKOFF = 30.0
BACKOFF_MULTIPLIER = 2.0
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _calc_backoff(attempt: int, retry_after: float | None = None) -> float:
    """Calculate backoff delay with exponential growth, jitter, capped at MAX_BACKOFF.

    Jitter prevents thundering herd: all clients back off at slightly different
    intervals instead of retrying in lockstep.
    """
    if retry_after is not None:
        # Add small jitter even to server-specified delays
        return min(retry_after, MAX_BACKOFF) * (0.8 + random.random() * 0.4)
    delay = INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
    # Full jitter: uniform random between 0 and calculated delay
    jittered = delay * (0.5 + random.random() * 0.5)
    return min(jittered, MAX_BACKOFF)


async def stream_chat_with_tools(
    messages: list[dict],
    tools: list[dict],
    temperature: float = 0.5,
    provider: str = "deepseek",
) -> AsyncGenerator[dict, None]:
    """
    Stream LLM response with tool calling support.

    Includes retry with exponential backoff for transient errors (429, 5xx).
    Respects Retry-After headers from rate-limited responses.

    Yields events:
    - {"type": "content_token", "token": "..."}  — incremental text
    - {"type": "final", "content": "...", "tool_calls": [...], "error": None}
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

    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        accumulated_content = ""
        tool_calls_acc: dict[int, dict] = {}

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
                    # Handle retryable HTTP errors
                    if response.status_code in RETRYABLE_STATUS_CODES:
                        error_body = await response.aread()
                        last_error = f"HTTP {response.status_code}"

                        if attempt < MAX_RETRIES:
                            # Parse Retry-After header for rate limits
                            retry_after = None
                            ra_header = response.headers.get("retry-after")
                            if ra_header:
                                try:
                                    retry_after = float(ra_header)
                                except ValueError:
                                    pass

                            delay = _calc_backoff(attempt, retry_after)
                            logger.warning(
                                f"LLM {last_error} (attempt {attempt + 1}/{MAX_RETRIES + 1}), "
                                f"retrying in {delay:.1f}s"
                            )
                            await asyncio.sleep(delay)
                            continue

                        # Max retries exhausted
                        logger.error(f"LLM {last_error} after {MAX_RETRIES + 1} attempts")
                        yield {
                            "type": "final",
                            "content": "",
                            "tool_calls": [],
                            "error": f"{last_error} after {MAX_RETRIES + 1} attempts",
                        }
                        return

                    # Non-retryable HTTP error
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

                    # Stream response
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

                        except json.JSONDecodeError:
                            continue  # Expected for non-JSON SSE lines
                        except (KeyError, IndexError) as e:
                            logger.debug(f"Unexpected SSE chunk format: {e} | data: {data_str[:200]}")
                            continue

            # Success — build final event and return
            if tool_calls_acc:
                tool_calls = [
                    {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
                    for _, tc in sorted(tool_calls_acc.items())
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
            return  # success, no retry

        except httpx.TimeoutException:
            last_error = f"LLM timeout ({LLM_TIMEOUT}s)"
            if attempt < MAX_RETRIES:
                delay = _calc_backoff(attempt)
                logger.warning(
                    f"{last_error} (attempt {attempt + 1}/{MAX_RETRIES + 1}), "
                    f"retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                continue

            logger.error(f"{last_error} after {MAX_RETRIES + 1} attempts")
            yield {
                "type": "final",
                "content": accumulated_content,
                "tool_calls": [],
                "error": f"{last_error} after {MAX_RETRIES + 1} attempts",
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

        except (ConnectionError, OSError) as e:
            last_error = f"Connection error: {e}"
            if attempt < MAX_RETRIES:
                delay = _calc_backoff(attempt)
                logger.warning(
                    f"{last_error} (attempt {attempt + 1}/{MAX_RETRIES + 1}), "
                    f"retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                continue

            logger.error(f"{last_error} after {MAX_RETRIES + 1} attempts")
            yield {
                "type": "final",
                "content": accumulated_content,
                "tool_calls": [],
                "error": last_error,
            }
            return

        except (json.JSONDecodeError, KeyError, ValueError, RuntimeError) as e:
            logger.error(f"LLM error: {e}")
            yield {
                "type": "final",
                "content": accumulated_content,
                "tool_calls": [],
                "error": str(e),
            }
            return
