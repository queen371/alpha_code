"""Regression tests for DEEP_SECURITY V2.0 MEDIOs.

Cobre #D103 (clipboard_read DESTRUCTIVE), #D105 (browser_screenshot
workspace), #D106 (web_search IP pinning).
"""

from pathlib import Path
from urllib.parse import urlparse

import pytest

from alpha.tools import TOOL_REGISTRY, ToolSafety, load_all_tools
from alpha.tools.delegate_tools import SUBAGENT_DESTRUCTIVE_BLOCKLIST
from alpha.web_search import _build_pinned_url


@pytest.fixture(scope="module", autouse=True)
def _ensure_tools_loaded():
    load_all_tools()


# ─── #D103 ───────────────────────────────────────────────────────


class TestClipboardReadDestructive:
    def test_clipboard_read_marked_destructive(self):
        td = TOOL_REGISTRY["clipboard_read"]
        assert td.safety == ToolSafety.DESTRUCTIVE

    def test_clipboard_read_in_subagent_blocklist(self):
        # Sub-agent sem callback nao pode ler clipboard silenciosamente.
        assert "clipboard_read" in SUBAGENT_DESTRUCTIVE_BLOCKLIST


# ─── #D105 ───────────────────────────────────────────────────────


class TestBrowserScreenshotWorkspaceValidation:
    @pytest.mark.asyncio
    async def test_absolute_path_outside_workspace_rejected(self, tmp_path, monkeypatch):
        # Forca workspace conhecido
        from alpha.tools import workspace as ws_mod

        monkeypatch.setattr(ws_mod, "AGENT_WORKSPACE", tmp_path)

        from alpha.tools import browser_tools

        # Mock do _require_page e do page.screenshot pra nao depender de Playwright
        class FakePage:
            url = "https://example.com"

            async def screenshot(self, **kwargs):
                Path(kwargs["path"]).write_bytes(b"\x89PNG")

        async def fake_require_page():
            return FakePage(), None

        monkeypatch.setattr(browser_tools, "_require_page", fake_require_page)

        # Path absoluto fora do workspace -> rejeitado
        result = await browser_tools._browser_screenshot(save_to="/etc/cron.d/foo.png")
        assert "error" in result
        assert "fora do workspace" in result["error"]

    @pytest.mark.asyncio
    async def test_relative_path_resolved_under_workspace(self, tmp_path, monkeypatch):
        from alpha.tools import workspace as ws_mod
        monkeypatch.setattr(ws_mod, "AGENT_WORKSPACE", tmp_path)

        from alpha.tools import browser_tools

        class FakePage:
            url = "https://example.com"
            async def screenshot(self, **kwargs):
                Path(kwargs["path"]).write_bytes(b"\x89PNG")

        async def fake_require_page():
            return FakePage(), None

        monkeypatch.setattr(browser_tools, "_require_page", fake_require_page)
        result = await browser_tools._browser_screenshot(save_to="x.png")
        assert "saved_to" in result
        # Confirma que o path final esta dentro do workspace
        Path(result["saved_to"]).resolve().relative_to(tmp_path.resolve())


# ─── #D106 ───────────────────────────────────────────────────────


class TestWebSearchPinnedURL:
    def test_pinned_url_lowercase_hostname(self):
        parsed = urlparse("https://example.com/path?q=1")
        out = _build_pinned_url(parsed, "203.0.113.1")
        assert out == "https://203.0.113.1/path?q=1"

    def test_pinned_url_uppercase_hostname(self):
        # urlunparse usa o que esta em parts[1] — cobertura para o caso
        # uppercase que str.replace falhava.
        parsed = urlparse("https://Example.COM/path")
        out = _build_pinned_url(parsed, "203.0.113.1")
        # Hostname original era Example.COM mas urlunparse troca pelo IP.
        assert "203.0.113.1" in out
        assert "Example.COM" not in out

    def test_pinned_url_with_port(self):
        parsed = urlparse("https://example.com:8443/x")
        out = _build_pinned_url(parsed, "203.0.113.1")
        assert out == "https://203.0.113.1:8443/x"

    def test_pinned_url_ipv6(self):
        parsed = urlparse("https://example.com/x")
        out = _build_pinned_url(parsed, "2001:db8::1")
        assert out == "https://[2001:db8::1]/x"

    def test_pinned_url_ipv6_with_port(self):
        parsed = urlparse("https://example.com:8443/x")
        out = _build_pinned_url(parsed, "2001:db8::1")
        assert out == "https://[2001:db8::1]:8443/x"
