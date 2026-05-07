"""Regression tests for DEEP_SECURITY V1.0/V1.1 batch 2.

Cobre:
- #027 — dead `_BLOCKED_IMPORT_RE` regex removida
- #028 — safe_env cache TTL refresh
- #022 — sub-agent task_content nao vaza absolute workspace path
- #030 — multi-statement SQL detector handles SQL standard `''` escape
"""

from unittest.mock import patch

import pytest


# ─── #027 — dead code removed ──────────────────────────────────────


class TestDeadRegexRemoved:
    def test_blocked_import_re_no_longer_module_level(self):
        from alpha.tools import code_tools

        # AST-based validation only (#D018-PERF + #027). A regex era 75 linhas
        # de codigo morto que duplicava o blocklist.
        assert not hasattr(code_tools, "_BLOCKED_IMPORT_RE")
        assert not hasattr(code_tools, "_BLOCKED_IMPORT_PATTERNS")

    def test_ast_validation_still_blocks_pickle(self):
        from alpha.tools.code_tools import _validate_code_safety

        err = _validate_code_safety("import pickle\nx = pickle.loads(b'')")
        assert err is not None

    def test_ast_validation_blocks_runpy(self):
        from alpha.tools.code_tools import _validate_code_safety

        err = _validate_code_safety("from runpy import run_path")
        assert err is not None

    def test_open_read_no_false_positive(self):
        # Bug do regex legacy: \bopen\s*\(.*(w|a|x) casava em open("read.txt")
        # por causa do "a" em "read".
        from alpha.tools.code_tools import _validate_code_safety

        assert _validate_code_safety('open("read.txt")') is None
        assert _validate_code_safety('open("read.txt", "r")') is None


# ─── #028 — safe_env TTL ───────────────────────────────────────────


class TestSafeEnvTTL:
    def test_cache_refreshes_after_ttl(self, monkeypatch):
        from alpha.tools import safe_env

        # Reset cache
        safe_env.invalidate_safe_env_cache()

        # First call — populates cache
        first = safe_env.get_safe_env()
        assert "PYTHONDONTWRITEBYTECODE" in first

        # Mutate environ AND advance time past TTL
        monkeypatch.setenv("ALPHA_TEST_NEW_VAR", "newvalue")
        # Patch time.monotonic para forcar TTL expiry
        original_at = safe_env._cached_at
        with patch.object(safe_env.time, "monotonic",
                          return_value=original_at + safe_env._CACHE_TTL_SECONDS + 1):
            refreshed = safe_env.get_safe_env()

        assert refreshed.get("ALPHA_TEST_NEW_VAR") == "newvalue"

    def test_cache_held_within_ttl(self, monkeypatch):
        from alpha.tools import safe_env

        safe_env.invalidate_safe_env_cache()
        first = safe_env.get_safe_env()
        # Sem avancar tempo: nova var em environ NAO aparece (cache ainda valido)
        monkeypatch.setenv("ALPHA_TEST_HELD_VAR", "held")
        second = safe_env.get_safe_env()
        assert "ALPHA_TEST_HELD_VAR" not in second
        assert first is second  # mesmo objeto cacheado

    def test_invalidate_forces_immediate_refresh(self, monkeypatch):
        from alpha.tools import safe_env

        safe_env.invalidate_safe_env_cache()
        safe_env.get_safe_env()
        monkeypatch.setenv("ALPHA_TEST_INVALIDATE", "ok")
        safe_env.invalidate_safe_env_cache()
        assert safe_env.get_safe_env().get("ALPHA_TEST_INVALIDATE") == "ok"


# ─── #022 — workspace path not leaked to sub-agent ─────────────────


class TestSubagentPathPrivacy:
    def test_subagent_task_content_uses_relative_scratch(self):
        # O fix nao deve incluir o workspace absoluto no task_content.
        # Lemos o source do _run_subagent para verificar — runtime check
        # precisaria de scaffolding pesado (provider mock + asyncio).
        import inspect

        from alpha.tools.delegate_tools import _run_subagent

        src = inspect.getsource(_run_subagent)
        # Antes: `[CWD: {workspace_root}]` na string. Apos #022 fix:
        # workspace_root nao aparece no task_content (so usado para criar
        # scratch_dir e passar como `workspace=` arg).
        assert "[CWD:" not in src or "workspace_root}" not in src.split("[CWD:")[-1][:200]
        assert "scratch_rel" in src or "relative" in src.lower()


# ─── #030 — SQL standard escape ────────────────────────────────────


class TestSQLStandardEscape:
    def test_doubled_quote_inside_string_no_false_block(self):
        from alpha.tools.database_tools import _is_dangerous_query

        # 'O''Brien' e string valida com apostrofe escapado por dobramento.
        # Nao deve bloquear (sem multi-statement real).
        assert _is_dangerous_query("SELECT 'O''Brien' AS name") is None

    def test_real_multistatement_blocked(self):
        from alpha.tools.database_tools import _is_dangerous_query

        result = _is_dangerous_query("SELECT 1; DROP TABLE users")
        assert result is not None
        assert "multi-statement" in result.lower()

    def test_backslash_quote_no_longer_treated_as_escape(self):
        # SQL standard: backslash NAO escapa quote. O detector legacy
        # tratava `\'` como escape, criando bypass:
        # `SELECT 'a\'; DROP TABLE t; --` parecia 1 statement mas em
        # SQL standard sao 2.
        from alpha.tools.database_tools import _is_dangerous_query

        result = _is_dangerous_query("SELECT 'a\\'; DROP TABLE t; --")
        assert result is not None
        assert "multi-statement" in result.lower()

    def test_semicolon_inside_doubled_quote_string_ok(self):
        from alpha.tools.database_tools import _is_dangerous_query

        # ';' literal dentro de string SQL valida — nao e multi-statement
        assert _is_dangerous_query("SELECT 'value; with; semicolons' AS x") is None

    def test_trailing_semicolon_with_only_comment_allowed(self):
        from alpha.tools.database_tools import _is_dangerous_query

        # Trailing comment apos `;` nao e multi-statement
        assert _is_dangerous_query("SELECT 1; -- trailing comment") is None
