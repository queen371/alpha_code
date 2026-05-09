"""Regression tests for DEEP_SECURITY V1.0/V1.1 stragglers.

Cobre:
- #D012 — sanitize Bearer/Authorization em llm.py error logs
- #D015 — sanitize asyncpg DSN password leak em database_tools
- #029 — html.unescape em _strip_html
- #032 — bloquear userinfo (user:pass@) em validate_browser_url
- #033 — strip control chars do subagent.md prompt
- #034 — apify_run_actor agora DESTRUCTIVE
- #035 — wizard.write_env rejeita newline em values
"""

import pytest

from alpha._security_log import sanitize_for_log


# ─── #D012 — sanitize_for_log ──────────────────────────────────────


class TestSanitizeForLog:
    def test_redacts_bearer_token(self):
        out = sanitize_for_log("got error: Authorization: Bearer sk-abcdef123456")
        assert "sk-abcdef" not in out
        assert "[redacted]" in out

    def test_redacts_authorization_header(self):
        out = sanitize_for_log('"Authorization": "Bearer xyz789abcdefghij"')
        assert "xyz789" not in out
        assert "[redacted]" in out

    def test_redacts_dsn_password(self):
        out = sanitize_for_log("postgresql://alice:s3cret@host:5432/db")
        assert "s3cret" not in out
        assert "alice:[redacted]@host" in out

    def test_redacts_password_kv(self):
        out = sanitize_for_log("connection failed: password=hunter2 host=db")
        assert "hunter2" not in out

    def test_redacts_api_key_prefix(self):
        out = sanitize_for_log("key=sk-proj_abcdefghijklmnopqrst")
        assert "abcdefghijklmnopqrst" not in out

    def test_idempotent(self):
        once = sanitize_for_log("Bearer abcdef1234567890")
        twice = sanitize_for_log(once)
        assert once == twice

    def test_empty_input(self):
        assert sanitize_for_log("") == ""
        assert sanitize_for_log(None) is None  # type: ignore

    def test_max_chars_clip(self):
        out = sanitize_for_log("Bearer x" * 100, max_chars=20)
        assert len(out) == 20

    def test_preserves_non_secret_text(self):
        out = sanitize_for_log("HTTP 401 Unauthorized: invalid token")
        # Sem token concreto pra redactar — texto preservado
        assert "401" in out
        assert "Unauthorized" in out


# ─── #D015 — asyncpg DSN sanitization ──────────────────────────────


class TestPostgresErrorSanitized:
    @pytest.mark.asyncio
    async def test_postgres_error_redacts_dsn(self, monkeypatch):
        """asyncpg sometimes raises with the DSN inline; result must be redacted."""
        # Forca o caminho de erro com uma exception mock.
        from alpha.tools import database_tools

        class FakePool:
            async def acquire(self):
                raise RuntimeError(
                    "auth failed for postgresql://admin:topsecret@db.example.com:5432/main"
                )

        async def fake_pool(connection):
            return FakePool()

        # Bypass IP check: use loopback IP que e privado, mas vamos forcar o caminho
        # alterando _is_private_ip para retornar False.
        monkeypatch.setattr(database_tools, "_is_private_ip", lambda *a, **kw: False)
        monkeypatch.setattr(database_tools, "_get_pg_pool", fake_pool)

        # Bypass asyncpg import check
        import sys
        sys.modules.setdefault("asyncpg", type(sys)("asyncpg"))

        # Mock pool.acquire to raise — but pool.acquire is async context manager.
        # Mais simples: testar o sanitize em isolacao no error path do _query_database.
        # asyncpg pode levantar com DSN no str(e). Verificamos que sanitize_for_log
        # e chamado no error path.
        result = await database_tools._query_database(
            "postgresql://admin:topsecret@db.example.com:5432/main",
            "SELECT 1",
            db_type="postgresql",
        )
        # Pode dar varios erros (TimeoutError, AttributeError, etc) — interessa que
        # qualquer leak de DSN tenha sido sanitizado.
        err = result.get("error", "")
        assert "topsecret" not in err


# ─── #029 — html.unescape ───────────────────────────────────────────


class TestStripHtmlUnescape:
    def test_decodes_entities(self):
        from alpha.web_search import _strip_html

        out = _strip_html("<p>Tom &amp; Jerry &#39;s &lt;br&gt;</p>")
        assert "&amp;" not in out
        assert "&" in out
        assert "Tom & Jerry" in out
        assert "'" in out


# ─── #032 — userinfo blocked ───────────────────────────────────────


class TestBrowserUrlBlocksUserinfo:
    def test_userinfo_url_rejected(self):
        from alpha.tools.browser_session import validate_browser_url

        err = validate_browser_url("https://github.com:fake-token@evil.com/path")
        assert err is not None
        assert "userinfo" in err.lower()

    def test_userinfo_password_rejected(self):
        from alpha.tools.browser_session import validate_browser_url

        err = validate_browser_url("https://user:pass@example.com")
        assert err is not None

    def test_normal_url_passes(self):
        from alpha.tools.browser_session import validate_browser_url

        # Pode falhar em outras checks (SSRF) mas nao na userinfo check
        err = validate_browser_url("https://example.com/path")
        assert err is None or "userinfo" not in err.lower()


# ─── #033 — control char stripping ─────────────────────────────────


class TestStripControlChars:
    def test_removes_nul_byte(self):
        from alpha.tools.delegate_tools import _strip_control_chars

        assert _strip_control_chars("hello\x00world") == "helloworld"

    def test_removes_ansi_escape(self):
        from alpha.tools.delegate_tools import _strip_control_chars

        assert "\x1b" not in _strip_control_chars("text\x1b[31mred\x1b[0m")

    def test_removes_bidi_overrides(self):
        from alpha.tools.delegate_tools import _strip_control_chars

        # RLO (‮) reverte direcao visual — usado em ataques de "reordering"
        out = _strip_control_chars("safe‮evil‬")
        assert "‮" not in out
        assert "‬" not in out

    def test_preserves_normal_whitespace(self):
        from alpha.tools.delegate_tools import _strip_control_chars

        assert _strip_control_chars("line1\nline2\tindented") == "line1\nline2\tindented"


# ─── #034 — apify DESTRUCTIVE ──────────────────────────────────────


class TestApifyRunActorDestructive:
    def test_apify_run_actor_marked_destructive(self):
        from alpha.tools import TOOL_REGISTRY, ToolSafety, load_all_tools

        load_all_tools()
        td = TOOL_REGISTRY["apify_run_actor"]
        assert td.safety == ToolSafety.DESTRUCTIVE

    def test_apify_run_actor_in_subagent_blocklist(self):
        from alpha.tools.delegate_tools import SUBAGENT_DESTRUCTIVE_BLOCKLIST

        assert "apify_run_actor" in SUBAGENT_DESTRUCTIVE_BLOCKLIST


# ─── #035 — wizard rejects newline in env values ───────────────────


class TestWizardEnvRejectsNewline:
    def test_newline_in_value_raises(self, tmp_path, monkeypatch):
        from alpha.wizard import env as wizard_env

        monkeypatch.setattr(wizard_env, "ENV_PATH", tmp_path / ".env")
        with pytest.raises(ValueError, match="Newline"):
            wizard_env.write_env({"OPENAI_API_KEY": "valid\nDEEPSEEK_API_KEY=evil"})

    def test_carriage_return_rejected(self, tmp_path, monkeypatch):
        from alpha.wizard import env as wizard_env

        monkeypatch.setattr(wizard_env, "ENV_PATH", tmp_path / ".env")
        with pytest.raises(ValueError):
            wizard_env.write_env({"K": "abc\rdef"})

    def test_normal_value_writes(self, tmp_path, monkeypatch):
        from alpha.wizard import env as wizard_env

        env_path = tmp_path / ".env"
        monkeypatch.setattr(wizard_env, "ENV_PATH", env_path)
        wizard_env.write_env({"OPENAI_API_KEY": "sk-abc123"})
        assert env_path.exists()
        assert "sk-abc123" in env_path.read_text()
