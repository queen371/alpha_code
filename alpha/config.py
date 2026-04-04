"""
Configuration for Alpha Code — provider settings, environment, system prompt.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# ─── Defaults ───

DEFAULT_PROVIDER = os.getenv("ALPHA_PROVIDER", "deepseek")
MAX_ITERATIONS = 25
TOOL_RESULT_MAX_CHARS = 20_000
LLM_TIMEOUT = 300  # seconds per LLM call

# ─── Provider configs ───

_PROVIDERS = {
    "deepseek": {
        "base_url": os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com"),
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "base_url": os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o",
    },
    "grok": {
        "base_url": os.getenv("GROK_API_BASE_URL", "https://api.x.ai/v1"),
        "api_key_env": "GROK_API_KEY",
        "model_env": "GROK_MODEL",
        "default_model": "grok-4-1-fast-reasoning",
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_API_BASE_URL", "http://localhost:11434/v1"),
        "api_key_env": None,
        "model_env": "OLLAMA_MODEL",
        "default_model": "qwen2.5-coder:14b",
    },
}


def get_provider_config(provider: str) -> dict:
    """
    Return config for a provider.

    Returns dict with keys: base_url, api_key, model.
    Raises RuntimeError if API key is missing (except ollama).
    """
    cfg = _PROVIDERS.get(provider)
    if not cfg:
        raise RuntimeError(
            f"Unknown provider: {provider}. Available: {list(_PROVIDERS)}"
        )

    base_url = cfg["base_url"]

    if cfg["api_key_env"]:
        api_key = os.getenv(cfg["api_key_env"], "")
        if not api_key:
            raise RuntimeError(
                f"API key not set for {provider}. "
                f"Set the environment variable {cfg['api_key_env']}"
            )
    else:
        api_key = "ollama"

    model = os.getenv(cfg.get("model_env", ""), cfg["default_model"])

    return {"base_url": base_url, "api_key": api_key, "model": model}


def get_available_providers() -> list[dict]:
    """List all providers with their availability status."""
    result = []
    for name, cfg in _PROVIDERS.items():
        available = True
        if cfg["api_key_env"]:
            available = bool(os.getenv(cfg["api_key_env"], ""))
        model = os.getenv(cfg.get("model_env", ""), cfg["default_model"])
        result.append({"id": name, "model": model, "available": available})
    return result


def load_system_prompt() -> str:
    """Load the system prompt from prompts/system.md."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "system.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return (
        "You are ALPHA, an autonomous terminal agent. "
        "Execute tasks directly using your tools. Be concise and effective."
    )


# ─── Agent Workspace ───
AGENT_WORKSPACE = os.getenv("AGENT_WORKSPACE", "")

# ─── Feature Flags (used by tool modules) ───
FEATURES: dict = {
    "sandbox_enabled": False,
    "multi_agent_enabled": False,
    "delegate_tool_enabled": False,
    "auto_delegate_parallel_groups": False,
    "max_parallel_agents": 3,
    "subagent_max_iterations": 15,
}

# Alias for backward compatibility with tools that import ALPHA_FEATURES
ALPHA_FEATURES = FEATURES

# ─── Tool Timeouts (seconds) ───
TOOL_TIMEOUTS: dict = {
    "shell": 30,
    "code": 60,
    "git": 30,
    "network": 30,
    "pipeline": 120,
    "database": 30,
}
