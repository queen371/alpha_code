"""Ordered steps of the onboarding wizard."""

from __future__ import annotations

from pathlib import Path

from ..display import C, c
from .prompts import ask, ask_choice, ask_secret, ask_yes_no

from ..config import _PROJECT_ROOT, _PROVIDERS as _CONFIG_PROVIDERS

_AGENTS_DIR = _PROJECT_ROOT / "agents"

# #086: derivar do config.py em vez de duplicar. Wizard mostrava lista
# desatualizada (4 entries) com modelo default divergente (qwen2.5-coder:14b
# vs config.py qwen-heavy-abliterated:32b). Agora a fonte unica.
_PROVIDERS = [
    {
        "id": pid,
        "api_key_env": cfg.get("api_key_env"),
        "model_env": cfg.get("model_env"),
        "default_model": cfg["default_model"],
    }
    for pid, cfg in _CONFIG_PROVIDERS.items()
]


def step_provider(current: dict[str, str]) -> dict:
    default = current.get("ALPHA_PROVIDER", "deepseek")
    choice = ask_choice("Which LLM provider?", [p["id"] for p in _PROVIDERS], default)
    return next(p for p in _PROVIDERS if p["id"] == choice)


def step_api_key(provider: dict, current: dict[str, str]) -> str | None:
    key_env = provider["api_key_env"]
    if not key_env:
        return None
    existing = current.get(key_env, "")
    if existing:
        print(f"  {c(C.GREEN, '✓')} {key_env} already set ({len(existing)} chars)")
        if not ask_yes_no("Replace it?", default=False):
            return existing
    while True:
        key = ask_secret(f"Enter {key_env}")
        if key:
            return key
        if ask_yes_no("API key empty — continue anyway?", default=False):
            return ""


def step_model(provider: dict, current: dict[str, str]) -> str:
    env = provider["model_env"]
    default = current.get(env, provider["default_model"])
    return ask(f"Model for {provider['id']}", default=default)


def step_workspace(current: dict[str, str]) -> str:
    default = current.get("AGENT_WORKSPACE") or str(Path.cwd())
    return ask("Agent workspace (directory where the agent operates)", default=default)


def step_create_agent(provider: dict, model: str, workspace: str) -> Path | None:
    """Optionally write an agent YAML profile. Returns the path if created."""
    print()
    name = ask(
        "Create a named agent profile? Enter a name (blank to skip)",
        default="",
    )
    if not name:
        return None

    name = name.strip().replace(" ", "-").lower()
    target_dir = _AGENTS_DIR / name
    target_file = target_dir / "agent.yaml"

    if target_file.exists():
        print(f"  {c(C.YELLOW, '!')} {target_file} already exists.")
        if not ask_yes_no("Overwrite?", default=False):
            return None

    description = ask(f"Description for '{name}'", default="")

    lines = [
        f"name: {name}",
    ]
    if description:
        lines.append(f"description: {description}")
    lines.extend([
        "",
        "model:",
        f"  provider: {provider['id']}",
        f"  id: {model}",
        "",
        f"workspace: {workspace}" if workspace else "# workspace: /path/to/dir",
        "",
        "# Uncomment and edit to restrict tools/skills:",
        "# tools:",
        "#   deny: [execute_shell, write_file, edit_file]",
        "# skills:",
        "#   allow: [github, summarize]",
        "",
        "# Extra guidance appended to the system prompt:",
        "# system_prompt_extra: |",
        "#   Focus on ... Do not ...",
    ])

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target_file


def step_review(summary: dict[str, str]) -> bool:
    print()
    print(f"  {c(C.CYAN + C.BOLD, 'Review')}")
    for k, v in summary.items():
        if "KEY" in k and v:
            display = v[:4] + "…" + v[-4:] if len(v) > 8 else "***"
        else:
            display = v
        print(f"    {c(C.GRAY, k)} = {display}")
    print()
    return ask_yes_no("Write these settings to .env?", default=True)
