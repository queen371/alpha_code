"""Regression tests for DEEP_PERFORMANCE V1.0 stragglers.

Cobre:
- #D006 — git regex pre-compilada module-level
- #D007 — list_sessions partial-read otimizacao
- #D009 — DDGS instancia compartilhada
- #D010 — composite rglob unificado
- #D012 — apify shared httpx client
- #D020 — HARD_BLOCKED regex combinada (single search)
- #D025 — save_session JSON compacto + cleanup probabilistico
- #027/#072 — glob_files iterator + skip noise dirs + sort subset
"""

import inspect
import json
from pathlib import Path

import pytest


# ─── #D006 — git regex module-level ────────────────────────────────


class TestGitRegexModuleLevel:
    def test_dangerous_fmt_at_module_scope(self):
        from alpha.tools import git_tools

        assert hasattr(git_tools, "_DANGEROUS_GIT_FMT")

    def test_sanitize_does_not_recompile(self):
        import inspect

        from alpha.tools.git_tools import _sanitize_git_args

        src = inspect.getsource(_sanitize_git_args)
        # Antes tinha `import re as _re` + `_re.compile(...)` dentro da
        # funcao. Apos #D006 nao deve ter recompilacao inline.
        assert "_re.compile" not in src
        assert "import re as _re" not in src
        assert "_DANGEROUS_GIT_FMT" in src

    def test_dangerous_format_still_blocked(self):
        from alpha.tools.git_tools import _sanitize_git_args

        _, err = _sanitize_git_args("log", "--format=%(if)evil%(end)")
        assert err is not None


# ─── #D009 — DDGS shared instance ──────────────────────────────────


class TestDDGSShared:
    def test_get_ddgs_returns_same_instance(self):
        from alpha.web_search import _get_ddgs

        a = _get_ddgs()
        b = _get_ddgs()
        assert a is b


# ─── #D025 — save_session compact JSON ─────────────────────────────


class TestSaveSessionCompact:
    def test_session_file_no_indent(self, tmp_path, monkeypatch):
        from alpha import history

        monkeypatch.setattr(history, "_HISTORY_DIR", tmp_path)
        path = history.save_session(
            "test_compact",
            [{"role": "user", "content": "hello"}],
        )
        text = path.read_text()
        # Sem indent=2 a saida nao tem newlines em meio a estrutura
        assert "\n  " not in text  # 2-space indent ausente

    def test_cleanup_is_probabilistic(self):
        import inspect

        from alpha import history

        src = inspect.getsource(history.save_session)
        # randbelow(10) == 0 → ~10% das chamadas roda cleanup
        assert "randbelow" in src or "random" in src


# ─── #D007 — list_sessions partial read ────────────────────────────


class TestListSessionsPartialRead:
    def test_uses_partial_read(self):
        import inspect

        from alpha.history import list_sessions

        src = inspect.getsource(list_sessions)
        # Lemos apenas chunk inicial e fallback completo se parse falha
        assert "_PARTIAL_BYTES" in src or "8192" in src

    def test_works_with_large_session(self, tmp_path, monkeypatch):
        from alpha import history

        monkeypatch.setattr(history, "_HISTORY_DIR", tmp_path)

        # Cria sessao grande (> 8KB) — deve cair no fallback de read completo
        big_messages = [{"role": "user", "content": "x" * 200}] * 100
        history.save_session("big", big_messages)

        sessions = history.list_sessions(limit=10)
        assert len(sessions) >= 1
        assert any(s["session_id"] == "big" for s in sessions)


# ─── #D010 — composite rglob unified ───────────────────────────────


class TestCompositeRglobUnified:
    def test_single_rglob_in_test_detection(self):
        import inspect

        from alpha.tools import composite_tools

        src = inspect.getsource(composite_tools)
        # Antes: rglob("test_*.py") + rglob("*_test.py") = duas iteracoes
        # Agora: rglob("*.py") + filter por nome = uma so
        idx = src.find("rglob(\"test_*.py\")")
        assert idx == -1


# ─── #D012 — apify shared client ───────────────────────────────────


class TestApifySharedClient:
    def test_get_apify_client_returns_same(self):
        import asyncio

        from alpha.tools import apify_tools

        # Module-level client + lazy init
        assert hasattr(apify_tools, "_get_apify_client")
        # Sem loop ativo, get sem comparacao retorna o mesmo
        c1 = apify_tools._get_apify_client()
        c2 = apify_tools._get_apify_client()
        assert c1 is c2

    def test_no_async_with_in_run_actor(self):
        import inspect

        from alpha.tools import apify_tools

        src = inspect.getsource(apify_tools._run_actor)
        # Antes: `async with httpx.AsyncClient(...) as client:` dentro da
        # funcao. Agora: client compartilhado via _get_apify_client.
        assert "async with httpx.AsyncClient" not in src
        assert "_get_apify_client" in src


# ─── #D020 — HARD_BLOCKED combined regex ───────────────────────────


class TestHardBlockedCombinedRegex:
    def test_combined_regex_exists(self):
        from alpha.tools import shell_tools

        assert hasattr(shell_tools, "HARD_BLOCKED_RE")
        # Continua valida — fork bomb e padrao inequivocamente perigoso
        assert shell_tools.HARD_BLOCKED_RE.search(":(){:|:&};:")

    def test_validate_uses_single_search(self):
        import inspect

        from alpha.tools.shell_tools import _validate_command

        src = inspect.getsource(_validate_command)
        # Antes: `for pattern in HARD_BLOCKED: if pattern.search(...)`.
        # Agora: `if HARD_BLOCKED_RE.search(command):`
        assert "HARD_BLOCKED_RE.search" in src
        assert "for pattern in HARD_BLOCKED" not in src

    def test_dangerous_commands_still_blocked(self):
        from alpha.tools.shell_tools import _validate_command

        # Casos que devem continuar bloqueados
        for cmd in ("rm -rf /", "mkfs.ext4 /dev/sda", "shutdown -h now"):
            assert _validate_command(cmd) is not None, f"Expected block: {cmd}"

    def test_safe_commands_still_pass(self):
        from alpha.tools.shell_tools import _validate_command

        for cmd in ("echo hello", "ls -la", "git status", "python -c 'print(1)'"):
            assert _validate_command(cmd) is None, f"Expected allow: {cmd}"


# ─── #027/#072 — glob_files iterator + skip ────────────────────────


class TestGlobFilesIterator:
    def test_skip_noise_dirs(self):
        import inspect

        from alpha.tools import file_tools

        src = inspect.getsource(file_tools._glob_files)
        # Skip dirs comuns de monorepos
        assert ".git" in src or "_SKIP_DIRS" in src
        assert "node_modules" in src

    @pytest.mark.asyncio
    async def test_glob_skips_node_modules(self, tmp_path, monkeypatch):
        from alpha.tools import file_tools, path_helpers, workspace as ws_module

        # Estrutura: workspace/{src/a.py, node_modules/foo.js, .git/HEAD}
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "foo.py").write_text("")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "x.py").write_text("")

        monkeypatch.setattr(ws_module, "AGENT_WORKSPACE", tmp_path)
        monkeypatch.setattr(file_tools, "AGENT_WORKSPACE", tmp_path)
        monkeypatch.setattr(path_helpers, "AGENT_WORKSPACE", tmp_path)

        result = await file_tools._glob_files("**/*.py", str(tmp_path))
        paths = [m["path"] for m in result["matches"]]
        rel_paths = [str(Path(p).relative_to(tmp_path)) for p in paths]
        # Deve achar src/a.py mas nao node_modules ou .git
        assert any("a.py" in p for p in rel_paths)
        assert not any("node_modules" in p for p in rel_paths)
        assert not any(p.startswith(".git") for p in rel_paths)
