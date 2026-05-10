"""File operation tools for ALPHA agent."""

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from . import ToolCategory, ToolDefinition, ToolSafety, register_tool
from .path_helpers import (
    _fuzzy_resolve,
    _validate_path,
    _validate_path_no_symlink,
)
from .workspace import AGENT_WORKSPACE, assert_within_workspace

logger = logging.getLogger(__name__)


# #025/#071 (V1.1): ripgrep (`rg`) e ~10-50x mais rapido que o scan Python
# (`os.walk` + `read_text` + regex line-a-line) em projetos > 1000 arquivos.
# Detectamos no import — `shutil.which` lookup e barato e o resultado
# nao muda a cada call. Quando ausente, caimos no fallback Python.
_RIPGREP_BIN = shutil.which("rg")
# Excluir os mesmos paths que o fallback Python pula
_RG_EXCLUDES = (".git", "node_modules", "__pycache__", ".venv")

# Inline edits load the whole file into memory; refuse anything larger so we
# don't allocate runaway buffers (the previous unconditional 10MB read also
# silently truncated bigger files).
_EDIT_MAX_BYTES = 10_000_000


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
        # DEEP_PERFORMANCE #D027: streaming read para arquivos grandes.
        # read_text() + splitlines() materializa o arquivo inteiro em RAM
        # mesmo com offset/limit pequenos. Para arquivos >= 100KB, fazemos
        # streaming com open() — conta total de linhas e só guarda as do range.
        # Custo: O(n) CPU (contar \n), O(limit) RAM (só as linhas visíveis).
        fsize = p.stat().st_size
        if fsize < 100_000:
            text = p.read_text(errors="replace")
            lines = text.splitlines()
            selected = lines[offset : offset + limit]
            total_lines = len(lines)
        else:
            selected: list[str] = []
            total_lines = 0
            with open(p, "r", errors="replace", buffering=65536) as f:
                for i, line in enumerate(f):
                    total_lines = i + 1
                    if offset <= i < offset + limit:
                        selected.append(line.rstrip("\n"))
        numbered = "\n".join(f"{i + offset + 1}: {line}" for i, line in enumerate(selected))
        return {
            "path": str(p),
            "total_lines": total_lines,
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


# Quantifier characters that, applied to a group, make ReDoS-prone shapes.
_QUANTIFIER_CHARS = ("*", "+", "?", "}")


def _detect_redos_pattern(pattern: str) -> str | None:
    """Best-effort static check for catastrophic-backtracking shapes.

    Returns a short reason string if the pattern matches a known ReDoS
    shape, else ``None``. Catches the cases the old subprocess timeout
    actually rejected in practice:

    - Nested quantifier on a group: ``(...)+`` / ``(...)*`` followed by
      another quantifier (``(a+)+``, ``(.*)+``, ``(.+)*``).
    - Star inside star: ``(.*)*``, ``(a*)*``.
    - Alternation with overlapping branches under a quantifier:
      ``(a|a)+``, ``(\\w|\\w+)+`` — heuristic, not exhaustive.

    Not a full regex parser — by design. We only look for shapes that
    are almost always ReDoS in practice; the search engine itself
    enforces a hard length cap (`MAX_REGEX_PATTERN_LENGTH`) above this.
    """
    # 1) `(...)<q1><q2>` where both q1 and q2 are quantifiers (e.g. `)*+`)
    #    or `)+*`. Also `){n,m}*` etc.
    i = 0
    depth = 0
    last_close: int | None = None
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            last_close = i
        elif ch in _QUANTIFIER_CHARS and last_close is not None and i == last_close + 1:
            # The just-closed group is now quantified. Look at the next char.
            if i + 1 < len(pattern) and pattern[i + 1] in _QUANTIFIER_CHARS:
                return "quantifier-on-quantified-group"
            # Look inside the just-closed group for an unbounded quantifier
            # (`*`, `+`, `{n,}`). Scan back to the matching `(`.
            open_pos = _matching_open_paren(pattern, last_close)
            if open_pos is not None and _has_unbounded_quantifier(
                pattern[open_pos + 1: last_close]
            ):
                # `(.*)+`, `(a+)*`, `(\w+)+` etc. — outer quantifier is `+`/`*`?
                if ch in ("+", "*"):
                    return "nested unbounded quantifiers"
            last_close = None
        else:
            last_close = None
        i += 1

    # 2) Alternation with duplicate single-char branches under a quantifier:
    #    `(a|a)+`, `(x|x)*`. Cheap pattern: `\((.)\|\1\)[+*]`.
    if re.search(r"\((.)\|\1\)[+*]", pattern):
        return "duplicate-branch alternation under quantifier"

    return None


def _matching_open_paren(pattern: str, close_idx: int) -> int | None:
    """Return the index of the `(` matching `pattern[close_idx]`, or None."""
    depth = 0
    i = close_idx
    while i >= 0:
        ch = pattern[i]
        if i > 0 and pattern[i - 1] == "\\":
            i -= 2
            continue
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                return i
        i -= 1
    return None


def _has_unbounded_quantifier(s: str) -> bool:
    """True if ``s`` contains an unescaped `*`, `+`, or `{n,}` quantifier."""
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 2
            continue
        if s[i] in ("*", "+"):
            return True
        if s[i] == "{":
            # `{n,}` (no upper bound) is unbounded; `{n,m}` is bounded.
            close = s.find("}", i)
            if close != -1 and "," in s[i:close] and not s[i:close].rstrip("}").rstrip().endswith(","):
                # `{n,m}` — bounded
                pass
            elif close != -1 and "," in s[i:close]:
                return True
        i += 1
    return False


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

    # Static ReDoS heuristic — replaces the old `subprocess.run([sys.executable,
    # "-c", ...])` validator that cost 30-100ms per call. The subprocess
    # approach existed to get a portable timeout, but it shipped a Python
    # interpreter just to compile + try-match a regex. The heuristic below
    # rejects the well-known ReDoS shapes (nested quantifiers on groups)
    # which is what the timeout caught in practice — we now reject
    # statically in microseconds.
    redos_reason = _detect_redos_pattern(pattern)
    if redos_reason:
        return {
            "error": (
                "Regex muito complexo (possível backtracking exponencial — "
                f"{redos_reason}). Simplifique o padrão."
            )
        }

    # #025/#071: ripgrep quando disponivel — 10-50x mais rapido em projetos
    # grandes. Fallback para scan Python preserva semantica (case-insensitive,
    # mesmas exclusoes de dir, mesmo formato de output).
    if _RIPGREP_BIN is not None:
        results = await _search_with_ripgrep(pattern, p, max_results)
    else:
        results = await asyncio.to_thread(
            _search_with_python, regex, p, max_results
        )

    return {"pattern": pattern, "path": str(p), "matches": len(results), "results": results}


async def _search_with_ripgrep(pattern: str, root: Path, max_results: int) -> list[dict]:
    """Run `rg --json` and parse match lines.

    A regex e a mesma string passada pelo usuario (re.compile ja validou
    sintaxe Python; ripgrep usa Rust regex que aceita um subset compativel
    para padroes comuns). Em caso de divergencia de syntax, ripgrep
    retorna codigo != 0 e caimos no fallback Python.
    """
    excludes: list[str] = []
    for d in _RG_EXCLUDES:
        excludes.extend(["--glob", f"!{d}/**"])

    cmd = [
        _RIPGREP_BIN,  # type: ignore[list-item]
        "--json",
        "--ignore-case",
        "--max-count", str(max_results),
        "--max-filesize", "1M",
        "--no-messages",
        *excludes,
        "--regexp", pattern,
        str(root),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        # Em timeout, fallback Python — ja temos `regex` compilado mas
        # a versao chamadora vai re-compilar. Devolver vazio aqui evita
        # double-work; o usuario pode re-tentar com pattern mais especifico.
        return []
    except asyncio.CancelledError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise

    if proc.returncode not in (0, 1):  # 0=match, 1=no match; 2=erro
        # Provavel divergencia de syntax regex — fallback Python.
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []
        return await asyncio.to_thread(
            _search_with_python, regex, root, max_results
        )

    out: list[dict] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data", {})
        path_text = data.get("path", {}).get("text") or ""
        line_no = data.get("line_number") or 0
        line_text = data.get("lines", {}).get("text") or ""
        out.append({
            "file": path_text,
            "line": line_no,
            "content": line_text.rstrip("\n").strip()[:200],
        })
        if len(out) >= max_results:
            break
    return out


def _search_with_python(regex: "re.Pattern[str]", root: Path, max_results: int) -> list[dict]:
    """Fallback puro-Python para `_search_files` quando ripgrep nao esta no PATH."""
    out: list[dict] = []
    for dirpath, _dirs, files in os.walk(str(root)):
        if len(out) >= max_results:
            break
        _dirs[:] = [
            d for d in _dirs
            if not d.startswith(".")
            and d not in _RG_EXCLUDES
        ]
        for fname in files:
            if len(out) >= max_results:
                break
            fpath = Path(dirpath) / fname
            try:
                if fpath.stat().st_size > 1_000_000:
                    continue
                # DEEP_PERFORMANCE #037: streaming read em vez de
                # read_text() + splitlines() que materializa o arquivo
                # inteiro (até 999KB) em RAM duas vezes (string + lista).
                with open(fpath, "r", errors="replace", buffering=65536) as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            out.append({
                                "file": str(fpath),
                                "line": i,
                                "content": line.strip()[:200],
                            })
                            if len(out) >= max_results:
                                return out
            except (PermissionError, OSError):
                continue
    return out


async def _glob_files(pattern: str, path: str = ".") -> dict:
    """Find files matching a glob pattern."""
    try:
        p = _validate_path(path)
    except PermissionError as e:
        return {"error": str(e)}
    if not p.exists():
        return {"error": f"Caminho não encontrado: {path}"}

    # #027/#072: era `sorted(p.glob(pattern))` — materializava a lista
    # inteira antes de cortar a 200. Em monorepos com .git/node_modules
    # incluido, isso pode produzir 100K+ entradas. Itera o generator,
    # acumula 200, ordena no final.
    _SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".mypy_cache",
                  ".pytest_cache", ".ruff_cache", "dist", "build"}
    matches = []
    skipped_outside = 0
    try:
        glob_iter = p.glob(pattern)
    except PermissionError as e:
        return {"error": f"Permissão negada ao acessar: {e}"}
    for match in glob_iter:
        # Skip subdirectories de noise para nao ler 100K entries em monorepos
        try:
            rel = match.relative_to(p)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
        except ValueError:
            pass
        # #D025: workspace dentro do glob — se p contem symlink que aponta
        # para fora, glob seguiria e listaria filenames externos. read_file
        # depois bloquearia, mas o filename ja vazou estrutura externa.
        if assert_within_workspace(match.resolve()):
            skipped_outside += 1
            continue
        info = {"path": str(match), "type": "dir" if match.is_dir() else "file"}
        if match.is_file():
            try:
                info["size"] = match.stat().st_size
            except OSError:
                pass
        matches.append(info)
        if len(matches) >= 200:
            break

    # Sort apenas o subset de 200 (ou menos), nao a coleta inteira.
    matches.sort(key=lambda info: info["path"])

    result = {
        "pattern": pattern,
        "base_path": str(p),
        "count": len(matches),
        "matches": matches,
    }
    if skipped_outside:
        result["skipped_outside_workspace"] = skipped_outside
    return result


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
        # Snapshot prior contents (best-effort, only used for the UI diff —
        # never returned to the LLM since the executor strips _-prefixed keys).
        previous_content = ""
        existed = p.exists()
        if existed and p.is_file():
            try:
                previous_content = p.read_text(errors="replace")
            except Exception:
                previous_content = ""

        p.parent.mkdir(parents=True, exist_ok=True)
        # Re-validate resolved path after mkdir (directory could have been swapped)
        p_resolved = Path(path).expanduser().resolve()
        err = assert_within_workspace(p_resolved)
        if err:
            raise PermissionError(err)
        # Atomic write: O_NOFOLLOW prevents symlink race between validate and open
        data = content.encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(str(p), flags, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return {
            "path": str(p),
            "bytes_written": len(data),
            "_previous_content": previous_content,
            "_created": not existed,
        }
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
        # O_NOFOLLOW closes the TOCTOU race where a symlink could be created
        # between the path validation above and this read.
        fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            size = os.fstat(fd).st_size
            if size > _EDIT_MAX_BYTES:
                return {
                    "error": f"Arquivo > {_EDIT_MAX_BYTES // 1_000_000}MB; "
                    f"edição inline não suportada para arquivos desse tamanho"
                }
            raw = os.read(fd, size) if size > 0 else b""
            original = raw.decode("utf-8", errors="replace")
        finally:
            os.close(fd)
        if old_text not in original:
            return {"error": "Texto não encontrado no arquivo. Verifique indentação e espaços."}
        count = original.count(old_text)
        updated = original.replace(old_text, new_text, 1)
        # Re-validate before write (defense against TOCTOU race)
        p_resolved = Path(path).expanduser().resolve()
        err = assert_within_workspace(p_resolved)
        if err:
            raise PermissionError(err)
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
        category=ToolCategory.FILESYSTEM,
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
        category=ToolCategory.FILESYSTEM,
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
        category=ToolCategory.FILESYSTEM,
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
        category=ToolCategory.FILESYSTEM,
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
        category=ToolCategory.FILESYSTEM,
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
        category=ToolCategory.FILESYSTEM,
        executor=_edit_file,
    )
)
