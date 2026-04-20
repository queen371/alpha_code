"""Agent registry — discovers agent.yaml files in known locations."""

from __future__ import annotations

import logging
from pathlib import Path

from .loader import load_agent_file
from .scope import AgentScope

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SEARCH_PATHS = [
    _PROJECT_ROOT / "agents",
    Path.home() / ".alpha" / "agents",
]

_REGISTRY: dict[str, AgentScope] = {}
_loaded = False


def load_all_agents(force: bool = False) -> dict[str, AgentScope]:
    """Scan search paths and populate the registry. Idempotent."""
    global _loaded
    if _loaded and not force:
        return _REGISTRY
    if force:
        _REGISTRY.clear()

    for base in _SEARCH_PATHS:
        if not base.is_dir():
            continue
        for agent_yaml in sorted(base.glob("*/agent.yaml")):
            try:
                agent = load_agent_file(agent_yaml)
                _REGISTRY[agent.name] = agent
            except Exception as e:
                logger.warning(f"Failed to load agent {agent_yaml}: {e}")

    _loaded = True
    logger.info(f"Agents loaded: {len(_REGISTRY)}")
    return _REGISTRY


def get_agent(name: str) -> AgentScope | None:
    if not _loaded:
        load_all_agents()
    return _REGISTRY.get(name)


def list_agents() -> list[AgentScope]:
    if not _loaded:
        load_all_agents()
    return sorted(_REGISTRY.values(), key=lambda a: a.name)
