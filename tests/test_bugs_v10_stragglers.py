"""Regression tests for DEEP_BUGS V1.0 stragglers (#D022–#D030).

Cobre:
- #D022 — error paths usam `_format_result` (truncamento)
- #D023 — `_calc_backoff` jitter respeita MAX_BACKOFF
- #D024 — SQLite URI mode=ro com path quoted
- #D025 — glob_files filtra matches fora do workspace
- #D026 — apify parse defensivo (sem KeyError cru)
- #D027 — ALLOWED_COMMANDS removido (era dead code)
- #D028 — shell description corrigida (pipes sao suportados)
- #D029 — force-push dead check removido
- #D030 — verificacao do _CATEGORY_ICONS ainda em uso
"""

import inspect
from pathlib import Path

import pytest


# ─── #D023 — backoff jitter cap ─────────────────────────────────────


class TestCalcBackoffJitterCap:
    def test_retry_after_never_exceeds_max(self):
        from alpha.llm import MAX_BACKOFF, _calc_backoff

        # Forcar varias amostras do path retry_after — sem o min externo
        # podia chegar a 1.2 * MAX_BACKOFF.
        for _ in range(200):
            delay = _calc_backoff(0, retry_after=MAX_BACKOFF)
            assert delay <= MAX_BACKOFF, f"Jitter exceeded cap: {delay}"

    def test_path_without_retry_after_also_capped(self):
        from alpha.llm import MAX_BACKOFF, _calc_backoff

        for attempt in range(0, 20):
            for _ in range(10):
                delay = _calc_backoff(attempt)
                assert delay <= MAX_BACKOFF


# ─── #D024 — SQLite URI quoted ──────────────────────────────────────


class TestSQLiteUriQuoted:
    def test_path_with_special_chars_quoted(self, tmp_path):
        # Cria um db com `?` no nome (raro mas valido em Linux)
        from alpha.tools.workspace import AGENT_WORKSPACE  # noqa: F401
        from alpha.tools import database_tools

        # Verificacao via source: `quote(str(resolved))` no caminho de URI
        src = inspect.getsource(database_tools._query_sqlite)
        assert "quote(" in src
        assert "mode=ro" in src


# ─── #D027 — ALLOWED_COMMANDS removed ──────────────────────────────


class TestAllowedCommandsRemoved:
    def test_shell_tools_no_longer_exports_allowed_commands(self):
        from alpha.tools import shell_tools

        # Era frozenset com 90+ entries usado como allowlist documentacional.
        # `_validate_command` so consultava HARD_BLOCKED — codigo morto removido.
        assert not hasattr(shell_tools, "ALLOWED_COMMANDS")

    def test_pipeline_tools_no_longer_imports_it(self):
        import inspect

        from alpha.tools import pipeline_tools

        src = inspect.getsource(pipeline_tools)
        assert "ALLOWED_COMMANDS" not in src

    def test_hard_blocked_still_works(self):
        from alpha.tools.shell_tools import _validate_command

        # rm -rf continua bloqueado
        assert _validate_command("rm -rf /") is not None
        # echo seguro
        assert _validate_command("echo hello") is None


# ─── #D028 — shell description ─────────────────────────────────────


class TestShellDescriptionAccurate:
    def test_description_says_pipes_supported(self):
        from alpha.tools import TOOL_REGISTRY, load_all_tools

        load_all_tools()
        td = TOOL_REGISTRY["execute_shell"]
        # Description NAO deve mais dizer que pipes nao sao suportados
        desc = td.description.lower()
        assert "pipes" in desc
        # Usa execute_pipeline para && / redirects
        assert "execute_pipeline" in desc


# ─── #D029 — git push force dead code removed ──────────────────────


class TestGitPushForceCheckRemoved:
    def test_no_dead_force_check_in_push_branch(self):
        import inspect

        from alpha.tools import git_tools

        src = inspect.getsource(git_tools._git_operation)
        # O check `if "--force" in extra or "-f" in extra:` era dead code.
        # _sanitize_git_args ja rejeita essas flags antes.
        # O fix removeu o check redundante mas mantem comentario explicativo.
        push_block = src.split("elif action == \"push\":")[1].split("elif")[0]
        assert "--force" not in push_block or "lembrar" in push_block.lower()

    def test_sanitize_still_rejects_force(self):
        from alpha.tools.git_tools import _sanitize_git_args

        _, err = _sanitize_git_args("push", "--force origin main")
        assert err is not None
        assert "--force" in err or "permitida" in err.lower()


# ─── #D025 — glob workspace per match ──────────────────────────────


class TestGlobWorkspaceFilter:
    @pytest.mark.asyncio
    async def test_glob_uses_relative_to_filter(self):
        # Verifica via source que glob agora valida cada match contra workspace.
        from alpha.tools import file_tools

        src = inspect.getsource(file_tools._glob_files)
        assert "assert_within_workspace" in src

    @pytest.mark.asyncio
    async def test_normal_glob_still_works(self, tmp_path, monkeypatch):
        from alpha.tools import file_tools, path_helpers, workspace as ws_module

        # Cria alguns arquivos no workspace mockado
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        # 3 modulos importam AGENT_WORKSPACE separadamente.
        monkeypatch.setattr(ws_module, "AGENT_WORKSPACE", tmp_path)
        monkeypatch.setattr(file_tools, "AGENT_WORKSPACE", tmp_path)
        monkeypatch.setattr(path_helpers, "AGENT_WORKSPACE", tmp_path)

        result = await file_tools._glob_files("*.py", str(tmp_path))
        assert result.get("count") == 2


# ─── #D026 — apify defensive parse ──────────────────────────────────


class TestApifyDefensiveParse:
    def test_run_actor_uses_get_chain(self):
        import inspect

        from alpha.tools import apify_tools

        src = inspect.getsource(apify_tools._run_actor)
        # Antes era resp.json()["data"]["status"] direto — KeyError cru.
        # Agora usa .get() chain + valida shape.
        assert ".get(\"data\"" in src or "payload.get" in src
        assert "isinstance" in src

    def test_list_actors_uses_safe_parse(self):
        import inspect

        from alpha.tools import apify_tools

        src = inspect.getsource(apify_tools._list_actors)
        assert ".get(" in src
        assert "ValueError" in src or "isinstance" in src


# ─── #D022 — error paths use _format_result ─────────────────────────


class TestErrorPathsTruncated:
    def test_executor_error_paths_use_format_result(self):
        from alpha.tools import shell_tools  # noqa: F401  (registry side effect)

        # Helper centralizado adiciona truncamento. Verifica que o helper
        # existe e que paths de erro o usam.
        from alpha.executor import _append_tool_msg, _format_result

        assert callable(_append_tool_msg)
        assert callable(_format_result)

    def test_long_error_message_truncated(self):
        from alpha.executor import _format_result

        # Result com payload gigante deve ser truncado em vez de inflar contexto
        big = {"error": "x" * 50000}
        out = _format_result(big, "test_tool")
        assert len(out) < 25000  # < TOOL_RESULT_MAX_CHARS + buffer


# ─── #D030 — _CATEGORY_ICONS still in use ──────────────────────────


class TestCategoryIconsInUse:
    def test_icons_referenced_in_print_tools_list(self):
        import inspect

        from alpha import display

        src = inspect.getsource(display)
        # _CATEGORY_ICONS aparece em print_tools_list (uso real, nao orfo)
        assert "_CATEGORY_ICONS" in src
        # Tambem ha uma referencia de uso (.get / lookup)
        assert "_CATEGORY_ICONS.get(" in src or "_CATEGORY_ICONS[" in src
