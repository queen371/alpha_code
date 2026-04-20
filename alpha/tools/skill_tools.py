"""Skill loader tool — exposes load_skill(name) to the agent."""

from __future__ import annotations

import logging
import shutil

from . import ToolDefinition, ToolSafety, register_tool

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


register_tool(
    ToolDefinition(
        name="load_skill",
        description=(
            "Load the full instructions for a named skill. "
            "Check the AVAILABLE SKILLS section of your system prompt for names. "
            "Call this BEFORE performing a task when a matching skill exists — "
            "it contains critical guidance, commands, and gotchas."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The exact skill name from the AVAILABLE SKILLS list",
                },
            },
            "required": ["name"],
        },
        safety=ToolSafety.SAFE,
        executor=_load_skill,
        category="skills",
    )
)
