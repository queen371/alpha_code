"""Shell execution tool for ALPHA agent."""

import asyncio
import re
import shlex
from pathlib import Path

from . import ToolDefinition, ToolSafety, register_tool
from .safe_env import get_safe_env
from .workspace import AGENT_WORKSPACE

# ─── Security: Hard Blocks (denylist model) ───
#
# Politica real (#D027): denylist only. Qualquer comando que NAO bate com
# `HARD_BLOCKED` abaixo passa pelo approval layer. A `ALLOWED_COMMANDS`
# frozenset que vivia aqui ate 2026-05-07 era codigo morto — nunca era
# consultada por `_validate_command`. Removida pra evitar confusao em
# leitores/auditores que assumiam allowlist enforcement.

# Catastrophic / system-destructive patterns — blocked regardless of approval.
HARD_BLOCKED = [
    # Recursive file deletion
    re.compile(r"\brm\s+(?:-\S*[rR]\S*|--recursive\b)", re.IGNORECASE),
    # Filesystem formatting / wiping
    re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b", re.IGNORECASE),
    re.compile(r"\bmke2fs\b", re.IGNORECASE),
    re.compile(r"\bwipefs\b", re.IGNORECASE),
    re.compile(r"\bshred\b", re.IGNORECASE),
    # Raw disk writes
    re.compile(r"\bdd\s+[^\n]*of=/dev/(sd|nvme|hd|xvd|vd|mmcblk)", re.IGNORECASE),
    re.compile(r">\s*/dev/(sd|nvme|hd|xvd|vd|mmcblk)", re.IGNORECASE),
    # Fork bomb
    re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;?\s*:"),
    # su (sudo is handled via pattern matches below, not blanket-blocked)
    re.compile(r"(^|[;&|]\s*)su(\s|$)", re.IGNORECASE),
    # Power / halt / reboot
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b", re.IGNORECASE),
    re.compile(r"\binit\s+[0-6]\b", re.IGNORECASE),
    re.compile(r"\btelinit\b", re.IGNORECASE),
    re.compile(r"\bsystemctl\s+(poweroff|reboot|halt|kexec|rescue|emergency|suspend|hibernate)\b", re.IGNORECASE),
    # Writes to critical system files
    re.compile(r">\s*/etc/(passwd|shadow|sudoers|fstab|hosts(\s|$))", re.IGNORECASE),
    re.compile(r"\b(tee|dd)\s+[^|;]*\s/etc/(passwd|shadow|sudoers|fstab)", re.IGNORECASE),
    re.compile(r"\bvisudo\b", re.IGNORECASE),
    # chmod on critical system dirs
    re.compile(r"\bchmod\s+\S+\s+/(etc|usr|boot|bin|sbin|lib|lib64|sys|proc)(\s|/|$)", re.IGNORECASE),
    re.compile(r"\bchmod\s+-R\s+\S+\s+/(\s|$)", re.IGNORECASE),
    # chown to root on system paths
    re.compile(r"\bchown\s+\S*root\S*\s+/(etc|usr|boot|bin|sbin|lib)", re.IGNORECASE),
    # Kernel module manipulation
    re.compile(r"\b(insmod|rmmod)\b", re.IGNORECASE),
    re.compile(r"\bmodprobe\s+-r\b", re.IGNORECASE),
    # LVM / crypto destruction
    re.compile(r"\b(lvremove|vgremove|pvremove)\b", re.IGNORECASE),
    re.compile(r"\bcryptsetup\s+(erase|luksErase|wipeKey|luksRemoveKey)\b", re.IGNORECASE),
    # User/group destruction
    re.compile(r"\b(userdel|groupdel)\b", re.IGNORECASE),
    # Interactive disk partitioning on real devices
    re.compile(r"\b(fdisk|gdisk|cfdisk|sfdisk|parted)\s+/dev/", re.IGNORECASE),
    # Firewall flush/reset
    re.compile(r"\b(iptables|ip6tables|nft)\b\s+(?:.*\s+)?(?:-F|-X|--flush)(?:\s|$)", re.IGNORECASE),
    re.compile(r"\bufw\s+(reset|disable)\b", re.IGNORECASE),
]


def _validate_command(command: str) -> str | None:
    """Return error message if command is destructive, None otherwise.

    Denylist model: only catastrophic patterns (HARD_BLOCKED) are rejected.
    Any other command runs. Approval layer decides user prompting.
    """
    if "\n" in command or "\r" in command:
        return "Comando bloqueado: caracteres de newline não são permitidos"

    for pattern in HARD_BLOCKED:
        if pattern.search(command):
            return "Comando bloqueado por segurança (padrão destrutivo detectado)"

    # Syntactic sanity check per pipe segment
    segments = command.split("|") if "|" in command else [command]
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        try:
            parts = shlex.split(segment)
            if not parts:
                continue
        except ValueError:
            return "Comando malformado"

    return None


# Comandos GUI que devem ser "fire-and-forget" (lançar e não esperar)
_GUI_COMMANDS = frozenset({"xdg-open", "xdg-mime", "notify-send"})


# ─── Tool ───


async def _execute_shell(command: str, cwd: str = None, timeout: int | None = None) -> dict:
    """Execute a shell command with timeout."""
    from ..config import TOOL_TIMEOUTS
    if timeout is None:
        timeout = TOOL_TIMEOUTS.get("shell", 30)
    # Validate command
    block_reason = _validate_command(command)
    if block_reason:
        return {"error": block_reason, "blocked": True}

    # Validate and restrict cwd
    if cwd:
        cwd_path = Path(cwd).expanduser().resolve()
        try:
            cwd_path.relative_to(AGENT_WORKSPACE)
        except ValueError:
            return {"error": f"cwd fora do workspace permitido ({AGENT_WORKSPACE})"}
        cwd = str(cwd_path)
    else:
        cwd = str(AGENT_WORKSPACE)

    # Cap timeout
    timeout = min(timeout, 300)

    try:
        try:
            cmd_parts = shlex.split(command)
        except ValueError as e:
            return {"error": f"Comando malformado: {e}"}

        base_cmd = Path(cmd_parts[0]).name

        # GUI commands: detach (fire-and-forget) — não capturar output
        if base_cmd in _GUI_COMMANDS:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=cwd,
                env=get_safe_env(),
                start_new_session=True,  # desanexar do processo pai
            )
            return {
                "exit_code": 0,
                "stdout": f"Comando '{base_cmd}' lançado em background (PID {proc.pid})",
                "stderr": "",
                "detached": True,
            }

        # Execute pipes safely via chained subprocess_exec (NEVER use subprocess_shell)
        has_pipe = "|" in command
        if has_pipe:
            pipe_segments = [s.strip() for s in command.split("|") if s.strip()]
            prev_output = None
            all_stderr = b""
            last_returncode = 0

            for seg in pipe_segments:
                try:
                    seg_parts = shlex.split(seg)
                except ValueError:
                    return {"error": f"Segmento malformado no pipe: {seg}"}
                if not seg_parts:
                    continue

                proc = await asyncio.create_subprocess_exec(
                    *seg_parts,
                    stdin=asyncio.subprocess.PIPE if prev_output is not None else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=get_safe_env(),
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(input=prev_output), timeout=timeout
                    )
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
                    return {
                        "error": f"Comando excedeu o timeout de {timeout}s",
                        "timeout": True,
                    }
                except (asyncio.CancelledError, KeyboardInterrupt):
                    proc.kill()
                    await proc.wait()
                    raise
                prev_output = stdout
                all_stderr += stderr
                last_returncode = proc.returncode

            return {
                "exit_code": last_returncode,
                "stdout": (prev_output or b"").decode(errors="replace")[:15000],
                "stderr": all_stderr.decode(errors="replace")[:5000],
            }
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=get_safe_env(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "error": f"Comando excedeu o timeout de {timeout}s",
                    "timeout": True,
                }
            except (asyncio.CancelledError, KeyboardInterrupt):
                # Sem este bloco, Ctrl+C deixa o subprocess rodando ate o
                # fim (ex: `git push` ou `npm install` continua exfiltrando
                # apesar do REPL ter "cancelado").
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


register_tool(
    ToolDefinition(
        name="execute_shell",
        description=(
            "Executar um comando shell. Pipes (|) são suportados — cada "
            "segmento e validado contra padrões catastróficos. "
            "Para && / || / ; / redirects (>, 2>) use execute_pipeline. "
            "Timeout máximo: 300s. Retorna stdout, stderr, exit_code."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando shell a executar"},
                "cwd": {
                    "type": "string",
                    "description": "Diretório de trabalho (opcional, deve estar dentro do workspace)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout em segundos (máx 300). Padrão: 30",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="shell",
        executor=_execute_shell,
    )
)
