"""
Configuration for Alpha Code — provider settings, environment, system prompt.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (not CWD) so `alpha` works from any directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 12-factor: env vars sao a fonte de verdade; .env so preenche faltantes.
load_dotenv(_PROJECT_ROOT / ".env", override=False)

# ─── Defaults ───

DEFAULT_PROVIDER = os.getenv("ALPHA_PROVIDER", "deepseek")

# #097 V1.1: limites de execucao agrupados em LIMITS para inspecao
# uniforme. Chaves duplicadas no module-level (`MAX_ITERATIONS` etc)
# ficam como aliases com retro-compat. Sub-agent limit tambem entra
# aqui em vez de viver isolado em FEATURES.
LIMITS = {
    "max_iterations": 100,          # iteracoes do agent loop principal
    "tool_result_max_chars": 12_000,
    "llm_timeout": 300,             # seconds per LLM call
    "max_messages": 500,            # hard cap antes de needs_compression
    "subagent_max_iterations": 15,  # max iteracoes do sub-agent loop
}

MAX_ITERATIONS = LIMITS["max_iterations"]
TOOL_RESULT_MAX_CHARS = LIMITS["tool_result_max_chars"]
LLM_TIMEOUT = LIMITS["llm_timeout"]

# Retry config centralizado (#DM036). LLM e HTTP usam backoff exponencial
# com jitter, mas com parametros diferentes (LLM calls sao mais caras e
# toleram retry mais agressivo; HTTP safe-methods podem retentar erros
# transientes sem duplicar efeito).
RETRY = {
    "llm": {
        "max_retries": 3,
        "initial_backoff": 1.0,
        "max_backoff": 30.0,
        "backoff_multiplier": 2.0,
        "retryable_status_codes": frozenset({429, 500, 502, 503, 504}),
    },
    "http": {
        "max_retries": 2,
        "initial_backoff": 0.5,
        "safe_methods": frozenset({"GET", "HEAD", "OPTIONS"}),
    },
}

# Loop detection (#085 V1.1): consts agrupadas em um dict para inspecao
# uniforme (ex: ALPHA_LOOP_DETECT_DISABLE=1 pode levantar todos os
# thresholds; testes podem mockar uma chave so). Lidas em alpha/agent.py.
LOOP_DETECTION = {
    "max_repeat_calls": 3,        # exact same call N times → loop
    "similar_repeat_calls": 5,    # similar calls threshold (false-positive guard)
    "similarity_threshold": 0.92, # fuzzy match threshold
    "cycle_window": 20,           # look-back window for cycle detection
    "stale_window": 6,            # last N tool calls com no new info → stale
    "min_iter": 3,                # iter <= N nao rodam detection (exploration)
    "min_calls": 6,               # gate loop detection by call count, not iter (#018)
}

# ─── Provider configs ───

_PROVIDERS = {
    "deepseek": {
        "base_url": os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com"),
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-v4-pro",
        # Vision: disponivel APENAS no chat web (chat.deepseek.com) como
        # beta fechado (Image Recognition Mode, Apr 2026). A API REST
        # NAO aceita image_url blocks — retorna HTTP 400.
        "supports_vision": False,
    },
    "openai": {
        "base_url": os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o",
        "supports_vision": True,
    },
    "anthropic": {
        "base_url": os.getenv("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com/v1"),
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-4-6",
        "api_format": "anthropic",
        "supports_vision": True,
    },
    "google": {
        "base_url": os.getenv(
            "GEMINI_API_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        "api_key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.5-flash",  # melhor custo-beneficio com visao
        "supports_vision": True,
        "vision_format": "openai",
    },
    "grok": {
        "base_url": os.getenv("GROK_API_BASE_URL", "https://api.x.ai/v1"),
        "api_key_env": "GROK_API_KEY",
        "model_env": "GROK_MODEL",
        "default_model": "grok-4-1-fast-reasoning",
        "supports_vision": False,
    },
    "ollama": {
        "base_url": os.getenv("OLLAMA_API_BASE_URL", "http://localhost:11434/v1"),
        "api_key_env": None,
        "model_env": "OLLAMA_MODEL",
        "default_model": "qwen-heavy-abliterated:32b",
        "low_temperature": True,
        "supports_vision": False,
    },
    "gemma-12b": {
        "base_url": os.getenv("OLLAMA_API_BASE_URL", "http://localhost:11434/v1"),
        "api_key_env": None,
        "model_env": "OLLAMA_GEMMA_12B_MODEL",
        "default_model": "gemma3:12b",
        "supports_tools": False,
        "low_temperature": True,
        "supports_vision": False,
    },
    "gemma-27b": {
        "base_url": os.getenv("OLLAMA_API_BASE_URL", "http://localhost:11434/v1"),
        "api_key_env": None,
        "model_env": "OLLAMA_GEMMA_27B_MODEL",
        "default_model": "gemma3:27b",
        "supports_tools": False,
        "low_temperature": True,
        "supports_vision": False,
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
    supports_vision = cfg.get("supports_vision", False)
    vision_format = cfg.get("vision_format", "openai")

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "supports_tools": supports_tools,
        "api_format": api_format,
        "supports_vision": supports_vision,
        "vision_format": vision_format,
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
# Toda chave aqui DEVE ser lida em algum lugar. Flags orfas viram codigo
# morto que confunde quem tenta desabilitar comportamento via env e nao
# ve efeito (#DL016).
FEATURES: dict = {
    # Master switch para o sistema de sub-agents. Quando False, `delegate_task`
    # e `delegate_parallel` retornam erro mesmo que `delegate_tool_enabled=True`.
    # Lido em `delegate_tools._delegate_task` / `_delegate_parallel`.
    "multi_agent_enabled": True,
    # Gate fino sobre as tools de delegate (independente do master switch).
    "delegate_tool_enabled": True,
    "max_parallel_agents": 3,
    # Cap total de tarefas em delegate_parallel — `max_parallel_agents`
    # controla concorrencia, nao total. Sem cap, o modelo pode submeter
    # array de 100 tasks * 15 iteracoes = 1500 chamadas LLM silenciosas.
    "max_delegate_total_tasks": 10,
    # subagent_policy / subagent_extra_block / subagent_allow: nao mais aqui.
    # AUDIT_V1.2 #014: o codigo antigo lia `os.environ.get(...)` no IMPORT do
    # modulo, congelando a flag. Mudancas runtime em os.environ (ex: hook
    # configurando policy, teste com monkeypatch) nao surtiam efeito ate
    # reload. Vide getters abaixo (`get_subagent_policy()` etc) — `delegate_tools`
    # consulta a env a cada call em vez de cache stale.
}


# AUDIT_V1.2 #014: getters runtime para flags dependentes de env. Ler env a
# cada call e barato (poucas chamadas por turn) e elimina a categoria de
# bug "mudou env mas nada mudou ate restart". Mantemos a leitura confinada
# a estes 3 helpers em vez de espalhar `os.environ.get(...)` pelo codebase.

def get_subagent_policy() -> str:
    """Politica de bloqueio do sub-agent (strict | relaxed).

    `strict` (default): bloqueia destructive tools quando nao ha approval
    callback do parent. `relaxed`: confia no sub-agent — so delegate_*
    bloqueado (anti-recursao). Lido a cada call para refletir mudancas
    em runtime (hooks, testes, scripts).
    """
    return os.environ.get("ALPHA_SUBAGENT_POLICY", "strict")


def get_subagent_extra_block() -> frozenset[str]:
    """Tools extras a bloquear no sub-agent alem de SUBAGENT_DESTRUCTIVE_BLOCKLIST.

    Lido de `ALPHA_SUBAGENT_EXTRA_BLOCK` (comma-separated). Ex: bloquear
    write_file e edit_file em sub-agents — `ALPHA_SUBAGENT_EXTRA_BLOCK=write_file,edit_file`.
    """
    raw = os.environ.get("ALPHA_SUBAGENT_EXTRA_BLOCK", "")
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def get_subagent_allow() -> frozenset[str]:
    """Tools que o sub-agent pode usar mesmo estando na blocklist default.

    Lido de `ALPHA_SUBAGENT_ALLOW`. Sobrepoe o default — usar com cuidado.
    `delegate_*` continua bloqueado mesmo se o usuario incluir aqui (anti-recursao
    e invariante, nao policy).
    """
    raw = os.environ.get("ALPHA_SUBAGENT_ALLOW", "")
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


# ─── Tool Timeouts (seconds) ───
#
# #D003 (V1.0 MAINT): tres camadas de timeout, todas centralizadas aqui:
#
# 1. `TOOL_TIMEOUTS` (per-category default) — usado quando o caller nao
#    especifica `timeout=` na chamada da tool. Categoria vem do registry.
#
# 2. `TOOL_TIMEOUT_CAPS` (per-category hard cap) — limite maximo mesmo que
#    o caller peca timeout maior. Aplicado via `min(timeout, cap)` nas
#    tools. Antes era hardcoded literal em 4 modulos divergentes
#    (shell_tools=300, code_tools=60, network_tools=60, pipeline_tools=120).
#
# 3. `TOOL_EXECUTION_TIMEOUT` / `SLOW_TOOL_TIMEOUT` (executor-level) — para
#    tools que nao tem categoria conhecida (composite, agent, etc.) ou
#    para o gate global do executor. Antes estavam em `executor.py`.
#
# Ajustar timeout = mudar UM lugar.

TOOL_TIMEOUTS: dict = {
    "shell": 180,
    "code": 60,
    "git": 30,
    "network": 30,
    "pipeline": 240,
    "database": 30,
    "browser": 180,
}

TOOL_TIMEOUT_CAPS: dict = {
    "shell": 300,
    "code": 60,
    "network": 60,
    "pipeline": 120,
    "database": 60,
    "browser": 300,
    "git": 60,
}

# Executor-level timeouts (tools sem categoria, gate global). Importados
# por `executor.py` — manter compatibilidade com tests que ainda lem do
# executor (ele re-exporta).
TOOL_EXECUTION_TIMEOUT = 120  # default para tools sem categoria
SLOW_TOOL_TIMEOUT = 300       # delegate_*, run_tests, deploy_check, etc.

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
