"""project_overview tool — composite (#030 split)."""

import asyncio

from . import ToolCategory, ToolDefinition, ToolSafety, register_tool
from ._composite_helpers import _run_tool, _violation
from .path_helpers import _validate_path
from .workspace import AGENT_WORKSPACE


async def _project_overview(path: str = None) -> dict:
    """Get a comprehensive overview of a project directory."""
    target = path or str(AGENT_WORKSPACE)
    try:
        target_path = _validate_path(target)
    except PermissionError as e:
        return _violation(str(e))

    results = {}

    # Run multiple reads in parallel
    tasks = {
        "listing": _run_tool("list_directory", path=target),
        "git": _run_tool("git_operation", action="status", path=target),
    }

    # Check for common project files
    project_files = [
        "package.json", "requirements.txt", "pyproject.toml",
        "Cargo.toml", "go.mod", "Makefile", "docker-compose.yml",
        "Dockerfile", ".env.example", "README.md",
    ]

    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for key, result in zip(tasks.keys(), gathered):
        results[key] = {"error": str(result)} if isinstance(result, Exception) else result

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


register_tool(
    ToolDefinition(
        name="project_overview",
        description=(
            "Obter visao geral de um projeto: estrutura de diretorios, tipo de projeto "
            "(Python/Node/Rust/Go), arquivos de configuracao, status git. "
            "Combina list_directory + git status + deteccao automatica."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Caminho do projeto (opcional, usa workspace padrao)",
                },
            },
        },
        safety=ToolSafety.SAFE,
        category=ToolCategory.COMPOSITE,
        executor=_project_overview,
    )
)
