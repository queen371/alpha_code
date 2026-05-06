"""Regression tests for DEEP_LOGIC V1.1 ALTOs.

Cobre #DL012, #DL013, #DL014, #DL019.
"""

import json
from pathlib import Path

import pytest

from alpha import history
from alpha.context import _find_compressible_range
from alpha.executor import _parse_and_validate_args
from alpha.history import _sanitize_for_save


# ─── #DL012 ───────────────────────────────────────────────────────


class TestCompressibleRangeOrphanTool:
    def _msgs(self, *roles):
        out = [{"role": "system", "content": "sys"}]
        for r in roles:
            if r == "assistant_tc":
                out.append({"role": "assistant", "tool_calls": [{"id": "x"}]})
            else:
                out.append({"role": r, "content": "x"})
        return out

    def test_tail_does_not_start_with_orphan_tool(self):
        # 12 messages: range default protege tail=8, end=4 inicialmente.
        # Forcamos messages[4] a ser `tool` para gatilhar o ajuste.
        roles = ["user", "assistant", "user", "assistant_tc", "tool",
                 "assistant", "user", "assistant", "user", "assistant", "user"]
        msgs = self._msgs(*roles)
        start, end = _find_compressible_range(msgs, tail=8)
        # tail comeca em messages[end]; nao pode ser role=tool
        if end > 0:
            assert msgs[end].get("role") != "tool"

    def test_range_does_not_split_assistant_tool_pair(self):
        roles = ["user", "assistant", "user", "assistant", "user",
                 "assistant_tc", "tool", "user", "assistant", "user", "assistant"]
        msgs = self._msgs(*roles)
        start, end = _find_compressible_range(msgs, tail=4)
        # range comprimido nao pode terminar em assistant.tool_calls
        if end > start:
            last_compressed = msgs[end - 1]
            assert not (
                last_compressed.get("role") == "assistant"
                and last_compressed.get("tool_calls")
            )

    def test_returns_zero_when_too_short(self):
        msgs = [{"role": "system", "content": "s"}]
        assert _find_compressible_range(msgs, tail=8) == (0, 0)


# ─── #DL013 ───────────────────────────────────────────────────────


class _FakeToolDef:
    def __init__(self, parameters: dict | None = None):
        self.parameters = parameters or {}


class TestParseAndValidateArgs:
    def test_invalid_json_returns_structured_error(self):
        tc = {"id": "1", "name": "x", "arguments": "{not json"}
        args, err = _parse_and_validate_args(tc, _FakeToolDef())
        assert args is None
        assert err["error"].startswith("Invalid JSON")
        assert "raw_preview" in err

    def test_non_object_args_rejected(self):
        tc = {"id": "1", "name": "x", "arguments": '"a string"'}
        args, err = _parse_and_validate_args(tc, _FakeToolDef())
        assert args is None
        assert "must be a JSON object" in err["error"]

    def test_missing_required_field(self):
        tool_def = _FakeToolDef({
            "properties": {"path": {"type": "string"}, "mode": {"type": "string"}},
            "required": ["path"],
        })
        tc = {"id": "1", "name": "x", "arguments": "{}"}
        args, err = _parse_and_validate_args(tc, tool_def)
        assert args is None
        assert err["missing"] == ["path"]

    def test_extra_fields_silently_dropped(self):
        tool_def = _FakeToolDef({
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })
        tc = {"id": "1", "name": "x",
              "arguments": json.dumps({"path": "/tmp", "extra": "ignored"})}
        args, err = _parse_and_validate_args(tc, tool_def)
        assert err is None
        assert args == {"path": "/tmp"}

    def test_empty_args_passes_with_no_required(self):
        tool_def = _FakeToolDef()
        tc = {"id": "1", "name": "x", "arguments": ""}
        args, err = _parse_and_validate_args(tc, tool_def)
        assert err is None
        assert args == {}


# ─── #DL014 ───────────────────────────────────────────────────────


class TestDelegateParallelCap:
    @pytest.mark.asyncio
    async def test_too_many_tasks_rejected(self):
        from alpha.tools.delegate_tools import _delegate_parallel
        tasks = json.dumps(["task " + str(i) for i in range(50)])
        result = await _delegate_parallel(tasks=tasks)
        assert "error" in result
        assert "Too many tasks" in result["error"]
        assert result["submitted"] == 50
        assert result["limit"] == 10


# ─── #DL019 ───────────────────────────────────────────────────────


class TestSanitizeForSave:
    def test_drops_orphan_tool_at_start(self):
        msgs = [
            {"role": "tool", "tool_call_id": "x", "content": "result"},
            {"role": "user", "content": "hi"},
        ]
        out = _sanitize_for_save(msgs)
        assert all(m.get("role") != "tool" for m in out)
        assert len(out) == 1

    def test_drops_trailing_assistant_tool_calls_without_responses(self):
        msgs = [
            {"role": "user", "content": "do x"},
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},
        ]
        out = _sanitize_for_save(msgs)
        assert all(m.get("role") != "assistant" or not m.get("tool_calls")
                   for m in out)

    def test_keeps_complete_pair(self):
        msgs = [
            {"role": "user", "content": "do x"},
            {"role": "assistant", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]
        out = _sanitize_for_save(msgs)
        assert len(out) == 4

    def test_partial_responses_drop_assistant(self):
        # Assistant emite 2 tool_calls, so 1 respondido -> drop assistant.
        msgs = [
            {"role": "user", "content": "do x"},
            {"role": "assistant", "tool_calls": [{"id": "c1"}, {"id": "c2"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
        out = _sanitize_for_save(msgs)
        # ambos assistant.tool_calls e o tool response correspondente
        # ficam orfaos -> sanitizer remove o assistant
        assert not any(
            m.get("role") == "assistant" and m.get("tool_calls")
            for m in out
        )


class TestSaveSessionRoundtripWithInterruption:
    def test_save_after_ctrlc_loads_cleanly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(history, "_HISTORY_DIR", tmp_path / "hist")
        # Cenario: Ctrl+C apos assistant.tool_calls, sem tool response.
        messages = [
            {"role": "user", "content": "do x"},
            {"role": "assistant", "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "f", "arguments": "{}"},
            }]},
        ]
        history.save_session("interrupted", messages)
        loaded = history.load_session("interrupted")
        # ultima entrada nao pode ser assistant.tool_calls orfa
        assert loaded
        last = loaded[-1]
        assert not (last.get("role") == "assistant" and last.get("tool_calls"))
