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

    # #DM015: serializa via yaml.safe_dump em vez de f-string. Antes,
    # description com `: ` ou aspas embutidas gerava YAML quebrado
    # (`description: foo: bar` parsea como dict aninhado). safe_dump
    # escapa/quote automaticamente. Comentarios sao append manual porque
    # PyYAML nao preserva-os.
    import yaml

    payload: dict = {
        "name": name,
        "model": {"provider": provider["id"], "id": model},
    }
    if description:
        payload["description"] = description
    if workspace:
        payload["workspace"] = workspace

    yaml_text = yaml.safe_dump(
        payload, sort_keys=False, default_flow_style=False, allow_unicode=True
    )

    if not workspace:
        yaml_text += "# workspace: /path/to/dir\n"

    yaml_text += (
        "\n"
        "# Uncomment and edit to restrict tools/skills:\n"
        "# tools:\n"
        "#   deny: [execute_shell, write_file, edit_file]\n"
        "# skills:\n"
        "#   allow: [github, summarize]\n"
        "\n"
        "# Extra guidance appended to the system prompt:\n"
        "# system_prompt_extra: |\n"
        "#   Focus on ... Do not ...\n"
    )

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file.write_text(yaml_text, encoding="utf-8")
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
