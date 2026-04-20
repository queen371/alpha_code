"""Inject the skill index into the system prompt."""

from __future__ import annotations

from collections.abc import Callable

from .registry import list_skills


def build_skill_index(
    name_filter: Callable[[list[str]], list[str]] | None = None,
) -> str:
    """Return a compact index of registered skills for the system prompt.

    Args:
        name_filter: Optional function to narrow the list (e.g. an AgentScope's
            filter_skills). Receives all names, returns kept names.
    """
    skills = list_skills()
    if name_filter is not None:
        allowed = set(name_filter([s.name for s in skills]))
        skills = [s for s in skills if s.name in allowed]
    if not skills:
        return ""

    lines = [
        "# AVAILABLE SKILLS",
        "You have access to specialized skills — structured playbooks for specific tasks.",
        "When the user's request matches one of these areas, call `load_skill(name)` to load",
        "the full instructions BEFORE acting. Each skill tells you when to use it and when NOT to.",
        "",
    ]
    lines.extend(s.index_line for s in skills)
    return "\n".join(lines)


def inject_skill_index(
    system_prompt: str,
    name_filter: Callable[[list[str]], list[str]] | None = None,
) -> str:
    """Append the skill index (optionally filtered) to a system prompt."""
    index = build_skill_index(name_filter)
    if not index:
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n{index}\n"
