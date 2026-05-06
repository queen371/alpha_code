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

    Note (#D017-PERF): a versao antiga inseria N x `index_line` (~6KB para
    80 skills) no system prompt e isso era retransmitido em cada iteracao
    do agent loop — ~75K tokens cumulativos numa sessao de 50 turnos.
    Agora so emite um pointer curto; a lista completa de nomes vai pra
    description do `load_skill` tool (mais compacta, e a description e parte
    do tool def que ja e transmitido).
    """
    skills = list_skills()
    if name_filter is not None:
        allowed = set(name_filter([s.name for s in skills]))
        skills = [s for s in skills if s.name in allowed]
    if not skills:
        return ""

    return (
        "# SKILLS\n"
        "Specialized playbooks are available via the `load_skill(name)` tool. "
        "The list of available skill names is in that tool's description. "
        "When a user request matches a skill area, call `load_skill` BEFORE acting."
    )


def list_skill_names_for_tool_description(
    name_filter: Callable[[list[str]], list[str]] | None = None,
) -> list[str]:
    """Lista de nomes de skills para a description do `load_skill`."""
    skills = list_skills()
    if name_filter is not None:
        allowed = set(name_filter([s.name for s in skills]))
        skills = [s for s in skills if s.name in allowed]
    return [s.name for s in skills]


def inject_skill_index(
    system_prompt: str,
    name_filter: Callable[[list[str]], list[str]] | None = None,
) -> str:
    """Append the skill index (optionally filtered) to a system prompt."""
    index = build_skill_index(name_filter)
    if not index:
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n{index}\n"
