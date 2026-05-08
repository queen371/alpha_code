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

# AST-based blocklists (#D018-PERF + #027/#075/#084 V1.1 + #009 V1.2):
# AST walk linear substituiu a busca regex MULTILINE (175K tentativas no
# pior caso para 5KB de codigo) e tambem elimina falsos positivos como
# `\bopen\s*\(.*(w|a|x)` que casava em `open("read.txt")`. A regex legacy
# foi removida — qualquer extensao do blocklist deve adicionar entradas
# em _BLOCKED_MODULES / _BLOCKED_CALL_NAMES / _BLOCKED_NAME_TOKENS abaixo.
#
# AUDIT V1.2 #009 (RCE crítica corrigida): a tabela anterior listava `os`
# e `subprocess` mas NÃO os módulos *low-level* que esses dois envelopam
# em CPython — `posix`, `nt`, `_posixsubprocess`, `_winapi`. Como a tool
# está em AUTO_APPROVE_TOOLS, `import posix; posix.system("...")` virava
# RCE sem prompt. Esta tabela agora cobre toda a superfície de OS-level
# access que o CPython expõe pra Python puro.
_BLOCKED_MODULES = frozenset({
    # User-facing OS / process / FS modules.
    "os", "subprocess", "shutil", "sys", "importlib", "ctypes", "_ctypes",
    "signal", "pathlib", "code", "multiprocessing", "webbrowser",
    # Low-level OS interfaces — CPython implementation detail of `os` and
    # `subprocess`. Same capabilities, different name (#009 V1.2).
    "posix", "nt", "_posixsubprocess", "_winapi", "msvcrt",
    # Memory / file-descriptor primitives.
    "mmap", "fcntl", "termios", "tty", "pty", "resource", "select",
    # Threading / scheduling that can spawn workers.
    "_thread", "threading", "concurrent", "sched",
    # Networking — direct + transitive through deps.
    "socket", "_socket", "ssl", "asyncore", "asynchat",
    "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib",
    "telnetlib", "xmlrpc",
    "httpx", "requests", "aiohttp", "urllib3",
    "duckduckgo_search", "ddgs", "dotenv",
    # Serialization that executes arbitrary code on load.
    "pickle", "_pickle", "shelve", "marshal", "dill", "cloudpickle",
    # Reflection / dynamic loading.
    "runpy", "inspect", "gc", "platform", "dis", "linecache",
    "site", "sysconfig", "modulefinder",
    # Direct access to the builtins namespace defeats the AST guards.
    "builtins", "__builtin__",
    # Low-level user/group/auth lookups.
    "pwd", "grp", "spwd", "crypt", "syslog", "nis",
})

_BLOCKED_CALL_NAMES = frozenset({
    "__import__", "eval", "exec", "compile", "breakpoint",
    "globals", "locals", "getattr", "setattr", "delattr",
    "vars", "chr", "input",
})

# Attribute / name access tokens. These names are dangerous either as
# dotted-attr (`x.__subclasses__`) or as bare Name nodes — both shapes
# are caught by the validator below.
_BLOCKED_NAME_TOKENS = frozenset({
    "__loader__", "__builtins__", "__subclasses__",
    # __getattribute__ / __getattr__ / __setattr__ allow indirect attribute
    # access that would otherwise be statically visible to the validator.
    # Block them as defense-in-depth even though `__subclasses__` already
    # closes the most common gadget chain (#012 V1.2).
    "__getattribute__", "__getattr__",
    "__setattr__", "__delattr__",
    # `()` chain to enumerate classes used to need __subclasses__, but a
    # well-known alternative is `__init_subclass__.__self_class__` — block
    # the whole metaclass-traversal cluster.
    "__init_subclass__", "__build_class__",
    # Code-object access — could let a determined caller construct a
    # function with a bytecode payload.
    "__code__", "__closure__", "__globals__",
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
        f"Acesso a módulos de OS (os, subprocess, posix, _posixsubprocess, "
        f"ctypes, mmap, fcntl, etc.) e a primitivas de reflection "
        f"(__getattribute__, __subclasses__, __code__) é bloqueado. "
        f"Use as ferramentas do agente (execute_shell, write_file) "
        f"para operações de sistema."
    )


# ─── Tools ───


async def _execute_python(code: str, timeout: int | None = None) -> dict:
    """Execute Python code in a subprocess with timeout."""
    from ..config import TOOL_TIMEOUT_CAPS, TOOL_TIMEOUTS
    if timeout is None:
        timeout = TOOL_TIMEOUTS.get("code", 60)
    timeout = min(timeout, TOOL_TIMEOUT_CAPS.get("code", 60))

    # Local execution with static import blocklist
    safety_error = _validate_code_safety(code)
    if safety_error:
        return {"error": safety_error, "blocked": True}

    # #DM014: pre-init para garantir que `finally: os.unlink(script_path)`
    # nao quebre com NameError caso a criacao do tempfile falhe.
    script_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            script_path = f.name
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
        if script_path is not None:
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
