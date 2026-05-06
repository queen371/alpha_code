"""File operation tools for ALPHA agent."""

import asyncio
import logging
import os
import re
from pathlib import Path

from . import ToolDefinition, ToolSafety, register_tool
from .path_helpers import (
    _fuzzy_resolve,
    _validate_path,
    _validate_path_no_symlink,
)
from .workspace import AGENT_WORKSPACE

logger = logging.getLogger(__name__)


# ─── Safe Tools ───


async def _read_file(path: str, offset: int = 0, limit: int = 500) -> dict:
    """Read file contents with optional line range."""
    try:
        p = _validate_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    if not p.exists():
        return {
            "error": f"Arquivo não encontrado: {path}",
            "workspace": str(AGENT_WORKSPACE),
            "hint": "O nome pode estar em inglês. Use list_directory() para verificar nomes reais.",
        }
    if not p.is_file():
        return {"error": f"Não é um arquivo: {path}"}
    try:
        text = p.read_text(errors="replace")
        lines = text.splitlines()
        selected = lines[offset : offset + limit]
        numbered = "\n".join(f"{i + offset + 1}: {line}" for i, line in enumerate(selected))
        return {
            "path": str(p),
            "total_lines": len(lines),
            "offset": offset,
            "lines_returned": len(selected),
            "content": numbered,
        }
    except Exception as e:
        return {"error": str(e)}


async def _list_directory(path: str = ".") -> dict:
    """List directory contents."""
    try:
        p = _validate_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    if not p.exists():
        # Suggest similar directories in parent
        parent = p.parent if p.parent.exists() else AGENT_WORKSPACE
        suggestions = [e.name for e in parent.iterdir() if e.is_dir()][:15]
        return {
            "error": f"Diretório não encontrado: {path}",
            "workspace": str(AGENT_WORKSPACE),
            "hint": "O nome pode estar em inglês (Documents, Downloads, etc). Use list_directory() para ver diretórios disponíveis.",
            "nearby_dirs": suggestions,
        }
    if not p.is_dir():
        return {"error": f"Não é um diretório: {path}"}
    entries = []
    try:
        for entry in sorted(p.iterdir()):
            try:
                info = {
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                }
                if entry.is_file():
                    info["size"] = entry.stat().st_size
                entries.append(info)
            except PermissionError:
                entries.append({"name": entry.name, "type": "unknown", "error": "permissão negada"})
    except PermissionError:
        return {"error": f"Permissão negada: {path}"}
    return {"path": str(p), "count": len(entries), "entries": entries[:300]}


MAX_REGEX_PATTERN_LENGTH = 500


async def _search_files(pattern: str, path: str = ".", max_results: int = 50) -> dict:
    """Search for a text pattern inside files (grep-like)."""
    if len(pattern) > MAX_REGEX_PATTERN_LENGTH:
        return {"error": f"Padrão regex muito longo (máx {MAX_REGEX_PATTERN_LENGTH} chars)"}

    try:
        p = _validate_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    if not p.exists():
        return {"error": f"Caminho não encontrado: {path}"}

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return {"error": f"Regex inválido: {e}"}

    # Validate regex complexity in a subprocess so the timeout funciona em
    # qualquer thread (sub-agent, parallel) e em Windows. SIGALRM so funciona
    # na main thread em POSIX, e nao existe em Windows.
    import sys
    validator_code = (
        "import re,sys;"
        f"re.compile({pattern!r}, re.IGNORECASE).search('a'*1000)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", validator_code,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        return {
            "error": "Regex muito complexo (possível backtracking exponencial). Simplifique o padrão."
        }

    # I/O sincrono (`os.walk`, `fpath.stat`, `fpath.read_text`) bloqueia o
    # event loop. Em delegate_parallel onde 3 sub-agents compartilham loop,
    # uma busca em projeto grande congela todos. Mover para thread.
    def _scan() -> list[dict]:
        out: list[dict] = []
        for root, _dirs, files in os.walk(str(p)):
            if len(out) >= max_results:
                break
            _dirs[:] = [
                d for d in _dirs
                if not d.startswith(".")
                and d not in ("node_modules", "__pycache__", ".venv", ".git")
            ]
            for fname in files:
                if len(out) >= max_results:
                    break
                fpath = Path(root) / fname
                try:
                    if fpath.stat().st_size > 1_000_000:
                        continue
                    text = fpath.read_text(errors="replace")
                except (PermissionError, OSError):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        out.append({
                            "file": str(fpath),
                            "line": i,
                            "content": line.strip()[:200],
                        })
                        if len(out) >= max_results:
                            break
        return out

    results = await asyncio.to_thread(_scan)

    return {"pattern": pattern, "path": str(p), "matches": len(results), "results": results}


async def _glob_files(pattern: str, path: str = ".") -> dict:
    """Find files matching a glob pattern."""
    try:
        p = _validate_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    if not p.exists():
        return {"error": f"Caminho não encontrado: {path}"}

    matches = []
    for match in sorted(p.glob(pattern)):
        info = {"path": str(match), "type": "dir" if match.is_dir() else "file"}
        if match.is_file():
            try:
                info["size"] = match.stat().st_size
            except OSError:
                pass
        matches.append(info)
        if len(matches) >= 200:
            break

    return {"pattern": pattern, "base_path": str(p), "count": len(matches), "matches": matches}


# ─── Destructive Tools ───


async def _write_file(path: str, content: str) -> dict:
    """Write content to a file (creates parent directories if needed).

    Uses O_NOFOLLOW to prevent symlink TOCTOU attacks: if the target path
    is a symlink at the moment of opening, the call fails with ELOOP.
    """
    try:
        p = _validate_path_no_symlink(path)
    except PermissionError as e:
        return {"error": str(e)}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Re-validate resolved path after mkdir (directory could have been swapped)
        p_resolved = Path(path).expanduser().resolve()
        p_resolved.relative_to(AGENT_WORKSPACE)
        # Atomic write: O_NOFOLLOW prevents symlink race between validate and open
        data = content.encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(str(p), flags, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return {"path": str(p), "bytes_written": len(data)}
    except OSError as e:
        if e.errno == 40:  # ELOOP — path is a symlink
            return {"error": "Acesso negado: operação de escrita em symlinks não é permitida"}
        return {"error": f"Erro de I/O: {e}"}
    except (PermissionError, ValueError) as e:
        return {"error": f"Acesso negado: {e}"}
    except Exception as e:
        return {"error": str(e)}


async def _edit_file(path: str, old_text: str, new_text: str) -> dict:
    """Edit a file by replacing old_text with new_text."""
    try:
        p = _validate_path_no_symlink(path)
    except PermissionError as e:
        return {"error": str(e)}
    if not p.exists():
        return {"error": f"Arquivo não encontrado: {path}"}
    try:
        original = p.read_text(errors="replace")
        if old_text not in original:
            return {"error": "Texto não encontrado no arquivo. Verifique indentação e espaços."}
        count = original.count(old_text)
        updated = original.replace(old_text, new_text, 1)
        # Re-validate before write (defense against TOCTOU race)
        p_resolved = Path(path).expanduser().resolve()
        p_resolved.relative_to(AGENT_WORKSPACE)
        # Atomic write with O_NOFOLLOW to prevent symlink race
        data = updated.encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(str(p), flags, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return {
            "path": str(p),
            "occurrences_found": count,
            "replaced": 1,
        }
    except (PermissionError, ValueError) as e:
        return {"error": f"Acesso negado: {e}"}
    except Exception as e:
        return {"error": str(e)}


# ─── Registration ───


register_tool(
    ToolDefinition(
        name="read_file",
        description="Ler o conteúdo de um arquivo. Retorna linhas numeradas com offset/limit opcionais para arquivos grandes.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Caminho do arquivo (absoluto ou relativo)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Linha inicial (0-based). Padrão: 0",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Máximo de linhas para ler. Padrão: 500",
                    "default": 500,
                },
            },
            "required": ["path"],
        },
        safety=ToolSafety.SAFE,
        category="filesystem",
        executor=_read_file,
    )
)

register_tool(
    ToolDefinition(
        name="list_directory",
        description="Listar conteúdo de um diretório (arquivos e subdiretórios com tamanhos).",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Caminho do diretório. Padrão: diretório atual",
                    "default": ".",
                },
            },
            "required": [],
        },
        safety=ToolSafety.SAFE,
        category="filesystem",
        executor=_list_directory,
    )
)

register_tool(
    ToolDefinition(
        name="search_files",
        description="Buscar um padrão de texto (regex) dentro de arquivos recursivamente (similar ao grep).",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Padrão regex para buscar"},
                "path": {
                    "type": "string",
                    "description": "Diretório base para busca. Padrão: diretório atual",
                    "default": ".",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Máximo de resultados. Padrão: 50",
                    "default": 50,
                },
            },
            "required": ["pattern"],
        },
        safety=ToolSafety.SAFE,
        category="filesystem",
        executor=_search_files,
    )
)

register_tool(
    ToolDefinition(
        name="glob_files",
        description="Encontrar arquivos por padrão glob (ex: '**/*.py', '*.json').",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Padrão glob (ex: '**/*.py')"},
                "path": {
                    "type": "string",
                    "description": "Diretório base. Padrão: diretório atual",
                    "default": ".",
                },
            },
            "required": ["pattern"],
        },
        safety=ToolSafety.SAFE,
        category="filesystem",
        executor=_glob_files,
    )
)

register_tool(
    ToolDefinition(
        name="write_file",
        description="Criar ou sobrescrever um arquivo com o conteúdo fornecido. Cria diretórios pais automaticamente.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Caminho do arquivo para criar/sobrescrever",
                },
                "content": {"type": "string", "description": "Conteúdo completo do arquivo"},
            },
            "required": ["path", "content"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="filesystem",
        executor=_write_file,
    )
)

register_tool(
    ToolDefinition(
        name="edit_file",
        description="Editar um arquivo substituindo uma string por outra (search-and-replace). SEMPRE leia o arquivo antes de editar.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Caminho do arquivo para editar"},
                "old_text": {
                    "type": "string",
                    "description": "Texto exato a ser substituído (inclua contexto suficiente para ser único)",
                },
                "new_text": {
                    "type": "string",
                    "description": "Novo texto que substituirá o antigo",
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="filesystem",
        executor=_edit_file,
    )
)
