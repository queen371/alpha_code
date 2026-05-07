"""
LLM streaming client for Alpha Code.

Handles OpenAI-compatible chat completions with tool calling support.
Streams SSE responses from providers (DeepSeek, OpenAI, Grok, Ollama).
Includes retry with exponential backoff, jitter, and rate-limit handling.
"""

import asyncio
import hashlib
import json
import logging
import random
from collections.abc import AsyncGenerator

import httpx

from ._security_log import sanitize_for_log
from .config import LLM_TIMEOUT, get_provider_config

logger = logging.getLogger(__name__)

# ─── Retry / Rate-limit config ───

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds
MAX_BACKOFF = 30.0
BACKOFF_MULTIPLIER = 2.0
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Smaller local models (Ollama-backed) hallucinate tool calls less often at
# lower temperatures. Politica vive como flag `low_temperature` em
# config._PROVIDERS — adicionar provider novo so requer setar a flag,
# sem editar este arquivo (#DM011).
_LOW_TEMPERATURE = 0.2


def _recover_tool_call_from_content(content: str) -> dict | None:
    """Recover a tool call from a content string when the model emitted it as
    text instead of via the OpenAI ``tool_calls`` field.

    Some Ollama-served models (notably qwen2.5-coder) occasionally drift into
    code-completion mode and dump a tool call as a fenced JSON block. Returns
    a tool_call dict matching the streamed format, or None if recovery isn't
    safe/possible.
    """
    if not content:
        return None
    text = content.strip()
    if not text:
        return None

    # Strip a single ``` or ```json fence wrapping the whole content.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl == -1:
            return None
        body = text[first_nl + 1 :]
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
        text = body.strip()

    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    # Accept the two common shapes models emit:
    #   {"name": "X", "arguments": {...}}
    #   {"function": {"name": "X", "arguments": {...}}}
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else {}
    name = obj.get("name") or fn.get("name")
    args = obj.get("arguments")
    if args is None:
        args = obj.get("parameters")
    if args is None:
        args = fn.get("arguments")
    if not isinstance(name, str) or not name or args is None:
        return None

    if isinstance(args, dict):
        args_str = json.dumps(args, ensure_ascii=False)
    elif isinstance(args, str):
        args_str = args
    else:
        return None

    # SHA1 (truncado) em vez de hash() — Python's hash() e seed-randomizado
    # entre processos, gerando ids instaveis ao replay/restore de sessao.
    # SHA1 e deterministico por input, com colisao desprezivel para uma sessao.
    digest = hashlib.sha1((name + args_str).encode("utf-8")).hexdigest()[:8]
    return {
        "id": f"call_recovered_{digest}",
        "name": name,
        "arguments": args_str,
    }


def _calc_backoff(attempt: int, retry_after: float | None = None) -> float:
    """Calculate backoff delay with exponential growth, jitter, capped at MAX_BACKOFF.

    Jitter prevents thundering herd: all clients back off at slightly different
    intervals instead of retrying in lockstep.
    """
    if retry_after is not None:
        # Add small jitter even to server-specified delays. Jitter pode
        # ate 1.2x o base, entao precisamos do `min(..., MAX_BACKOFF)`
        # explicito (#D023): sem ele, o resultado podia exceder o cap em
        # ate 20% (ex: 30s -> 36s) violando o invariante anunciado.
        base = min(retry_after, MAX_BACKOFF)
        return min(base * (0.8 + random.random() * 0.4), MAX_BACKOFF)
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
    supports_tools = cfg["supports_tools"]
    api_format = cfg.get("api_format", "openai")

    if cfg.get("low_temperature") and temperature > _LOW_TEMPERATURE:
        temperature = _LOW_TEMPERATURE

    if api_format == "anthropic":
        from .llm_anthropic import stream_anthropic

        tools_to_send = tools if tools and supports_tools else []
        async for event in stream_anthropic(
            messages=messages,
            tools=tools_to_send,
            temperature=temperature,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=LLM_TIMEOUT,
        ):
            yield event
        return

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
    if tools and supports_tools:
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
                            if accumulated_content:
                                yield {"type": "stream_reset", "reason": last_error}
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
                        # Some providers echo back the request (incl. Authorization
                        # header) in error responses — sanitize before logging.
                        body_str = error_body.decode("utf-8", errors="replace")
                        logger.error(
                            f"LLM HTTP {response.status_code}: "
                            f"{sanitize_for_log(body_str, max_chars=500)}"
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
                # Fallback: some models (Ollama qwen-coder etc.) emit tool calls
                # as fenced JSON in content instead of via the tool_calls field.
                recovered = _recover_tool_call_from_content(accumulated_content)
                if recovered is not None:
                    logger.info(
                        f"Recovered tool call '{recovered['name']}' from content "
                        f"(provider={provider})"
                    )
                    yield {
                        "type": "final",
                        "content": "",
                        "tool_calls": [recovered],
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
                if accumulated_content:
                    yield {"type": "stream_reset", "reason": last_error}
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

        # Nota: httpx.HTTPStatusError nao e capturado porque `client.stream`
        # NAO chama `raise_for_status()` automaticamente — o status_code
        # >= 400 e tratado inline no caminho principal (linhas ~190 e
        # ~226). Manter um handler aqui era codigo morto (#052).

        except (ConnectionError, OSError) as e:
            last_error = f"Connection error: {e}"
            if attempt < MAX_RETRIES:
                delay = _calc_backoff(attempt)
                logger.warning(
                    f"{last_error} (attempt {attempt + 1}/{MAX_RETRIES + 1}), "
                    f"retrying in {delay:.1f}s"
                )
                if accumulated_content:
                    yield {"type": "stream_reset", "reason": last_error}
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
