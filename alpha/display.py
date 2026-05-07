"""
Terminal display helpers for Alpha Code.

Kali Linux-inspired color scheme with priority-based visual indicators.
Green/red dominant palette, safety-aware tool display, hacker aesthetic.
"""

import asyncio
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

# Category icons for /tools display.
# Chaves baterem com `td.category` em register_tool. Antes tinha `file`
# (tool usa `filesystem`), `pipeline` (nenhuma tool), e faltava
# `composite/browser/scraping/skills` — metade das categorias caia no
# fallback `◆`. (#DM013)
_CATEGORY_ICONS = {
    "filesystem": "📁",
    "shell": "🖥",
    "code": "⟨⟩",
    "git": "⎇ ",
    "network": "🌐",
    "search": "🔍",
    "database": "🗄",
    "system": "⚙ ",
    "agent": "🤖",
    "browser": "🌍",
    "scraping": "🕷",
    "skills": "📚",
    "composite": "⛓ ",
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


_TODO_STATUS_GLYPH = {
    "pending": ("☐", C.GRAY),
    "in_progress": ("◐", C.YELLOW),
    "completed": ("☑", C.GREEN),
    "cancelled": ("☒", C.RED_DARK),
}


def _print_todo_list(todos: list) -> None:
    if not todos:
        print(f"  {c(C.GRAY, '(empty todo list)')}")
        return
    for t in todos:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status", "pending"))
        glyph, color = _TODO_STATUS_GLYPH.get(status, ("•", C.GRAY))
        content = str(t.get("content", ""))
        if len(content) > 200:
            content = content[:197] + "..."
        line_color = C.GRAY if status in ("completed", "cancelled") else C.WHITE
        print(f"  {c(color, glyph)} {c(line_color, content)}")




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

        # todo_write — render checklist
        if name == "todo_write" and isinstance(result.get("todos"), list):
            _print_todo_list(result["todos"])
            warning = result.get("warning")
            if warning:
                print(f"  {c(C.YELLOW, '⚠')} {c(C.YELLOW, warning)}")
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


def _print_plan_card(args: dict) -> None:
    """Pretty-print a present_plan approval card."""
    summary = str(args.get("summary", ""))
    steps = args.get("steps", []) or []
    print()
    print(f"  {c(C.YELLOW + C.BOLD, '┌─ PLANO PROPOSTO ─────────────────────')}")
    print(f"  {c(C.YELLOW, '│')} {c(C.WHITE + C.BOLD, summary)}")
    print(f"  {c(C.YELLOW, '│')}")
    for i, step in enumerate(steps, start=1):
        text = str(step)
        if len(text) > 100:
            text = text[:97] + "..."
        print(f"  {c(C.YELLOW, '│')} {c(C.GRAY, f'{i:>2}.')} {text}")
    print(f"  {c(C.YELLOW + C.BOLD, '└──────────────────────────────────────')}")


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

    if tool_name == "present_plan":
        _print_plan_card(args)
    else:
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
    except KeyboardInterrupt:
        # Sem este handler, Ctrl+C durante o prompt mata o REPL inteiro.
        # Tratar como "negado" e devolver controle preserva a sessao.
        print(f"\n  {c(C.RED, '✗ Negado (Ctrl+C)')}")
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
    print(f"  {c(C.GREEN_DARK, '│')} {c(C.GRAY, 'Commands:')} /clear /history /continue /tools /model /help /exit")
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


def print_providers_list(
    providers: list[dict],
    *,
    current: str | None = None,
    default: str | None = None,
    numbered: bool = False,
) -> None:
    """Render a provider list with unified formatting.

    numbered=True prefixes rows with `1.`, `2.` (for startup picker).
    current=<id> marks the active provider with a green dot.
    default=<id> appends a gray `(default)` suffix.
    """
    for i, p in enumerate(providers, 1):
        status = c(C.GREEN, "available") if p["available"] else c(C.RED, "no key")
        tag = "" if p["supports_tools"] else c(C.YELLOW, "  chat-only")
        if numbered:
            prefix = f"{c(C.CYAN, str(i))}."
        elif current is not None:
            prefix = c(C.GREEN, "●") if p["id"] == current else " "
        else:
            prefix = " "
        suffix = c(C.GRAY, " (default)") if default and p["id"] == default else ""
        print(f"  {prefix} {c(C.CYAN, p['id']):15s} {p['model']:30s} {status}{tag}{suffix}")


# ─── Thinking indicator (spinner) ───

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_FLOWER_FRAMES = ["✻", "✽", "✾", "✿", "❀", "✿", "✾", "✽"]


class ThinkingIndicator:
    """Animated spinner for in-progress async work.

    Writes a rotating frame + label + elapsed time on a single line with \\r.
    Call start() before work begins and stop() before printing other output.
    """

    def __init__(self, label: str = "Pensando", style: str = "flower") -> None:
        self.label = label
        self.frames = _FLOWER_FRAMES if style == "flower" else _SPINNER_FRAMES
        self._task: asyncio.Task | None = None
        self._running = False
        self._start_time = 0.0
        self._enabled = supports_color()

    def start(self, label: str | None = None) -> None:
        if not self._enabled or self._running:
            if label:
                self.label = label
            return
        if label:
            self.label = label
        self._running = True
        self._start_time = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._animate())
        except RuntimeError:
            self._running = False

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        if self._enabled:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def update_label(self, label: str) -> None:
        self.label = label

    async def _animate(self) -> None:
        i = 0
        try:
            while self._running:
                frame = self.frames[i % len(self.frames)]
                elapsed = time.monotonic() - self._start_time
                dur = f" ({int(elapsed)}s)" if elapsed >= 1 else ""
                line = f"\r{c(C.MAGENTA, frame)} {c(C.GRAY, self.label + dur)}\033[K"
                sys.stdout.write(line)
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.12)
        except asyncio.CancelledError:
            pass
