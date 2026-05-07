"""Regression tests for DEEP_RESILIENCE V1.0/V1.1 stragglers batch 2.

Cobre:
- #053 — compress_context error handling distingue timeout vs bug
- #055 — atexit shutdown_browser detecta loop ativo
- #057 — http_request retry de transientes em metodos seguros
- #051/#D012 — apify polling loga e aborta apos N consecutivos
- #065 — listener `_on_new_page` removido em close
- #067 — SIGTERM handler instalado
- #D010 — extract_multiple_pages log finalizado com contagem
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── #053 — compress_context error handling ────────────────────────


class TestCompressContextErrorHandling:
    def test_distinguishes_timeout_from_bug(self):
        import inspect

        from alpha.agent import run_agent

        src = inspect.getsource(run_agent)
        # Caminhos separados: TimeoutError -> warning + continue,
        # outros Exception -> logger.exception (preserva traceback)
        assert "TimeoutError" in src or "asyncio.TimeoutError" in src
        assert "logger.exception" in src


# ─── #055 — atexit shutdown loop detection ─────────────────────────


class TestShutdownLoopDetection:
    def test_uses_get_running_loop(self):
        import inspect

        import main

        src = inspect.getsource(main._shutdown_browser_session)
        # Deve detectar loop ativo via get_running_loop e cair em loop
        # dedicado em vez de quebrar com asyncio.run.
        assert "get_running_loop" in src
        assert "new_event_loop" in src or "ImportError" in src

    def test_logs_real_errors_instead_of_pass(self):
        import inspect

        import main

        src = inspect.getsource(main._shutdown_browser_session)
        # Antes era `except: pass` — agora distingue ImportError silencioso
        # do erro real que merece print pra stderr.
        assert "except ImportError" in src
        assert "stderr" in src or "print" in src


# ─── #057 — http_request retry ──────────────────────────────────────


class TestHttpRequestRetry:
    @pytest.mark.asyncio
    async def test_get_retries_on_transient(self, monkeypatch):
        from alpha.tools import network_tools

        call_count = {"n": 0}

        async def flaky_http(url, method="GET", headers=None, body=None, timeout=None):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return {"error": "Connection reset", "transient": True}
            return {"status_code": 200, "body": "ok"}

        monkeypatch.setattr(network_tools, "_http_request", flaky_http)
        # Patch sleep para nao esperar de verdade
        monkeypatch.setattr(network_tools.asyncio, "sleep",
                            AsyncMock(return_value=None))

        result = await network_tools._http_request_with_retry("https://example.com")
        assert result.get("status_code") == 200
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_post_does_not_retry(self, monkeypatch):
        from alpha.tools import network_tools

        call_count = {"n": 0}

        async def flaky_http(url, method="GET", headers=None, body=None, timeout=None):
            call_count["n"] += 1
            return {"error": "Connection reset", "transient": True}

        monkeypatch.setattr(network_tools, "_http_request", flaky_http)
        monkeypatch.setattr(network_tools.asyncio, "sleep",
                            AsyncMock(return_value=None))

        # POST nunca retenta — retentar duplicaria criacao de recurso.
        result = await network_tools._http_request_with_retry(
            "https://example.com", method="POST", body="{}"
        )
        assert call_count["n"] == 1
        assert result.get("transient") is True

    @pytest.mark.asyncio
    async def test_retry_budget_exhausted(self, monkeypatch):
        from alpha.tools import network_tools

        call_count = {"n": 0}

        async def always_fails(url, method="GET", headers=None, body=None, timeout=None):
            call_count["n"] += 1
            return {"error": "Conn reset", "transient": True}

        monkeypatch.setattr(network_tools, "_http_request", always_fails)
        monkeypatch.setattr(network_tools.asyncio, "sleep",
                            AsyncMock(return_value=None))

        result = await network_tools._http_request_with_retry("https://example.com")
        # Budget = MAX_RETRIES + 1
        assert call_count["n"] == network_tools._HTTP_MAX_RETRIES + 1
        assert result.get("retried") == network_tools._HTTP_MAX_RETRIES + 1


# ─── #051/#D012 — apify polling logging ────────────────────────────


class TestApifyPollLogging:
    def test_consecutive_error_threshold_in_source(self):
        import inspect

        from alpha.tools import apify_tools

        src = inspect.getsource(apify_tools._run_actor)
        # Polling agora tem `consecutive_errors` e aborta apos N consecutivos
        assert "consecutive_errors" in src
        assert "logger.warning" in src or "logger.error" in src


# ─── #065 — listener cleanup on close ──────────────────────────────


class TestListenerCleanup:
    def test_close_removes_on_new_page_listener(self):
        import inspect

        from alpha.tools.browser_session import BrowserSession

        src = inspect.getsource(BrowserSession.close)
        assert "remove_listener" in src
        assert "_on_new_page" in src


# ─── #067 — SIGTERM handler ────────────────────────────────────────


class TestSigtermHandler:
    def test_install_function_exists(self):
        import main

        assert hasattr(main, "_install_sigterm_handler")

    def test_handler_calls_sys_exit(self):
        import inspect

        import main

        src = inspect.getsource(main._install_sigterm_handler)
        # Handler precisa chamar sys.exit para disparar atexit hooks.
        # Usar `os._exit` pularia atexit — bug conhecido.
        assert "sys.exit" in src
        assert "SIGTERM" in src


# ─── #D010 — extract_multiple_pages log ────────────────────────────


class TestExtractMultipleLogFinalize:
    def test_logs_summary_after_gather(self):
        import inspect

        from alpha.web_search import extract_multiple_pages

        src = inspect.getsource(extract_multiple_pages)
        # Apos asyncio.gather agora tem logger.info com contagem ok/failed/empty
        assert "logger.info" in src
        assert "failed" in src and "empty" in src
