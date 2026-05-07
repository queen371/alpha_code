"""Regression tests for the last 6 V2.0 MEDIOs of the DEEP audits.

Cobre:
- #DL016 — `multi_agent_enabled` gate em delegate_tools
- #D020-RES — subprocess.kill em CancelledError (shell/git/code)
- #D021-RES — Playwright cleanup em launch failure
- #D110 — `_run_tool` documenta trust model explicitamente
- #D111 — session_id com sufixo aleatorio
- #D112 — git_operation rejeita branch/message/files com '-' prefix
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── #DL016 ────────────────────────────────────────────────────────


class TestMultiAgentEnabledGate:
    @pytest.mark.asyncio
    async def test_delegate_task_blocked_when_multi_agent_disabled(self):
        from alpha.config import FEATURES
        from alpha.tools.delegate_tools import _delegate_task

        original = FEATURES.get("multi_agent_enabled", True)
        FEATURES["multi_agent_enabled"] = False
        try:
            r = await _delegate_task("dummy")
            assert "multi-agent" in r.get("error", "").lower()
        finally:
            FEATURES["multi_agent_enabled"] = original

    @pytest.mark.asyncio
    async def test_delegate_parallel_blocked_when_multi_agent_disabled(self):
        from alpha.config import FEATURES
        from alpha.tools.delegate_tools import _delegate_parallel

        original = FEATURES.get("multi_agent_enabled", True)
        FEATURES["multi_agent_enabled"] = False
        try:
            r = await _delegate_parallel('["t1","t2"]')
            assert "multi-agent" in r.get("error", "").lower()
        finally:
            FEATURES["multi_agent_enabled"] = original

    def test_dead_flags_removed_from_features(self):
        # sandbox_enabled e auto_delegate_parallel_groups eram codigo morto.
        from alpha.config import FEATURES

        assert "sandbox_enabled" not in FEATURES
        assert "auto_delegate_parallel_groups" not in FEATURES


# ─── #D111 ─────────────────────────────────────────────────────────


class TestSessionIdSuffix:
    def test_session_id_has_random_suffix(self):
        from alpha.history import generate_session_id

        sid = generate_session_id()
        # YYYYMMDD_HHMMSS_xxxxxxxx
        parts = sid.split("_")
        assert len(parts) == 3, sid
        assert len(parts[2]) == 8
        # Hex
        int(parts[2], 16)

    def test_no_collision_in_burst(self):
        from alpha.history import generate_session_id

        ids = [generate_session_id() for _ in range(100)]
        assert len(set(ids)) == 100


# ─── #D112 ─────────────────────────────────────────────────────────


class TestGitArgInjectionRejected:
    @pytest.mark.asyncio
    async def test_branch_starting_with_dash_rejected(self):
        from alpha.tools.git_tools import _git_operation

        r = await _git_operation("checkout", branch="--detach")
        assert "branch" in r.get("error", "")
        assert "-" in r.get("error", "")

    @pytest.mark.asyncio
    async def test_branch_dash_f_rejected(self):
        from alpha.tools.git_tools import _git_operation

        r = await _git_operation("checkout", branch="-f")
        assert r.get("error")

    @pytest.mark.asyncio
    async def test_message_starting_with_dash_rejected(self):
        from alpha.tools.git_tools import _git_operation

        r = await _git_operation("commit", message="--amend")
        assert "message" in r.get("error", "")

    @pytest.mark.asyncio
    async def test_files_with_dash_prefix_rejected(self):
        from alpha.tools.git_tools import _git_operation

        r = await _git_operation("add", files=["--exec=evil"])
        assert "files" in r.get("error", "")

    @pytest.mark.asyncio
    async def test_normal_branch_name_passes_validation(self):
        # Validacao deve permitir nomes normais — falhara depois por nao ser
        # repo git, nao pela validacao de '-'.
        from alpha.tools.git_tools import _git_operation

        r = await _git_operation("checkout", branch="feature/x", path="/tmp")
        # Nao deve ser o erro de "branch nao pode comecar com '-'"
        assert "começar com" not in r.get("error", "")


# ─── #D110 ─────────────────────────────────────────────────────────


class TestCompositeTrustModelDocumented:
    def test_run_tool_docstring_explains_trust_model(self):
        from alpha.tools.composite_tools import _run_tool

        doc = _run_tool.__doc__ or ""
        # Documenta explicitamente o bypass intencional do gate de aprovacao.
        assert "TRUST MODEL" in doc or "trust model" in doc.lower()
        assert "approval" in doc.lower() or "aprov" in doc.lower()


# ─── #D021-RES ─────────────────────────────────────────────────────


class TestBrowserSessionLaunchFailureCleanup:
    @pytest.mark.asyncio
    async def test_playwright_stopped_when_launch_fails(self):
        # Mock async_playwright().start() retornando obj com .chromium.launch
        # que levanta. Verifica que pw.stop() foi chamado.
        from alpha.tools import browser_session as bs

        if not bs.PLAYWRIGHT_AVAILABLE:
            pytest.skip("Playwright nao instalado")

        pw_mock = MagicMock()
        pw_mock.stop = AsyncMock()
        pw_mock.chromium.launch = AsyncMock(side_effect=RuntimeError("launch failed"))

        ap_mock = MagicMock()
        ap_mock.start = AsyncMock(return_value=pw_mock)

        with patch.object(bs, "async_playwright", return_value=ap_mock):
            session = bs.BrowserSession()
            with pytest.raises(RuntimeError, match="launch failed"):
                await session.open(headless=True)

        # pw foi explicitamente parado durante o cleanup do raise.
        pw_mock.stop.assert_awaited_once()
        # E a session ficou em estado limpo (atribuicao atomica no fim).
        assert session.playwright is None
        assert session.browser is None


# ─── #D020-RES ─────────────────────────────────────────────────────


class TestSubprocessKillOnCancel:
    """Verifica que `proc.kill()` e chamado quando o aguardo e cancelado.

    Mock de `asyncio.create_subprocess_exec` com `proc.communicate` que e
    cancelavel. Confirma que o handler `except (CancelledError, ...)`
    chama `proc.kill()` antes de propagar.
    """

    @pytest.mark.asyncio
    async def test_shell_kills_subprocess_on_cancel(self):
        from alpha.tools import shell_tools

        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = -9
        # communicate trava ate ser cancelado
        async def hang(*a, **kw):
            await asyncio.sleep(60)
        proc.communicate = hang

        async def fake_subprocess_exec(*a, **kw):
            return proc

        with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_subprocess_exec):
            task = asyncio.create_task(
                shell_tools._execute_shell("echo hi")
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()
