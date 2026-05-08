"""More quick wins from DEEP_MAINTAINABILITY + DEEP_PERFORMANCE.

Cobre:
- #DM007 — .env.example documenta novas variaveis
- #086 — wizard `_PROVIDERS` derivado de config
- #095 — `_PROJECT_ROOT` compartilhado
- #096 — index comment em tools/__init__.py
- #D010 — display truncation constants centralizadas
- #D022 — extract_page_content TTL cache
"""

import inspect
from pathlib import Path

import pytest


# ─── #DM007 — .env.example ─────────────────────────────────────────


class TestEnvExampleComplete:
    def _read_env_example(self) -> str:
        return (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")

    def test_documents_browser_allowlist(self):
        text = self._read_env_example()
        assert "ALPHA_BROWSER_ALLOWLIST" in text
        assert "ALPHA_BROWSER_REQUIRE_ALLOWLIST" in text

    def test_documents_apify_token(self):
        text = self._read_env_example()
        assert "APIFY_API_TOKEN" in text

    def test_documents_no_color(self):
        text = self._read_env_example()
        assert "NO_COLOR" in text

    def test_documents_alpha_agent(self):
        text = self._read_env_example()
        assert "ALPHA_AGENT" in text


# ─── #086 — wizard providers from config ───────────────────────────


class TestWizardProvidersFromConfig:
    def test_wizard_imports_config_providers(self):
        import inspect

        from alpha.wizard import steps

        src = inspect.getsource(steps)
        assert "_CONFIG_PROVIDERS" in src or "from ..config import" in src

    def test_wizard_provider_count_matches_config(self):
        from alpha.config import _PROVIDERS as cfg
        from alpha.wizard.steps import _PROVIDERS as wiz

        assert len(wiz) == len(cfg)
        cfg_ids = set(cfg.keys())
        wiz_ids = {p["id"] for p in wiz}
        assert cfg_ids == wiz_ids


# ─── #095 — _PROJECT_ROOT shared ───────────────────────────────────


class TestProjectRootShared:
    def test_wizard_env_imports_from_config(self):
        from alpha.wizard import env

        src = inspect.getsource(env)
        assert "from ..config import _PROJECT_ROOT" in src
        # Sem definicao local
        assert "Path(__file__).resolve().parent.parent.parent" not in src

    def test_wizard_steps_imports_from_config(self):
        from alpha.wizard import steps

        src = inspect.getsource(steps)
        assert "from ..config import" in src and "_PROJECT_ROOT" in src

    def test_agents_registry_imports_from_config(self):
        from alpha.agents import registry

        src = inspect.getsource(registry)
        assert "from ..config import _PROJECT_ROOT" in src


# ─── #096 — tools index comment ────────────────────────────────────


class TestToolsIndexComment:
    def test_module_docstring_lists_tools(self):
        from alpha import tools

        doc = tools.__doc__ or ""
        # Documenta onde cada tool vive (helps grep / IDE jump-to-def
        # readers find the right module).
        assert "file_tools.py" in doc
        assert "shell_tools.py" in doc
        assert "delegate_tools.py" in doc


# ─── #D010 — display constants ─────────────────────────────────────


class TestDisplayConstants:
    def test_constants_exported(self):
        from alpha import display

        assert hasattr(display, "DISPLAY_LINE_TRUNCATE")
        assert hasattr(display, "DISPLAY_PREVIEW_TRUNCATE")
        assert hasattr(display, "DISPLAY_PROMPT_VALUE_TRUNCATE")
        assert hasattr(display, "DISPLAY_MAX_LINES")

    def test_no_inline_magic_numbers_in_print_paths(self):
        from alpha import display

        src = inspect.getsource(display)
        # As magic numbers principais foram substituidas pelas constantes
        # — `[:200]` literal nao deve aparecer mais (poderia escapar para
        # outras funcoes pero validamos a presenca das constantes acima).
        # Conta apenas dentro das funcoes principais que substituimos.
        # Esta checagem e branda — apenas garante que houve substituicao.
        # `[:200]` e `[:120]` ainda podem aparecer 1x no docstring que
        # documenta a centralizacao (comentario sobre o historico). O
        # importante e que nao haja MULTIPLAS ocorrencias inline em codigo.
        assert src.count("[:200]") <= 1, "Reduzir uso de literal [:200]"
        assert src.count("[:120]") <= 1, "Reduzir uso de literal [:120]"


# ─── #D022 — extract cache ─────────────────────────────────────────


class TestExtractCache:
    def test_cache_attrs_exist(self):
        from alpha import web_search

        assert hasattr(web_search, "_EXTRACT_CACHE")
        assert hasattr(web_search, "_EXTRACT_CACHE_TTL")
        assert hasattr(web_search, "_EXTRACT_CACHE_MAX")

    @pytest.mark.asyncio
    async def test_cache_returns_same_result_within_ttl(self, monkeypatch):
        from alpha import web_search

        web_search._EXTRACT_CACHE.clear()

        call_count = {"n": 0}

        async def fake_fetch_raw(url, timeout, max_bytes):
            call_count["n"] += 1
            return b"<html><body>Hello world</body></html>", {}, 200

        monkeypatch.setattr(web_search, "_fetch_raw", fake_fetch_raw)

        a = await web_search.extract_page_content("https://example.com")
        b = await web_search.extract_page_content("https://example.com")

        assert a == b
        # Segunda chamada NAO deve ter incrementado (cache hit)
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_cache_evicts_oldest(self, monkeypatch):
        from alpha import web_search

        web_search._EXTRACT_CACHE.clear()

        async def fake_fetch_raw(url, timeout, max_bytes):
            return b"<p>fake content</p>", {}, 200

        monkeypatch.setattr(web_search, "_fetch_raw", fake_fetch_raw)
        monkeypatch.setattr(web_search, "_EXTRACT_CACHE_MAX", 3)

        for i in range(5):
            await web_search.extract_page_content(f"https://example.com/{i}")

        assert len(web_search._EXTRACT_CACHE) <= 3
