"""Shell execution tool for ALPHA agent."""

import asyncio
import re
import shlex
from pathlib import Path

from . import ToolDefinition, ToolSafety, register_tool
from .safe_env import get_safe_env
from .workspace import AGENT_WORKSPACE

# ─── Security: Allowlist + Hard Blocks ───

# Commands explicitly allowed for execution
ALLOWED_COMMANDS = frozenset(
    {
        # Filesystem navigation and inspection
        "ls", "cat", "head", "tail", "wc", "find", "file", "stat",
        "du", "df", "tree", "realpath", "basename", "dirname",
        "readlink", "pwd",
        # Text processing
        "grep", "awk", "sed", "sort", "uniq", "cut", "tr", "diff", "jq",
        # Development
        "python3", "python", "pip", "pip3",
        "node", "npm", "npx", "yarn", "pnpm", "bun",
        "git", "make",
        "cargo", "go", "rustc", "gcc", "g++", "javac", "java",
        "mvn", "gradle",
        "ruff", "eslint", "prettier", "mypy", "tsc",
        "pytest", "vitest", "jest",
        # Networking (read-only / recon)
        "curl", "wget", "ping", "nslookup", "dig", "traceroute", "whois",
        "nmap", "netstat", "ss",
        "ip", "route", "arp", "ifconfig", "iwconfig", "nmcli",
        "hostname",
        # Package managers
        "apt", "apt-get", "brew", "dnf", "yum",
        # Archives
        "tar", "zip", "unzip", "gzip", "gunzip",
        # System info
        "uname", "whoami", "id", "groups", "env", "printenv",
        "date", "uptime", "ps", "top", "htop", "free",
        "lscpu", "lsblk", "lspci", "lsusb", "lsmem", "lshw",
        "nproc", "getconf", "blkid", "fdisk", "parted",
        "vmstat", "iostat", "mpstat", "sar",
        "sensors", "inxi", "neofetch", "screenfetch",
        "hostnamectl", "timedatectl", "journalctl", "dmesg",
        "last", "w",
        # Docker
        "docker", "docker-compose",
        # Desktop / multimedia control
        "pactl", "pacmd", "amixer", "wpctl",        # volume / audio
        "playerctl",                                  # media player
        "brightnessctl", "xbacklight",                # brightness
        "xrandr", "wlr-randr",                        # display
        "xdg-open", "xdg-mime",                       # open files/URLs
        "xdotool",
        "xclip", "xsel", "wl-copy", "wl-paste",      # clipboard / input
        "bluetoothctl",                                # bluetooth
        "gsettings", "dconf",                          # desktop settings
        "notify-send",                                 # notifications
        "xset", "setxkbmap",                           # keyboard/display
        # Misc utilities
        "echo", "printf", "test", "true", "false", "yes",
        "tee", "xargs",
        "touch", "mkdir", "cp", "mv", "rm",
        "chmod", "chown", "ln",
        "which", "type", "command",
    }
)

# Patterns that are ALWAYS blocked regardless of allowlist (catastrophic)
# Pre-compiled for performance (called on every execute_shell/execute_pipeline)
HARD_BLOCKED = [
    re.compile(r"\brm\s+.*(-r\b|-R\b|--recursive)", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+.*of=/dev/", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;?\s*:", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"^\s*su\s", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\binit\s+[0-6]\b", re.IGNORECASE),
    re.compile(r">\s*/dev/[sh]d", re.IGNORECASE),
    re.compile(r"\bchmod\s+.*[0-7]{3,4}", re.IGNORECASE),
    re.compile(r"\bchown\s+.*root", re.IGNORECASE),
]


def _validate_command(command: str) -> str | None:
    """Return error message if command is blocked, None if OK.

    Strategy: allowlist of known-safe base commands + hard blocks for catastrophic patterns.
    Supports pipes: each segment is validated independently.
    """
    # Block newline/carriage return injection (shell interprets as command separator)
    if "\n" in command or "\r" in command:
        return "Comando bloqueado: caracteres de newline não são permitidos"

    # Hard blocks first (catastrophic patterns — pre-compiled)
    for pattern in HARD_BLOCKED:
        if pattern.search(command):
            return "Comando bloqueado por segurança (padrão perigoso detectado)"

    # Split by pipe and validate each segment
    segments = command.split("|") if "|" in command else [command]

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        try:
            parts = shlex.split(segment)
            if not parts:
                continue
            base_cmd = Path(parts[0]).name  # /usr/bin/python3 -> python3
        except ValueError:
            return "Comando malformado"

        if base_cmd not in ALLOWED_COMMANDS:
            return (
                f"Comando '{base_cmd}' não está na lista de comandos permitidos. "
                f"Use execute_python para lógica complexa."
            )

    # When sandbox is disabled, restrict package managers that modify the system
    from ..config import FEATURES as ALPHA_FEATURES

    if not ALPHA_FEATURES.get("sandbox_enabled"):
        # Only block system-modifying commands, not dev tools
        _SANDBOX_ONLY_COMMANDS = frozenset({
            "nmap", "apt", "apt-get", "brew", "dnf", "yum",
        })
        try:
            first_cmd = Path(shlex.split(segments[0].strip())[0]).name
        except (ValueError, IndexError):
            first_cmd = ""
        if first_cmd in _SANDBOX_ONLY_COMMANDS:
            return (
                f"Comando '{first_cmd}' requer sandbox habilitado (sandbox_enabled=true). "
                f"Ou use install_package para instalar pacotes."
            )

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
    timeout = min(timeout, 120)

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
        description="Executar um comando no sistema. Operadores shell (|, >, &&, ||) não são suportados — use execute_python com subprocess para pipelines complexos. Retorna stdout, stderr e exit code.",
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
                    "description": "Timeout em segundos (máx 120). Padrão: 30",
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
