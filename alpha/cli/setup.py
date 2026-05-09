"""Startup helpers for the CLI.

Five small functions that compose at the start of every REPL or
single-shot run:

- ``build_system_prompt`` — assemble the system prompt from base +
  agent extras + skill index + ALPHA.md.
- ``get_tools_for_agent`` — load tools (built-in + MCP) and filter
  by the active agent profile.
- ``resolve_active_agent`` — read ``ALPHA_AGENT`` env or fall back
  to a "default" profile.
- ``pick_provider_interactive`` — startup prompt to choose a provider.
- ``approval_callback`` — sync wrapper that delegates to the display
  layer's approval prompt.

Pulled out of ``main.py`` so the entry point reads top-down without
detours through helper definitions.
"""

from __future__ import annotations

import logging
import os

from alpha.agents import AgentScope, get_agent, list_agents, load_all_agents
from alpha.config import get_available_providers, load_system_prompt
from alpha.display import (
    C,
    c,
    print_approval_request,
    print_error,
    print_providers_list,
)
from alpha.mcp import load_mcp_servers
from alpha.skills import inject_skill_index, load_all_skills


def build_system_prompt(agent: AgentScope | None = None) -> str:
    """Load base prompt, apply agent extras, inject skill index, append ALPHA.md."""
    from alpha.project_context import inject_project_context, load_project_context

    load_all_skills()
    base = load_system_prompt()
    if agent is not None and agent.system_prompt_extra:
        base = f"{base}\n\n# AGENT PROFILE: {agent.name}\n{agent.system_prompt_extra}"
    skill_filter = (
        agent.filter_skills
        if agent is not None and (agent.skills_allow or agent.skills_deny)
        else None
    )
    base = inject_skill_index(base, name_filter=skill_filter)
    return inject_project_context(base, load_project_context())


def get_tools_for_agent(agent: AgentScope | None):
    """Return (get_tool_fn, openai_tools_list) filtered by the agent's tool scope."""
    try:
        from alpha.tools import get_openai_tools, get_tool, load_all_tools

        load_all_tools()
        # MCP tools register into the same registry; load them after the
        # built-in tools so a misbehaving MCP server can't shadow native ones.
        try:
            load_mcp_servers()
        except Exception as e:
            logging.getLogger(__name__).warning("MCP load failed: %s", e)
        if agent is not None and (agent.tools_allow or agent.tools_deny):
            tools = get_openai_tools(name_filter=agent.filter_tools)
        else:
            tools = get_openai_tools()
        return get_tool, tools
    except ImportError:
        return None, []


def pick_provider_interactive(default: str) -> str:
    """Prompt user to pick a provider at startup. Falls back to ``default``."""
    providers = get_available_providers()
    print(c(C.CYAN + C.BOLD, "\nSelect a model / provider:"))
    print_providers_list(providers, default=default, numbered=True)

    while True:
        try:
            choice = input(
                c(C.GRAY, f"\n  Choice [1-{len(providers)}, Enter={default}]: ")
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return default
        if not choice:
            return default

        pick = None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                pick = providers[idx]
        else:
            pick = next((p for p in providers if p["id"] == choice), None)

        if pick is None:
            print(c(C.RED, "  Invalid choice."))
            continue
        if not pick["available"]:
            print(c(C.RED, f"  {pick['id']} not available — pick another."))
            continue
        return pick["id"]


def resolve_active_agent() -> AgentScope | None:
    """Pick the active agent: ``ALPHA_AGENT`` env, else a 'default' profile."""
    load_all_agents()
    explicit = os.getenv("ALPHA_AGENT", "").strip()
    if explicit:
        agent = get_agent(explicit)
        if agent is None:
            print_error(f"Agent '{explicit}' not found (ALPHA_AGENT). Using no profile.")
        return agent
    return get_agent("default")


def approval_callback(tool_name: str, args: dict) -> bool:
    """Synchronous approval callback for the REPL."""
    return print_approval_request(tool_name, args)


__all__ = [
    "approval_callback",
    "build_system_prompt",
    "get_tools_for_agent",
    "list_agents",  # re-export for /agents command in main.py
    "pick_provider_interactive",
    "resolve_active_agent",
]
