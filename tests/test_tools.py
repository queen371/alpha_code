"""Tests for the tool registry system."""

from alpha.tools import (
    ToolSafety,
    get_openai_tools,
    get_tool,
    load_all_tools,
)


def setup_module():
    load_all_tools()


class TestToolRegistry:
    def test_tools_loaded(self):
        tools = get_openai_tools()
        assert len(tools) > 10

    def test_tool_has_required_fields(self):
        tools = get_openai_tools()
        for t in tools:
            assert "function" in t
            assert "name" in t["function"]
            assert "description" in t["function"]
            assert "parameters" in t["function"]

    def test_get_tool_by_name(self):
        tool = get_tool("read_file")
        assert tool is not None
        assert tool.name == "read_file"
        assert tool.safety == ToolSafety.SAFE

    def test_get_unknown_tool(self):
        tool = get_tool("nonexistent_tool_xyz")
        assert tool is None

    def test_delegate_tools_registered(self):
        assert get_tool("delegate_task") is not None
        assert get_tool("delegate_parallel") is not None

    def test_delegate_tools_are_safe(self):
        dt = get_tool("delegate_task")
        dp = get_tool("delegate_parallel")
        assert dt.safety == ToolSafety.SAFE
        assert dp.safety == ToolSafety.SAFE

    def test_delegate_tools_in_agent_category(self):
        dt = get_tool("delegate_task")
        dp = get_tool("delegate_parallel")
        assert dt.category == "agent"
        assert dp.category == "agent"

    def test_tool_names_unique(self):
        tools = get_openai_tools()
        names = [t["function"]["name"] for t in tools]
        assert len(names) == len(set(names)), "Duplicate tool names found"

    def test_all_tools_have_executor(self):
        tools = get_openai_tools()
        for t in tools:
            tool_def = get_tool(t["function"]["name"])
            assert tool_def.executor is not None
            assert callable(tool_def.executor)
