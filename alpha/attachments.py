"""Build provider-specific message content from text + image attachments.

Both OpenAI and Anthropic accept either a plain string or a list of
content blocks. We use the string form when there are no attachments
(unchanged old behavior), and switch to a block list when images are
present.

Block shapes used here:

  OpenAI:
    {"type": "text",      "text": "..."}
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

  Anthropic:
    {"type": "text",  "text": "..."}
    {"type": "image", "source": {"type": "base64",
                                 "media_type": "image/png",
                                 "data": "<base64>"}}

The OpenAI shape lives on the wire all the way until the Anthropic
adapter (`alpha/llm_anthropic.py`) translates user messages — so when
talking to Claude the rest of the system stays unchanged.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB per image

_EXT_TO_MEDIA_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _media_type_for_path(path: Path) -> str:
    return _EXT_TO_MEDIA_TYPE.get(path.suffix.lower(), "image/png")


def _read_image(path: Path) -> tuple[bytes, str] | None:
    if not path.is_file():
        logger.warning("Attachment skipped — file not found: %s", path)
        return None
    try:
        size = path.stat().st_size
    except OSError as e:
        logger.warning("Attachment skipped — stat failed for %s: %s", path, e)
        return None
    if size > MAX_IMAGE_BYTES:
        logger.warning(
            "Attachment skipped — %s exceeds %d bytes (got %d)",
            path, MAX_IMAGE_BYTES, size,
        )
        return None
    try:
        data = path.read_bytes()
    except OSError as e:
        logger.warning("Attachment skipped — read failed for %s: %s", path, e)
        return None
    return data, _media_type_for_path(path)


def build_user_content(text: str, image_paths: list[Path]) -> str | list[dict]:
    """Return content suitable for `messages.append({"role": "user", "content": ...})`.

    With no images, returns the original text string (backward-compat).
    With images, returns an OpenAI-shaped content list. The Anthropic
    adapter translates this list when it converts messages.
    """
    if not image_paths:
        return text

    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})

    for path in image_paths:
        loaded = _read_image(path)
        if loaded is None:
            continue
        data, media_type = loaded
        b64 = base64.b64encode(data).decode("ascii")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
            }
        )

    if len(blocks) == 0:
        # All images failed to load and there was no text — return empty text
        # rather than an empty list (which most providers reject).
        return ""
    if len(blocks) == 1 and blocks[0]["type"] == "text":
        # Only text survived — use the string form for normal-path compat.
        return blocks[0]["text"]
    return blocks


def extract_text(content: str | list[dict]) -> str:
    """Pull the concatenated text out of a content value (str or block list)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)
