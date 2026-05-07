"""Shell pipeline execution tool for ALPHA agent.

Extends shell capabilities with pipe chains, redirects, and composed commands.
Each command in the pipeline is validated against the allowlist individually.

SECURITY: Each pipeline stage is validated. Hard-blocked patterns checked on full pipeline.
"""

import asyncio
import logging
import re
import shlex
from pathlib import Path

from . import ToolDefinition, ToolSafety, register_tool
from .safe_env import get_safe_env
from .shell_tools import HARD_BLOCKED
from .workspace import AGENT_WORKSPACE

logger = logging.getLogger(__name__)

# Operators allowed in pipelines
_PIPE_OPERATORS = frozenset({"|", "&&", "||", ";", ">", ">>", "2>&1", "2>", "<"})


_SHELL_EXPANSION_RE = re.compile(
    r"\$\(|`"  # command substitution: $(...) or `...`
    r"|\$\{"  # variable expansion: ${...}
    r"|\$[A-Za-z_]"  # variable reference: $VAR
    r"|<\("  # process substitution: <(...)
    r"|\$\(\("  # arithmetic expansion: $((...))
)


def _validate_pipeline(pipeline: str) -> str | None:
    """Validate a full pipeline string. Returns error message or None.

    Denylist model: catastrophic patterns blocked; everything else runs.
    """
    # Block shell variable/command expansion (injection vector)
    if _SHELL_EXPANSION_RE.search(pipeline):
        return "Pipeline bloqueado: expansão de variáveis/comandos ($(), ``, ${}) não é permitida"

    # Check hard-blocked patterns on the full string first (pre-compiled)
    for pattern in HARD_BLOCKED:
        if pattern.search(pipeline):
            return "Pipeline bloqueado por segurança (padrão destrutivo detectado)"

    # Syntactic check per segment (no allowlist; HARD_BLOCKED already gated)
    segments = re.split(r"\s*(?:\|\||&&|;|\|)\s*", pipeline)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        cmd_part = re.split(r"\s*(?:>>?|2>>?|<)\s*", segment)[0].strip()
        if not cmd_part:
            continue
        try:
            parts = shlex.split(cmd_part)
            if not parts:
                continue
        except ValueError:
            return f"Segmento malformado no pipeline: {segment}"

    return None


def _validate_redirect_paths(pipeline: str) -> str | None:
    """Ensure redirect targets are within workspace.

    `2>&1` (e variantes `>&N`) sao FD duplications — `&1` nao e path nem
    cria arquivo. Pular esses para evitar tanto criar arquivos chamados
    `&1` quanto rejeitar pipelines comuns como `cmd 2>&1 | grep ...`.
    """
    redirects = re.findall(r"(?:>>?|2>>?)\s*(\S+)", pipeline)
    for target in redirects:
        if target.startswith("&"):
            continue  # &1, &2 sao FD references, nao paths
        target_path = Path(target).expanduser().resolve()
        try:
            target_path.relative_to(AGENT_WORKSPACE)
        except ValueError:
            return f"Redirect para '{target}' fora do workspace permitido ({AGENT_WORKSPACE})"
    return None


_REDIRECT_RE = re.compile(r"(2>>|2>|>>|>|<)\s*(\S+)")


def _parse_segment(segment: str) -> tuple[list[str], dict[str, str]]:
    """
    Separa comando e redirects de um segmento de pipeline.
    Retorna (cmd_parts, redirects_dict).
    """
    redirects = {}
    for match in _REDIRECT_RE.finditer(segment):
        op, target = match.group(1), match.group(2)
        if op == ">":
            redirects["stdout"] = target
        elif op == ">>":
            redirects["stdout_append"] = target
        elif op == "2>":
            redirects["stderr"] = target
        elif op == "2>>":
            redirects["stderr_append"] = target
        elif op == "<":
            redirects["stdin"] = target

    cmd_str = _REDIRECT_RE.sub("", segment).strip()
    parts = shlex.split(cmd_str) if cmd_str else []
    return parts, redirects


def _open_redirect_files(redirects: dict[str, str]) -> dict:
    """Abre file handles para redirects. Valida contra workspace."""
    handles = {}
    for key, target in redirects.items():
        target_path = Path(target).expanduser().resolve()
        try:
            target_path.relative_to(AGENT_WORKSPACE)
        except ValueError:
            raise ValueError(f"Redirect '{target}' fora do workspace ({AGENT_WORKSPACE})")

        if key == "stdout":
            handles["stdout"] = open(target_path, "w")
        elif key == "stdout_append":
            handles["stdout"] = open(target_path, "a")
        elif key == "stderr":
            handles["stderr"] = open(target_path, "w")
        elif key == "stderr_append":
            handles["stderr"] = open(target_path, "a")
        elif key == "stdin":
            handles["stdin"] = open(target_path)
    return handles


async def _execute_pipe_chain(
    pipe_segments: list[str],
    cwd: str,
    env: dict,
    timeout: int,
) -> tuple[int, str, str]:
    """
    Executa uma cadeia de pipes (cmd1 | cmd2 | cmd3) sem shell.
    Cada segmento é executado com create_subprocess_exec.
    Para pipes, lê stdout do anterior e passa como stdin do próximo.
    Retorna (exit_code, stdout, stderr).
    """
    if not pipe_segments:
        return 0, "", ""

    # Caso simples: um único comando
    if len(pipe_segments) == 1:
        parts, redirects = _parse_segment(pipe_segments[0])
        if not parts:
            return 0, "", ""
        handles = _open_redirect_files(redirects)
        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdin=handles.get("stdin"),
                stdout=handles.get("stdout", asyncio.subprocess.PIPE),
                stderr=handles.get("stderr", asyncio.subprocess.PIPE),
                cwd=cwd,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except (TimeoutError, asyncio.CancelledError):
                # Sem kill, o subprocess vira zumbi ate ESGOTAR PIDs.
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
                raise
            return (
                proc.returncode,
                (stdout_bytes or b"").decode(errors="replace"),
                (stderr_bytes or b"").decode(errors="replace"),
            )
        finally:
            for h in handles.values():
                if hasattr(h, "close"):
                    h.close()

    # Multi-command pipe: run sequentially, passing stdout → stdin
    prev_output = None
    all_stderr = ""
    last_exit_code = 0

    for j, seg in enumerate(pipe_segments):
        parts, redirects = _parse_segment(seg)
        if not parts:
            continue

        handles = _open_redirect_files(redirects)
        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdin=asyncio.subprocess.PIPE if prev_output is not None else handles.get("stdin"),
                stdout=handles.get("stdout", asyncio.subprocess.PIPE),
                stderr=handles.get("stderr", asyncio.subprocess.PIPE),
                cwd=cwd,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=prev_output),
                    timeout=timeout,
                )
            except (TimeoutError, asyncio.CancelledError):
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
                raise
            prev_output = stdout_bytes
            last_exit_code = proc.returncode
            if stderr_bytes:
                all_stderr += stderr_bytes.decode(errors="replace")
        finally:
            for h in handles.values():
                if hasattr(h, "close"):
                    h.close()

    return (
        last_exit_code,
        (prev_output or b"").decode(errors="replace"),
        all_stderr,
    )


async def _execute_pipeline(pipeline: str, cwd: str = None, timeout: int | None = None) -> dict:
    """Execute a shell pipeline with pipes, redirects, and operators — sem shell."""
    from ..config import TOOL_TIMEOUTS
    if timeout is None:
        timeout = TOOL_TIMEOUTS.get("pipeline", 120)

    # Validate entire pipeline
    block_reason = _validate_pipeline(pipeline)
    if block_reason:
        return {"error": block_reason, "blocked": True}

    # Validate redirect paths
    redirect_error = _validate_redirect_paths(pipeline)
    if redirect_error:
        return {"error": redirect_error, "blocked": True}

    # Validate cwd
    if cwd:
        cwd_path = Path(cwd).expanduser().resolve()
        try:
            cwd_path.relative_to(AGENT_WORKSPACE)
        except ValueError:
            return {"error": f"cwd fora do workspace permitido ({AGENT_WORKSPACE})"}
        cwd = str(cwd_path)
    else:
        cwd = str(AGENT_WORKSPACE)

    timeout = min(timeout, 120)
    env = get_safe_env()

    try:
        # Separar por operadores lógicos (&&, ||, ;) preservando o operador
        logical_segments = re.split(r"\s*(&&|\|\||;)\s*", pipeline)

        final_stdout = ""
        final_stderr = ""
        last_exit_code = 0

        i = 0
        skip_next = False
        while i < len(logical_segments):
            part = logical_segments[i].strip()
            i += 1

            if not part:
                continue

            # É um operador lógico?
            if part in ("&&", "||", ";"):
                if part == ";":
                    # Unconditional separator — always continue
                    skip_next = False
                elif (part == "&&" and last_exit_code != 0) or (part == "||" and last_exit_code == 0):
                    # Short-circuit: skip the NEXT command only
                    skip_next = True
                else:
                    skip_next = False
                continue

            if skip_next:
                skip_next = False
                continue

            # É um pipe chain: cmd1 | cmd2 | cmd3
            pipe_segments = [s.strip() for s in part.split("|") if s.strip()]

            exit_code, stdout, stderr = await _execute_pipe_chain(
                pipe_segments,
                cwd,
                env,
                timeout,
            )
            last_exit_code = exit_code
            final_stdout += stdout
            final_stderr += stderr

        return {
            "exit_code": last_exit_code,
            "stdout": final_stdout[:15000],
            "stderr": final_stderr[:5000],
            "pipeline": pipeline,
        }
    except TimeoutError:
        return {"error": f"Pipeline excedeu o timeout de {timeout}s", "timeout": True}
    except ValueError as e:
        return {"error": str(e), "blocked": True}
    except Exception as e:
        return {"error": str(e)}


register_tool(
    ToolDefinition(
        name="execute_pipeline",
        description=(
            "Executar um pipeline de comandos shell com pipes (|), operadores lógicos "
            "(&&, ||, ;) e redirects (>, >>). Cada comando do pipeline é validado "
            "individualmente. Exemplo: 'cat server.log | grep ERROR | wc -l'"
        ),
        parameters={
            "type": "object",
            "properties": {
                "pipeline": {
                    "type": "string",
                    "description": "Pipeline completo. Ex: 'cat app.log | grep ERROR | sort | uniq -c | sort -rn | head -20'",
                },
                "cwd": {
                    "type": "string",
                    "description": "Diretório de trabalho (opcional, deve estar dentro do workspace)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout em segundos (máx 120). Padrão: 60",
                    "default": 60,
                },
            },
            "required": ["pipeline"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="shell",
        executor=_execute_pipeline,
    )
)
