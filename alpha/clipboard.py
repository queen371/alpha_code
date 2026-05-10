"""Read images and text from the system clipboard.

Works on X11 (xclip), Wayland (wl-paste), and Windows (PIL). Returns None if the
clipboard has no image (or the relevant binary is not installed).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

CLIPBOARD_TIMEOUT = 3  # seconds — clipboard reads should be near-instant


def _detect_display_server() -> str:
    """Return 'windows' | 'wayland' | 'x11' | 'unknown'."""
    import sys as _sys
    if _sys.platform == "win32":
        return "windows"
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _wl_paste_image() -> bytes | None:
    if not shutil.which("wl-paste"):
        return None
    try:
        # `--list-types` first to skip the 5s timeout when no image is present.
        types_proc = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, timeout=CLIPBOARD_TIMEOUT, check=False,
        )
        types = types_proc.stdout.decode("utf-8", errors="replace").splitlines()
        image_type = next((t for t in types if t.startswith("image/")), None)
        if image_type is None:
            return None
        proc = subprocess.run(
            ["wl-paste", "--type", image_type],
            capture_output=True, timeout=CLIPBOARD_TIMEOUT, check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug("wl-paste image read failed: %s", e)
        return None


def _xclip_paste_image() -> bytes | None:
    if not shutil.which("xclip"):
        return None
    try:
        # Probe targets to know whether an image is present.
        targets = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True, timeout=CLIPBOARD_TIMEOUT, check=False,
        )
        target_lines = targets.stdout.decode("utf-8", errors="replace").splitlines()
        image_target = next((t for t in target_lines if t.startswith("image/")), None)
        if image_target is None:
            return None
        proc = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", image_target, "-o"],
            capture_output=True, timeout=CLIPBOARD_TIMEOUT, check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug("xclip image read failed: %s", e)
        return None


def _win_paste_image() -> bytes | None:
    """Read image from Windows clipboard via PIL.ImageGrab."""
    try:
        from PIL import ImageGrab
        import io
        img = ImageGrab.grabclipboard()
        if img is None:
            return None
        # PIL may return a list of filenames if files are copied
        if isinstance(img, list):
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        logger.debug("PIL not installed; clipboard image read unavailable on Windows")
    except Exception as e:
        logger.debug("Windows clipboard image read failed: %s", e)
    return None


def read_image_from_clipboard() -> tuple[bytes, str] | None:
    """Read an image from the system clipboard.

    Returns (bytes, media_type) like (data, "image/png") on success,
    or None if no image is present, the backend is missing, or the
    read fails. Tries Wayland first, then X11.
    """
    server = _detect_display_server()

    if server == "windows":
        data = _win_paste_image()
        if data:
            return data, _guess_media_type(data)
        return None

    if server == "wayland":
        data = _wl_paste_image()
        if data:
            return data, _guess_media_type(data)
        return None

    if server == "x11":
        data = _xclip_paste_image()
        if data:
            return data, _guess_media_type(data)
        return None

    # Unknown display server: try both.
    for fn in (_wl_paste_image, _xclip_paste_image):
        data = fn()
        if data:
            return data, _guess_media_type(data)
    return None


def _guess_media_type(data: bytes) -> str:
    """Identify image format from magic bytes. Defaults to image/png."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"
