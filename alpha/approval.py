"""
Auto-approval logic for Alpha Code.

Determines which tool calls are safe to auto-execute and which need user approval.
Extracted from CORA34's approval_logic.py with security fixes (V-001).
"""

import logging
import re
import shlex
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Auto-approval tiers ───

AUTO_APPROVE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "read_file",
        "list_directory",
        "search_files",
        "glob_files",
        "execute_python",
        "search_and_replace",
        "project_overview",
        "run_tests",
        "web_search",
    }
)

REQUIRE_APPROVAL_TOOLS = frozenset({"install_package", "docker_run"})

# Shell commands safe for auto-approval (desktop control + read-only + dev tools)
SAFE_SHELL_COMMANDS = frozenset(
    {
        # Audio / volume
        "pactl", "pacmd", "amixer", "wpctl", "playerctl",
        # Display / brightness
        "brightnessctl", "xbacklight", "xrandr", "wlr-randr",
        # Open files / URLs
        "xdg-open", "xdg-mime",
        # Clipboard
        "xclip", "xsel", "wl-copy", "wl-paste",
        # Notifications
        "notify-send",
        # Desktop settings (read-only)
        "gsettings", "dconf",
        # System info (read-only)
        "uname", "whoami", "date", "uptime", "free", "lscpu", "lsblk",
        "lspci", "lsusb", "lsmem", "ps", "df", "du", "top", "htop",
        "vmstat", "iostat", "mpstat", "sar", "sensors", "inxi",
        "neofetch", "screenfetch", "hostnamectl", "timedatectl",
        "journalctl", "dmesg", "id", "groups", "last", "w", "nproc",
        "getconf", "lshw", "blkid", "fdisk", "parted",
        "ip", "ss", "netstat", "route", "arp", "iwconfig", "ifconfig",
        # Networking info (read-only)
        "ping", "nslookup", "dig",
        # Filesystem read-only
        "ls", "cat", "head", "tail", "wc", "find", "file", "stat",
        "tree", "grep", "sort", "uniq", "diff",
        # Dev tools — build, test, lint, format
        "python", "python3", "node", "npm", "npx", "yarn", "pnpm", "bun",
        "pip", "pip3", "pytest", "vitest", "jest",
        "ruff", "eslint", "prettier", "mypy", "tsc",
        "make", "cargo", "go", "rustc", "gcc", "g++", "javac", "java",
        "mvn", "gradle",
        # Version control (read + safe writes)
        "git",
        # Environment
        "env", "printenv", "which", "type", "echo", "printf",
        "touch", "mkdir", "cp", "mv", "basename", "dirname",
        "realpath", "readlink",
    }
)

# Dangerous operators (subshells, redirection, variable expansion)
# Pipes (|) are allowed if all commands in the pipeline are safe
_DANGEROUS_OPS = re.compile(r"[;&`<>]|\$\(|&&|\|\||\$\{")

# Dangerous args per command (exfiltration / destructive writes)
# V-001 FIX: validate individual args (not joined string)
_DANGEROUS_ARGS = {
    "curl": re.compile(r"^-[dXoT]|^--data|^--output|^--upload-file|^--upload", re.I),
    "wget": re.compile(r"^-O|^--output-document|^--post-data|^--post-file", re.I),
    "find": re.compile(r"^-exec$|^-delete$|^-execdir$", re.I),
    "nc": re.compile(r"^-[ec]|^--exec", re.I),
    "ncat": re.compile(r"^-[ec]|^--exec", re.I),
}

# Git actions considered read-only (safe for auto-approval)
_SAFE_GIT_ACTIONS = frozenset(
    {
        "status", "diff", "log", "branch", "show",
        "blame", "stash_list", "remote", "tag",
    }
)

# Git write actions auto-approved (non-destructive)
_AUTO_GIT_ACTIONS = frozenset(
    {"add", "commit", "checkout", "stash", "pull", "fetch"}
)


def _is_single_command_safe(cmd_str: str) -> bool:
    """Check if a single command (no pipes) is safe."""
    cmd_str = cmd_str.strip()
    if not cmd_str:
        return False

    # Block dangerous operators within the single command
    if _DANGEROUS_OPS.search(cmd_str):
        return False

    try:
        parts = shlex.split(cmd_str)
        if not parts:
            return False

        base_cmd = Path(parts[0]).name
        if base_cmd not in SAFE_SHELL_COMMANDS:
            return False

        # Check per-command dangerous args (V-001: check each arg individually)
        if base_cmd in _DANGEROUS_ARGS:
            pattern = _DANGEROUS_ARGS[base_cmd]
            for arg in parts[1:]:
                if pattern.search(arg):
                    return False

        return True
    except ValueError:
        return False


def is_safe_shell_command(command: str) -> bool:
    """
    Check if a shell command is safe for auto-approval.

    Rules:
    1. REJECT any command with dangerous operators (semicolons, backticks, redirects, subshells, etc.)
    2. Allow pipes (|) if ALL commands in the pipeline are safe
    3. REJECT if the base command is not in the allowlist
    4. REJECT if the command has known dangerous args
    """
    # Check for dangerous operators (excluding pipe)
    if _DANGEROUS_OPS.search(command):
        return False

    # Split by pipe and check each segment
    if "|" in command:
        segments = command.split("|")
        return all(_is_single_command_safe(seg) for seg in segments)

    return _is_single_command_safe(command)


def needs_approval(tool_name: str, args: dict) -> bool:
    """
    Determine if a tool call needs user approval.

    Auto-approves safe tools and read-only operations.
    Requires approval for destructive actions.
    """
    if tool_name in AUTO_APPROVE_TOOLS:
        if tool_name == "write_file" and not args.get("content", "").strip():
            return True
        return False

    if tool_name in REQUIRE_APPROVAL_TOOLS:
        return True

    # execute_shell / execute_pipeline: auto-approve safe commands
    if tool_name in ("execute_shell", "execute_pipeline"):
        command = args.get("command", args.get("pipeline", ""))
        if is_safe_shell_command(command):
            logger.info(f"Auto-approve safe shell: {command[:80]}")
            return False
        return True

    # git_operation: read-only auto-approved, write needs approval
    if tool_name == "git_operation":
        action = args.get("action", "")
        if action in _SAFE_GIT_ACTIONS:
            return False
        if action in _AUTO_GIT_ACTIONS:
            return False
        return True

    # http_request: GET/HEAD/OPTIONS auto-approved
    if tool_name == "http_request":
        method = args.get("method", "GET").upper()
        if method in ("GET", "HEAD", "OPTIONS"):
            return False
        return True

    # query_database: read_only auto-approved
    if tool_name == "query_database":
        if args.get("read_only", True):
            return False
        return True

    # Default: require approval for unknown tools
    return True
