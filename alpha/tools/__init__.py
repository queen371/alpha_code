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


def get_openai_tools(
    mode: str | None = None,
    name_filter: "Callable[[list[str]], list[str]] | None" = None,
) -> list[dict]:
    """Return tools in OpenAI function-calling format.

    Args:
        mode: Optional mode filter (keeps tools whose modes include it).
        name_filter: Optional function that takes all eligible tool names and
            returns a narrowed list (e.g. from an AgentScope's filter_tools).
    """
    eligible = [
        (name, td) for name, td in TOOL_REGISTRY.items()
        if td.enabled and (not td.modes or mode is None or mode in td.modes)
    ]
    if name_filter is not None:
        allowed = set(name_filter([name for name, _ in eligible]))
        eligible = [(n, td) for n, td in eligible if n in allowed]
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": td.description,
                "parameters": td.parameters,
            },
        }
        for name, td in eligible
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

    # Carregar via `spec_from_file_location` com prefix `alpha_plugin_` em vez
    # de `import_module(name)` + `sys.path.insert(0, ...)`. O esquema antigo
    # poderia silenciosamente sombrear stdlib (plugin chamado `json.py` ou
    # `os.py` viraria a importacao default em todo o processo) ou simplesmente
    # carregar o stdlib em vez do plugin (priority de path).
    import importlib.util

    for item in sorted(plugins_dir.iterdir()):
        if item.suffix == ".py" and not item.name.startswith("_"):
            module_name = item.stem
            qualified = f"alpha_plugin_{module_name}"
            try:
                spec = importlib.util.spec_from_file_location(qualified, item)
                if spec is None or spec.loader is None:
                    logger.error(f"Plugin {module_name}: no loader")
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                logger.info(f"Loaded plugin: {module_name}")
            except Exception as e:
                logger.error(f"Failed to load plugin {module_name}: {e}")
        elif item.is_dir() and (item / "__init__.py").exists():
            qualified = f"alpha_plugin_{item.name}"
            init_path = item / "__init__.py"
            try:
                spec = importlib.util.spec_from_file_location(
                    qualified, init_path, submodule_search_locations=[str(item)]
                )
                if spec is None or spec.loader is None:
                    logger.error(f"Plugin package {item.name}: no loader")
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
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
