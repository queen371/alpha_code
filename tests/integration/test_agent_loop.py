"""End-to-end tests for ``run_agent`` against a scripted mock LLM.

These tests exercise the full agent loop (LLM call → tool dispatch → executor
→ next LLM call) without ever hitting a real provider. Each test scripts a
sequence of LLM "turns" (one per iteration) and asserts on the events the
agent yields and the messages it accumulates.

The mock replaces ``stream_chat_with_tools`` with an async generator that
serves pre-recorded turns in order. Each turn is one of:
- ``("text", str)``               — model returns plain text, no tool calls
- ``("tools", str|None, [calls])``— model returns tool_calls (calls is a list
                                    of ``{"id","name","arguments"}`` dicts)

Use these to add coverage as new agent behaviors land.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import alpha.agent as agent_mod
from alpha.tools import ToolDefinition, ToolSafety


# ─── Mock provider ────────────────────────────────────────────────


def _make_mock_stream(turns: list[tuple]):
    """Build a stand-in for ``stream_chat_with_tools`` that replays ``turns``.

    Each call consumes the next turn. Running out of turns is a test bug —
    the agent should have stopped before — so we raise loudly.
    """
    iter_state = {"i": 0}

    async def fake_stream(messages, tools, temperature=0.5, provider="deepseek"):
        i = iter_state["i"]
        iter_state["i"] += 1
        if i >= len(turns):
            raise AssertionError(
                f"Mock LLM ran out of scripted turns after {i} calls — "
                "the agent looped past the script."
            )
        turn = turns[i]
        kind = turn[0]

        if kind == "text":
            content = turn[1]
            for ch in content:
                yield {"type": "content_token", "token": ch}
            yield {
                "type": "final",
                "content": content,
                "tool_calls": [],
                "error": None,
            }
        elif kind == "tools":
            content = turn[1] or ""
            calls = turn[2]
            if content:
                for ch in content:
                    yield {"type": "content_token", "token": ch}
            yield {
                "type": "final",
                "content": content,
                "tool_calls": calls,
                "error": None,
            }
        else:
            raise ValueError(f"Unknown mock turn kind: {kind}")

    return fake_stream, iter_state


# ─── Mock tool registry ──────────────────────────────────────────


def _make_mock_tool(name: str, result: dict[str, Any]) -> ToolDefinition:
    """Build a ToolDefinition that returns ``result`` when called."""

    async def executor(**_kwargs):
        return result

    return ToolDefinition(
        name=name,
        description=f"mock {name}",
        parameters={"type": "object", "properties": {}},
        safety=ToolSafety.SAFE,
        executor=executor,
    )


def _get_tool_fn(tools: dict[str, ToolDefinition]):
    return lambda n: tools.get(n)


# ─── Helpers ─────────────────────────────────────────────────────


async def _drain(gen) -> list[dict]:
    return [event async for event in gen]


def _patch_stream(monkeypatch, fake_stream):
    """Patch every call site of stream_chat_with_tools.

    The agent imports it at module load (``from .llm import ...``), so
    patching ``alpha.llm.stream_chat_with_tools`` alone misses the binding
    inside ``alpha.agent``. We patch both for safety.
    """
    monkeypatch.setattr(agent_mod, "stream_chat_with_tools", fake_stream)
    import alpha.llm as llm_mod
    monkeypatch.setattr(llm_mod, "stream_chat_with_tools", fake_stream)


# ─── Tests ───────────────────────────────────────────────────────


class TestHappyPath:
    """One tool call, one final response."""

    @pytest.mark.asyncio
    async def test_single_tool_then_text_completes(self, monkeypatch):
        tools = {"echo": _make_mock_tool("echo", {"ok": True, "value": "hi"})}

        turns = [
            ("tools", "Calling echo.", [
                {"id": "call_1", "name": "echo", "arguments": "{}"},
            ]),
            ("text", "Done. Echoed hi."),
        ]
        fake, state = _make_mock_stream(turns)
        _patch_stream(monkeypatch, fake)

        messages = [{"role": "system", "content": "sys"},
                    {"role": "user", "content": "do it"}]
        events = await _drain(agent_mod.run_agent(
            messages=messages,
            user_message="do it",
            provider="deepseek",
            get_tool_fn=_get_tool_fn(tools),
            tools=[],
        ))

        types = [e["type"] for e in events]
        assert "tool_call" in types
        assert "tool_result" in types
        assert types[-1] == "done"
        # full_response accumulates across iterations.
        assert "Done. Echoed hi." in events[-1]["reply"]
        # Mock served exactly 2 turns; agent didn't loop further.
        assert state["i"] == 2


class TestLoopDetection:
    """Loop detection should fire only after the configured min iteration,
    and the forced-final message should not contain orphan tool_calls."""

    @pytest.mark.asyncio
    async def test_sibling_paths_in_iter_2_does_not_force_final(
        self, monkeypatch
    ):
        # Regression for the bug seen in screenshot: parallel tool batch
        # across sibling paths in iter 2 was flagged as a loop.
        tools = {"list_directory": _make_mock_tool(
            "list_directory", {"entries": []}
        )}

        sibling_calls = [
            {"id": f"c{i}", "name": "list_directory",
             "arguments": json.dumps({"path": f"/proj/{name}"})}
            for i, name in enumerate(["alpha", "agents", "tests",
                                      "skills", "docs", "scripts"])
        ]
        turns = [
            ("tools", None, [
                {"id": "c0", "name": "list_directory",
                 "arguments": json.dumps({"path": "/proj"})},
            ]),
            ("tools", "Mapping subdirs.", sibling_calls),
            ("text", "Project mapped."),
        ]
        fake, state = _make_mock_stream(turns)
        _patch_stream(monkeypatch, fake)

        events = await _drain(agent_mod.run_agent(
            messages=[{"role": "user", "content": "analyze"}],
            user_message="analyze",
            provider="deepseek",
            get_tool_fn=_get_tool_fn(tools),
            tools=[],
        ))
        assert events[-1]["type"] == "done"
        assert "Project mapped." in events[-1]["reply"]
        # All three turns consumed → loop did not force-final early.
        assert state["i"] == 3

    @pytest.mark.asyncio
    async def test_real_loop_eventually_forces_final(self, monkeypatch):
        # Same exact call hammered → loop detection fires once
        # _LOOP_DETECT_MIN_ITER is reached (iter 3) AND there are 3 exact
        # repeats. The forced-final stream then consumes the next turn.
        tools = {"read_file": _make_mock_tool(
            "read_file", {"content": "x"}
        )}
        repeat_call = {"id": "c0", "name": "read_file",
                       "arguments": json.dumps({"path": "/etc/hosts"})}
        turns = [
            ("tools", None, [dict(repeat_call, id=f"c{i}")])
            for i in range(3)
        ] + [("text", "FORCED_FINAL_TEXT")]

        fake, _ = _make_mock_stream(turns)
        _patch_stream(monkeypatch, fake)

        messages = [{"role": "user", "content": "analyze"}]
        events = await _drain(agent_mod.run_agent(
            messages=messages,
            user_message="analyze",
            provider="deepseek",
            get_tool_fn=_get_tool_fn(tools),
            tools=[],
        ))
        assert events[-1]["type"] == "done"
        # The forced final's text must surface in the reply.
        assert "FORCED_FINAL_TEXT" in events[-1]["reply"]

    @pytest.mark.asyncio
    async def test_loop_bailout_does_not_leave_orphan_tool_calls(
        self, monkeypatch
    ):
        # Regression: before the fix, when loop fired we appended a user
        # note but skipped appending the assistant message — and on the next
        # provider call, any later request that included the partial state
        # would see assistant.tool_calls without matching tool responses
        # (HTTP 400). After the fix, we either drop the tool_calls entirely
        # or append matching responses. Verify no orphan persists.
        tools = {"read_file": _make_mock_tool(
            "read_file", {"content": "x"}
        )}
        repeat_call = {"id": "c0", "name": "read_file",
                       "arguments": json.dumps({"path": "/etc/hosts"})}
        turns = [
            ("tools", "loop content", [dict(repeat_call, id=f"c{i}")])
            for i in range(3)
        ] + [("text", "ok")]

        fake, _ = _make_mock_stream(turns)
        _patch_stream(monkeypatch, fake)

        messages = [{"role": "user", "content": "x"}]
        await _drain(agent_mod.run_agent(
            messages=messages,
            user_message="x",
            provider="deepseek",
            get_tool_fn=_get_tool_fn(tools),
            tools=[],
        ))

        # Walk the history: every assistant message with tool_calls must be
        # followed by tool messages whose tool_call_id covers each call.
        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                ids = {tc["id"] for tc in msg["tool_calls"]}
                # Find subsequent tool responses
                responded: set[str] = set()
                for later in messages[i + 1:]:
                    if later.get("role") == "tool":
                        responded.add(later.get("tool_call_id"))
                    elif later.get("role") in ("user", "assistant"):
                        # Allow user/assistant after responses are complete
                        if not ids - responded:
                            break
                missing = ids - responded
                assert not missing, (
                    f"Orphan tool_calls without matching tool responses: "
                    f"{missing}"
                )


class TestPlainTextResponse:
    """Pure chat (no tool calls) finishes in one turn."""

    @pytest.mark.asyncio
    async def test_chat_response_completes_in_one_turn(self, monkeypatch):
        turns = [("text", "Olá. Como posso ajudar?")]
        fake, state = _make_mock_stream(turns)
        _patch_stream(monkeypatch, fake)

        events = await _drain(agent_mod.run_agent(
            messages=[{"role": "user", "content": "oi"}],
            user_message="oi",
            provider="deepseek",
            get_tool_fn=lambda n: None,
            tools=[],
        ))
        assert events[-1]["type"] == "done"
        assert events[-1]["reply"] == "Olá. Como posso ajudar?"
        assert state["i"] == 1
