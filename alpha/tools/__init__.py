"""
Tool registry for ALPHA agent mode.

Supports:
- Auto-discovery of *_tools.py in this directory
- External plugins from plugins/
- Category and mode filtering
- Runtime enable/disable
"""

import importlib
import logging
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ToolSafety(str, Enum):
    SAFE = "safe"
    DESTRUCTIVE = "destructive"


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    safety: ToolSafety
    executor: Callable[..., Awaitable[dict[str, Any]]]
    category: str = "general"
    modes: list[str] = field(default_factory=list)  # empty = all modes
    enabled: bool = True


TOOL_REGISTRY: dict[str, ToolDefinition] = {}
_tools_loaded = False


def register_tool(tool_def: ToolDefinition):
    """Register a tool in the global registry."""
    TOOL_REGISTRY[tool_def.name] = tool_def
    logger.debug(f"Tool registered: {tool_def.name} [{tool_def.category}]")


def get_openai_tools(mode: str | None = None) -> list[dict]:
    """Return tools in OpenAI function-calling format, optionally filtered by mode."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": td.description,
                "parameters": td.parameters,
            },
        }
        for name, td in TOOL_REGISTRY.items()
        if td.enabled and (not td.modes or mode is None or mode in td.modes)
    ]


def get_tool(name: str) -> ToolDefinition | None:
    """Get a tool by name."""
    return TOOL_REGISTRY.get(name)


def list_tools(mode: str | None = None) -> list[dict]:
    """Return tool metadata for API responses."""
    return [
        {
            "name": td.name,
            "description": td.description,
            "category": td.category,
            "safety": td.safety.value,
            "enabled": td.enabled,
            "modes": td.modes or ["all"],
        }
        for td in TOOL_REGISTRY.values()
        if not td.modes or mode is None or mode in td.modes
    ]


def set_tool_enabled(name: str, enabled: bool) -> bool:
    """Enable or disable a tool at runtime. Returns True if found."""
    td = TOOL_REGISTRY.get(name)
    if td:
        td.enabled = enabled
        return True
    return False


def _discover_builtin_tools():
    """Auto-import all *_tools.py modules in this package."""
    package_dir = Path(__file__).parent
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.endswith("_tools"):
            try:
                importlib.import_module(f".{module_info.name}", package=__package__)
                logger.debug(f"Loaded built-in tools: {module_info.name}")
            except Exception as e:
                logger.error(f"Failed to load tool module {module_info.name}: {e}")


def _discover_plugins():
    """Load external plugins from plugins/ directory."""
    plugins_dir = Path(__file__).parent.parent.parent / "plugins"
    if not plugins_dir.is_dir():
        return

    import sys

    if str(plugins_dir) not in sys.path:
        sys.path.insert(0, str(plugins_dir))

    for item in sorted(plugins_dir.iterdir()):
        if item.suffix == ".py" and not item.name.startswith("_"):
            module_name = item.stem
            try:
                importlib.import_module(module_name)
                logger.info(f"Loaded plugin: {module_name}")
            except Exception as e:
                logger.error(f"Failed to load plugin {module_name}: {e}")
        elif item.is_dir() and (item / "__init__.py").exists():
            try:
                importlib.import_module(item.name)
                logger.info(f"Loaded plugin package: {item.name}")
            except Exception as e:
                logger.error(f"Failed to load plugin package {item.name}: {e}")


def load_all_tools():
    """Discover and load all tools (built-in + plugins). Safe to call multiple times."""
    global _tools_loaded
    if _tools_loaded:
        return
    _discover_builtin_tools()
    _discover_plugins()
    _tools_loaded = True
    logger.info(f"Tool registry loaded: {len(TOOL_REGISTRY)} tools")
