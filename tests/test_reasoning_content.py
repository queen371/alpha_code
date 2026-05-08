"""Regression tests for `reasoning_content` round-trip (DeepSeek-reasoner).

Bug seen em prod: chamada com tool_call em modo thinking gerava HTTP 400
"The `reasoning_content` in the thinking mode must be passed back to the
API." porque o stream loop descartava o campo e o assistant message
serializado de volta nao o continha.

Cobertura:
- `build_assistant_tool_message` inclui `reasoning_content` quando
  fornecido, omite quando ausente (nao polui mensagens de outros providers)
- `stream_chat_with_tools` agrega `reasoning_content` de deltas SSE e
  expoe no evento `final`
- O agent passa `reasoning_content` adiante para a proxima request
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from alpha.executor import build_assistant_tool_message


class TestBuildAssistantToolMessage:
    def test_omits_reasoning_when_none(self):
        msg = build_assistant_tool_message(
            "hi", [{"id": "c1", "name": "x", "arguments": "{}"}]
        )
        assert "reasoning_content" not in msg
        assert msg["content"] == "hi"
        assert msg["tool_calls"][0]["id"] == "c1"

    def test_includes_reasoning_when_provided(self):
        msg = build_assistant_tool_message(
            "hi",
            [{"id": "c1", "name": "x", "arguments": "{}"}],
            reasoning_content="thought process here",
        )
        assert msg["reasoning_content"] == "thought process here"

    def test_omits_reasoning_when_empty_string(self):
        msg = build_assistant_tool_message(
            "hi",
            [{"id": "c1", "name": "x", "arguments": "{}"}],
            reasoning_content="",
        )
        assert "reasoning_content" not in msg


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b""


class _FakeAsyncClient:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    @asynccontextmanager
    async def stream(self, *_args, **_kwargs):
        yield self._response


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}"


@pytest.mark.asyncio
class TestStreamReasoningContent:
    async def test_reasoning_collected_and_emitted_in_final(self, monkeypatch):
        from alpha import llm

        # Monkeypatch the provider config so we hit the openai-compat path.
        monkeypatch.setattr(
            llm,
            "get_provider_config",
            lambda _p: {
                "base_url": "http://x",
                "api_key": "k",
                "model": "deepseek-reasoner",
                "supports_tools": True,
                "api_format": "openai",
                "low_temperature": False,
            },
        )

        chunks = [
            _sse({"choices": [{"delta": {"reasoning_content": "thinking..."}}]}),
            _sse({"choices": [{"delta": {"reasoning_content": "more thoughts."}}]}),
            _sse({"choices": [{"delta": {"content": "Hello"}}]}),
            _sse({"choices": [{"delta": {"content": " world"}}]}),
            "data: [DONE]",
        ]
        fake_resp = _FakeStreamResponse(chunks)
        monkeypatch.setattr(
            llm.httpx, "AsyncClient", lambda **_: _FakeAsyncClient(fake_resp)
        )

        events = []
        async for ev in llm.stream_chat_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            provider="deepseek",
        ):
            events.append(ev)

        finals = [e for e in events if e["type"] == "final"]
        assert len(finals) == 1
        final = finals[0]
        assert final["content"] == "Hello world"
        assert final["reasoning_content"] == "thinking...more thoughts."
        assert final["error"] is None

    async def test_reasoning_none_when_provider_does_not_emit(self, monkeypatch):
        from alpha import llm

        monkeypatch.setattr(
            llm,
            "get_provider_config",
            lambda _p: {
                "base_url": "http://x",
                "api_key": "k",
                "model": "gpt-4o",
                "supports_tools": True,
                "api_format": "openai",
                "low_temperature": False,
            },
        )

        chunks = [
            _sse({"choices": [{"delta": {"content": "no reasoning here"}}]}),
            "data: [DONE]",
        ]
        fake_resp = _FakeStreamResponse(chunks)
        monkeypatch.setattr(
            llm.httpx, "AsyncClient", lambda **_: _FakeAsyncClient(fake_resp)
        )

        finals = [
            ev async for ev in llm.stream_chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                provider="openai",
            )
            if ev["type"] == "final"
        ]
        assert len(finals) == 1
        # None (not empty string) so the agent's `if reasoning_content:`
        # check skips the field entirely.
        assert finals[0]["reasoning_content"] is None

    async def test_reasoning_with_tool_calls(self, monkeypatch):
        """Simula o caso real do bug: thinking + tool_call simultaneos."""
        from alpha import llm

        monkeypatch.setattr(
            llm,
            "get_provider_config",
            lambda _p: {
                "base_url": "http://x",
                "api_key": "k",
                "model": "deepseek-reasoner",
                "supports_tools": True,
                "api_format": "openai",
                "low_temperature": False,
            },
        )

        chunks = [
            _sse({"choices": [{"delta": {"reasoning_content": "I should call X"}}]}),
            _sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "c1",
                "function": {"name": "project_overview", "arguments": "{}"},
            }]}}]}),
            "data: [DONE]",
        ]
        fake_resp = _FakeStreamResponse(chunks)
        monkeypatch.setattr(
            llm.httpx, "AsyncClient", lambda **_: _FakeAsyncClient(fake_resp)
        )

        finals = [
            ev async for ev in llm.stream_chat_with_tools(
                messages=[{"role": "user", "content": "analyse the project"}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "project_overview",
                        "description": "x",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }],
                provider="deepseek",
            )
            if ev["type"] == "final"
        ]
        assert len(finals) == 1
        f = finals[0]
        assert f["reasoning_content"] == "I should call X"
        assert len(f["tool_calls"]) == 1
        assert f["tool_calls"][0]["name"] == "project_overview"

        # Round-trip: o que voltaria pra API contem reasoning_content.
        msg = build_assistant_tool_message(
            f["content"], f["tool_calls"], f.get("reasoning_content")
        )
        assert msg["reasoning_content"] == "I should call X"
        assert msg["tool_calls"][0]["function"]["name"] == "project_overview"
