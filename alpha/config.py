"""
Configuration for Alpha Code — provider settings, environment, system prompt.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (not CWD) so `alpha` works from any directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

# ─── Defaults ───

DEFAULT_PROVIDER = os.getenv("ALPHA_PROVIDER", "deepseek")
MAX_ITERATIONS = 50
TOOL_RESULT_MAX_CHARS = 12_000
LLM_TIMEOUT = 300  # seconds per LLM call

# ─── Provider configs ───

_PROVIDERS = {
    "deepseek": {
        "base_url": os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com"),
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-v4-pro",
    },
    "openai": {
        "base_url": os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o",
    },
    "anthropic": {
        "base_url": os.getenv("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com/v1"),
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-4-6",
        "api_format": "anthropic",
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
        "default_model": "qwen-heavy-abliterated:32b",
        "low_temperature": True,
    },
    "gemma-12b": {
        "base_url": os.getenv("OLLAMA_API_BASE_URL", "http://localhost:11434/v1"),
        "api_key_env": None,
        "model_env": "OLLAMA_GEMMA_12B_MODEL",
        "default_model": "gemma3:12b",
        "supports_tools": False,
        "low_temperature": True,
    },
    "gemma-27b": {
        "base_url": os.getenv("OLLAMA_API_BASE_URL", "http://localhost:11434/v1"),
        "api_key_env": None,
        "model_env": "OLLAMA_GEMMA_27B_MODEL",
        "default_model": "gemma3:27b",
        "supports_tools": False,
        "low_temperature": True,
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
    supports_tools = cfg.get("supports_tools", True)
    api_format = cfg.get("api_format", "openai")

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "supports_tools": supports_tools,
        "api_format": api_format,
    }


def get_available_providers() -> list[dict]:
    """List all providers with their availability status."""
    result = []
    for name, cfg in _PROVIDERS.items():
        available = True
        if cfg["api_key_env"]:
            available = bool(os.getenv(cfg["api_key_env"], ""))
        model = os.getenv(cfg.get("model_env", ""), cfg["default_model"])
        result.append({
            "id": name,
            "model": model,
            "available": available,
            "supports_tools": cfg.get("supports_tools", True),
        })
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
# Fonte canonical: `alpha.tools.workspace.AGENT_WORKSPACE` (Path resolvida
# com forbidden-system-dir guard). A versao string-vazia que vivia aqui
# era bug latente (#D021-BUGS) — qualquer `from .config import AGENT_WORKSPACE`
# pegava uma string falsy enquanto `from .tools.workspace import ...` pegava
# o Path real. Importe direto de `alpha.tools.workspace`.

# ─── Feature Flags (used by tool modules) ───
FEATURES: dict = {
    "sandbox_enabled": False,
    "multi_agent_enabled": True,
    "delegate_tool_enabled": True,
    "auto_delegate_parallel_groups": False,
    "max_parallel_agents": 3,
    "subagent_max_iterations": 15,
    # Cap total de tarefas em delegate_parallel — `max_parallel_agents`
    # controla concorrencia, nao total. Sem cap, o modelo pode submeter
    # array de 100 tasks * 15 iteracoes = 1500 chamadas LLM silenciosas.
    "max_delegate_total_tasks": 10,
}

# Alias for backward compatibility with tools that import ALPHA_FEATURES
ALPHA_FEATURES = FEATURES

# ─── Tool Timeouts (seconds) ───
TOOL_TIMEOUTS: dict = {
    "shell": 180,
    "code": 60,
    "git": 30,
    "network": 30,
    "pipeline": 240,
    "database": 30,
    "browser": 180,
}

# ─── Browser tool policies ───
# Comma-separated env vars. By default, an empty allowlist still permits
# qualquer dominio (compatibilidade) — definir ALPHA_BROWSER_REQUIRE_ALLOWLIST=1
# torna a allowlist obrigatoria (allowlist vazia => bloqueia tudo).
BROWSER_DOMAIN_ALLOWLIST: list[str] = [
    d.strip().lower()
    for d in os.getenv("ALPHA_BROWSER_ALLOWLIST", "").split(",")
    if d.strip()
]
BROWSER_DOMAIN_BLOCKLIST: list[str] = [
    d.strip().lower()
    for d in os.getenv("ALPHA_BROWSER_BLOCKLIST", "").split(",")
    if d.strip()
]
BROWSER_REQUIRE_ALLOWLIST: bool = os.getenv(
    "ALPHA_BROWSER_REQUIRE_ALLOWLIST", ""
).strip().lower() in ("1", "true", "yes", "on")

if not BROWSER_DOMAIN_ALLOWLIST and not BROWSER_REQUIRE_ALLOWLIST:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "ALPHA_BROWSER_ALLOWLIST esta vazia (browser tools aceitam qualquer "
        "dominio). Defina ALPHA_BROWSER_ALLOWLIST=dominio1,dominio2 ou "
        "ALPHA_BROWSER_REQUIRE_ALLOWLIST=1 para reduzir vetor de exfil."
    )
