"""Sanitized environment for subprocess execution.

Strips API keys, secrets, and credentials from os.environ
to prevent exfiltration by user-submitted code.
"""

import os
import re
import time

_SENSITIVE_PATTERNS = re.compile(
    # Boundary nota: `\b` em regex Python trata `_` como char de palavra,
    # entao `\bAUTH\b` NAO matcha em `BASIC_AUTH` (a fronteira entre `_` e
    # `A` nao existe). Usamos `(?:^|_)WORD(?:_|$)` explicitamente para
    # tratar `_` como separador, sem casar `KEYBOARD`/`MONKEY` etc.
    #
    # Generic credential tokens.
    r"(?:^|_)(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|CREDENTIALS"
    r"|AUTH|PAT|DSN|APIKEY)(?:_|$)"
    # Composicoes consagradas (#011: AWS_ACCESS_KEY_ID, OPENAI_KEY,
    # KAGGLE_KEY, AZURE_OPENAI_KEY, BASIC_AUTH, GITHUB_PAT vazavam).
    r"|(?:^|_)(API|ACCESS|PRIVATE|PUBLIC|SIGNING|ENCRYPTION|MASTER)"
    r"_(KEY|SECRET|TOKEN)(?:_|$)"
    # Provedores cloud / SaaS comuns — captura prefixo do env mesmo se o
    # sufixo for atipico (GH_X, AWS_FOO, AZURE_BAR, etc).
    r"|^(AWS|GH|GCP|GITHUB|AZURE|OPENAI|ANTHROPIC|HF|HUGGINGFACE|NPM|PYPI"
    r"|KAGGLE|DOCKER|DEEPSEEK|GROK|APIFY|STRIPE|TWILIO|SENDGRID|MAILGUN"
    r"|SLACK|DISCORD|TELEGRAM|FIREBASE|VERCEL|CLOUDFLARE|HEROKU|RAILWAY"
    r"|SUPABASE|PLANETSCALE|DIGITALOCEAN|LINODE|UPSTASH|DO)_"
    # Dados de conexao / DB.
    r"|(?:^|_)(DATABASE|REDIS|MONGO|POSTGRES|MYSQL|SQLITE)_URL(?:$|_)"
    r"|_URL$"
    r"|(?:^|_)(PROXY|CONTROL_PORT)(?:$|_)",
    re.IGNORECASE,
)

# Vars que nao casam o regex acima mas vazam credenciais por contexto.
_EXPLICIT_KEYS = frozenset(
    {
        "GOOGLE_APPLICATION_CREDENTIALS",
        "SSH_AUTH_SOCK", "SSH_AGENT_PID",
        "GPG_AGENT_INFO",
        "GIT_ASKPASS", "SSH_ASKPASS",
        "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
        "KUBECONFIG",
        "NETRC",
    }
)

# DEEP_PERFORMANCE #036: cache invalida por mudança real em os.environ
# (len()), não por TTL. Se o usuário modificar o VALOR de uma variável
# existente sem alterar o número de vars, o cache não invalida — nesse
# caso raro, chame invalidate_safe_env_cache() explicitamente.
_cached_safe_env: dict[str, str] | None = None
_last_env_size: int = -1


def _build_safe_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not _SENSITIVE_PATTERNS.search(k) and k not in _EXPLICIT_KEYS
    } | {"PYTHONDONTWRITEBYTECODE": "1"}


def get_safe_env() -> dict[str, str]:
    """Return os.environ stripped of credentials for subprocess use.

    Cache invalida quando o número de variáveis de ambiente muda.
    """
    global _cached_safe_env, _last_env_size
    cur_size = len(os.environ)
    if _cached_safe_env is None or cur_size != _last_env_size:
        _cached_safe_env = _build_safe_env()
        _last_env_size = cur_size
    return _cached_safe_env


def invalidate_safe_env_cache():
    """Invalidate cached safe env (call if os.environ is modified at runtime)."""
    global _cached_safe_env, _last_env_size
    _cached_safe_env = None
    _last_env_size = -1
