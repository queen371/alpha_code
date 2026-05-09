"""
Named agent profiles for Alpha Code.

A named agent is a persistent profile that scopes a session:
  - model/provider override
  - allowed/denied tools
  - allowed/denied skills
  - workspace directory
  - extra system prompt

Agents live in:
  ./agents/<name>/agent.yaml            (project-local)
  ~/.alpha/agents/<name>/agent.yaml     (user-global)

Switch in the REPL:
  /agent researcher         — switch active agent
  @researcher explain X     — one-shot with a specific agent
  /agents                   — list all agents
"""

from .loader import load_agent_file
from .registry import get_agent, list_agents, load_all_agents
from .scope import AgentScope
from .workspace import validate_args as validate_workspace_args

__all__ = [
    "AgentScope",
    "get_agent",
    "list_agents",
    "load_agent_file",
    "load_all_agents",
    "validate_workspace_args",
]
