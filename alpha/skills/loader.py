"""Parse a SKILL.md file: YAML frontmatter + markdown body."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    metadata: dict = field(default_factory=dict)
    emoji: str = ""
    requires_bins: list[str] = field(default_factory=list)

    @property
    def index_line(self) -> str:
        prefix = f"{self.emoji} " if self.emoji else ""
        return f"- **{prefix}{self.name}** — {self.description}"


def load_skill_file(path: Path) -> Skill:
    """Parse a SKILL.md file into a Skill.

    Expected frontmatter:
        ---
        name: <str>
        description: <str>
        metadata:
          alpha:
            emoji: "<str>"
            requires: { bins: [...] }
            install: [...]
        ---
        <markdown body>
    """
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"Missing YAML frontmatter in {path}")

    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip()

    name = fm.get("name") or path.parent.name
    description = (fm.get("description") or "").strip()
    metadata = fm.get("metadata") or {}
    alpha_meta = metadata.get("alpha") or {}

    emoji = alpha_meta.get("emoji", "")
    requires = alpha_meta.get("requires") or {}
    requires_bins = list(requires.get("bins") or [])

    return Skill(
        name=name,
        description=description,
        body=body,
        path=path,
        metadata=metadata,
        emoji=emoji,
        requires_bins=requires_bins,
    )
