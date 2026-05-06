"""Skill registry — discovers SKILL.md files in known locations.

Backed por `alpha._registry.FileBackedRegistry` para evitar duplicacao
com agents/registry.py (#DM008).
"""

from __future__ import annotations

from pathlib import Path

from .._registry import FileBackedRegistry
from .loader import Skill, load_skill_file

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SEARCH_PATHS = [
    _PROJECT_ROOT / "skills",
    Path.home() / ".alpha" / "skills",
]

_registry: FileBackedRegistry[Skill] = FileBackedRegistry(
    _SEARCH_PATHS, "*/SKILL.md", load_skill_file, kind="skill"
)


def load_all_skills(force: bool = False) -> dict[str, Skill]:
    return _registry.load_all(force=force)


def get_skill(name: str) -> Skill | None:
    return _registry.get(name)


def list_skills() -> list[Skill]:
    return _registry.list()
