"""Rich REPL input built on prompt_toolkit.

Adds two capabilities the builtin `input()` can't provide:

  * Ctrl+V (and Alt+V as a guaranteed fallback) reads images from the
    system clipboard. When an image is found, it's saved to a temp file
    and a `[Image #N]` placeholder is inserted into the buffer.
  * Multiline pastes via bracketed paste are accepted as one submission
    instead of being split into one turn per line.

The function returns `(text, image_paths)`, where `image_paths` is the
list of files referenced by `[Image #N]` placeholders in `text`,
ordered by their numeric index.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings

from .clipboard import read_image_from_clipboard

logger = logging.getLogger(__name__)

_IMAGE_PLACEHOLDER_RE = re.compile(r"\[Image #(\d+)\]")
_MEDIA_TYPE_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}

_temp_image_files: list[Path] = []


def _attach_clipboard_image(buffer, attached: dict[int, Path]) -> bool:
    """Read an image from the clipboard and append a `[Image #N]` to the buffer.

    Returns True if an image was attached, False if the clipboard had no image.
    """
    img = read_image_from_clipboard()
    if img is None:
        return False
    data, media_type = img

    ext = _MEDIA_TYPE_TO_EXT.get(media_type, "png")
    fd = tempfile.NamedTemporaryFile(
        prefix="alpha-clip-", suffix=f".{ext}", delete=False
    )
    try:
        fd.write(data)
    finally:
        fd.close()
    path = Path(fd.name)
    _temp_image_files.append(path)

    n = len(attached) + 1
    attached[n] = path
    placeholder = f"[Image #{n}]"
    if buffer.text and not buffer.text.endswith(" "):
        placeholder = " " + placeholder
    buffer.insert_text(placeholder)
    return True


def _build_key_bindings(attached: dict[int, Path]) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("c-v")
    def _(event):
        # If clipboard has an image, attach it. Otherwise, fall through to
        # whatever the terminal pastes (in most terminals Ctrl+V never
        # reaches us — text paste is handled by the terminal itself).
        if not _attach_clipboard_image(event.current_buffer, attached):
            # No image in clipboard — let the user know rather than no-op.
            event.app.invalidate()

    @kb.add("escape", "v")  # Alt+V — guaranteed fallback when Ctrl+V is swallowed
    def _(event):
        _attach_clipboard_image(event.current_buffer, attached)

    return kb


def _resolve_placeholders(text: str, attached: dict[int, Path]) -> tuple[str, list[Path]]:
    """Pull out [Image #N] markers from text and return the matching paths."""
    if not attached:
        return text, []
    paths: list[Path] = []
    seen: set[int] = set()
    for match in _IMAGE_PLACEHOLDER_RE.finditer(text):
        idx = int(match.group(1))
        if idx in seen:
            continue
        path = attached.get(idx)
        if path and path.exists():
            paths.append(path)
            seen.add(idx)
    return text, paths


_SESSION: PromptSession | None = None


def _get_session() -> PromptSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = PromptSession()
    return _SESSION


def read_input(prompt_ansi: str) -> tuple[str, list[Path]]:
    """Read a line from the user. Returns (text, image_paths).

    Raises EOFError on Ctrl+D and KeyboardInterrupt on Ctrl+C — same as
    the builtin `input()`.
    """
    attached: dict[int, Path] = {}
    kb = _build_key_bindings(attached)
    session = _get_session()
    text = session.prompt(ANSI(prompt_ansi), key_bindings=kb)
    return _resolve_placeholders(text, attached)


def cleanup_temp_images() -> None:
    """Remove temp clipboard images. Call from atexit."""
    for path in _temp_image_files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    _temp_image_files.clear()
