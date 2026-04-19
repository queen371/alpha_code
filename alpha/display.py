"""
Terminal display helpers for Alpha Code.

Kali Linux-inspired color scheme with priority-based visual indicators.
Green/red dominant palette, safety-aware tool display, hacker aesthetic.
"""

import json
import os
import sys
import time


# ─── ANSI Colors (Kali Linux palette) ───


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    # Core palette (Kali-inspired: green dominant, red for danger)
    GREEN = "\033[38;5;82m"       # Bright green (Kali signature)
    GREEN_DARK = "\033[38;5;34m"  # Dark green (secondary)
    RED = "\033[38;5;196m"        # Bright red (errors/critical)
    RED_DARK = "\033[38;5;124m"   # Dark red (warnings)
    YELLOW = "\033[38;5;220m"     # Yellow (caution/approval)
    ORANGE = "\033[38;5;208m"     # Orange (medium priority)
    BLUE = "\033[38;5;33m"        # Blue (info)
    CYAN = "\033[38;5;51m"        # Cyan (tool names)
    MAGENTA = "\033[38;5;135m"    # Purple (sub-agents)
    WHITE = "\033[38;5;255m"      # Bright white
    GRAY = "\033[38;5;245m"       # Medium gray
    GRAY_DARK = "\033[38;5;238m"  # Dark gray (borders)

    # Backgrounds
    BG_RED = "\033[48;5;52m"      # Dark red background
    BG_GREEN = "\033[48;5;22m"    # Dark green background
    BG_YELLOW = "\033[48;5;58m"   # Dark yellow background
    BG_GRAY = "\033[48;5;236m"    # Dark gray background


def supports_color() -> bool:
    """Check if the terminal supports ANSI color codes."""
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


NO_COLOR = not supports_color()


def c(color: str, text: str) -> str:
    """Wrap text in ANSI color codes. Returns plain text if color is unsupported."""
    if NO_COLOR:
        return text
    return f"{color}{text}{C.RESET}"


# ─── Safety color mapping ───

_SAFETY_COLORS = {
    "safe": C.GREEN,
    "destructive": C.RED,
    "unknown": C.YELLOW,
}

_SAFETY_ICONS = {
    "safe": "✦",
    "destructive": "⚠",
    "unknown": "?",
}

# Category icons for /tools display
_CATEGORY_ICONS = {
    "file": "📁",
    "shell": "🖥",
    "code": "⟨⟩",
    "git": "⎇ ",
    "network": "🌐",
    "search": "🔍",
    "database": "🗄",
    "system": "⚙ ",
    "agent": "🤖",
    "pipeline": "⛓ ",
    "general": "◆ ",
}


# ─── Display functions ───


def print_tool_call(name: str, args: dict, safety: str = "safe") -> None:
    """Display a tool call with safety-colored indicator."""
    args_str = ""
    if isinstance(args, dict):
        for key in ("path", "command", "query", "action", "pattern", "file", "code"):
            if key in args:
                val = str(args[key])
                if len(val) > 120:
                    val = val[:117] + "..."
                args_str = f" {c(C.GRAY, val)}"
                break
        if not args_str and args:
            first_val = str(next(iter(args.values())))
            if len(first_val) > 120:
                first_val = first_val[:117] + "..."
            args_str = f" {c(C.GRAY, first_val)}"

    safety_color = _SAFETY_COLORS.get(safety, C.YELLOW)
    icon = c(safety_color, _SAFETY_ICONS.get(safety, "⚡"))
    tool_name = c(C.CYAN + C.BOLD, name) if safety == "safe" else c(safety_color + C.BOLD, name)

    print(f"  {icon} {tool_name}{args_str}")


def print_tool_result(name: str, result: dict) -> None:
    """Display a tool result with status-aware formatting."""
    border = c(C.GRAY_DARK, "│")

    if isinstance(result, dict):
        # Error results in red
        if result.get("error"):
            print(f"  {c(C.RED, '✗')} {c(C.RED, str(result['error'])[:200])}")
            return

        # Skipped/denied results
        if result.get("skipped"):
            reason = result.get("reason", "denied")
            print(f"  {c(C.YELLOW, '⊘')} {c(C.YELLOW, reason[:200])}")
            return

        # Show output/content preview
        output = result.get("output") or result.get("content") or result.get("result")
        if isinstance(output, str) and output.strip():
            lines = output.strip().split("\n")
            max_lines = 8
            for line in lines[:max_lines]:
                print(f"  {border} {line[:200]}")
            if len(lines) > max_lines:
                remaining = len(lines) - max_lines
                print(f"  {border} {c(C.GRAY, f'... ({remaining} more lines)')}")
        else:
            # Show as compact JSON
            preview = json.dumps(result, ensure_ascii=False, default=str)
            if len(preview) > 200:
                preview = preview[:197] + "..."
            print(f"  {border} {c(C.GRAY, preview)}")
    else:
        result_str = str(result)[:200]
        print(f"  {border} {result_str}")


# Session-level approval state
_approve_all: bool = False


def reset_approve_all() -> None:
    """Reset the approve-all state (call on /clear or new session)."""
    global _approve_all
    _approve_all = False


def print_approval_request(tool_name: str, args: dict) -> bool:
    """Show approval request with Kali-style danger indication.

    Returns True if approved. Supports:
    - s/y: approve this action
    - n: deny this action
    - a: approve ALL actions for the rest of this session
    """
    global _approve_all

    # If user previously chose "approve all", auto-approve
    if _approve_all:
        print(f"  {c(C.GREEN, '✦')} {c(C.CYAN, tool_name)} {c(C.GREEN_DARK, '(auto-approved)')}")
        return True

    print()
    print(f"  {c(C.RED + C.BOLD, '┌─ APPROVAL NEEDED ─────────────────────')}")
    print(f"  {c(C.RED, '│')} Tool: {c(C.CYAN + C.BOLD, tool_name)}")
    if isinstance(args, dict):
        for k, v in args.items():
            val_str = str(v)
            if len(val_str) > 100:
                val_str = val_str[:97] + "..."
            print(f"  {c(C.RED, '│')} {c(C.GRAY, k)}: {val_str}")
    print(f"  {c(C.RED + C.BOLD, '└────────────────────────────────────────')}")

    try:
        while True:
            resp = input(
                f"\n  {c(C.YELLOW + C.BOLD, 'Aprovar? [s/n/a(ll)]:')} "
            ).strip().lower()
            if resp in ("s", "sim", "y", "yes"):
                print(f"  {c(C.GREEN, '✓ Aprovado')}")
                return True
            if resp in ("n", "não", "nao", "no"):
                print(f"  {c(C.RED, '✗ Negado')}")
                return False
            if resp in ("a", "all", "todos"):
                _approve_all = True
                print(f"  {c(C.GREEN + C.BOLD, '✓ Aprovado (all para esta sessão)')}")
                return True
    except EOFError:
        print(f"  {c(C.GRAY, '(auto-denied — sem terminal interativo)')}")
        return False


def print_phase(detail: str) -> None:
    """Display a phase/progress update."""
    print(f"  {c(C.GREEN_DARK, '→')} {c(C.DIM, detail)}")


def print_error(message: str) -> None:
    """Display an error message in red with border."""
    print(f"\n  {c(C.RED + C.BOLD, '✗ Error:')} {c(C.RED, message)}")


def print_context_compressed(before: int, after: int) -> None:
    """Display context compression event with stats."""
    saved = before - after
    pct = (saved / before * 100) if before > 0 else 0
    print(
        f"  {c(C.BLUE, '⟳')} {c(C.DIM, 'Context compressed:')} "
        f"{c(C.GRAY, str(before))} → {c(C.GREEN, str(after))} tokens "
        f"{c(C.GREEN_DARK, f'(-{pct:.0f}%)')}"
    )


def print_subagent_event(event: dict, agent_label: str = "") -> None:
    """Display a sub-agent event with indentation."""
    prefix = f"  {c(C.GRAY_DARK, '┊')}"
    label = f" {c(C.MAGENTA + C.BOLD, agent_label)}" if agent_label else ""

    event_type = event.get("type", "")
    if event_type == "tool_call":
        name = event.get("name", "")
        args = event.get("args", {})
        safety = event.get("safety", "safe")
        safety_color = _SAFETY_COLORS.get(safety, C.YELLOW)
        icon = c(safety_color, _SAFETY_ICONS.get(safety, "✦"))
        args_str = ""
        for key in ("path", "command", "query", "action", "pattern"):
            if key in args:
                val = str(args[key])
                if len(val) > 80:
                    val = val[:77] + "..."
                args_str = f" {c(C.GRAY, val)}"
                break
        print(f"{prefix}{label} {icon} {c(C.CYAN, name)}{args_str}")
    elif event_type == "done":
        reply = event.get("reply", "")
        preview = reply[:120].replace("\n", " ") if reply else ""
        print(f"{prefix}{label} {c(C.GREEN, '✓')} {c(C.DIM, preview)}")


def print_tools_list(tools: list[dict]) -> None:
    """Display tools grouped by category with safety indicators."""
    if not tools:
        print(c(C.GRAY, "  No tools loaded."))
        return

    # Group by category
    categories: dict[str, list[dict]] = {}
    for t in tools:
        fn = t.get("function", {})
        # Try to get category from description or name patterns
        name = fn.get("name", "")
        desc = fn.get("description", "")

        # Infer category from tool name prefix
        cat = "general"
        for prefix in ("read_file", "write_file", "edit_file", "list_directory",
                        "search_files", "glob_files", "search_and_replace"):
            if name == prefix:
                cat = "file"
                break
        if name.startswith("git_"):
            cat = "git"
        elif name.startswith("execute_shell"):
            cat = "shell"
        elif name.startswith("execute_python") or name.startswith("code_"):
            cat = "code"
        elif name.startswith("http_") or name.startswith("web_") or name.startswith("dns_"):
            cat = "network"
        elif name.startswith("query_") or name.startswith("db_"):
            cat = "database"
        elif name.startswith("delegate_"):
            cat = "agent"
        elif name in ("project_overview", "run_tests", "deploy_check"):
            cat = "pipeline"
        elif name.startswith("search"):
            cat = "search"
        elif name.startswith("system_") or name.startswith("env_"):
            cat = "system"

        categories.setdefault(cat, []).append(fn)

    # Display grouped
    for cat in sorted(categories.keys()):
        icon = _CATEGORY_ICONS.get(cat, "◆ ")
        print(f"\n  {c(C.GREEN + C.BOLD, f'{icon} {cat.upper()}')} {c(C.GRAY_DARK, '─' * 30)}")
        for fn in sorted(categories[cat], key=lambda f: f.get("name", "")):
            name = fn.get("name", "")
            desc = fn.get("description", "")[:55]
            print(f"    {c(C.CYAN, name):38s} {c(C.GRAY, desc)}")

    total = sum(len(v) for v in categories.values())
    print(f"\n  {c(C.GRAY, f'{total} tools in {len(categories)} categories')}")


def print_banner(provider: str, model: str) -> None:
    """Display the Alpha Code startup banner — Kali Linux inspired."""
    cwd = os.getcwd()

    # Kali-style ASCII banner
    banner = r"""
  ╔══════════════════════════════════════════════════╗
  ║   █████╗ ██╗     ██████╗ ██╗  ██╗ █████╗        ║
  ║  ██╔══██╗██║     ██╔══██╗██║  ██║██╔══██╗       ║
  ║  ███████║██║     ██████╔╝███████║███████║       ║
  ║  ██╔══██║██║     ██╔═══╝ ██╔══██║██╔══██║       ║
  ║  ██║  ██║███████╗██║     ██║  ██║██║  ██║       ║
  ║  ╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝       ║
  ╚══════════════════════════════════════════════════╝"""

    print(c(C.GREEN + C.BOLD, banner))
    print(f"  {c(C.GREEN_DARK, '│')} {c(C.WHITE + C.BOLD, 'ALPHA CODE')} {c(C.GRAY, '— Terminal Agent')}")
    print(f"  {c(C.GREEN_DARK, '│')} {c(C.GRAY, 'cwd:')} {c(C.GREEN, cwd)}")
    print(f"  {c(C.GREEN_DARK, '│')} {c(C.GRAY, 'provider:')} {c(C.CYAN, f'{provider} ({model})')}")
    print(f"  {c(C.GREEN_DARK, '│')} {c(C.GRAY, 'Commands:')} /clear /history /continue /tools /help /exit")
    print()


def print_iteration_status(iteration: int, max_iter: int, tokens: int = 0) -> None:
    """Show current iteration and token usage."""
    token_str = f" | {tokens} tokens" if tokens else ""
    print(
        f"  {c(C.GRAY_DARK, '[')} "
        f"{c(C.GREEN_DARK, f'iter {iteration}/{max_iter}')}"
        f"{c(C.GRAY, token_str)} "
        f"{c(C.GRAY_DARK, ']')}"
    )


def print_sessions_list(sessions: list[dict]) -> None:
    """Display saved sessions with formatted output."""
    if not sessions:
        print(c(C.GRAY, "  No saved sessions."))
        return
    for s in sessions:
        sid = c(C.GREEN, s["session_id"])
        ts = c(C.GRAY, s.get("timestamp_human", ""))
        count = c(C.BLUE, f'{s["message_count"]} msgs')
        preview = c(C.DIM, s.get("preview", ""))
        print(f"  {sid} {ts} ({count}) {preview}")
