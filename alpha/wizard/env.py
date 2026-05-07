"""Idempotent .env reader/writer at project root.

Preserves comments, blank lines, and key order. Updates keys in place;
appends new keys at the end.
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = _PROJECT_ROOT / ".env"


def read_env() -> dict[str, str]:
    """Parse .env into a dict. Tolerates quoted values and inline comments."""
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


def write_env(updates: dict[str, str]) -> Path:
    """Merge updates into .env. Existing keys are updated in place.

    Rejeita values com `\\n`/`\\r` para evitar injecao de chaves via input
    de usuario malicioso (e.g. `OPENAI_API_KEY=valid\\nDEEPSEEK_API_KEY=evil`).
    """
    for k, v in updates.items():
        if "\n" in v or "\r" in v:
            raise ValueError(f"Newline em valor de '{k}' — bloqueado por segurança")
    existing = (
        ENV_PATH.read_text(encoding="utf-8").splitlines()
        if ENV_PATH.exists()
        else []
    )

    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        k = stripped.split("=", 1)[0].strip()
        if k in updates:
            new_lines.append(f"{k}={updates[k]}")
            updated_keys.add(k)
        else:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return ENV_PATH
