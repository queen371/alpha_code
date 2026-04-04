"""
Terminal display helpers for Alpha Code.

ANSI color output, tool call formatting, approval prompts.
"""

import json
import os
import sys


# ─── ANSI Colors ───


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    GRAY = "\033[90m"
    WHITE = "\033[97m"


def supports_color() -> bool:
    """Check if the terminal supports ANSI color codes."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


NO_COLOR = not supports_color()


def c(color: str, text: str) -> str:
    """Wrap text in ANSI color codes. Returns plain text if color is unsupported."""
    if NO_COLOR:
        return text
    return f"{color}{text}{C.RESET}"


# ─── Display functions ───


def print_tool_call(name: str, args: dict) -> None:
    """Display a tool call with its primary argument."""
    args_str = ""
    if isinstance(args, dict):
        # Show key args concisely
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

    icon = c(C.YELLOW, "⚡")
    print(f"  {icon} {c(C.CYAN, name)}{args_str}")


def print_tool_result(name: str, result: dict) -> None:
    """Display a tool result compactly (max 8 lines)."""
    if isinstance(result, dict):
        if result.get("error"):
            print(f"  {c(C.RED, '✗')} {c(C.RED, str(result['error'])[:200])}")
            return

        # Show output/content preview
        output = result.get("output") or result.get("content") or result.get("result")
        if isinstance(output, str) and output.strip():
            lines = output.strip().split("\n")
            max_lines = 8
            for line in lines[:max_lines]:
                print(f"  {c(C.DIM, '│')} {line[:200]}")
            if len(lines) > max_lines:
                print(
                    f"  {c(C.DIM, '│')} "
                    f"{c(C.GRAY, f'... ({len(lines) - max_lines} more lines)')}"
                )
        else:
            # Show as compact JSON
            preview = json.dumps(result, ensure_ascii=False, default=str)
            if len(preview) > 200:
                preview = preview[:197] + "..."
            print(f"  {c(C.DIM, '│')} {c(C.GRAY, preview)}")
    else:
        result_str = str(result)[:200]
        print(f"  {c(C.DIM, '│')} {result_str}")


def print_approval_request(tool_name: str, args: dict) -> bool:
    """Show approval request and prompt the user. Returns True if approved."""
    print(f"\n  {c(C.YELLOW + C.BOLD, '⚠ APPROVAL NEEDED')}")
    print(f"  Tool: {c(C.CYAN, tool_name)}")
    if isinstance(args, dict):
        for k, v in args.items():
            val_str = str(v)
            if len(val_str) > 100:
                val_str = val_str[:97] + "..."
            print(f"  {c(C.GRAY, k)}: {val_str}")

    try:
        while True:
            resp = input(f"\n  {c(C.YELLOW, 'Aprovar? [s/n]:')} ").strip().lower()
            if resp in ("s", "sim", "y", "yes"):
                return True
            if resp in ("n", "não", "nao", "no"):
                return False
    except EOFError:
        print(f"  {c(C.GRAY, '(auto-denied — sem terminal interativo)')}")
        return False


def print_phase(detail: str) -> None:
    """Display a phase/progress update."""
    print(f"  {c(C.MAGENTA, '→')} {c(C.DIM, detail)}")


def print_error(message: str) -> None:
    """Display an error message in red."""
    print(f"\n  {c(C.RED, f'Error: {message}')}")


def print_banner(provider: str, model: str) -> None:
    """Display the Alpha Code startup banner."""
    cwd = os.getcwd()
    print()
    print(f"  {c(C.CYAN + C.BOLD, 'ALPHA CODE')} {c(C.DIM, '— Terminal Agent')}")
    print(f"  {c(C.GRAY, f'cwd: {cwd}')}")
    print(f"  {c(C.GRAY, f'provider: {provider} ({model})')}")
    print(f"  {c(C.GRAY, 'Commands: /clear /history /tools /help /exit')}")
    print()
