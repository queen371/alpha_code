"""Tests for present_plan and todo_write."""

import pytest

from alpha.tools import ToolSafety, get_tool, load_all_tools
from alpha.tools.plan_tools import VALID_TODO_STATUSES, _present_plan, _todo_write


def setup_module():
    load_all_tools()


def test_display_glyph_keys_match_tool_statuses():
    # Catches drift between the tool's status enum and the display's glyph map.
    from alpha.display import _TODO_STATUS_GLYPH

    assert set(_TODO_STATUS_GLYPH) == set(VALID_TODO_STATUSES)


class TestRegistration:
    def test_present_plan_registered(self):
        td = get_tool("present_plan")
        assert td is not None
        assert td.safety == ToolSafety.DESTRUCTIVE  # gates execution behind approval
        assert td.category == "planning"

    def test_todo_write_registered(self):
        td = get_tool("todo_write")
        assert td is not None
        assert td.safety == ToolSafety.SAFE
        assert td.category == "planning"


class TestPresentPlan:
    @pytest.mark.asyncio
    async def test_valid_plan_returns_approved(self):
        result = await _present_plan(
            summary="Refactor logger module",
            steps=["Read logger.py", "Extract config", "Write tests"],
        )
        assert result["approved"] is True
        assert result["summary"] == "Refactor logger module"
        assert result["steps"] == ["Read logger.py", "Extract config", "Write tests"]

    @pytest.mark.asyncio
    async def test_empty_summary_rejected(self):
        result = await _present_plan(summary="   ", steps=["x"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_steps_rejected(self):
        result = await _present_plan(summary="ok", steps=[])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_steps_must_be_list(self):
        result = await _present_plan(summary="ok", steps="not a list")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_blank_step_rejected(self):
        result = await _present_plan(summary="ok", steps=["a", "  ", "b"])
        assert "error" in result
        assert "step 2" in result["error"]


class TestTodoWrite:
    @pytest.mark.asyncio
    async def test_valid_list(self):
        todos = [
            {"content": "Read file", "status": "completed"},
            {"content": "Write test", "status": "in_progress"},
            {"content": "Run suite", "status": "pending"},
        ]
        result = await _todo_write(todos)
        assert result["ok"] is True
        assert result["counts"]["completed"] == 1
        assert result["counts"]["in_progress"] == 1
        assert result["counts"]["pending"] == 1
        assert "warning" not in result

    @pytest.mark.asyncio
    async def test_multiple_in_progress_warns(self):
        todos = [
            {"content": "A", "status": "in_progress"},
            {"content": "B", "status": "in_progress"},
        ]
        result = await _todo_write(todos)
        assert result["ok"] is True
        assert "warning" in result
        assert "in_progress" in result["warning"]

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self):
        result = await _todo_write([{"content": "x", "status": "weird"}])
        assert "error" in result
        assert "weird" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_content_rejected(self):
        result = await _todo_write([{"content": "  ", "status": "pending"}])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_non_list_rejected(self):
        result = await _todo_write("not a list")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_list_is_valid(self):
        result = await _todo_write([])
        assert result["ok"] is True
        assert result["todos"] == []
