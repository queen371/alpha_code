"""Skill registry — discovers SKILL.md files in known locations.

Backed por `alpha._registry.FileBackedRegistry` para evitar duplicacao
com agents/registry.py (#DM008).
"""

from __future__ import annotations

from pathlib import Path

from .._registry import FileBackedRegistry
from ..config import _PROJECT_ROOT
from .loader import Skill, load_skill_file

_SEARCH_PATHS = [
    _PROJECT_ROOT / "skills",
    Path.home() / ".alpha" / "skills",
]

_registry: FileBackedRegistry[Skill] = FileBackedRegistry(
    _SEARCH_PATHS, "*/SKILL.md", load_skill_file, kind="skill"
)


def load_all_skills(force: bool = False) -> dict[str, Skill]:
    result = _registry.load_all(force=force)
    # DEEP_PERFORMANCE #D029: invalidar cache do _SlashCompleter quando
    # skills são recarregadas (startup ou /reload).
    try:
        from ..repl_input import _SlashCompleter
        _SlashCompleter.invalidate_cache()
    except Exception:
        pass
    return result


def get_skill(name: str) -> Skill | None:
    return _registry.get(name)


def list_skills() -> list[Skill]:
    return _registry.list()
