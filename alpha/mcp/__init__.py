"""Model Context Protocol (MCP) integration for Alpha Code.

Exposes a stdio-only client that connects to external MCP servers and
auto-registers their tools into the global alpha TOOL_REGISTRY under the
prefix `mcp__<server>__<tool>`.
"""

from .client import MCPClient, MCPError
from .config import MCPServerConfig, find_config_file, load_mcp_config
from .loader import (
    list_active_servers,
    load_mcp_servers,
    shutdown_mcp_servers,
)

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPServerConfig",
    "find_config_file",
    "list_active_servers",
    "load_mcp_config",
    "load_mcp_servers",
    "shutdown_mcp_servers",
]
