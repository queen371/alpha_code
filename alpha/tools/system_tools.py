"""System interaction tools for ALPHA agent.

Clipboard access, screenshot capture, and desktop notifications.

SECURITY: Clipboard write is destructive (overwrites content).
Screenshot is safe (read-only). Notifications are safe.
"""

import asyncio
import logging
import shutil
import subprocess

from . import ToolDefinition, ToolSafety, register_tool

logger = logging.getLogger(__name__)


def _detect_display_server() -> str:
    """Detect X11 vs Wayland."""
    import os

    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


# ─── Clipboard ───


async def _clipboard_read() -> dict:
    """Read from system clipboard."""
    display = _detect_display_server()

    if display == "wayland":
        cmd = ["wl-paste"]
    elif display == "x11":
        cmd = ["xclip", "-selection", "clipboard", "-o"]
    else:
        return {"error": "Servidor de display não detectado (nem X11 nem Wayland)"}

    if not shutil.which(cmd[0]):
        return {"error": f"Comando '{cmd[0]}' não encontrado. Instale: {cmd[0]}"}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)

        if proc.returncode != 0:
            return {"error": f"Falha ao ler clipboard: {stderr.decode(errors='replace')}"}

        content = stdout.decode(errors="replace")
        return {
            "content": content[:10000],
            "length": len(content),
            "truncated": len(content) > 10000,
        }
    except TimeoutError:
        return {"error": "Timeout ao ler clipboard"}
    except Exception as e:
        return {"error": str(e)}


async def _clipboard_write(content: str) -> dict:
    """Write to system clipboard."""
    display = _detect_display_server()

    if display == "wayland":
        cmd = ["wl-copy"]
    elif display == "x11":
        cmd = ["xclip", "-selection", "clipboard"]
    else:
        return {"error": "Servidor de display não detectado (nem X11 nem Wayland)"}

    if not shutil.which(cmd[0]):
        return {"error": f"Comando '{cmd[0]}' não encontrado. Instale: {cmd[0]}"}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(input=content.encode()), timeout=5)

        if proc.returncode != 0:
            return {"error": f"Falha ao escrever no clipboard: {stderr.decode(errors='replace')}"}

        return {"success": True, "length": len(content)}
    except TimeoutError:
        return {"error": "Timeout ao escrever no clipboard"}
    except Exception as e:
        return {"error": str(e)}


# ─── Screenshot ───


async def _screenshot(region: str = "full") -> dict:
    """Capture a screenshot."""
    from pathlib import Path

    screenshot_dir = Path("/tmp/alpha_screenshots")
    screenshot_dir.mkdir(exist_ok=True)

    import time

    filename = f"screenshot_{int(time.time())}.png"
    filepath = screenshot_dir / filename

    # Try different screenshot tools
    tools_to_try = []

    if region == "active_window":
        # Resolve active window ID first (shell expansion doesn't work in subprocess_exec)
        window_id = None
        try:
            xdotool_proc = await asyncio.create_subprocess_exec(
                "xdotool",
                "getactivewindow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            xd_stdout, _ = await asyncio.wait_for(xdotool_proc.communicate(), timeout=5)
            if xdotool_proc.returncode == 0:
                window_id = xd_stdout.decode().strip()
        except (TimeoutError, FileNotFoundError):
            window_id = None

        tools_to_try = [
            ["gnome-screenshot", "-w", "-f", str(filepath)],
            ["scrot", "-u", str(filepath)],
        ]
        if window_id:
            tools_to_try.append(["import", "-window", window_id, str(filepath)])
    else:
        tools_to_try = [
            ["gnome-screenshot", "-f", str(filepath)],
            ["scrot", str(filepath)],
            ["import", "-window", "root", str(filepath)],
            ["grim", str(filepath)],
        ]

    for cmd in tools_to_try:
        tool_name = cmd[0]
        if not shutil.which(tool_name):
            continue

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

            if proc.returncode == 0 and filepath.exists():
                size = filepath.stat().st_size
                return {
                    "path": str(filepath),
                    "size_bytes": size,
                    "region": region,
                    "tool_used": tool_name,
                }
        except (OSError, subprocess.CalledProcessError) as e:
            logger.debug("Screenshot tool %s failed: %s", tool_name, e)
            continue

    return {
        "error": "Nenhuma ferramenta de screenshot disponível. "
        "Instale uma: gnome-screenshot, scrot, grim (Wayland), ou import (ImageMagick)"
    }


# ─── Notifications ───


async def _notify_user(
    message: str,
    title: str = "ALPHA",
    urgency: str = "normal",
    channel: str = "desktop",
) -> dict:
    """Send a notification to the user."""
    if channel == "desktop":
        if not shutil.which("notify-send"):
            return {"error": "notify-send não encontrado. Instale: libnotify-bin"}

        urgency_map = {"low": "low", "normal": "normal", "high": "critical"}
        urg = urgency_map.get(urgency, "normal")

        try:
            proc = await asyncio.create_subprocess_exec(
                "notify-send",
                "--urgency",
                urg,
                "--app-name",
                "ALPHA",
                title,
                message,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)

            if proc.returncode != 0:
                return {"error": f"Falha ao enviar notificação: {stderr.decode(errors='replace')}"}

            return {"sent": True, "channel": "desktop", "urgency": urgency}
        except Exception as e:
            return {"error": str(e)}

    elif channel == "voice":
        # TTS notification — delegate to the TTS system
        return {
            "message": message,
            "channel": "voice",
            "note": "Notificação por voz deve ser tratada pelo frontend/TTS engine",
        }

    return {"error": f"Canal '{channel}' não suportado. Use 'desktop' ou 'voice'."}


register_tool(
    ToolDefinition(
        name="clipboard_read",
        description="Ler o conteúdo atual do clipboard do sistema.",
        parameters={"type": "object", "properties": {}},
        safety=ToolSafety.SAFE,
        category="system",
        executor=_clipboard_read,
    )
)

register_tool(
    ToolDefinition(
        name="clipboard_write",
        description="Escrever conteúdo no clipboard do sistema (sobrescreve o conteúdo atual).",
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Conteúdo para copiar ao clipboard",
                },
            },
            "required": ["content"],
        },
        safety=ToolSafety.DESTRUCTIVE,
        category="system",
        executor=_clipboard_write,
    )
)

register_tool(
    ToolDefinition(
        name="screenshot",
        description="Capturar screenshot da tela. Salva em /tmp e retorna o caminho do arquivo.",
        parameters={
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Região: 'full' (tela inteira) ou 'active_window' (janela ativa). Padrão: full",
                    "enum": ["full", "active_window"],
                    "default": "full",
                },
            },
        },
        safety=ToolSafety.SAFE,
        category="system",
        executor=_screenshot,
    )
)

register_tool(
    ToolDefinition(
        name="notify_user",
        description=(
            "Send an OS-level desktop popup (notify-send) or TTS voice alert. "
            "USE ONLY for asynchronous/background updates the user must see "
            "outside the terminal (e.g. long task finished, error needs attention). "
            "DO NOT use this to reply, greet, or chat with the user — for those, "
            "respond with plain text instead. Never call this for greetings like "
            "'oi', 'hi', 'hello', or to acknowledge messages."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Mensagem da notificação",
                },
                "title": {
                    "type": "string",
                    "description": "Título da notificação. Padrão: ALPHA",
                    "default": "ALPHA",
                },
                "urgency": {
                    "type": "string",
                    "description": "Urgência: low, normal, high",
                    "enum": ["low", "normal", "high"],
                    "default": "normal",
                },
                "channel": {
                    "type": "string",
                    "description": "Canal: 'desktop' (popup) ou 'voice' (TTS)",
                    "enum": ["desktop", "voice"],
                    "default": "desktop",
                },
            },
            "required": ["message"],
        },
        safety=ToolSafety.SAFE,
        category="system",
        executor=_notify_user,
    )
)
