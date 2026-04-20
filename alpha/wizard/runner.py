"""Wizard orchestrator. Call run_wizard() as entry point."""

from __future__ import annotations

from ..display import C, c
from .env import read_env, write_env
from .steps import (
    step_api_key,
    step_create_agent,
    step_model,
    step_provider,
    step_review,
    step_workspace,
)


def run_wizard() -> bool:
    """Run the onboarding wizard. Returns True on completion, False if canceled."""
    print()
    print(c(C.CYAN + C.BOLD, "  Alpha Code — Onboarding"))
    print(c(C.GRAY, "  ~60 seconds. Ctrl+C cancels."))
    print()

    current = read_env()

    try:
        provider = step_provider(current)
        api_key = step_api_key(provider, current)
        model = step_model(provider, current)
        workspace = step_workspace(current)
    except (KeyboardInterrupt, EOFError):
        print(c(C.YELLOW, "\n  Canceled. No changes written."))
        return False

    updates: dict[str, str] = {
        "ALPHA_PROVIDER": provider["id"],
        provider["model_env"]: model,
    }
    if provider["api_key_env"] and api_key is not None:
        updates[provider["api_key_env"]] = api_key
    if workspace:
        updates["AGENT_WORKSPACE"] = workspace

    if not step_review(updates):
        print(c(C.YELLOW, "  Aborted. No changes written."))
        return False

    path = write_env(updates)
    print()
    print(f"  {c(C.GREEN, '✓')} Wrote {path}")

    try:
        agent_path = step_create_agent(provider, model, workspace)
    except (KeyboardInterrupt, EOFError):
        agent_path = None
        print(c(C.YELLOW, "\n  Skipped agent creation."))

    if agent_path:
        print(f"  {c(C.GREEN, '✓')} Wrote {agent_path}")
        print(f"  {c(C.GRAY, 'Use it:')} ALPHA_AGENT={agent_path.parent.name} python main.py")
    else:
        print(f"  {c(C.GRAY, 'Next:')} python main.py")
    print()
    return True
