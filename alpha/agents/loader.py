"""Parse an agent.yaml file into an AgentScope."""

from __future__ import annotations

from pathlib import Path

import yaml

from .scope import AgentScope


def load_agent_file(path: Path) -> AgentScope:
    """Parse agent.yaml into an AgentScope.

    Expected shape:
        name: <str>
        description: <str>
        model:
          provider: <str>
          id: <str>
          temperature: <float>
        workspace: <str>
        skills:
          allow: [<name>, ...]
          deny:  [<name>, ...]
        tools:
          allow: [<name>, ...]
          deny:  [<name>, ...]
        system_prompt_extra: |
          <text>
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    name = data.get("name") or path.parent.name
    model_cfg = data.get("model") or {}
    skills_cfg = data.get("skills") or {}
    tools_cfg = data.get("tools") or {}

    temperature = model_cfg.get("temperature")
    if temperature is not None:
        temperature = float(temperature)

    return AgentScope(
        name=name,
        description=(data.get("description") or "").strip(),
        provider=model_cfg.get("provider"),
        model=model_cfg.get("id"),
        temperature=temperature,
        workspace=data.get("workspace"),
        system_prompt_extra=(data.get("system_prompt_extra") or "").strip(),
        skills_allow=list(skills_cfg.get("allow") or []),
        skills_deny=list(skills_cfg.get("deny") or []),
        tools_allow=list(tools_cfg.get("allow") or []),
        tools_deny=list(tools_cfg.get("deny") or []),
        path=path,
    )
