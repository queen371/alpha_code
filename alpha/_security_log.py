"""Centralized log/error sanitization.

Strips credentials before they leak into logs, error responses, or tool
results visible to the model. Used by:

- llm.py — provider error bodies that echo back Authorization headers (#D012)
- database_tools.py — asyncpg exception strings that include the DSN (#D015)
- any future module that logs/returns text potentially containing tokens

Patterns covered:
- ``Bearer <token>`` → ``Bearer [redacted]``
- ``Authorization: <value>`` (any scheme) → ``Authorization: [redacted]``
- ``password=<value>`` in connection strings → ``password=[redacted]``
- ``<scheme>://user:pass@host`` → ``<scheme>://user:[redacted]@host``
- Common API key prefixes (``sk-``, ``api-``) followed by 16+ chars

The patterns are conservative: false negatives are acceptable, false
positives (over-redaction) are preferred over leaks.
"""

from __future__ import annotations

import re

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]+")
_AUTHZ_HEADER_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*)([^\s,;\"']+)"
)
_DSN_PASSWORD_RE = re.compile(r"(?i)(password\s*=\s*)([^\s&;]+)")
# scheme://user:pass@host  — captures only credentials, keeps host visible
_URL_USERINFO_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:@/\s]+):(?P<pwd>[^@\s]+)@"
)
# sk-XXX (OpenAI), api-XXX, ANTHROPIC_API_KEY-style — 16+ chars after prefix
_API_KEY_PREFIX_RE = re.compile(
    r"\b(sk|api|key|token)[-_]([A-Za-z0-9_-]{16,})", re.IGNORECASE
)


def sanitize_for_log(text: str, *, max_chars: int | None = None) -> str:
    """Redact common credential shapes from a log/error string.

    Idempotent: applying twice yields the same output. Optional
    ``max_chars`` truncates the result (after sanitization, so tokens
    chopped in half don't slip through).
    """
    if not text:
        return text
    out = text
    out = _BEARER_RE.sub("Bearer [redacted]", out)
    out = _AUTHZ_HEADER_RE.sub(r"\1[redacted]", out)
    out = _DSN_PASSWORD_RE.sub(r"\1[redacted]", out)
    out = _URL_USERINFO_RE.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:[redacted]@", out
    )
    out = _API_KEY_PREFIX_RE.sub(r"\1-[redacted]", out)
    if max_chars is not None and len(out) > max_chars:
        out = out[:max_chars]
    return out
