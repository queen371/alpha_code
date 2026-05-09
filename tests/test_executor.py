"""Tests for the tool executor."""

import asyncio
import json

import pytest

from alpha.executor import _execute_single_tool, _format_result, build_assistant_tool_message
from alpha.config import TOOL_RESULT_MAX_CHARS


class TestBuildAssistantMessage:
    def test_basic_message(self):
        msg = build_assistant_tool_message("hello", [
            {"id": "c1", "name": "read_file", "arguments": '{"path": "f.py"}'},
        ])
        assert msg["role"] == "assistant"
        assert msg["content"] == "hello"
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["function"]["name"] == "read_file"

    def test_none_content(self):
        msg = build_assistant_tool_message(None, [
            {"id": "c1", "name": "test", "arguments": "{}"},
        ])
        assert msg["content"] is None

    def test_multiple_tool_calls(self):
        msg = build_assistant_tool_message("", [
            {"id": "c1", "name": "a", "arguments": "{}"},
            {"id": "c2", "name": "b", "arguments": "{}"},
        ])
        assert len(msg["tool_calls"]) == 2


class TestFormatResult:
    def test_short_result(self):
        result = {"success": True, "data": "hello"}
        formatted = _format_result(result, "test")
        assert "hello" in formatted
        assert "truncated" not in formatted

    def test_long_result_truncated(self):
        result = {"data": "x" * (TOOL_RESULT_MAX_CHARS + 1000)}
        formatted = _format_result(result, "test")
        parsed = json.loads(formatted)
        assert parsed["truncated"] is True
        assert len(formatted) <= TOOL_RESULT_MAX_CHARS + 200


class TestExecuteSingleTool:
    @pytest.mark.asyncio
    async def test_successful_execution(self):
        class MockTool:
            async def executor(self, x=1):
                return {"result": x * 2}

        result = await _execute_single_tool(MockTool(), "mock", {"x": 5})
        assert result == {"result": 10}

    @pytest.mark.asyncio
    async def test_timeout(self):
        class SlowTool:
            async def executor(self):
                await asyncio.sleep(10)
                return {"done": True}

        # Patch timeout to 0.1s for test speed
        import alpha.executor as ex
        orig = ex.TOOL_EXECUTION_TIMEOUT
        ex.TOOL_EXECUTION_TIMEOUT = 0.1
        try:
            result = await _execute_single_tool(SlowTool(), "slow", {})
            assert "error" in result
            assert "timed out" in result["error"]
        finally:
            ex.TOOL_EXECUTION_TIMEOUT = orig

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        class BadTool:
            async def executor(self):
                raise ValueError("something broke")

        result = await _execute_single_tool(BadTool(), "bad", {})
        assert "error" in result
        assert "something broke" in result["error"]
