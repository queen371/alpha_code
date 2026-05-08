"""Regression tests for DEEP_MAINTAINABILITY + DEEP_PERFORMANCE quick wins.

Cobre:
- #DM004 — `is_private_ip_address` import morto removido
- #DM005 — urllib imports no topo (não inline)
- #DM014 — `script_path = None` antes do try em code_tools
- #D012 (V1.0 MAINT) — `ALPHA_FEATURES` alias removido
- #083 — dedup ip/route em SAFE_SHELL_COMMANDS
- #085 — loop-detection consts em config.LOOP_DETECTION
- #090 — shlex.quote em _run_tests cmd
- #091 — comentario de _safe_get_tool atualizado
- #093 — analyze_tools.py com docstring
- #094 — bin/alpha mensagem corrigida
- #097 — MAX_ITERATIONS em LIMITS dict
- #D013 (V1.0 PERF) — _validate_pg_ssrf helper extraido
- #D005 (V1.0 PERF) — fuzzy resolve cache
"""

import inspect
from pathlib import Path

import pytest


# ─── #DM004 — dead import removed ──────────────────────────────────


class TestIsPrivateIpDeadImportRemoved:
    def test_no_alias_in_network_tools(self):
        from alpha.tools import network_tools

        assert not hasattr(network_tools, "_is_private_ip_address")


# ─── #DM005 — urllib imports at top ────────────────────────────────


class TestUrllibImportsAtTop:
    def test_no_inline_urllib_imports(self):
        from alpha.tools import network_tools

        src = inspect.getsource(network_tools)
        # Antes tinha `import urllib.request as _urllib_request` em meio
        # ao modulo + `import urllib.error/import urllib.request` dentro
        # de `_http_request_urllib`. Agora ambos no topo.
        assert "import urllib.request as _urllib_request" not in src
        # urllib.request ainda eh usado no top-level (subclass) e funcao
        assert "urllib.request.HTTPRedirectHandler" in src or "urllib.request.build_opener" in src


# ─── #DM014 — script_path init ─────────────────────────────────────


class TestScriptPathInit:
    def test_init_before_try(self):
        from alpha.tools import code_tools

        src = inspect.getsource(code_tools._execute_python)
        # Init `script_path: str | None = None` antes do try, e finally
        # checa `if script_path is not None` antes de unlink.
        assert "script_path: str | None = None" in src
        assert "if script_path is not None" in src


# ─── #D012 (V1.0 MAINT) — ALPHA_FEATURES removed ───────────────────


class TestAlphaFeaturesAliasRemoved:
    def test_alias_no_longer_exported(self):
        from alpha import config

        assert not hasattr(config, "ALPHA_FEATURES")
        assert hasattr(config, "FEATURES")  # original ainda existe


# ─── #083 — dedup ip/route ─────────────────────────────────────────


class TestSafeShellCommandsDedupe:
    def test_no_duplicate_in_safe_shell_commands(self):
        from alpha import approval

        src = inspect.getsource(approval)
        # Pega regiao SAFE_SHELL_COMMANDS — termina no `}\n)` que fecha
        # a frozenset.
        start = src.find("SAFE_SHELL_COMMANDS = frozenset(")
        end = src.find("\n)", start)
        region = src[start:end]
        assert region.count('"ip"') == 1, f"`ip` duplicado em {region!r}"
        assert region.count('"route"') == 1, f"`route` duplicado em {region!r}"


# ─── #085 — loop detection consts in config ────────────────────────


class TestLoopDetectionInConfig:
    def test_config_has_loop_detection_dict(self):
        from alpha.config import LOOP_DETECTION

        assert "max_repeat_calls" in LOOP_DETECTION
        assert "similarity_threshold" in LOOP_DETECTION
        assert "min_iter" in LOOP_DETECTION

    def test_agent_reads_from_config(self):
        from alpha import agent

        src = inspect.getsource(agent)
        assert "LOOP_DETECTION" in src

    def test_consts_match_config_values(self):
        from alpha.agent import (
            _CYCLE_WINDOW,
            _MAX_REPEAT_CALLS,
            _SIMILAR_REPEAT_CALLS,
            _SIMILARITY_THRESHOLD,
            _STALE_WINDOW,
        )
        from alpha.config import LOOP_DETECTION

        assert _MAX_REPEAT_CALLS == LOOP_DETECTION["max_repeat_calls"]
        assert _SIMILAR_REPEAT_CALLS == LOOP_DETECTION["similar_repeat_calls"]
        assert _SIMILARITY_THRESHOLD == LOOP_DETECTION["similarity_threshold"]
        assert _CYCLE_WINDOW == LOOP_DETECTION["cycle_window"]
        assert _STALE_WINDOW == LOOP_DETECTION["stale_window"]


# ─── #090 — shlex.quote em _run_tests ──────────────────────────────


class TestRunTestsShlexQuote:
    def test_pattern_uses_shlex_quote(self):
        from alpha.tools import composite_tools

        src = inspect.getsource(composite_tools._run_tests)
        # Antes: f" -k '{pattern}'" — quebra com aspas
        # Agora: shlex.quote
        assert "shlex.quote" in src or "_shlex.quote" in src


# ─── #091 — _safe_get_tool comment ─────────────────────────────────


class TestSafeGetToolCommentUpdated:
    def test_comment_explains_policy(self):
        from alpha.tools import delegate_tools

        src = inspect.getsource(delegate_tools._run_subagent)
        # Comentario antigo: "Safe get_tool that blocks disallowed tools"
        # — vago. Agora menciona explicitamente blocklist + anti-recursao
        # + tools_filter.
        assert "anti-recursao" in src or "blocklist" in src
        assert "tools_filter" in src


# ─── #093 — analyze_tools docstring ────────────────────────────────
# Removido: `analyze_tools.py` foi apagado (script ad-hoc da raiz que
# nao era importado nem listado em pyproject.toml; lixo de fase de
# desenvolvimento).


# ─── #094 — bin/alpha message ──────────────────────────────────────


class TestBinAlphaMessage:
    def test_no_requirements_txt_reference(self):
        bin_alpha = Path(__file__).parent.parent / "bin" / "alpha"
        text = bin_alpha.read_text(encoding="utf-8")
        # requirements.txt nao existe no projeto; mensagem mencionava
        # esse arquivo, agora menciona `pip install -e .`
        assert "requirements.txt" not in text
        assert "pip install -e ." in text


# ─── #097 — LIMITS dict ────────────────────────────────────────────


class TestLimitsDict:
    def test_config_has_limits_dict(self):
        from alpha.config import LIMITS

        assert "max_iterations" in LIMITS
        assert LIMITS["max_iterations"] == 50

    def test_subagent_max_iterations_lives_in_features_only(self):
        # `subagent_max_iterations` antes existia tambem em LIMITS, mas nada
        # lia de la — codigo le sempre de FEATURES (vide delegate_tools.py).
        # Manter dois lugares era drift garantido.
        from alpha.config import FEATURES, LIMITS
        assert "subagent_max_iterations" not in LIMITS
        assert "subagent_max_iterations" in FEATURES

    def test_legacy_aliases_match(self):
        from alpha.config import LIMITS, LLM_TIMEOUT, MAX_ITERATIONS, TOOL_RESULT_MAX_CHARS

        assert MAX_ITERATIONS == LIMITS["max_iterations"]
        assert TOOL_RESULT_MAX_CHARS == LIMITS["tool_result_max_chars"]
        assert LLM_TIMEOUT == LIMITS["llm_timeout"]


# ─── #D013 (V1.0 PERF) — PG SSRF helper ────────────────────────────


class TestValidatePgSsrf:
    def test_helper_exists(self):
        from alpha.tools import database_tools

        assert callable(database_tools._validate_pg_ssrf)

    async def test_blocks_private_ip(self):
        # AUDIT_V1.2 #002: helper agora e async (asyncio.to_thread em
        # _is_private_ip que faz socket.getaddrinfo bloqueante).
        from alpha.tools.database_tools import _validate_pg_ssrf

        result = await _validate_pg_ssrf("postgresql://user:pass@127.0.0.1/db")
        assert result is not None
        assert result.get("blocked") is True

    async def test_allows_public_host(self, monkeypatch):
        from alpha.tools import database_tools

        # Mock _is_private_ip para evitar dependencia de DNS no test
        monkeypatch.setattr(database_tools, "_is_private_ip", lambda h: False)
        result = await database_tools._validate_pg_ssrf(
            "postgresql://user:pass@db.example.com/db"
        )
        assert result is None

    async def test_does_not_block_event_loop(self, monkeypatch):
        """AUDIT_V1.2 #002: _is_private_ip e sync (socket.getaddrinfo).

        Antes da correcao, _validate_pg_ssrf chamava direto e travava o
        event loop ate o DNS responder (ate 5s). Apos `asyncio.to_thread`,
        outras tasks devem progredir em paralelo.
        """
        import asyncio
        import time
        from alpha.tools import database_tools

        # Simular DNS lento: 0.5s de bloqueio
        def slow_is_private_ip(hostname: str) -> bool:
            time.sleep(0.5)
            return False

        monkeypatch.setattr(database_tools, "_is_private_ip", slow_is_private_ip)

        # Outra task deve avancar enquanto _validate_pg_ssrf espera DNS
        progress_marker = []

        async def background_task():
            for i in range(5):
                await asyncio.sleep(0.05)
                progress_marker.append(i)

        bg = asyncio.create_task(background_task())
        result = await database_tools._validate_pg_ssrf(
            "postgresql://user:pass@example.com/db"
        )
        await bg

        assert result is None
        # Se loop foi bloqueado, progress_marker estaria vazio ou < 3
        assert len(progress_marker) == 5, (
            f"Event loop bloqueado durante DNS: progress={progress_marker}"
        )


# ─── #D005 (V1.0 PERF) — fuzzy cache ───────────────────────────────


class TestFuzzyResolveCache:
    def test_cache_module_attrs_exist(self):
        from alpha.tools import path_helpers

        assert hasattr(path_helpers, "_fuzzy_cache")
        assert hasattr(path_helpers, "_FUZZY_CACHE_SIZE")

    def test_uncached_function_separate(self):
        from alpha.tools import path_helpers

        # _fuzzy_resolve_uncached e a versao sem cache (para tests/forcar miss)
        assert callable(path_helpers._fuzzy_resolve_uncached)

    def test_cached_returns_same_result(self, monkeypatch):
        from alpha.tools import path_helpers

        # Reset cache
        path_helpers._fuzzy_cache.clear()
        path_helpers._fuzzy_cache_order.clear()

        call_count = {"n": 0}
        original = path_helpers._fuzzy_resolve_uncached

        def counting_uncached(p):
            call_count["n"] += 1
            return original(p)

        monkeypatch.setattr(path_helpers, "_fuzzy_resolve_uncached", counting_uncached)

        # Primeira chamada — miss
        path_helpers._fuzzy_resolve("/totally/nonexistent/path")
        assert call_count["n"] == 1
        # Segunda chamada com mesmo arg — hit
        path_helpers._fuzzy_resolve("/totally/nonexistent/path")
        assert call_count["n"] == 1  # nao incrementou
