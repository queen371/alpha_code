"""Sanitized environment for subprocess execution.

Strips API keys, secrets, and credentials from os.environ
to prevent exfiltration by user-submitted code.
"""

import os
import re
import time

_SENSITIVE_PATTERNS = re.compile(
    r"(API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|PRIVATE_KEY"
    r"|_URL$|PROXY|DATABASE|DSN|REDIS|MONGO|POSTGRES|CONTROL_PORT)",
    re.IGNORECASE,
)

_EXPLICIT_KEYS = frozenset(
    {
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)

# TTL (#028 V1.1): cache sem expiracao perdia mudancas em os.environ feitas
# durante a sessao (ex: usuario carrega um novo .env via /load, ou um hook
# define ALPHA_DEBUG=1). 60s e curto o suficiente para refletir mudancas
# em tempo razoavel sem reconstruir o dict a cada subprocess call.
_CACHE_TTL_SECONDS = 60.0

_cached_safe_env: dict[str, str] | None = None
_cached_at: float = 0.0


def _build_safe_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not _SENSITIVE_PATTERNS.search(k) and k not in _EXPLICIT_KEYS
    } | {"PYTHONDONTWRITEBYTECODE": "1"}


def get_safe_env() -> dict[str, str]:
    """Return os.environ stripped of credentials for subprocess use.

    Result is cached for `_CACHE_TTL_SECONDS`; call
    `invalidate_safe_env_cache()` for an immediate refresh.
    """
    global _cached_safe_env, _cached_at
    now = time.monotonic()
    if _cached_safe_env is None or (now - _cached_at) >= _CACHE_TTL_SECONDS:
        _cached_safe_env = _build_safe_env()
        _cached_at = now
    return _cached_safe_env


def invalidate_safe_env_cache():
    """Invalidate cached safe env (call if os.environ is modified at runtime)."""
    global _cached_safe_env, _cached_at
    _cached_safe_env = None
    _cached_at = 0.0
