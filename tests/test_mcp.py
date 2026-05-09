"""Tests for the MCP client + loader.

The end-to-end test spawns a tiny stub MCP server (this same Python file run
with `--server`) so we exercise the real subprocess + JSON-RPC path without
adding a network or external-binary dependency.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from alpha.mcp.client import MCPClient, MCPError
from alpha.mcp.config import MCPServerConfig, load_mcp_config
from alpha.mcp.loader import _format_tool_result


# ── Stub server ──

STUB_SERVER = textwrap.dedent(
    """
    import json, sys

    def reply(req_id, result):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\\n")
        sys.stdout.flush()

    def err(req_id, code, msg):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}}) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "initialize":
            reply(req_id, {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "stub"}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            reply(req_id, {"tools": [
                {"name": "echo", "description": "Echo back text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
                {"name": "boom", "description": "Always errors", "inputSchema": {"type": "object", "properties": {}}},
            ]})
        elif method == "tools/call":
            params = msg.get("params", {})
            tool = params.get("name")
            args = params.get("arguments", {})
            if tool == "echo":
                reply(req_id, {"content": [{"type": "text", "text": args.get("text", "")}], "isError": False})
            elif tool == "boom":
                reply(req_id, {"content": [{"type": "text", "text": "kaboom"}], "isError": True})
            else:
                err(req_id, -32601, f"unknown tool: {tool}")
        else:
            err(req_id, -32601, f"unknown method: {method}")
    """
).strip()


@pytest.fixture
def stub_server_path(tmp_path: Path) -> Path:
    p = tmp_path / "stub_mcp_server.py"
    p.write_text(STUB_SERVER, encoding="utf-8")
    return p


@pytest.fixture
def mcp_client(stub_server_path: Path):
    client = MCPClient(name="stub", command=sys.executable, args=[str(stub_server_path)])
    client.start()
    try:
        yield client
    finally:
        client.stop()


# ── Client / protocol ──


class TestMCPClient:
    def test_initialize_and_list_tools(self, mcp_client: MCPClient):
        mcp_client.initialize()
        tools = mcp_client.list_tools()
        names = [t["name"] for t in tools]
        assert names == ["echo", "boom"]

    def test_call_tool_success(self, mcp_client: MCPClient):
        mcp_client.initialize()
        mcp_client.list_tools()
        result = mcp_client.call_tool("echo", {"text": "hello"})
        assert result["isError"] is False
        assert result["content"][0]["text"] == "hello"

    def test_call_tool_isError_propagates(self, mcp_client: MCPClient):
        mcp_client.initialize()
        result = mcp_client.call_tool("boom", {})
        assert result["isError"] is True

    def test_unknown_tool_returns_jsonrpc_error(self, mcp_client: MCPClient):
        mcp_client.initialize()
        with pytest.raises(MCPError, match="unknown tool"):
            mcp_client.call_tool("does_not_exist", {})


class TestMissingBinary:
    def test_start_raises_when_command_missing(self):
        client = MCPClient(name="ghost", command="/nonexistent/binary", args=[])
        with pytest.raises(MCPError, match="Cannot spawn"):
            client.start()


# ── Result formatting ──


class TestFormatToolResult:
    def test_text_content_concatenated(self):
        raw = {"content": [{"type": "text", "text": "hi"}, {"type": "text", "text": "ya"}], "isError": False}
        assert _format_tool_result(raw) == {"output": "hi\nya"}

    def test_error_returns_error_key(self):
        raw = {"content": [{"type": "text", "text": "boom"}], "isError": True}
        assert _format_tool_result(raw) == {"error": "boom"}

    def test_empty_output(self):
        assert _format_tool_result({"content": [], "isError": False}) == {"output": ""}

    def test_non_dict_input(self):
        assert _format_tool_result("oops") == {"output": "oops"}


# ── Config loader ──


class TestConfigLoader:
    def test_no_file_returns_empty(self, tmp_path: Path):
        assert load_mcp_config(tmp_path / "missing.json") == []

    def test_invalid_json_returns_empty(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        assert load_mcp_config(p) == []

    def test_parses_servers(self, tmp_path: Path):
        p = tmp_path / "mcp.json"
        p.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "fs": {"command": "echo", "args": ["a", "b"], "env": {"K": "v"}},
                        "off": {"command": "true", "disabled": True},
                    }
                }
            ),
            encoding="utf-8",
        )
        servers = load_mcp_config(p)
        assert len(servers) == 2
        fs = next(s for s in servers if s.name == "fs")
        assert fs.command == "echo"
        assert fs.args == ["a", "b"]
        assert fs.env == {"K": "v"}
        assert fs.disabled is False
        off = next(s for s in servers if s.name == "off")
        assert off.disabled is True

    def test_skips_entries_without_command(self, tmp_path: Path):
        p = tmp_path / "mcp.json"
        p.write_text(json.dumps({"mcpServers": {"bad": {"args": []}}}), encoding="utf-8")
        assert load_mcp_config(p) == []

    def test_env_var_expansion(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        p = tmp_path / "mcp.json"
        p.write_text(
            json.dumps({"mcpServers": {"x": {"command": "cmd", "args": ["${MY_TOKEN}"], "env": {"T": "${MY_TOKEN}"}}}}),
            encoding="utf-8",
        )
        servers = load_mcp_config(p)
        assert servers[0].args == ["secret123"]
        assert servers[0].env == {"T": "secret123"}

    def test_unknown_env_var_falls_through(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("DEFINITELY_NOT_SET_xyz", raising=False)
        p = tmp_path / "mcp.json"
        p.write_text(
            json.dumps({"mcpServers": {"x": {"command": "cmd", "args": ["${DEFINITELY_NOT_SET_xyz}"]}}}),
            encoding="utf-8",
        )
        servers = load_mcp_config(p)
        assert servers[0].args == ["${DEFINITELY_NOT_SET_xyz}"]
