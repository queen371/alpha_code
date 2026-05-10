"""Shell execution tool for ALPHA agent."""

import asyncio
import re
import shlex
from pathlib import Path

from .._platform import IS_WINDOWS
from . import ToolDefinition, ToolSafety, register_tool
from ._subprocess_helpers import SubprocessTimeoutError, run_subprocess_safe
from ..config import TOOL_TIMEOUTS
from .safe_env import get_safe_env
from .workspace import AGENT_WORKSPACE, assert_within_workspace

# ─── Security: Hard Blocks (denylist model) ───
#
# Politica real (#D027): denylist only. Qualquer comando que NAO bate com
# `HARD_BLOCKED` abaixo passa pelo approval layer. A `ALLOWED_COMMANDS`
# frozenset que vivia aqui ate 2026-05-07 era codigo morto — nunca era
# consultada por `_validate_command`. Removida pra evitar confusao em
# leitores/auditores que assumiam allowlist enforcement.

# Catastrophic / system-destructive patterns — blocked regardless of approval.
# Lista mantida individualmente para facilitar diff/review; a regex combinada
# logo abaixo (#D020) e o que `_validate_command` consulta em runtime.
_HARD_BLOCKED_PATTERNS = [
    # Recursive file deletion
    r"\brm\s+(?:-\S*[rR]\S*|--recursive\b)",
    # Filesystem formatting / wiping
    r"\bmkfs(?:\.[a-z0-9]+)?\b",
    r"\bmke2fs\b",
    r"\bwipefs\b",
    r"\bshred\b",
    # Raw disk writes
    r"\bdd\s+[^\n]*of=/dev/(sd|nvme|hd|xvd|vd|mmcblk)",
    r">\s*/dev/(sd|nvme|hd|xvd|vd|mmcblk)",
    # Fork bomb (case-sensitive — `:` literal)
    r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;?\s*:",
    # su (sudo is handled via pattern matches below, not blanket-blocked)
    r"(^|[;&|]\s*)su(\s|$)",
    # Power / halt / reboot
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\binit\s+[0-6]\b",
    r"\btelinit\b",
    r"\bsystemctl\s+(poweroff|reboot|halt|kexec|rescue|emergency|suspend|hibernate)\b",
    # Writes to critical system files
    r">\s*/etc/(passwd|shadow|sudoers|fstab|hosts(\s|$))",
    r"\b(tee|dd)\s+[^|;]*\s/etc/(passwd|shadow|sudoers|fstab)",
    r"\bvisudo\b",
    # chmod on critical system dirs
    r"\bchmod\s+\S+\s+/(etc|usr|boot|bin|sbin|lib|lib64|sys|proc)(\s|/|$)",
    r"\bchmod\s+-R\s+\S+\s+/(\s|$)",
    # chown to root on system paths
    r"\bchown\s+\S*root\S*\s+/(etc|usr|boot|bin|sbin|lib)",
    # Kernel module manipulation
    r"\b(insmod|rmmod)\b",
    r"\bmodprobe\s+-r\b",
    # LVM / crypto destruction
    r"\b(lvremove|vgremove|pvremove)\b",
    r"\bcryptsetup\s+(erase|luksErase|wipeKey|luksRemoveKey)\b",
    # User/group destruction
    r"\b(userdel|groupdel)\b",
    # Interactive disk partitioning on real devices
    r"\b(fdisk|gdisk|cfdisk|sfdisk|parted)\s+/dev/",
    # Firewall flush/reset
    r"\b(iptables|ip6tables|nft)\b\s+(?:.*\s+)?(?:-F|-X|--flush)(?:\s|$)",
    r"\bufw\s+(reset|disable)\b",
    # find with -fprint/-fprintf writes output to arbitrary files (sandbox escape)
    r"\bfind\s+.*-fprintf?\s",
    # ─── Windows destructive patterns (defesa em profundidade) ───
    # HARD_BLOCKED_RE ja compila com re.IGNORECASE — flags inline `(?i)`
    # invalidariam a alternation, entao escrevemos lower.
    r"\b(?:rmdir|rd)\s+(?:/s\b|/q\s+/s\b|/s\s+/q\b)",
    r"\bdel\s+(?:/[fsq]\s*)*?/s\b",
    r"\bRemove-Item\b[^\n]*-Recurse",
    # Disk format / partition
    r"\bformat\s+[a-z]:\s",
    r"\bdiskpart\b",
    # Power / shutdown (Windows)
    r"\bshutdown\s+/[rstpgha]",
    r"\b(Stop-Computer|Restart-Computer)\b",
    # Registry destruction
    r"\breg\s+delete\b",
    r"\bRemove-ItemProperty\b",
    # User/account destruction
    r"\bnet\s+user\s+\S+\s+/delete",
    r"\bRemove-LocalUser\b",
]

# #D020: 27 regex viraram uma alternation unica. Antes `_validate_command`
# fazia 27 chamadas `pattern.search(command)` (~3-5ms total por call,
# cumulativo em sessoes ativas com varios shell calls). Agora 1 chamada.
HARD_BLOCKED_RE = re.compile(
    "|".join(f"(?:{p})" for p in _HARD_BLOCKED_PATTERNS), re.IGNORECASE
)

# Backwards compat: codigo externo que importava `HARD_BLOCKED` (e.g.
# pipeline_tools) ainda funciona — exposto como wrapper iteravel da
# regex combinada para nao quebrar contratos. Cada elemento ainda e
# uma re.Pattern com `.search()`.
HARD_BLOCKED = [re.compile(p, re.IGNORECASE) for p in _HARD_BLOCKED_PATTERNS]


def _validate_command(command: str) -> str | None:
    """Return error message if command is destructive, None otherwise.

    Denylist model: only catastrophic patterns (HARD_BLOCKED_RE) are rejected.
    Any other command runs. Approval layer decides user prompting.
    """
    if "\n" in command or "\r" in command:
        return "Comando bloqueado: caracteres de newline não são permitidos"

    if HARD_BLOCKED_RE.search(command):
        return "Comando bloqueado por segurança (padrão destrutivo detectado)"

    # No Windows, cmd.exe parseia o comando — shlex POSIX quebra paths
    # com backslash e nao reflete a sintaxe de cmd. Newline + HARD_BLOCKED
    # ja foram checados acima; nao temos mais o que validar aqui.
    if IS_WINDOWS:
        return None

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


async def _execute_shell_windows(command: str, cwd: str, timeout: int) -> dict:
    """Execute via cmd.exe /c — necessario pra builtins (`dir`, `type`,
    `echo`), pipes e redirects, que `subprocess_exec` direto nao parseia.
    Injection vem so da string do comando, ja validada via HARD_BLOCKED_RE.
    """
    try:
        r = await run_subprocess_safe(
            "cmd.exe", "/c", command, timeout=timeout, cwd=cwd,
        )
    except SubprocessTimeoutError:
        return {
            "error": f"Comando excedeu o timeout de {timeout}s",
            "timeout": True,
        }
    return {
        "exit_code": r.returncode,
        "stdout": r.stdout.decode(errors="replace")[:15000],
        "stderr": r.stderr.decode(errors="replace")[:5000],
    }


async def _execute_shell(command: str, cwd: str = None, timeout: int | None = None) -> dict:
    """Execute a shell command with timeout."""
    if timeout is None:
        timeout = TOOL_TIMEOUTS.get("shell", 30)
    # Validate command
    block_reason = _validate_command(command)
    if block_reason:
        return {"error": block_reason, "blocked": True}

    # Validate and restrict cwd
    if cwd:
        cwd_path = Path(cwd).expanduser().resolve()
        err = assert_within_workspace(cwd_path)
        if err:
            return {"error": err}
        cwd = str(cwd_path)
    else:
        cwd = str(AGENT_WORKSPACE)

    # Cap timeout (#D003: fonte unica em config.TOOL_TIMEOUT_CAPS)
    from ..config import TOOL_TIMEOUT_CAPS
    timeout = min(timeout, TOOL_TIMEOUT_CAPS.get("shell", 300))

    try:
        if IS_WINDOWS:
            return await _execute_shell_windows(command, cwd, timeout)

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

                try:
                    r = await run_subprocess_safe(
                        *seg_parts, timeout=timeout, cwd=cwd,
                        stdin=prev_output,
                    )
                except SubprocessTimeoutError:
                    return {
                        "error": f"Comando excedeu o timeout de {timeout}s",
                        "timeout": True,
                    }
                prev_output = r.stdout
                all_stderr += r.stderr
                last_returncode = r.returncode

            return {
                "exit_code": last_returncode,
                "stdout": (prev_output or b"").decode(errors="replace")[:15000],
                "stderr": all_stderr.decode(errors="replace")[:5000],
            }
        else:
            try:
                r = await run_subprocess_safe(
                    *cmd_parts, timeout=timeout, cwd=cwd,
                )
            except SubprocessTimeoutError:
                return {
                    "error": f"Comando excedeu o timeout de {timeout}s",
                    "timeout": True,
                }

            return {
                "exit_code": r.returncode,
                "stdout": r.stdout.decode(errors="replace")[:15000],
                "stderr": r.stderr.decode(errors="replace")[:5000],
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
