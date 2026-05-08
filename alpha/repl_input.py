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
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

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

    @kb.add("s-tab")  # Shift+Tab toggles auto-accept-edits mode
    def _(event):
        from .display import toggle_auto_accept

        toggle_auto_accept()
        event.app.invalidate()  # redraw bottom toolbar

    return kb


def _bottom_toolbar() -> "ANSI":
    """Status line below the prompt showing auto-accept mode + hint."""
    from .display import C, c, is_auto_accept

    if is_auto_accept():
        text = (
            f" {c(C.GREEN + C.BOLD, '»»')} "
            f"{c(C.GREEN + C.BOLD, 'accept edits on')} "
            f"{c(C.GRAY, '(shift+tab to cycle) · ctrl+c to interrupt')}"
        )
    else:
        text = (
            f" {c(C.GRAY, '»»')} "
            f"{c(C.GRAY, 'accept edits off')} "
            f"{c(C.GRAY_DARK, '(shift+tab to enable) · ctrl+c to interrupt')}"
        )
    return ANSI(text)


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


_BUILTIN_COMMANDS: list[tuple[str, str]] = [
    ("/init", "Draft an ALPHA.md for this project"),
    ("/clear", "Clear history and screen"),
    ("/history", "Show conversation history"),
    ("/save", "Save current session"),
    ("/load", "Load a previous session"),
    ("/continue", "Resume from last session"),
    ("/sessions", "List saved sessions"),
    ("/tools", "List available tools"),
    ("/skills", "List registered skills (ready vs inactive)"),
    ("/mcp", "List connected MCP servers"),
    ("/image", "Attach an image (Ctrl+V also works)"),
    ("/agents", "List named agents"),
    ("/agent", "Show/switch active agent"),
    ("/model", "Show/switch provider & model"),
    ("/help", "Show all commands"),
    ("/exit", "Exit"),
]


class _SlashCompleter(Completer):
    """Autocomplete for ``/command`` and ``/<skill-name>``.

    Triggers only when the line is a single slash-token with no whitespace
    yet — that's the only place where typing a name matters. Once the user
    hits space, completion stops so it doesn't compete with normal text.
    """

    def get_completions(self, document, complete_event):
        line = document.text_before_cursor
        if not line.startswith("/") or " " in line:
            return

        # Skills are imported lazily so this module stays import-cheap and
        # doesn't pull the registry at definition time.
        entries: list[tuple[str, str]] = list(_BUILTIN_COMMANDS)
        try:
            from .skills import list_skills
            for s in list_skills():
                meta = (s.description or "").strip().split("\n", 1)[0]
                entries.append((f"/{s.name}", meta[:80] or "skill"))
        except Exception:
            pass

        # Substring match with prefix-first ranking: typing `/save` matches
        # both `/save-anything` (prefix) and `/git-save` (substring). Prefix
        # hits stream first so the closest match is at the top of the popup.
        needle = line[1:].lower()
        if not needle:
            ordered = entries
        else:
            prefix_hits: list[tuple[str, str]] = []
            substr_hits: list[tuple[str, str]] = []
            for cmd, desc in entries:
                name = cmd[1:].lower()
                if name.startswith(needle):
                    prefix_hits.append((cmd, desc))
                elif needle in name:
                    substr_hits.append((cmd, desc))
            ordered = prefix_hits + substr_hits

        for cmd, desc in ordered:
            yield Completion(
                cmd,
                start_position=-len(line),
                display_meta=desc,
            )


_SESSION: PromptSession | None = None


# Style applied to the user-typed text so it's visually distinct from the
# prompt arrow and from agent output. Bright neon green + bold mirrors the
# Alpha brand and matches the indicator/prompt arrow tint.
_INPUT_STYLE = Style.from_dict({
    "": "fg:#5fff5f bold",  # the empty class styles unstyled buffer text
    # prompt_toolkit gives bottom-toolbar a reversed bg by default, which
    # turns our ANSI greens into hard-to-read fg-on-light. Force a dark bg
    # + neutral fg so the embedded ANSI escapes (from _bottom_toolbar) win.
    "bottom-toolbar": "bg:#1a1a1a fg:#cccccc noreverse",
    "bottom-toolbar.text": "bg:#1a1a1a fg:#cccccc noreverse",
})


def _get_session() -> PromptSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = PromptSession(
            completer=_SlashCompleter(),
            complete_while_typing=True,
            bottom_toolbar=_bottom_toolbar,
            style=_INPUT_STYLE,
            include_default_pygments_style=False,
        )
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
