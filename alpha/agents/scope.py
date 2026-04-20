"""AgentScope — configuration profile for a named agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentScope:
    """A named agent profile. All fields optional except name."""

    name: str
    description: str = ""
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    workspace: str | None = None
    system_prompt_extra: str = ""
    skills_allow: list[str] = field(default_factory=list)
    skills_deny: list[str] = field(default_factory=list)
    tools_allow: list[str] = field(default_factory=list)
    tools_deny: list[str] = field(default_factory=list)
    path: Path | None = None

    def filter_names(self, names: list[str], allow: list[str], deny: list[str]) -> list[str]:
        """Apply allow/deny filters to a list of names. Allow wins over deny."""
        if allow:
            allowset = set(allow)
            return [n for n in names if n in allowset]
        if deny:
            denyset = set(deny)
            return [n for n in names if n not in denyset]
        return names

    def filter_skills(self, names: list[str]) -> list[str]:
        return self.filter_names(names, self.skills_allow, self.skills_deny)

    def filter_tools(self, names: list[str]) -> list[str]:
        return self.filter_names(names, self.tools_allow, self.tools_deny)
