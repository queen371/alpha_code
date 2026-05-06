"""Composite tools (macros) for ALPHA agent.

Higher-level operations that combine multiple atomic tools into workflows.
These are meta-tools that orchestrate sequences of tool calls.

SECURITY: Each step in a composite tool uses the existing tool security model.
"""

import asyncio
import logging
import re
from pathlib import Path

from . import ToolDefinition, ToolSafety, get_tool, register_tool
from .workspace import AGENT_WORKSPACE

logger = logging.getLogger(__name__)


async def _run_tool(name: str, *, timeout: float | None = None, **kwargs) -> dict:
    """Execute a registered tool by name.

    Adiciona enforcement de timeout (TOOL_EXECUTION_TIMEOUT por default,
    _SLOW_TOOL_TIMEOUT para tools registradas como slow). Sem isso, sub-tools
    da composite hangam indefinidamente — o timeout do agent so corta apos
    o cap do composite (300s), nao o do sub-tool.
    """
    from ..executor import (
        TOOL_EXECUTION_TIMEOUT,
        _SLOW_TOOL_TIMEOUT,
        _SLOW_TOOLS,
    )

    tool_def = get_tool(name)
    if not tool_def:
        return {"error": f"Tool '{name}' não encontrada no registry"}

    if timeout is None:
        timeout = _SLOW_TOOL_TIMEOUT if name in _SLOW_TOOLS else TOOL_EXECUTION_TIMEOUT

    try:
        return await asyncio.wait_for(tool_def.executor(**kwargs), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        return {
            "error": f"Tool '{name}' excedeu timeout de {timeout}s",
            "timeout": True,
        }
    except Exception as e:
        return {"error": f"Erro ao executar {name}: {e}"}


async def _project_overview(path: str = None) -> dict:
    """Get a comprehensive overview of a project directory."""
    target = path or str(AGENT_WORKSPACE)
    target_path = Path(target).expanduser().resolve()

    try:
        target_path.relative_to(AGENT_WORKSPACE)
    except ValueError:
        return {"error": f"Path fora do workspace permitido ({AGENT_WORKSPACE})"}

    results = {}

    # Run multiple reads in parallel
    tasks = {
        "listing": _run_tool("list_directory", path=target),
        "git": _run_tool("git_operation", action="status", path=target),
    }

    # Check for common project files
    project_files = [
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "docker-compose.yml",
        "Dockerfile",
        ".env.example",
        "README.md",
    ]

    gathered = await asyncio.gather(
        *tasks.values(),
        return_exceptions=True,
    )

    for key, result in zip(tasks.keys(), gathered):
        if isinstance(result, Exception):
            results[key] = {"error": str(result)}
        else:
            results[key] = result

    # Detect project type
    project_type = []
    if (target_path / "package.json").exists():
        project_type.append("node/javascript")
    if (target_path / "requirements.txt").exists() or (target_path / "pyproject.toml").exists():
        project_type.append("python")
    if (target_path / "Cargo.toml").exists():
        project_type.append("rust")
    if (target_path / "go.mod").exists():
        project_type.append("go")
    if (target_path / "Dockerfile").exists():
        project_type.append("docker")

    # Find existing project files
    found_files = [f for f in project_files if (target_path / f).exists()]

    results["project_type"] = project_type or ["unknown"]
    results["project_files"] = found_files
    results["path"] = str(target_path)

    return results


async def _run_tests(
    path: str = None,
    framework: str = "auto",
    pattern: str = None,
) -> dict:
    """Detect test framework and run tests."""
    target = path or str(AGENT_WORKSPACE)
    target_path = Path(target).expanduser().resolve()

    try:
        target_path.relative_to(AGENT_WORKSPACE)
    except ValueError:
        return {"error": f"Path fora do workspace permitido ({AGENT_WORKSPACE})"}

    # Auto-detect framework
    if framework == "auto":
        if (target_path / "pytest.ini").exists() or (target_path / "pyproject.toml").exists():
            framework = "pytest"
        elif (target_path / "package.json").exists():
            framework = "npm"
        elif (target_path / "Cargo.toml").exists():
            framework = "cargo"
        elif (target_path / "go.mod").exists():
            framework = "go"
        else:
            # Look for test files
            test_files = list(target_path.rglob("test_*.py")) + list(target_path.rglob("*_test.py"))
            if test_files:
                framework = "pytest"
            else:
                return {
                    "error": "Não foi possível detectar o framework de testes automaticamente. Especifique 'framework'."
                }

    # Build command based on framework
    if framework == "pytest":
        cmd = "python3 -m pytest -v"
        if pattern:
            cmd += f" -k '{pattern}'"
    elif framework == "npm":
        cmd = "npm test"
    elif framework == "cargo":
        cmd = "cargo test"
        if pattern:
            cmd += f" {pattern}"
    elif framework == "go":
        cmd = "go test ./..."
        if pattern:
            cmd += f" -run '{pattern}'"
    else:
        return {"error": f"Framework '{framework}' não suportado. Use: pytest, npm, cargo, go"}

    # Execute via shell tool
    result = await _run_tool("execute_shell", command=cmd, cwd=str(target_path), timeout=120)

    result["framework"] = framework
    result["command"] = cmd
    return result


async def _search_and_replace(
    path: str,
    search: str,
    replace: str,
    file_pattern: str = "**/*",
    dry_run: bool = True,
) -> dict:
    """Search and replace across multiple files."""
    target_path = Path(path).expanduser().resolve()

    try:
        target_path.relative_to(AGENT_WORKSPACE)
    except ValueError:
        return {"error": f"Path fora do workspace permitido ({AGENT_WORKSPACE})"}

    # Find files with matches
    search_result = await _run_tool("search_files", path=str(target_path), pattern=re.escape(search))
    if "error" in search_result:
        return search_result

    results_list = search_result.get("results", [])
    if not results_list:
        return {"matches": 0, "message": f"Nenhuma ocorrência de '{search}' encontrada"}

    # Group by file
    files_to_change = {}
    for match in results_list:
        filepath = match.get("file", match.get("path", ""))
        if filepath:
            if filepath not in files_to_change:
                files_to_change[filepath] = 0
            files_to_change[filepath] += 1

    if dry_run:
        return {
            "dry_run": True,
            "files_affected": len(files_to_change),
            "total_matches": sum(files_to_change.values()),
            "files": files_to_change,
            "search": search,
            "replace": replace,
            "message": "Execute com dry_run=false para aplicar as mudanças",
        }

    # Apply replacements: read once, replace ALL in memory, write once.
    # A versao antiga chamava edit_file ate 500 vezes por arquivo, fazendo
    # 500 read + 500 write para arquivos com muitas ocorrencias (#D024-PERF).
    # Reuso `_validate_path_no_symlink` de file_tools para preservar a
    # validacao defense-in-depth.
    import os
    from .file_tools import _validate_path_no_symlink

    changed_files = []
    errors = []

    for filepath in files_to_change:
        try:
            p = _validate_path_no_symlink(filepath)
        except (PermissionError, OSError) as e:
            errors.append({"file": filepath, "error": str(e)})
            continue
        if not p.exists():
            errors.append({"file": filepath, "error": "Arquivo não encontrado"})
            continue
        try:
            original = p.read_text(errors="replace")
        except OSError as e:
            errors.append({"file": filepath, "error": str(e)})
            continue
        count = original.count(search)
        if count == 0:
            continue
        updated = original.replace(search, replace)
        # Single write — same atomic open pattern de edit_file
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(str(p), flags, 0o644)
            try:
                os.write(fd, updated.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError as e:
            errors.append({"file": filepath, "error": str(e)})
            continue
        changed_files.append({"file": filepath, "replacements": count})

    return {
        "dry_run": False,
        "files_changed": len(changed_files),
        "total_replacements": sum(f["replacements"] for f in changed_files),
        "changed": changed_files,
        "errors": errors if errors else None,
        "search": search,
        "replace": replace,
    }


async def _deploy_check(path: str = None) -> dict:
    """Run pre-deployment checklist: tests, git status, lint."""
    target = path or str(AGENT_WORKSPACE)
    target_path = Path(target).expanduser().resolve()

    try:
        target_path.relative_to(AGENT_WORKSPACE)
    except ValueError:
        return {"error": f"Path fora do workspace permitido ({AGENT_WORKSPACE})"}

    checks = {}

    # Run checks in parallel
    tasks = {
        "git_status": _run_tool("git_operation", action="status", path=str(target_path)),
        "tests": _run_tests(path=str(target_path)),
    }

    gathered = await asyncio.gather(
        *tasks.values(),
        return_exceptions=True,
    )

    all_passed = True
    for key, result in zip(tasks.keys(), gathered):
        if isinstance(result, Exception):
            checks[key] = {"status": "error", "error": str(result)}
            all_passed = False
        elif "error" in result:
            checks[key] = {"status": "error", **result}
            all_passed = False
        else:
            exit_code = result.get("exit_code", 0)
            checks[key] = {
                "status": "pass" if exit_code == 0 else "fail",
                **result,
            }
            if exit_code != 0:
                all_passed = False

    return {
        "all_passed": all_passed,
        "checks": checks,
        "path": str(target_path),
        "recommendation": "Pronto para deploy"
        if all_passed
        else "Corrija os problemas antes de fazer deploy",
    }


register_tool(
    ToolDefinition(
        name="project_overview",
        description=(
            "Obter visão geral de um projeto: estrutura de diretórios, tipo de projeto "
            "(Python/Node/Rust/Go), arquivos de configuração, status git. "
            "Combina list_directory + git status + detecção automática."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Caminho do projeto (opcional, usa workspace padrão)",
                },
            },
        },
        safety=ToolSafety.SAFE,
        category="composite",
        executor=_project_overview,
    )
)

register_tool(
    ToolDefinition(
        name="run_tests",
        description=(
            "Detectar framework de testes e executar. Suporta pytest, npm test, cargo test, go test. "
            "Auto-detecção baseada em arquivos de configuração do projeto."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Caminho do projeto (opcional, usa workspace padrão)",
                },
                "framework": {
                    "type": "string",
                    "description": "Framework de testes. 'auto' para detectar automaticamente",
                    "enum": ["auto", "pytest", "npm", "cargo", "go"],
                    "default": "auto",
                },
                "pattern": {
                    "type": "string",
                    "description": "Padrão para filtrar testes específicos (ex: 'test_auth' para pytest)",
                },
            },
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="composite",
        executor=_run_tests,
    )
)

register_tool(
    ToolDefinition(
        name="search_and_replace",
        description=(
            "Buscar e substituir texto em múltiplos arquivos. Modo dry_run por padrão "
            "(mostra o que seria alterado sem alterar). Use dry_run=false para aplicar."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Diretório raiz para busca",
                },
                "search": {
                    "type": "string",
                    "description": "Texto a buscar",
                },
                "replace": {
                    "type": "string",
                    "description": "Texto de substituição",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Padrão glob para filtrar arquivos (ex: '**/*.py'). Padrão: **/*",
                    "default": "**/*",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Se true, apenas mostra o que seria alterado. Padrão: true",
                    "default": True,
                },
            },
            "required": ["path", "search", "replace"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="composite",
        executor=_search_and_replace,
    )
)

register_tool(
    ToolDefinition(
        name="deploy_check",
        description=(
            "Executar checklist de pré-deploy: status git, testes, e verificações gerais. "
            "Retorna se está pronto para deploy ou quais problemas corrigir."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Caminho do projeto (opcional, usa workspace padrão)",
                },
            },
        },
        safety=ToolSafety.SAFE,
        category="composite",
        executor=_deploy_check,
    )
)
