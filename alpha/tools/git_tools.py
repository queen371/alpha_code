"""Git operations tool for ALPHA agent.

Provides safe, structured git operations within the workspace.

SECURITY: Only operates within AGENT_WORKSPACE. Destructive operations
(push, reset, clean) require approval. Read operations are safe.
"""

import asyncio
import logging
import re
import shlex
from pathlib import Path

from . import ToolDefinition, ToolSafety, register_tool
from ._subprocess_helpers import SubprocessTimeoutError, run_subprocess_safe
from ..config import TOOL_TIMEOUTS
from .safe_env import get_safe_env
from .workspace import AGENT_WORKSPACE, assert_within_workspace

# #D006: pre-compilada no module level. Antes era recompilada em cada
# `_sanitize_git_args` (3-5 chamadas por tool call de log/show/diff/push/etc).
_DANGEROUS_GIT_FMT = re.compile(
    r"%\((if|then|else|end|contents:signature|trailers)\)", re.IGNORECASE
)

logger = logging.getLogger(__name__)

# Whitelist de flags permitidas por action
_ALLOWED_GIT_FLAGS = {
    "diff": {"--stat", "--name-only", "--cached", "--staged", "--shortstat", "--no-color"},
    "log": {
        "--oneline",
        "--graph",
        "--all",
        "-n",
        "--format",
        "--since",
        "--author",
        "--pretty",
        "--abbrev-commit",
        "--no-color",
    },
    "show": {"--stat", "--no-color", "--format", "--pretty"},
    "push": {"--set-upstream", "-u", "--tags"},
    "reset": {"--soft", "--mixed"},  # --hard requer aprovação via _needs_approval
    "tag_create": {"-a", "--annotate", "-m", "--message"},  # DL035
}

# Flags globalmente bloqueadas (escape do workspace / configuração)
_BLOCKED_GIT_FLAGS = frozenset({"--no-index", "--work-tree", "--git-dir", "-C", "--file"})

# Read-only git actions (SAFE)
_SAFE_ACTIONS = frozenset(
    {
        "status",
        "diff",
        "log",
        "branch",
        "show",
        "blame",
        "stash_list",
        "remote",
        "tag",
    }
)

# Write/mutating git actions (DESTRUCTIVE)
_DESTRUCTIVE_ACTIONS = frozenset(
    {
        "add",
        "commit",
        "checkout",
        "stash",
        "stash_pop",
        "pull",
        "push",
        "merge",
        "rebase",
        "reset",
        "clean",
        "branch_create",
        "branch_delete",
        "tag_create",
    }
)

_ALL_ACTIONS = _SAFE_ACTIONS | _DESTRUCTIVE_ACTIONS


async def _run_git(args: list[str], cwd: str, timeout: int | None = None) -> dict:
    """Run a git command and return result."""
    if timeout is None:
        timeout = TOOL_TIMEOUTS.get("git", 30)
    cmd = ["git"] + args
    try:
        r = await run_subprocess_safe(*cmd, timeout=timeout, cwd=cwd)
    except SubprocessTimeoutError:
        return {"error": f"git excedeu timeout de {timeout}s", "timeout": True}
    except Exception as e:
        return {"error": str(e)}

    return {
        "exit_code": r.returncode,
        "stdout": r.stdout.decode(errors="replace")[:15000],
        "stderr": r.stderr.decode(errors="replace")[:3000],
    }


def _find_git_repo(path: str) -> str | None:
    """Walk up to find .git directory. Never escapes AGENT_WORKSPACE."""
    p = Path(path).resolve()
    ws = AGENT_WORKSPACE.resolve()
    while p != p.parent:
        try:
            p.relative_to(ws)
        except ValueError:
            return None
        if (p / ".git").exists():
            return str(p)
        p = p.parent
    return None


def _sanitize_git_args(action: str, args: str) -> tuple[list[str], str | None]:
    """Valida e sanitiza args git. Retorna (args_limpos, erro_ou_None)."""
    if not args:
        return [], None

    try:
        parts = shlex.split(args)
    except ValueError:
        return [], "Args malformados"

    for part in parts:
        # Bloquear flags globalmente perigosas (match exato ou prefixo com =)
        for blocked in _BLOCKED_GIT_FLAGS:
            if part == blocked or part.startswith(f"{blocked}=") or part.startswith(f"{blocked}/"):
                return [], f"Flag '{blocked}' bloqueada por segurança"
        # Bloquear force push via +refspec
        if part.startswith("+") and action == "push":
            return [], "Force push via +refspec bloqueado"

    # Block dangerous format string expansions (can execute hooks).
    # Regex pre-compilada em module level (#D006) — evitar recompilacao a
    # cada chamada de _sanitize_git_args.
    for j, part in enumerate(parts):
        # Check --format=VALUE and --pretty=VALUE (with =)
        if part.startswith("--format=") or part.startswith("--pretty="):
            fmt_value = part.split("=", 1)[1]
            if _DANGEROUS_GIT_FMT.search(fmt_value):
                return [], f"Format string com expansões perigosas bloqueada: '{part[:50]}'"
        # Check --format VALUE and --pretty VALUE (space-separated)
        elif part in ("--format", "--pretty") and j + 1 < len(parts):
            next_val = parts[j + 1]
            if _DANGEROUS_GIT_FMT.search(next_val):
                return [], f"Format string com expansões perigosas bloqueada: '{next_val[:50]}'"

    # Se a action tem whitelist, validar flags
    allowed = _ALLOWED_GIT_FLAGS.get(action)
    if allowed is not None:
        for part in parts:
            if part.startswith("-"):
                # Permitir flags numéricas como -20 para log
                if part.lstrip("-").isdigit():
                    continue
                # Allow --format=... and --pretty=... (already validated above)
                flag_base = part.split("=")[0]
                if flag_base in ("--format", "--pretty") and "=" in part:
                    if flag_base in allowed:
                        continue
                if part not in allowed:
                    return [], f"Flag '{part}' não permitida para git {action}"

    return parts, None


def _reject_dash_prefixed(label: str, value: str) -> str | None:
    """Bloqueia values comecando com '-' que git interpretaria como flag.

    Sem isso, `branch="--detach"` em checkout vira flag (descartando local
    changes); `message="--amend"` em commit reescreve o ultimo commit;
    `files=["--exec=evil"]` em add executa hooks. subprocess_exec ja
    previne shell injection, mas nao protege contra arg-injection no
    proprio git.
    """
    if value and value.startswith("-"):
        return f"{label} não pode começar com '-' (interpretado como flag git): {value!r}"
    return None


async def _git_operation(
    action: str,
    path: str = None,
    message: str = None,
    branch: str = None,
    files: list = None,
    args: str = None,
) -> dict:
    """Execute a structured git operation."""
    action = action.lower().strip()

    if action not in _ALL_ACTIONS:
        return {
            "error": f"Ação git '{action}' não reconhecida. "
            f"Ações disponíveis: {', '.join(sorted(_ALL_ACTIONS))}",
        }

    if branch is not None:
        err = _reject_dash_prefixed("branch", branch)
        if err:
            return {"error": err}
    if message is not None:
        err = _reject_dash_prefixed("message", message)
        if err:
            return {"error": err}
    if files:
        for f in files:
            err = _reject_dash_prefixed("files[]", f)
            if err:
                return {"error": err}

    # Resolve repo path
    if path:
        repo_path = Path(path).expanduser().resolve()
        err = assert_within_workspace(repo_path)
        if err:
            return {"error": err}
        cwd = str(repo_path)
    else:
        cwd = str(AGENT_WORKSPACE)

    # Find git repo
    repo_root = _find_git_repo(cwd)
    if not repo_root:
        return {"error": f"Nenhum repositório git encontrado em {cwd} ou diretórios pais"}

    cwd = repo_root

    # Route to specific action
    if action == "status":
        return await _run_git(["status", "--porcelain", "-b"], cwd)

    elif action == "diff":
        extra, err = _sanitize_git_args("diff", args)
        if err:
            return {"error": err}
        return await _run_git(["diff"] + extra, cwd)

    elif action == "log":
        extra, err = _sanitize_git_args("log", args)
        if err:
            return {"error": err}
        if not extra:
            extra = ["--oneline", "-20"]
        return await _run_git(["log"] + extra, cwd)

    elif action == "branch":
        return await _run_git(["branch", "-a", "-v"], cwd)

    elif action == "show":
        extra, err = _sanitize_git_args("show", args)
        if err:
            return {"error": err}
        ref = extra[0] if extra else "HEAD"
        return await _run_git(["show", "--stat", ref], cwd)

    elif action == "blame":
        if not files:
            return {"error": "blame requer 'files' com pelo menos um arquivo"}
        return await _run_git(["blame", "--porcelain", files[0]], cwd, timeout=60)

    elif action == "stash_list":
        return await _run_git(["stash", "list"], cwd)

    elif action == "remote":
        return await _run_git(["remote", "-v"], cwd)

    elif action == "tag":
        return await _run_git(["tag", "-l", "--sort=-creatordate"], cwd)

    elif action == "add":
        targets = files if files else ["."]
        return await _run_git(["add"] + targets, cwd)

    elif action == "commit":
        if not message:
            return {"error": "commit requer 'message'"}
        return await _run_git(["commit", "-m", message], cwd)

    elif action == "checkout":
        if not branch:
            return {"error": "checkout requer 'branch'"}
        return await _run_git(["checkout", branch], cwd)

    elif action == "branch_create":
        if not branch:
            return {"error": "branch_create requer 'branch'"}
        return await _run_git(["checkout", "-b", branch], cwd)

    elif action == "branch_delete":
        if not branch:
            return {"error": "branch_delete requer 'branch'"}
        if branch in ("main", "master"):
            return {"error": "Não é permitido deletar branch main/master"}
        return await _run_git(["branch", "-d", branch], cwd)

    elif action == "stash":
        msg = ["-m", message] if message else []
        return await _run_git(["stash", "push"] + msg, cwd)

    elif action == "stash_pop":
        return await _run_git(["stash", "pop"], cwd)

    elif action == "pull":
        return await _run_git(["pull", "--rebase"], cwd, timeout=60)

    elif action == "push":
        extra, err = _sanitize_git_args("push", args)
        if err:
            return {"error": err}
        # Force push e bloqueado a priori: `_ALLOWED_GIT_FLAGS["push"]` so
        # permite `--set-upstream`, `-u`, `--tags`. Qualquer `--force`/`-f`
        # rejeitado pelo `_sanitize_git_args` antes de chegar aqui (#D029).
        # Se um dia force push for permitido em branches nao-main, lembrar
        # de adicionar a allowlist E reintroduzir o check de current branch.
        return await _run_git(["push"] + extra, cwd, timeout=60)

    elif action == "merge":
        if not branch:
            return {"error": "merge requer 'branch'"}
        return await _run_git(["merge", branch], cwd)

    elif action == "rebase":
        if not branch:
            return {"error": "rebase requer 'branch'"}
        return await _run_git(["rebase", branch], cwd)

    elif action == "reset":
        extra, err = _sanitize_git_args("reset", args)
        if err:
            return {"error": err}
        if not extra:
            extra = ["--mixed", "HEAD~1"]
        else:
            # Inject --mixed if no mode flag provided (avoid implicit git defaults)
            has_mode = any(f in extra for f in ("--soft", "--mixed", "--hard", "--merge", "--keep"))
            if not has_mode:
                extra = ["--mixed"] + extra
        return await _run_git(["reset"] + extra, cwd)

    elif action == "clean":
        return await _run_git(["clean", "-fd"], cwd)

    elif action == "tag_create":
        if not args:
            return {"error": "tag_create requer 'args' com o nome da tag"}
        tag_args, err = _sanitize_git_args("tag_create", args)
        if err:
            return {"error": err}
        if not tag_args:
            return {"error": "tag_create requer pelo menos o nome da tag"}
        if message:
            return await _run_git(["tag", "-a", tag_args[0], "-m", message], cwd)
        return await _run_git(["tag"] + tag_args, cwd)

    return {"error": f"Ação '{action}' não implementada"}


# Register safe version (read-only operations)
register_tool(
    ToolDefinition(
        name="git_operation",
        description=(
            "Executar operações git de forma segura e estruturada. "
            "Ações de leitura: status, diff, log, branch, show, blame, stash_list, remote, tag. "
            "Ações de escrita: add, commit, checkout, branch_create, branch_delete, stash, "
            "stash_pop, pull, push, merge, rebase, reset, clean, tag_create. "
            "Force push em main/master é bloqueado."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Ação git a executar",
                    "enum": sorted(_ALL_ACTIONS),
                },
                "path": {
                    "type": "string",
                    "description": "Caminho do repositório (opcional, usa workspace padrão)",
                },
                "message": {
                    "type": "string",
                    "description": "Mensagem para commit, stash ou tag",
                },
                "branch": {
                    "type": "string",
                    "description": "Nome da branch (para checkout, merge, rebase, branch_create, branch_delete)",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de arquivos (para add, blame)",
                },
                "args": {
                    "type": "string",
                    "description": "Argumentos extras como string (para diff, log, push, reset, show, tag_create)",
                },
            },
            "required": ["action"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="git",
        executor=_git_operation,
    )
)
