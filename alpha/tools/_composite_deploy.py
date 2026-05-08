"""deploy_check tool — composite (#030 split)."""

import asyncio

from . import ToolDefinition, ToolSafety, register_tool
from ._composite_helpers import _run_tool, _violation
from ._composite_tests import _run_tests
from .path_helpers import _validate_path
from .workspace import AGENT_WORKSPACE


async def _deploy_check(path: str = None) -> dict:
    """Run pre-deployment checklist: tests, git status, lint."""
    target = path or str(AGENT_WORKSPACE)
    try:
        target_path = _validate_path(target)
    except PermissionError as e:
        return _violation(str(e))

    checks = {}

    tasks = {
        "git_status": _run_tool("git_operation", action="status", path=str(target_path)),
        "tests": _run_tests(path=str(target_path)),
    }

    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

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
            checks[key] = {"status": "pass" if exit_code == 0 else "fail", **result}
            if exit_code != 0:
                all_passed = False

    return {
        "all_passed": all_passed,
        "checks": checks,
        "path": str(target_path),
        "recommendation": (
            "Pronto para deploy" if all_passed
            else "Corrija os problemas antes de fazer deploy"
        ),
    }


register_tool(
    ToolDefinition(
        name="deploy_check",
        description=(
            "Executar checklist de pre-deploy: status git, testes, e verificacoes gerais. "
            "Retorna se esta pronto para deploy ou quais problemas corrigir."
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
        category="composite",
        executor=_deploy_check,
    )
)
