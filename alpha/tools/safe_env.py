"""Sanitized environment for subprocess execution.

Strips API keys, secrets, and credentials from os.environ
to prevent exfiltration by user-submitted code.
"""

import os
import re

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

_cached_safe_env: dict[str, str] | None = None


def get_safe_env() -> dict[str, str]:
    """Return os.environ stripped of credentials for subprocess use.

    Result is cached — call invalidate_safe_env_cache() if os.environ changes.
    """
    global _cached_safe_env
    if _cached_safe_env is None:
        _cached_safe_env = {
            k: v
            for k, v in os.environ.items()
            if not _SENSITIVE_PATTERNS.search(k) and k not in _EXPLICIT_KEYS
        } | {"PYTHONDONTWRITEBYTECODE": "1"}
    return _cached_safe_env


def invalidate_safe_env_cache():
    """Invalidate cached safe env (call if os.environ is modified at runtime)."""
    global _cached_safe_env
    _cached_safe_env = None
