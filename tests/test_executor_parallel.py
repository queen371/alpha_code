"""End-to-end tests for the executor's parallel path.

The agent fans out tool_calls > 1 in `execute_tool_calls`. Coverage gaps
identified during the audit:

- partial-deny: 1 of 3 calls is denied (`is_denied_fn`) while the others run.
- approval_callback denying one of N calls in parallel.
- pre_tool hook with `blocking: true` vetoing a call.
- mixed safety levels: SAFE auto-runs, DESTRUCTIVE waits for approval.

These tests use small mock tools and a programmable approval/deny callback
so the assertions exercise the executor logic directly without real I/O.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from alpha.executor import execute_tool_calls
from alpha.tools import ToolDefinition, ToolSafety


# ─── Helpers ────────────────────────────────────────────────────────


def _mk_tool(name: str, *, safety=ToolSafety.SAFE, result=None):
    """Build a minimal tool that returns `result` (default: {"ok": True})."""
    payload = result if result is not None else {"ok": True, "from": name}

    async def executor(**_kwargs):
        return payload

    return ToolDefinition(
        name=name,
        description=f"mock {name}",
        parameters={"type": "object", "properties": {}},
        safety=safety,
        executor=executor,
    )


def _tc(call_id: str, name: str, args: dict | None = None) -> dict:
    """Build the tool_call shape the executor expects (id, name, JSON args)."""
    return {
        "id": call_id,
        "name": name,
        "arguments": json.dumps(args or {}),
    }


async def _drain(gen):
    return [event async for event in gen]


def _by_type(events, kind):
    return [e for e in events if e["type"] == kind]


# ─── Fan-out tests ──────────────────────────────────────────────────


class TestParallelFanOut:
    """When tool_calls > 1, auto-approved tools run together."""

    @pytest.mark.asyncio
    async def test_three_safe_tools_all_run(self):
        tools = {
            "alpha": _mk_tool("alpha", result={"value": "a"}),
            "beta":  _mk_tool("beta",  result={"value": "b"}),
            "gamma": _mk_tool("gamma", result={"value": "c"}),
        }
        calls = [
            _tc("c1", "alpha"),
            _tc("c2", "beta"),
            _tc("c3", "gamma"),
        ]
        messages: list[dict] = []
        events = await _drain(execute_tool_calls(
            calls, messages,
            needs_approval_fn=lambda *_: False,
            get_tool_fn=lambda n: tools.get(n),
        ))

        # Each call produces (tool_call, tool_result). 3 calls => 6 events.
        assert len(_by_type(events, "tool_call")) == 3
        results = _by_type(events, "tool_result")
        assert len(results) == 3
        # Each tool returned its own value, in some order.
        values = sorted(r["result"]["value"] for r in results)
        assert values == ["a", "b", "c"]

        # Messages got one `tool` entry per call, all with matching ids.
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert {m["tool_call_id"] for m in tool_msgs} == {"c1", "c2", "c3"}


class TestPartialDeny:
    """1-of-N denied — others must still run; denied result is annotated."""

    @pytest.mark.asyncio
    async def test_one_denied_others_run(self):
        tools = {
            "read": _mk_tool("read"),
            "evil": _mk_tool("evil", safety=ToolSafety.DESTRUCTIVE),
            "list": _mk_tool("list"),
        }

        # is_denied_fn returns (True, reason) only for `evil`.
        def is_denied(name, args):
            if name == "evil":
                return (True, "test deny rule")
            return (False, "")

        calls = [_tc("c1", "read"), _tc("c2", "evil"), _tc("c3", "list")]
        messages: list[dict] = []
        events = await _drain(execute_tool_calls(
            calls, messages,
            needs_approval_fn=lambda *_: False,
            is_denied_fn=is_denied,
            get_tool_fn=lambda n: tools.get(n),
        ))

        results = _by_type(events, "tool_result")
        assert len(results) == 3
        by_name = {r["name"]: r["result"] for r in results}

        # The denied one is annotated as such — invariant `ok=false, category=denied`
        assert by_name["evil"].get("ok") is False
        assert by_name["evil"].get("category") == "denied"
        # The others ran successfully.
        assert by_name["read"].get("ok", True) is not False
        assert by_name["list"].get("ok", True) is not False

        # All three call_ids have a matching tool message.
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert {m["tool_call_id"] for m in tool_msgs} == {"c1", "c2", "c3"}


class TestApprovalDenyInParallel:
    """approval_callback returning False denies *only* that call."""

    @pytest.mark.asyncio
    async def test_callback_false_blocks_only_target(self):
        tools = {
            "safe_a":   _mk_tool("safe_a"),
            "needs_ok": _mk_tool("needs_ok", safety=ToolSafety.DESTRUCTIVE),
            "safe_b":   _mk_tool("safe_b"),
        }
        approval_log: list[str] = []

        def callback(tool_name, args):
            approval_log.append(tool_name)
            return False  # deny everything that hits the prompt

        # The 2 SAFE tools won't trigger approval; only `needs_ok` will.
        def needs_approval(name, args):
            return name == "needs_ok"

        calls = [
            _tc("c1", "safe_a"),
            _tc("c2", "needs_ok"),
            _tc("c3", "safe_b"),
        ]
        messages: list[dict] = []
        events = await _drain(execute_tool_calls(
            calls, messages,
            needs_approval_fn=needs_approval,
            approval_callback=callback,
            get_tool_fn=lambda n: tools.get(n),
        ))

        # Approval was asked exactly once, for the destructive tool.
        assert approval_log == ["needs_ok"]

        results = _by_type(events, "tool_result")
        by_name = {r["name"]: r["result"] for r in results}

        # Denied tool got the skipped invariant.
        assert by_name["needs_ok"].get("ok") is False
        assert by_name["needs_ok"].get("category") == "denied"
        # Safe tools ran.
        assert by_name["safe_a"].get("ok", True) is not False
        assert by_name["safe_b"].get("ok", True) is not False


class TestMessageInvariants:
    """Across every parallel scenario, the post-condition for messages is:
    the assistant tool_calls block is followed by ONE tool message per
    call_id. Without this, the next provider turn returns HTTP 400."""

    @pytest.mark.asyncio
    async def test_no_orphan_tool_calls_after_partial_deny(self):
        tools = {
            "ok":  _mk_tool("ok"),
            "no":  _mk_tool("no", safety=ToolSafety.DESTRUCTIVE),
        }

        def is_denied(name, args):
            return (name == "no", "denied for test")

        calls = [_tc("c1", "ok"), _tc("c2", "no")]
        messages: list[dict] = []
        await _drain(execute_tool_calls(
            calls, messages,
            needs_approval_fn=lambda *_: False,
            is_denied_fn=is_denied,
            get_tool_fn=lambda n: tools.get(n),
        ))

        ids = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
        assert ids == {"c1", "c2"}, (
            "Every tool_call must be matched by exactly one tool message — "
            "missing ids would crash the next provider turn with HTTP 400"
        )

    @pytest.mark.asyncio
    async def test_each_call_appears_exactly_once(self):
        tools = {f"t{i}": _mk_tool(f"t{i}") for i in range(5)}
        calls = [_tc(f"c{i}", f"t{i}") for i in range(5)]
        messages: list[dict] = []
        await _drain(execute_tool_calls(
            calls, messages,
            needs_approval_fn=lambda *_: False,
            get_tool_fn=lambda n: tools.get(n),
        ))

        counter = Counter(
            m["tool_call_id"] for m in messages if m.get("role") == "tool"
        )
        assert dict(counter) == {f"c{i}": 1 for i in range(5)}
