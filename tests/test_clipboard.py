"""Tests for clipboard helpers — focuses on the format-detection layer.

The actual subprocess paths (xclip / wl-paste) are exercised via dependency
injection; we don't spawn the real binaries in CI.
"""

from __future__ import annotations

import pytest

from alpha import clipboard


def test_guess_media_type_png():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    assert clipboard._guess_media_type(png_header) == "image/png"


def test_guess_media_type_jpeg():
    assert clipboard._guess_media_type(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"


def test_guess_media_type_gif():
    assert clipboard._guess_media_type(b"GIF89a" + b"\x00" * 4) == "image/gif"


def test_guess_media_type_webp():
    data = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8
    assert clipboard._guess_media_type(data) == "image/webp"


def test_guess_media_type_unknown_defaults_to_png():
    assert clipboard._guess_media_type(b"random bytes") == "image/png"


class TestDetectDisplayServer:
    def test_wayland_via_session_type(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.delenv("DISPLAY", raising=False)
        assert clipboard._detect_display_server() == "wayland"

    def test_wayland_via_wayland_display(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert clipboard._detect_display_server() == "wayland"

    def test_x11_when_only_display_set(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("DISPLAY", ":0")
        assert clipboard._detect_display_server() == "x11"

    def test_unknown_when_nothing_set(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        assert clipboard._detect_display_server() == "unknown"

    def test_windows_platform_detection(self, monkeypatch):
        """On Windows, return 'windows' regardless of display env vars."""
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("DISPLAY", ":0")
        assert clipboard._detect_display_server() == "windows"


class TestReadImageReturnsNone:
    """When no backend can produce an image, the public API yields None."""

    def test_unknown_display_with_no_binaries(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setattr(clipboard.shutil, "which", lambda _: None)
        assert clipboard.read_image_from_clipboard() is None
