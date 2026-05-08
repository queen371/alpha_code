"""Tests for the attachments helpers + Anthropic image-block conversion."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from alpha.attachments import build_user_content, extract_text
from alpha.llm_anthropic import _convert_messages, _convert_user_content


# Smallest valid PNG: 1x1 transparent pixel.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c489000000017352474200aece1ce90000000d49444154789c6300"
    "01000000050001a0a3a8540000000049454e44ae426082"
)


@pytest.fixture
def png_path(tmp_path: Path) -> Path:
    p = tmp_path / "tiny.png"
    p.write_bytes(_TINY_PNG)
    return p


# ── build_user_content ──


class TestBuildUserContent:
    def test_no_images_returns_string(self):
        assert build_user_content("hello", []) == "hello"

    def test_with_image_returns_block_list(self, png_path: Path):
        out = build_user_content("describe", [png_path])
        assert isinstance(out, list)
        assert out[0] == {"type": "text", "text": "describe"}
        assert out[1]["type"] == "image_url"

    def test_with_image_anthropic_format(self, png_path: Path):
        out = build_user_content("describe", [png_path], vision_format="anthropic")
        assert isinstance(out, list)
        assert out[0] == {"type": "text", "text": "describe"}
        assert out[1]["type"] == "image"
        assert out[1]["source"]["type"] == "base64"
        assert out[1]["source"]["media_type"] == "image/png"
        decoded = base64.b64decode(out[1]["source"]["data"])
        assert decoded == _TINY_PNG
        assert "image_url" not in out[1]

    def test_with_image_openai_format_explicit(self, png_path: Path):
        out = build_user_content("describe", [png_path], vision_format="openai")
        assert isinstance(out, list)
        assert out[1]["type"] == "image_url"
        url = out[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded == _TINY_PNG

    def test_missing_file_skipped(self, tmp_path: Path):
        ghost = tmp_path / "does-not-exist.png"
        out = build_user_content("describe", [ghost])
        # All images failed to load → falls back to plain text.
        assert out == "describe"

    def test_oversized_image_skipped(self, tmp_path: Path, monkeypatch):
        big = tmp_path / "big.png"
        big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
        monkeypatch.setattr("alpha.attachments.MAX_IMAGE_BYTES", 50)
        out = build_user_content("hi", [big])
        assert out == "hi"

    def test_extension_drives_media_type(self, tmp_path: Path):
        for ext, media in (("jpg", "image/jpeg"), ("gif", "image/gif"), ("webp", "image/webp")):
            p = tmp_path / f"x.{ext}"
            p.write_bytes(b"\x00\x01" * 16)
            out = build_user_content("", [p])
            assert isinstance(out, list)
            assert media in out[0]["image_url"]["url"]


# ── extract_text ──


class TestExtractText:
    def test_string_passthrough(self):
        assert extract_text("hi") == "hi"

    def test_block_list(self):
        blocks = [
            {"type": "text", "text": "a"},
            {"type": "image_url", "image_url": {"url": "..."}},
            {"type": "text", "text": "b"},
        ]
        assert extract_text(blocks) == "a\nb"

    def test_other_types_ignored(self):
        assert extract_text(None) == ""
        assert extract_text(123) == ""


# ── Anthropic conversion ──


class TestAnthropicImageConversion:
    def test_image_url_block_becomes_image_source(self):
        b64 = base64.b64encode(_TINY_PNG).decode()
        openai_content = [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]
        out = _convert_user_content(openai_content)
        assert isinstance(out, list)
        assert out[0] == {"type": "text", "text": "look"}
        assert out[1] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        }

    def test_string_content_passthrough(self):
        assert _convert_user_content("hi") == "hi"

    def test_full_message_conversion_carries_image(self, png_path: Path):
        b64 = base64.b64encode(_TINY_PNG).decode()
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}
        ]
        _, converted = _convert_messages(msgs)
        assert converted[0]["role"] == "user"
        blocks = converted[0]["content"]
        assert any(b.get("type") == "image" for b in blocks)
        img_block = next(b for b in blocks if b.get("type") == "image")
        assert img_block["source"]["data"] == b64

    def test_non_data_url_uses_url_source(self):
        out = _convert_user_content([
            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
        ])
        assert out[0] == {"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}}
