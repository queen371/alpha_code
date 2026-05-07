"""Code execution tools for ALPHA agent.

SECURITY: A static import blocklist prevents the most dangerous escape vectors
(os, subprocess, shutil, etc.) and project dependencies that enable network
exfiltration (httpx, aiohttp, requests, duckduckgo_search).
The blocklist is best-effort — NOT a real sandbox. Single-user only.
"""

import ast
import asyncio
import logging
import os
import re
import sys
import tempfile

from . import ToolDefinition, ToolSafety, register_tool
from .safe_env import get_safe_env

logger = logging.getLogger(__name__)

# ─── Security ───

VALID_PACKAGE_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9._-]*(\[[\w,]+\])?(([=!<>~]=?)[a-zA-Z0-9.*]+)?$"
)

URL_PREFIXES = ("http://", "https://", "git+", "svn+", "ftp://", "/", "\\")

# Modules that allow shell escape / filesystem damage from executed code.
# Checked via static analysis before execution (not runtime-bypassable via __import__).
_BLOCKED_IMPORT_PATTERNS = [
    r"\bimport\s+os\b",
    r"\bfrom\s+os\b",
    r"\bimport\s+subprocess\b",
    r"\bfrom\s+subprocess\b",
    r"\bimport\s+shutil\b",
    r"\bfrom\s+shutil\b",
    r"\bimport\s+sys\b",
    r"\bfrom\s+sys\b",
    r"\bimport\s+importlib\b",
    r"\bfrom\s+importlib\b",
    r"\bimport\s+ctypes\b",
    r"\bfrom\s+ctypes\b",
    r"\bimport\s+signal\b",
    r"\bfrom\s+signal\b",
    r"\bimport\s+pathlib\b",
    r"\bfrom\s+pathlib\b",
    r"\bimport\s+socket\b",
    r"\bfrom\s+socket\b",
    r"\bimport\s+pty\b",
    r"\bfrom\s+pty\b",
    r"\bimport\s+code\b",
    r"\bfrom\s+code\b",
    r"\bimport\s+multiprocessing\b",
    r"\bfrom\s+multiprocessing\b",
    r"\bimport\s+webbrowser\b",
    r"\bfrom\s+webbrowser\b",
    r"\bimport\s+http\b",
    r"\bfrom\s+http\b",
    r"\bimport\s+urllib\b",
    r"\bfrom\s+urllib\b",
    # Block project dependencies (network exfiltration vectors)
    r"\bimport\s+httpx\b",
    r"\bfrom\s+httpx\b",
    r"\bimport\s+requests\b",
    r"\bfrom\s+requests\b",
    r"\bimport\s+aiohttp\b",
    r"\bfrom\s+aiohttp\b",
    r"\bimport\s+duckduckgo_search\b",
    r"\bfrom\s+duckduckgo_search\b",
    r"\bimport\s+ddgs\b",
    r"\bfrom\s+ddgs\b",
    r"\bimport\s+dotenv\b",
    r"\bfrom\s+dotenv\b",
    # Deserialization / runtime introspection — pickle.loads e marshal.loads
    # executam __reduce__ arbitrario; runpy/inspect/gc/platform/dis dao escapes
    # via globals, frames ou execucao de modulos externos.
    r"\bimport\s+pickle\b",
    r"\bfrom\s+pickle\b",
    r"\bimport\s+marshal\b",
    r"\bfrom\s+marshal\b",
    r"\bimport\s+runpy\b",
    r"\bfrom\s+runpy\b",
    r"\bimport\s+inspect\b",
    r"\bfrom\s+inspect\b",
    r"\bimport\s+gc\b",
    r"\bfrom\s+gc\b",
    r"\bimport\s+platform\b",
    r"\bfrom\s+platform\b",
    r"\bimport\s+dis\b",
    r"\bfrom\s+dis\b",
    r"\b__import__\s*\(",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bcompile\s*\(",
    r"\bopen\s*\(.*(w|a|x)",  # block write-mode open()
    r"\bglobals\s*\(\)",
    r"\bgetattr\s*\(",  # getattr can bypass any restriction
    r"\bvars\s*\(",  # vars() exposes namespace
    r"\bchr\s*\(",  # chr() can build blocked strings dynamically
    r"\b__loader__\b",  # importlib loader escape
    r"\b__builtins__\b",  # builtins namespace access
    r"\b__subclasses__\b",  # class hierarchy traversal
    r"\bbreakpoint\s*\(",  # debugger escape
]
_BLOCKED_IMPORT_RE = re.compile("|".join(_BLOCKED_IMPORT_PATTERNS), re.MULTILINE)

# AST-based blocklists (#D018-PERF + #027/#075/#084 V1.1):
# Substitui a busca em alternation regex de 35+ ramos com MULTILINE
# (175K tentativas no pior caso para 5KB de codigo) por um AST walk
# linear e tambem elimina falsos positivos como `\bopen\s*\(.*(w|a|x)` que
# casava em `open("read.txt")`.
_BLOCKED_MODULES = frozenset({
    "os", "subprocess", "shutil", "sys", "importlib", "ctypes", "signal",
    "pathlib", "socket", "pty", "code", "multiprocessing", "webbrowser",
    "http", "urllib",
    "httpx", "requests", "aiohttp",
    "duckduckgo_search", "ddgs", "dotenv",
    "pickle", "marshal", "runpy", "inspect", "gc", "platform", "dis",
})

_BLOCKED_CALL_NAMES = frozenset({
    "__import__", "eval", "exec", "compile", "breakpoint",
    "globals", "getattr", "vars", "chr",
})

_BLOCKED_NAME_TOKENS = frozenset({
    "__loader__", "__builtins__", "__subclasses__",
})

_OPEN_WRITE_MODES = frozenset({"w", "wb", "a", "ab", "x", "xb", "w+", "r+", "rb+"})


def _validate_code_safety(code: str) -> str | None:
    """Static analysis via AST. Returns error message or None if safe."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Sintaxe invalida e detectada pelo subprocess do executor;
        # nao e nossa responsabilidade aqui. Permitir.
        return None

    for node in ast.walk(tree):
        # import X / from X import ...
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _BLOCKED_MODULES:
                    return _format_block(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _BLOCKED_MODULES:
                    return _format_block(f"from {node.module} import ...")
        # eval/exec/compile/getattr/chr/__import__/breakpoint(...)
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = None
            if isinstance(fn, ast.Name):
                fname = fn.id
            elif isinstance(fn, ast.Attribute):
                fname = fn.attr
            if fname in _BLOCKED_CALL_NAMES:
                return _format_block(f"{fname}(...)")
            # open(path, "w") — block apenas modo de escrita real
            if fname == "open" and len(node.args) >= 2:
                mode_node = node.args[1]
                if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
                    if mode_node.value in _OPEN_WRITE_MODES:
                        return _format_block(f"open(..., {mode_node.value!r})")
        # __builtins__ / __loader__ / x.__subclasses__()
        elif isinstance(node, ast.Name) and node.id in _BLOCKED_NAME_TOKENS:
            return _format_block(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _BLOCKED_NAME_TOKENS:
            return _format_block(f".{node.attr}")
    return None


def _format_block(snippet: str) -> str:
    return (
        f"Código bloqueado por segurança: '{snippet[:60]}' não é permitido. "
        f"Módulos como os, subprocess, shutil, sys, ctypes são bloqueados. "
        f"Use as ferramentas do agente (execute_shell, write_file) para operações de sistema."
    )


# ─── Tools ───


async def _execute_python(code: str, timeout: int | None = None) -> dict:
    """Execute Python code in a subprocess with timeout."""
    from ..config import TOOL_TIMEOUTS
    if timeout is None:
        timeout = TOOL_TIMEOUTS.get("code", 60)
    timeout = min(timeout, 60)  # Hard cap

    # Local execution with static import blocklist
    safety_error = _validate_code_safety(code)
    if safety_error:
        return {"error": safety_error, "blocked": True}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        f.flush()
        script_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=get_safe_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "error": f"Execução excedeu o timeout de {timeout}s",
                "timeout": True,
            }
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Sem este bloco, Ctrl+C durante codigo Python longo deixa o
            # subprocess rodando ate o fim (loop infinito, fork bomb leve).
            proc.kill()
            await proc.wait()
            raise

        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode(errors="replace")[:15000],
            "stderr": stderr.decode(errors="replace")[:5000],
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


async def _install_package(package: str) -> dict:
    """Install a Python package via pip."""
    package = package.strip()

    # Block URL-based installs
    if any(package.lower().startswith(prefix) for prefix in URL_PREFIXES):
        return {
            "error": "Instalação de URLs/caminhos não é permitida. Use apenas nomes de pacotes PyPI."
        }

    # Strict package name validation
    if not VALID_PACKAGE_RE.match(package):
        return {
            "error": f"Nome de pacote inválido: '{package}'. Use formato: 'nome' ou 'nome==versão'"
        }

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        package,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=get_safe_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {"error": "Instalação excedeu timeout de 120s"}

    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace")[:5000],
        "stderr": stderr.decode(errors="replace")[:5000],
    }


# ─── Registration ───


register_tool(
    ToolDefinition(
        name="execute_python",
        description="Executar código Python em um processo separado. Retorna stdout, stderr e exit code.",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Código Python a executar"},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout em segundos (máx 60). Padrão: 30",
                    "default": 30,
                },
            },
            "required": ["code"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="code",
        executor=_execute_python,
    )
)

register_tool(
    ToolDefinition(
        name="install_package",
        description="Instalar um pacote Python via pip.",
        parameters={
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": "Nome do pacote pip (ex: 'requests', 'numpy==1.24')",
                },
            },
            "required": ["package"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="code",
        executor=_install_package,
    )
)
