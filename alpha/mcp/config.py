"""Loader for `.alpha/mcp.json` — the MCP server configuration file.

Schema (Claude-Code compatible subset):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {"FOO": "bar"},
      "disabled": false
    }
  }
}
```

Environment-variable expansion: any string in `args`/`env` of the form
`${VAR}` is expanded from the parent process environment. Missing vars
fall through unchanged so the user sees what's wrong instead of a silent
empty value.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..settings import find_config_file as _find_alpha_config
from ..settings import read_json

logger = logging.getLogger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    disabled: bool = False


def _expand(value: str) -> str:
    def repl(m: re.Match) -> str:
        var = m.group(1)
        return os.environ.get(var, m.group(0))

    return _ENV_VAR_PATTERN.sub(repl, value)


def find_config_file() -> Path | None:
    return _find_alpha_config("mcp.json")


def load_mcp_config(path: Path | None = None) -> list[MCPServerConfig]:
    """Parse the MCP config file. Returns [] if no config is present."""
    if path is None:
        path = find_config_file()
    raw = read_json(path, default=None)
    if not isinstance(raw, dict):
        return []

    servers_block = raw.get("mcpServers") or raw.get("servers") or {}
    if not isinstance(servers_block, dict):
        logger.warning("MCP config at %s: 'mcpServers' must be an object", path)
        return []

    out: list[MCPServerConfig] = []
    for name, spec in servers_block.items():
        if not isinstance(spec, dict):
            logger.warning("MCP server '%s': entry must be an object, skipping", name)
            continue
        command = spec.get("command")
        if not isinstance(command, str) or not command:
            logger.warning("MCP server '%s': missing 'command', skipping", name)
            continue

        args = [_expand(str(a)) for a in spec.get("args", []) or []]
        env_raw = spec.get("env") or {}
        env = {str(k): _expand(str(v)) for k, v in env_raw.items()}
        disabled = bool(spec.get("disabled", False))

        out.append(
            MCPServerConfig(
                name=name,
                command=command,
                args=args,
                env=env,
                disabled=disabled,
            )
        )
    return out
