"""Small CLI prompt helpers — zero deps, stdin only."""

from __future__ import annotations

import getpass

from ..display import C, c


def ask(label: str, default: str | None = None) -> str:
    """Free-text question. Empty input returns default if provided."""
    suffix = c(C.GRAY, f" [{default}]") if default else ""
    try:
        value = input(f"  {c(C.CYAN, '?')} {label}{suffix}: ").strip()
    except EOFError:
        value = ""
    return value or (default or "")


def ask_secret(label: str) -> str:
    """Ask for a secret (API key). Input is hidden."""
    try:
        return getpass.getpass(f"  {c(C.CYAN, '?')} {label}: ").strip()
    except EOFError:
        return ""


def ask_choice(label: str, choices: list[str], default: str | None = None) -> str:
    """Pick from numbered choices. Empty input returns default."""
    print(f"  {c(C.CYAN, '?')} {label}")
    for i, ch in enumerate(choices, 1):
        marker = c(C.GREEN, "●") if ch == default else " "
        print(f"    {marker} {c(C.GRAY, str(i))} {ch}")
    suffix = c(C.GRAY, f" [{default}]") if default else ""
    while True:
        try:
            raw = input(f"    > Choose 1-{len(choices)}{suffix}: ").strip()
        except EOFError:
            return default or choices[0]
        if not raw and default:
            return default
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        if raw in choices:
            return raw
        print(c(C.RED, "    Invalid choice."))


def ask_yes_no(label: str, default: bool = True) -> bool:
    suffix = c(C.GRAY, "[S/n]" if default else "[s/N]")
    try:
        raw = input(f"  {c(C.CYAN, '?')} {label} {suffix}: ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("s", "y", "sim", "yes")
