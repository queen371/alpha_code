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


def get_safe_env() -> dict[str, str]:
    """Return os.environ stripped of credentials for subprocess use."""
    return {
        k: v
        for k, v in os.environ.items()
        if not _SENSITIVE_PATTERNS.search(k) and k not in _EXPLICIT_KEYS
    } | {"PYTHONDONTWRITEBYTECODE": "1"}
