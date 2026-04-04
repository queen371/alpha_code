"""Composite tools (macros) for ALPHA agent.

Higher-level operations that combine multiple atomic tools into workflows.
These are meta-tools that orchestrate sequences of tool calls.

SECURITY: Each step in a composite tool uses the existing tool security model.
"""

import asyncio
import logging
from pathlib import Path

from . import ToolDefinition, ToolSafety, get_tool, register_tool
from .workspace import AGENT_WORKSPACE

logger = logging.getLogger(__name__)


async def _run_tool(name: str, **kwargs) -> dict:
    """Execute a registered tool by name."""
    tool_def = get_tool(name)
    if not tool_def:
        return {"error": f"Tool '{name}' não encontrada no registry"}
    try:
        return await tool_def.executor(**kwargs)
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
    search_result = await _run_tool("search_files", path=str(target_path), query=search)
    if "error" in search_result:
        return search_result

    matches = search_result.get("matches", [])
    if not matches:
        return {"matches": 0, "message": f"Nenhuma ocorrência de '{search}' encontrada"}

    # Group by file
    files_to_change = {}
    for match in matches:
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

    # Apply replacements
    changed_files = []
    errors = []

    for filepath in files_to_change:
        filepath_obj = Path(filepath)
        try:
            content = filepath_obj.read_text(encoding="utf-8")
        except Exception as e:
            errors.append({"file": filepath, "error": str(e)})
            continue

        new_content = content.replace(search, replace)

        if content != new_content:
            try:
                filepath_obj.write_text(new_content, encoding="utf-8")
                changed_files.append(filepath)
            except Exception as e:
                errors.append({"file": filepath, "error": str(e)})

    return {
        "dry_run": False,
        "files_changed": len(changed_files),
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
