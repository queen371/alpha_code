"""Connect to MCP servers and register their tools in the alpha tool registry.

The loader is idempotent: calling `load_mcp_servers()` twice does nothing
the second time. `shutdown_mcp_servers()` terminates all subprocesses.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..tools import ToolDefinition, ToolSafety, register_tool
from .client import MCPClient, MCPError
from .config import MCPServerConfig, load_mcp_config

logger = logging.getLogger(__name__)

TOOL_PREFIX = "mcp__"
_active_clients: list[MCPClient] = []
_loaded = False


def _qualified_name(server: str, tool: str) -> str:
    return f"{TOOL_PREFIX}{server}__{tool}"


def _format_tool_result(raw: dict) -> dict[str, Any]:
    """Convert an MCP tools/call result to the alpha tool-result shape."""
    if not isinstance(raw, dict):
        return {"output": str(raw)}

    parts: list[str] = []
    for item in raw.get("content", []) or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind == "resource":
            resource = item.get("resource", {})
            uri = resource.get("uri", "")
            text = resource.get("text", "")
            parts.append(f"[resource {uri}]\n{text}" if text else f"[resource {uri}]")
        else:
            parts.append(f"[{kind} content omitted]")

    text = "\n".join(parts).strip()
    if raw.get("isError"):
        return {"error": text or "MCP tool returned isError without content"}
    return {"output": text} if text else {"output": ""}


def _make_executor(client: MCPClient, tool_name: str):
    async def executor(**kwargs) -> dict[str, Any]:
        try:
            raw = await asyncio.to_thread(client.call_tool, tool_name, kwargs)
        except MCPError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        return _format_tool_result(raw)

    return executor


def _register_server_tools(client: MCPClient) -> int:
    count = 0
    for tool in client.tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        qualified = _qualified_name(client.name, name)
        params = tool.get("inputSchema") or {"type": "object", "properties": {}}
        register_tool(
            ToolDefinition(
                name=qualified,
                description=tool.get("description") or f"MCP tool {name} from {client.name}",
                parameters=params,
                safety=ToolSafety.DESTRUCTIVE,  # MCP tools require approval by default
                executor=_make_executor(client, name),
                category=f"mcp:{client.name}",
            )
        )
        count += 1
    return count


def _connect_one(spec: MCPServerConfig) -> MCPClient | None:
    client = MCPClient(
        name=spec.name,
        command=spec.command,
        args=spec.args,
        env=spec.env,
    )
    try:
        client.start()
        client.initialize()
        client.list_tools()
    except MCPError as e:
        logger.warning("MCP '%s' failed to start: %s", spec.name, e)
        client.stop()
        return None
    except Exception as e:
        logger.warning("MCP '%s' unexpected error: %s", spec.name, e)
        client.stop()
        return None
    return client


def load_mcp_servers() -> list[MCPClient]:
    """Spawn all enabled MCP servers and register their tools.

    Returns the list of clients that connected successfully. Failures are
    logged and skipped — a broken server config never blocks startup.
    """
    global _loaded
    if _loaded:
        return list(_active_clients)

    specs = load_mcp_config()
    enabled = [s for s in specs if not s.disabled]
    if not enabled:
        _loaded = True
        return []

    for spec in enabled:
        client = _connect_one(spec)
        if client is None:
            continue
        n = _register_server_tools(client)
        logger.info("MCP '%s' connected with %d tool(s)", client.name, n)
        _active_clients.append(client)

    _loaded = True
    return list(_active_clients)


def shutdown_mcp_servers() -> None:
    global _loaded
    for client in _active_clients:
        try:
            client.stop()
        except Exception:
            pass
    _active_clients.clear()
    _loaded = False


def list_active_servers() -> list[dict]:
    return [
        {"name": c.name, "tools": [t.get("name") for t in c.tools]}
        for c in _active_clients
    ]
