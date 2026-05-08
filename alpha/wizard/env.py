"""Idempotent .env reader/writer at project root.

Preserves comments, blank lines, and key order. Updates keys in place;
appends new keys at the end.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# #095: importa de config em vez de duplicar `Path(__file__).parent...`.
# 4 modulos definiam o mesmo _PROJECT_ROOT separadamente.
from ..config import _PROJECT_ROOT

ENV_PATH = _PROJECT_ROOT / ".env"

# #021/#115: perms restritivas — `.env` carrega API keys e tokens. 0o644
# permite leitura por outros usuarios do sistema (em maquinas multi-user
# isso vaza credenciais para qualquer processo). 0o600 = so o owner le/escreve.
_ENV_FILE_MODE = 0o600


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

    # #021/#115: write atomico via tmp + os.replace. Sem isso, Ctrl+C
    # ou crash no meio do `write_text` deixa .env truncado e perde
    # chaves ja existentes. tempfile.mkstemp ja cria com 0o600 — mantemos
    # explicito via fchmod para nao depender de umask. os.replace e
    # atomico em POSIX (mesma filesystem).
    content = "\n".join(new_lines) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        prefix=".env.", dir=str(_PROJECT_ROOT)
    )
    try:
        os.fchmod(fd, _ENV_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, ENV_PATH)
    except Exception:
        # Cleanup do tmp em caso de erro antes do replace
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Garantia adicional caso o file ja existisse com outro mode
    # (e.g. arquivos de antes do fix com 0o644)
    try:
        os.chmod(ENV_PATH, _ENV_FILE_MODE)
    except OSError:
        pass

    return ENV_PATH
