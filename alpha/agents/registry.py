"""Agent registry — discovers agent.yaml files in known locations.

Backed por `alpha._registry.FileBackedRegistry` para evitar duplicacao
com skills/registry.py (#DM008).
"""

from __future__ import annotations

from pathlib import Path

from .._registry import FileBackedRegistry
from .loader import load_agent_file
from .scope import AgentScope

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SEARCH_PATHS = [
    _PROJECT_ROOT / "agents",
    Path.home() / ".alpha" / "agents",
]

_registry: FileBackedRegistry[AgentScope] = FileBackedRegistry(
    _SEARCH_PATHS, "*/agent.yaml", load_agent_file, kind="agent"
)


def load_all_agents(force: bool = False) -> dict[str, AgentScope]:
    return _registry.load_all(force=force)


def get_agent(name: str) -> AgentScope | None:
    return _registry.get(name)


def list_agents() -> list[AgentScope]:
    return _registry.list()
