"""
Auto-approval logic for Alpha Code.

Determines which tool calls are safe to auto-execute and which need user approval.
Extracted from CORA34's approval_logic.py with security fixes (V-001).

User-defined `allow` / `deny` rules from `.alpha/settings.json` override the
built-in defaults — see `_load_permission_rules` for the schema.
"""

import logging
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ._platform import IS_WINDOWS
from .settings import find_config_file, read_json

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
        "todo_write",
        # Browser read-only / navigation
        "browser_open",
        "browser_close",
        "browser_status",
        "browser_navigate",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "browser_get_content",
        "browser_screenshot",
        "browser_describe_page",
        "browser_query",
        "browser_wait_for",
        "browser_list_tabs",
        "browser_new_tab",
        "browser_switch_tab",
        "browser_close_tab",
    }
)

REQUIRE_APPROVAL_TOOLS = frozenset({"install_package", "docker_run", "delegate_task", "delegate_parallel"})

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
        # Networking info (read-only) — #083: dedupe
        "ip", "ss", "netstat", "route", "arp", "iwconfig", "ifconfig",
        "ping", "nslookup", "dig",
        "hostname", "nmcli", "traceroute", "tracepath", "mtr", "host", "whois",
        # Filesystem read-only
        "ls", "cat", "head", "tail", "wc", "find", "file", "stat",
        "tree", "grep", "sort", "uniq", "diff", "pwd",
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
        # `touch` / `mkdir` create empty artefacts — low risk, kept here.
        # `cp` / `mv` were removed because both can move data ACROSS the
        # workspace boundary without prompt (e.g. `cp /etc/passwd /tmp/x`):
        # auto-approval does not validate the *source* path. A user who
        # actually relies on cp/mv should add an explicit allow rule in
        # `.alpha/settings.json` — see the example file. (#audit-cp-exfil)
        "touch", "mkdir", "basename", "dirname",
        "realpath", "readlink",
    }
)

# Windows allowlist — consultada so quando IS_WINDOWS. `del`/`rmdir`/`move`/
# `copy` ficam DE FORA pelo mesmo motivo de `rm`/`mv`/`cp` no Unix:
# atravessam o workspace sem validacao de source.
SAFE_SHELL_COMMANDS_WIN = frozenset(
    {
        # cmd.exe builtins read-only / info
        "dir", "type", "where", "findstr", "find", "more", "tree",
        "echo", "ver", "vol", "set", "path", "title", "cd", "pwd",
        "hostname", "whoami", "tasklist", "systeminfo", "time", "date",
        "fc", "comp",
        # PowerShell cmdlets read-only / info
        "Get-ChildItem", "Get-Content", "Get-Item", "Get-Location",
        "Get-Process", "Get-Service", "Get-Date", "Get-Host",
        "Get-Command", "Get-Help", "Get-Member", "Get-Module",
        "Get-Variable", "Get-PSDrive", "Get-ComputerInfo",
        "Select-String", "Select-Object", "Where-Object", "ForEach-Object",
        "Measure-Object", "Sort-Object", "Group-Object", "Format-Table",
        "Format-List", "Out-String", "Out-Default",
        "Test-Path", "Resolve-Path", "Split-Path", "Join-Path",
        "Convert-Path", "ConvertTo-Json", "ConvertFrom-Json",
        # Networking info
        "ipconfig", "ping", "nslookup", "tracert", "pathping", "netstat",
        "Test-Connection", "Test-NetConnection",
        # Dev tools (mesmos do Unix — sao executaveis cross-platform)
        "python", "python3", "py", "node", "npm", "npx", "yarn", "pnpm",
        "bun", "pip", "pip3", "pytest", "vitest", "jest",
        "ruff", "eslint", "prettier", "mypy", "tsc",
        "cargo", "go", "rustc", "javac", "java", "mvn", "gradle",
        # Version control
        "git",
        # NOTA: `cmd`, `powershell`, `pwsh` ficam DE FORA — mesmo motivo
        # de `bash -c` exigir aprovacao no Unix. shell_tools.py ja envolve
        # comandos com `cmd /c` por baixo dos panos.
    }
)


# Case-insensitive lookup pra Windows: cmdlets e builtins do cmd nao
# distinguem caixa (`Get-ChildItem` vs `get-childitem` vs `DIR`).
_SAFE_SHELL_COMMANDS_WIN_LOWER = frozenset(c.lower() for c in SAFE_SHELL_COMMANDS_WIN)

# Dangerous operators (subshells, redirection, variable expansion)
# Pipes (|) are allowed if all commands in the pipeline are safe
_DANGEROUS_OPS = re.compile(r"[;&`<>\n\r]|\$\(|&&|\|\||\$\{")

# Dangerous args per command (exfiltration / destructive writes)
# V-001 FIX: validate individual args (not joined string)
_DANGEROUS_ARGS = {
    "curl": re.compile(r"^-[dXoT]|^--data|^--output|^--upload-file|^--upload", re.I),
    "wget": re.compile(r"^-O|^--output-document|^--post-data|^--post-file", re.I),
    "find": re.compile(r"^-delete$|^-execdir$", re.I),
    "nc": re.compile(r"^-[ec]|^--exec", re.I),
    "ncat": re.compile(r"^-[ec]|^--exec", re.I),
}

# Commands safe to use after find -exec (read-only / counting)
_SAFE_EXEC_COMMANDS = frozenset({
    "wc", "cat", "head", "tail", "grep", "file", "stat", "basename",
    "dirname", "md5sum", "sha256sum", "sort", "uniq", "du",
})

# Interpretadores que aceitam codigo inline via flag — usar essas flags
# bypassa o sandbox do execute_python. `python -c "..."` e `node -e "..."`
# auto-aprovados eram um vetor de RCE silenciosa.
_INTERPRETER_EVAL_FLAGS = {
    "python":  frozenset({"-c"}),
    "python3": frozenset({"-c"}),
    "node":    frozenset({"-e", "--eval", "-p", "--print"}),
    "perl":    frozenset({"-e", "-E"}),
    "ruby":    frozenset({"-e"}),
    "bash":    frozenset({"-c"}),
    "sh":      frozenset({"-c"}),
    "zsh":     frozenset({"-c"}),
    "deno":    frozenset({"eval"}),
    # Windows shells — qualquer flag de eval inline exige aprovacao
    # humana, mesma logica de `bash -c` no Unix.
    "cmd":        frozenset({"/c", "/C", "/k", "/K"}),
    "powershell": frozenset({"-c", "-C", "-Command", "-command",
                             "-EncodedCommand", "-encodedcommand"}),
    "pwsh":       frozenset({"-c", "-C", "-Command", "-command",
                             "-EncodedCommand", "-encodedcommand"}),
}

# Git actions considered read-only (safe for auto-approval)
_SAFE_GIT_ACTIONS = frozenset(
    {
        "status", "diff", "log", "branch", "show",
        "blame", "stash_list", "remote", "tag",
    }
)

# Git write actions auto-approved (non-destructive AND no data-loss risk).
# DEEP_SECURITY V3.0 #D116: `checkout`, `stash`, `pull`, `merge`, `rebase`
# foram REMOVIDOS desta lista — todos podem destruir trabalho local sem
# prompt:
#   - checkout: `git checkout file.txt` overwrite uncommitted changes
#   - stash: `git stash` (sem -k) move working dir state, sumindo do view
#     ate `stash pop`; pode dar a sensacao de perda de dados
#   - pull: `git pull --rebase` re-escreve commits locais em conflito
#   - merge/rebase: idem
# Ficam aqui so as acoes idempotentes ou aditivas (add, commit, fetch).
_AUTO_GIT_ACTIONS = frozenset({"add", "commit", "fetch"})


def _is_find_exec_safe(parts: list[str]) -> bool:
    """Check if a find command with -exec uses only safe commands.

    Allows: find ... -exec wc -l {} +
    Blocks: find ... -exec rm {} ;
    """
    i = 0
    while i < len(parts):
        if parts[i] == "-exec":
            # Next token after -exec is the command to execute
            if i + 1 >= len(parts):
                return False
            exec_cmd = Path(parts[i + 1]).name
            if exec_cmd not in _SAFE_EXEC_COMMANDS:
                return False
            # Skip past the -exec ... ; or -exec ... +
            i += 2
            while i < len(parts) and parts[i] not in (";", "+"):
                i += 1
        i += 1
    return True


def _is_single_command_safe(cmd_str: str) -> bool:
    """Check if a single command (no pipes) is safe."""
    cmd_str = cmd_str.strip()
    if not cmd_str:
        return False

    # Block dangerous operators within the single command
    if _DANGEROUS_OPS.search(cmd_str):
        return False

    try:
        if IS_WINDOWS:
            parts = cmd_str.split()
        else:
            parts = shlex.split(cmd_str)
        if not parts:
            return False

        if False:
            # `Path.stem` strips a extensao (.exe/.cmd/.bat/.ps1) e .lower()
            # bate com `_SAFE_SHELL_COMMANDS_WIN_LOWER` — cmdlets/builtins
            # do Windows sao case-insensitive.
            base_cmd = Path(parts[0]).stem.lower()
            if base_cmd not in _SAFE_SHELL_COMMANDS_WIN_LOWER:
                return False
        else:
            base_cmd = Path(parts[0]).name
            if base_cmd not in SAFE_SHELL_COMMANDS:
                return False

        # Check per-command dangerous args (V-001: check each arg individually)
        if base_cmd in _DANGEROUS_ARGS:
            pattern = _DANGEROUS_ARGS[base_cmd]
            for arg in parts[1:]:
                if pattern.search(arg):
                    return False

        # Interpretador com flag de eval inline (python -c, node -e, etc.):
        # exigir aprovacao humana — caso contrario o sandbox de execute_python
        # e dribavel via execute_shell.
        if base_cmd in _INTERPRETER_EVAL_FLAGS:
            eval_flags = _INTERPRETER_EVAL_FLAGS[base_cmd]
            if any(arg in eval_flags for arg in parts[1:]):
                return False

        # Special handling: find -exec with safe commands is OK
        if base_cmd == "find" and "-exec" in parts:
            if not _is_find_exec_safe(parts):
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


def _is_safe_pipeline(pipeline: str) -> bool:
    """
    Check if a pipeline string (with &&, ||, ;, |) is safe for auto-approval.

    Unlike is_safe_shell_command (for execute_shell), this allows logical
    operators (&&, ||, ;) as long as every individual command is safe.
    Still blocks dangerous operators like backticks, $(), redirects.
    """
    # Block shell expansion / injection vectors (but NOT &&, ||, ;)
    _PIPELINE_DANGEROUS = re.compile(r"[`<>]|\$\(|\$\{|\n|\r")
    if _PIPELINE_DANGEROUS.search(pipeline):
        return False

    # Split by logical operators and pipes, validate each command
    segments = re.split(r"\s*(?:&&|\|\||;|\|)\s*", pipeline)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # Strip redirects for validation
        cmd_part = re.split(r"\s*(?:>>?|2>>?|<)\s*", seg)[0].strip()
        if not cmd_part:
            continue
        if not _is_single_command_safe(cmd_part):
            return False
    return True


# ─── User-defined permission rules (from .alpha/settings.json) ───

# Pattern syntax:
#   "tool"                — match by tool name only (any args)
#   "tool(literal)"       — primary arg equals "literal"
#   "tool:regex"          — primary arg matches regex (search, not anchored)
_RULE_PARSE = re.compile(r"^([a-zA-Z_][\w]*)(?:\(([^)]*)\)|:(.+))?$")

# Per-tool primary arg name (used to match args against rule patterns).
# Falls back to the first string value if the tool isn't listed here.
_PRIMARY_ARG = {
    "execute_shell": "command",
    "execute_pipeline": "pipeline",
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "list_directory": "path",
    "search_files": "pattern",
    "glob_files": "pattern",
    "http_request": "url",
    "git_operation": "action",
    "query_database": "query",
    "search_and_replace": "path",
}


@dataclass
class PermissionRule:
    raw: str
    tool: str
    literal: str | None = None
    pattern: re.Pattern | None = None

    def matches(self, tool_name: str, args: dict) -> bool:
        if self.tool != tool_name:
            return False
        if self.literal is None and self.pattern is None:
            return True  # tool-name-only rule
        primary = _primary_arg_value(tool_name, args)
        if primary is None:
            return False
        if self.literal is not None:
            return primary == self.literal
        return self.pattern.search(primary) is not None


def _primary_arg_value(tool_name: str, args: dict) -> str | None:
    if not isinstance(args, dict):
        return None
    key = _PRIMARY_ARG.get(tool_name)
    if key and key in args:
        val = args[key]
        return str(val) if val is not None else None
    for v in args.values():
        if isinstance(v, str):
            return v
    return None


def _parse_rule(raw: str) -> PermissionRule | None:
    raw = raw.strip()
    if not raw:
        return None
    m = _RULE_PARSE.match(raw)
    if not m:
        logger.warning("Invalid permission rule '%s' (skipped)", raw)
        return None
    tool, literal, pattern = m.group(1), m.group(2), m.group(3)
    compiled = None
    if pattern is not None:
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            logger.warning("Invalid regex in rule '%s': %s (skipped)", raw, e)
            return None
    return PermissionRule(raw=raw, tool=tool, literal=literal, pattern=compiled)


_rules_cached: str | None = None  # resolved settings path currently cached
_allow_rules: list[PermissionRule] = []
_deny_rules: list[PermissionRule] = []


def _load_permission_rules() -> tuple[list[PermissionRule], list[PermissionRule]]:
    """Read .alpha/settings.json's `permissions` block.

    Cache keyed by the resolved settings path — re-loads when CWD changes
    and a different settings.json is discovered (DL029: stale cache across
    agent scopes).

    Schema:
    ```json
    {
      "permissions": {
        "allow": ["read_file", "execute_shell:^npm "],
        "deny":  ["execute_shell(rm -rf /)", "execute_shell:sudo"]
      }
    }
    ```
    """
    global _rules_cached, _allow_rules, _deny_rules

    settings_path = find_config_file("settings.json")
    if _rules_cached == str(settings_path):
        return _allow_rules, _deny_rules

    raw = read_json(settings_path, default={})
    perms = raw.get("permissions") if isinstance(raw, dict) else None
    if not isinstance(perms, dict):
        _rules_cached = str(settings_path)
        _allow_rules, _deny_rules = [], []
        return [], []

    allow = [r for r in (_parse_rule(s) for s in perms.get("allow") or []) if r]
    deny = [r for r in (_parse_rule(s) for s in perms.get("deny") or []) if r]
    _allow_rules, _deny_rules = allow, deny
    _rules_cached = str(settings_path)
    if allow or deny:
        logger.info(
            "Loaded %d allow / %d deny permission rule(s) from %s",
            len(allow), len(deny), settings_path,
        )
    return allow, deny


def reset_permission_cache() -> None:
    """Force a re-read of permission rules. For tests."""
    global _rules_cached, _allow_rules, _deny_rules
    _rules_cached = None
    _allow_rules = []
    _deny_rules = []


def is_denied(tool_name: str, args: dict) -> tuple[bool, str]:
    """Check if a deny rule matches. Denied tools never prompt and never run."""
    _, deny = _load_permission_rules()
    for rule in deny:
        if rule.matches(tool_name, args):
            return True, f"Denied by permission rule: {rule.raw}"
    return False, ""


def _matches_allow(tool_name: str, args: dict) -> bool:
    allow, _ = _load_permission_rules()
    return any(rule.matches(tool_name, args) for rule in allow)


def needs_approval(tool_name: str, args: dict) -> bool:
    """
    Determine if a tool call needs user approval.

    Resolution order:
      1. User `allow` rule → False (auto-approve).
      2. Built-in defaults below.

    Deny rules are enforced upstream by the executor via `is_denied`; by the
    time we reach this function, denied calls have already been short-circuited.
    """
    if _matches_allow(tool_name, args):
        return False

    if tool_name in AUTO_APPROVE_TOOLS:
        if tool_name == "write_file" and not args.get("content", "").strip():
            return True
        return False

    if tool_name in REQUIRE_APPROVAL_TOOLS:
        return True

    # execute_shell: auto-approve safe commands (no &&, ||, ;)
    if tool_name == "execute_shell":
        command = args.get("command", "")
        if is_safe_shell_command(command):
            logger.info(f"Auto-approve safe shell: {command[:80]}")
            return False
        return True

    # execute_pipeline: auto-approve if all commands are safe (allows &&, ||, ;)
    if tool_name == "execute_pipeline":
        pipeline = args.get("pipeline", "")
        if _is_safe_pipeline(pipeline):
            logger.info(f"Auto-approve safe pipeline: {pipeline[:80]}")
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
