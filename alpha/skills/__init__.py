"""
Skill bundle system for Alpha Code.

A skill is a self-contained markdown file (SKILL.md) with YAML frontmatter
that teaches the agent how to do one thing well. Skills live in:
  - ./skills/<name>/SKILL.md              (project-local)
  - ~/.alpha/skills/<name>/SKILL.md       (user-global)

The agent gets a compact index of available skills in its system prompt
and can call load_skill(name) to pull the full instructions on demand.
"""

from .loader import Skill, load_skill_file
from .prompt import build_skill_index, inject_skill_index
from .registry import get_skill, list_skills, load_all_skills

__all__ = [
    "Skill",
    "build_skill_index",
    "get_skill",
    "inject_skill_index",
    "list_skills",
    "load_all_skills",
    "load_skill_file",
]
