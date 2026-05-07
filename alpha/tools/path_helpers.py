"""Path validation + fuzzy resolution helpers shared by file/composite tools.

Extraido de `file_tools.py` quando o arquivo passou de 500L (#DM002).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .workspace import AGENT_WORKSPACE

logger = logging.getLogger(__name__)


# ─── Mapeamento PT→EN para nomes de diretórios comuns ───

_PT_EN_DIR_MAP = {
    "documentos": "Documents",
    "documento": "Documents",
    "downloads": "Downloads",
    "área de trabalho": "Desktop",
    "area de trabalho": "Desktop",
    "imagens": "Pictures",
    "imagem": "Pictures",
    "fotos": "Pictures",
    "músicas": "Music",
    "musicas": "Music",
    "música": "Music",
    "musica": "Music",
    "vídeos": "Videos",
    "videos": "Videos",
    "video": "Videos",
    "modelos": "Templates",
    "público": "Public",
    "publico": "Public",
    "projetos": "Projects",
    "projeto": "Projects",
}


def _fuzzy_resolve_from_base(base: Path, components: list[str]) -> Path | None:
    """Try to resolve path components from a base directory with fuzzy matching.

    Returns the resolved Path or None if any component fails to match.
    """
    resolved = base
    changed = False

    for comp in components:
        candidate = resolved / comp
        if candidate.exists():
            resolved = candidate
            continue

        # Try 1: PT→EN translation
        comp_lower = comp.lower()
        if comp_lower in _PT_EN_DIR_MAP:
            translated = resolved / _PT_EN_DIR_MAP[comp_lower]
            if translated.exists():
                resolved = translated
                changed = True
                logger.info(f"Fuzzy path: '{comp}' → '{_PT_EN_DIR_MAP[comp_lower]}' (PT→EN)")
                continue

        # Try 2: Case-insensitive match in parent directory
        if resolved.is_dir():
            match = None
            try:
                for entry in resolved.iterdir():
                    if entry.name.lower() == comp_lower:
                        match = entry
                        break
            except PermissionError:
                pass
            if match:
                resolved = match
                changed = True
                logger.info(f"Fuzzy path: '{comp}' → '{match.name}' (case-insensitive)")
                continue

        # No match found for this component — give up
        return None

    return resolved if changed else None


# #D005 (V1.0): cache LRU para fuzzy resolve. Cada path miss antes
# tentava ate 3 bases (home, workspace, cwd) com listdir + match
# case-insensitive — caro em sessoes que o LLM tenta multiplas variantes
# do mesmo path. Cache mantem 256 entries; eviction natural via LRU.
_FUZZY_CACHE_SIZE = 256
_fuzzy_cache: dict[str, str | None] = {}
_fuzzy_cache_order: list[str] = []


def _fuzzy_resolve(path: str) -> str | None:
    """Try to resolve a path that doesn't exist by matching fuzzy variants.

    Handles voice transcription issues where English dir names
    are transcribed in Portuguese (e.g., "documentos" → "Documents").

    Returns the corrected path string or None if no match found.
    """
    cached = _fuzzy_cache.get(path, _SENTINEL)
    if cached is not _SENTINEL:
        return cached  # type: ignore[return-value]

    result = _fuzzy_resolve_uncached(path)

    _fuzzy_cache[path] = result
    _fuzzy_cache_order.append(path)
    if len(_fuzzy_cache_order) > _FUZZY_CACHE_SIZE:
        evicted = _fuzzy_cache_order.pop(0)
        _fuzzy_cache.pop(evicted, None)
    return result


_SENTINEL = object()


def _fuzzy_resolve_uncached(path: str) -> str | None:
    p = Path(path).expanduser()

    if p.is_absolute():
        parts = list(p.parts)
        base = Path(parts[0])  # root "/"
        components = parts[1:]
        result = _fuzzy_resolve_from_base(base, components)
        return str(result) if result else None

    # For relative paths, try multiple base directories
    components = list(p.parts)

    # Try 1: From home directory (handles "documentos" → ~/Documents)
    result = _fuzzy_resolve_from_base(Path.home(), components)
    if result:
        return str(result)

    # Try 2: From AGENT_WORKSPACE (handles subdirs like "projeto/src")
    result = _fuzzy_resolve_from_base(AGENT_WORKSPACE, components)
    if result:
        return str(result)

    # Try 3: From cwd (handles relative paths from current directory)
    cwd = Path.cwd()
    if cwd != Path.home() and cwd != AGENT_WORKSPACE:
        result = _fuzzy_resolve_from_base(cwd, components)
        if result:
            return str(result)

    return None


# ─── Workspace Security ───


def _validate_path(path: str) -> Path:
    """Resolve path and ensure it's within AGENT_WORKSPACE.

    Mitigations:
    - Tries fuzzy resolution for voice-transcribed paths (PT→EN, case-insensitive)
    - Rejects symlinks whose final target resolves outside the workspace
    - Post-resolve validation ensures the canonical path is still inside workspace
    """
    p = Path(path).expanduser().resolve()

    # If path doesn't exist, try fuzzy resolution before giving up
    if not p.exists():
        fuzzy = _fuzzy_resolve(path)
        if fuzzy:
            p = Path(fuzzy).resolve()
            logger.info(f"Path resolved via fuzzy matching: '{path}' → '{p}'")

    # Also try resolving relative paths against AGENT_WORKSPACE
    if not p.exists() and not Path(path).expanduser().is_absolute():
        workspace_path = (AGENT_WORKSPACE / path).resolve()
        if workspace_path.exists():
            p = workspace_path
        else:
            # Try fuzzy on workspace-relative path too
            fuzzy = _fuzzy_resolve(str(AGENT_WORKSPACE / path))
            if fuzzy:
                p = Path(fuzzy).resolve()

    # Check that resolved path is within workspace
    try:
        p.relative_to(AGENT_WORKSPACE)
    except ValueError:
        raise PermissionError(
            f"Acesso negado: caminho fora do workspace permitido ({AGENT_WORKSPACE})"
        )

    return p


def _validate_path_no_symlink(path: str) -> Path:
    """Like _validate_path but also rejects symlinks (for write operations).

    Prevents TOCTOU attacks where a symlink is swapped between validation and write.
    Also checks ALL parent path components for symlinks, not just the target.
    Blocks writes to plugins/ directory to prevent plugin injection.
    """
    p = _validate_path(path)

    # Block writes to plugins/ directory (prevents plugin injection via write_file)
    try:
        plugins_dir = (AGENT_WORKSPACE / "plugins").resolve()
        p.relative_to(plugins_dir)
        raise PermissionError("Acesso negado: escrita no diretório plugins/ bloqueada por segurança")
    except ValueError:
        pass  # not inside plugins/ — OK

    # Reject if the target itself is a symlink
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise PermissionError("Acesso negado: operação de escrita em symlinks não é permitida")

    # Check every parent component for symlinks
    current = raw
    while current != current.parent:
        if current.is_symlink():
            raise PermissionError(f"Acesso negado: componente do caminho é um symlink ({current})")
        current = current.parent

    return p
