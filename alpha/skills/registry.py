"""Skill registry — discovers SKILL.md files in known locations."""

from __future__ import annotations

import logging
from pathlib import Path

from .loader import Skill, load_skill_file

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SEARCH_PATHS = [
    _PROJECT_ROOT / "skills",
    Path.home() / ".alpha" / "skills",
]

_REGISTRY: dict[str, Skill] = {}
_loaded = False


def load_all_skills(force: bool = False) -> dict[str, Skill]:
    """Scan search paths and populate the registry. Idempotent."""
    global _loaded
    if _loaded and not force:
        return _REGISTRY
    if force:
        _REGISTRY.clear()

    for base in _SEARCH_PATHS:
        if not base.is_dir():
            continue
        for skill_md in sorted(base.glob("*/SKILL.md")):
            try:
                skill = load_skill_file(skill_md)
                _REGISTRY[skill.name] = skill
            except Exception as e:
                logger.warning(f"Failed to load skill {skill_md}: {e}")

    _loaded = True
    logger.info(f"Skills loaded: {len(_REGISTRY)}")
    return _REGISTRY


def get_skill(name: str) -> Skill | None:
    if not _loaded:
        load_all_skills()
    return _REGISTRY.get(name)


def list_skills() -> list[Skill]:
    if not _loaded:
        load_all_skills()
    return sorted(_REGISTRY.values(), key=lambda s: s.name)
