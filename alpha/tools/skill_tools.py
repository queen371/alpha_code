"""Skill loader tool — exposes load_skill(name) to the agent."""

from __future__ import annotations

import logging
import shutil

from . import ToolCategory, ToolDefinition, ToolSafety, register_tool

logger = logging.getLogger(__name__)


async def _load_skill(name: str) -> dict:
    """Return the full body of a named skill plus bin availability info."""
    from ..skills import get_skill, list_skills

    target = (name or "").strip()
    if not target:
        return {"error": "Missing skill name", "available": [s.name for s in list_skills()]}

    skill = get_skill(target)
    if not skill:
        return {
            "error": f"Skill '{target}' not found",
            "available": [s.name for s in list_skills()],
        }

    missing = [b for b in skill.requires_bins if not shutil.which(b)]
    result: dict = {
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.body,
    }
    if missing:
        result["missing_binaries"] = missing
        result["warning"] = (
            f"Skill requires binaries not found on PATH: {missing}. "
            "Install them before proceeding, or choose another approach."
        )
    return result


def _build_load_skill_description() -> str:
    """Build description com a lista de skills disponiveis (#D017-PERF)."""
    try:
        from ..skills import list_skills, load_all_skills
        load_all_skills()
        names = sorted(s.name for s in list_skills())
    except Exception:
        names = []
    base = (
        "Load the full instructions for a named skill. "
        "Call this BEFORE performing a task when a matching skill exists — "
        "it contains critical guidance, commands, and gotchas. "
        "Call with no name to receive the available list."
    )
    if names:
        base += f" Available skills: {', '.join(names)}."
    return base


register_tool(
    ToolDefinition(
        name="load_skill",
        description=_build_load_skill_description(),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The exact skill name (see the Available skills list in this tool's description).",
                },
            },
            "required": ["name"],
        },
        safety=ToolSafety.SAFE,
        executor=_load_skill,
        category=ToolCategory.SKILLS,
    )
)
