"""Platform detection and Windows-console initialization.

Centraliza decisoes de compatibilidade Windows. Em Linux/macOS todas as
funcoes aqui sao no-ops baratos. Importar este modulo cedo (no boot do
main.py) garante ANSI + UTF-8 em PowerShell/cmd antes do display rodar.
"""

from __future__ import annotations

import os
import sys

IS_WINDOWS = sys.platform == "win32"


def is_windows() -> bool:
    return IS_WINDOWS


def is_modern_windows_terminal() -> bool:
    """True dentro do Windows Terminal (define `WT_SESSION`).

    Sem essa env, e provavel conhost legado / PowerShell ISE — onde o
    framed input do prompt_toolkit costuma quebrar.
    """
    if not IS_WINDOWS:
        return False
    return bool(os.environ.get("WT_SESSION"))


def use_simple_input() -> bool:
    """True quando o framed input deve cair pra `input()` builtin."""
    if os.environ.get("ALPHA_SIMPLE_INPUT") == "1":
        return True
    if IS_WINDOWS and not is_modern_windows_terminal():
        return True
    return False


def init_console() -> None:
    """Habilita ANSI + UTF-8 no Windows. No-op em Linux/macOS."""
    if not IS_WINDOWS:
        return

    try:
        import colorama
        colorama.just_fix_windows_console()
    except ImportError:
        sys.stderr.write(
            "[alpha] aviso: colorama nao instalado; cores podem nao "
            "renderizar corretamente. Instale com: pip install colorama\n"
        )

    # Muitos consoles Windows abrem em cp1252/cp850 e quebram caracteres
    # do banner. reconfigure() existe desde Python 3.7.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
