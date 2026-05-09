"""
OCR via Google Gemini — extrai texto de imagens usando o endpoint
OpenAI-compatible do Gemini. Usado como fallback quando o provider
principal nao suporta visao (ex: DeepSeek).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GEMINI_OCR_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_OCR_MODEL = "gemini-2.5-flash"  # barato, rapido, multimodal
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_EXT_TO_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _read_image(path: Path) -> tuple[bytes, str] | None:
    if not path.is_file():
        logger.warning("OCR skipped — file not found: %s", path)
        return None
    try:
        size = path.stat().st_size
    except OSError as e:
        logger.warning("OCR skipped — stat failed for %s: %s", path, e)
        return None
    if size > MAX_IMAGE_BYTES:
        logger.warning("OCR skipped — %s exceeds %d bytes", path, MAX_IMAGE_BYTES)
        return None
    try:
        data = path.read_bytes()
    except OSError as e:
        logger.warning("OCR skipped — read failed for %s: %s", path, e)
        return None
    media = _EXT_TO_MEDIA.get(path.suffix.lower(), "image/png")
    return data, media


async def ocr_images(
    image_paths: list[Path],
    api_key: str,
    model: str = GEMINI_OCR_MODEL,
    timeout: float = 60.0,
) -> str:
    """Send images to Gemini and return extracted text.

    Returns empty string if no images could be read or OCR fails.
    """
    content_blocks: list[dict] = [
        {
            "type": "text",
            "text": (
                "Extract ALL text visible in this image. "
                "Include any code, error messages, logs, file paths, numbers, "
                "labels, UI elements, terminal output — everything that is text. "
                "If it's a screenshot, describe the layout briefly then list all text. "
                "Return ONLY the extracted content, no conversational filler."
            ),
        }
    ]

    for path in image_paths:
        loaded = _read_image(path)
        if loaded is None:
            continue
        data, media_type = loaded
        b64 = base64.b64encode(data).decode("ascii")
        content_blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
            }
        )

    if len(content_blocks) <= 1:  # only the text prompt, no images loaded
        return ""

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content_blocks}],
        "max_tokens": 2000,
        "temperature": 0.0,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(
                f"{GEMINI_OCR_BASE}/chat/completions",
                json=payload,
                headers=headers,
            )
            if resp.status_code >= 400:
                logger.error(
                    "Gemini OCR HTTP %d: %s",
                    resp.status_code,
                    resp.text[:500],
                )
                return ""
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except httpx.TimeoutException:
        logger.error("Gemini OCR timeout (%.0fs)", timeout)
        return ""
    except Exception as e:
        logger.error("Gemini OCR failed: %s", e)
        return ""


def ocr_images_sync(
    image_paths: list[Path],
    api_key: str,
    model: str = GEMINI_OCR_MODEL,
    timeout: float = 60.0,
) -> str:
    """Synchronous wrapper for REPL context."""
    return asyncio.run(ocr_images(image_paths, api_key, model=model, timeout=timeout))
