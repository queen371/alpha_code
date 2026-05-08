"""Quick-wins from PROXIMO SPRINT Opcao A.

Cobre:
- #D009 — composite_tools imports promovidos para topo
- #DM015 — wizard usa yaml.safe_dump (escapa values com `:`/aspas)
- #021/#115 — .env perms 0o600 + write atomico
- #026/#076 — llm.py com httpx client compartilhado
- #D008 PERF — network_tools com aiohttp session compartilhada
- #025/#026 SEC — pytest pinned >= 9.0.2
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest


# ─── #D009 — composite_tools imports ──────────────────────────────


class TestCompositeImportsAtTop:
    def test_no_inline_imports_in_run_tool(self):
        """Executor imports now live in _composite_helpers.py (post #030 split)."""
        import inspect

        from alpha.tools import _composite_helpers

        src = inspect.getsource(_composite_helpers)
        top_block, _, body = src.partition("\nasync def _run_tool")
        assert "from ..executor import" in top_block

    def test_no_inline_executor_import_in_function_body(self):
        """Executor import exists exactly once in _composite_helpers (not inline)."""
        import inspect

        from alpha.tools import _composite_helpers

        src = inspect.getsource(_composite_helpers)
        assert src.count("from ..executor import") == 1


# ─── #DM015 — yaml.safe_dump no wizard ─────────────────────────────


class TestWizardYamlSafeDump:
    def test_uses_yaml_safe_dump(self):
        from alpha.wizard import steps

        src = inspect.getsource(steps.step_create_agent)
        assert "yaml.safe_dump" in src

    def test_serialization_quotes_special_chars(self):
        """Description com `:` ou aspas DEVE ser escapada."""
        import yaml

        # Reproduz o payload que step_create_agent montaria para uma
        # description "agressiva" com caracteres que quebrariam f-string.
        payload = {
            "name": "test",
            "description": "Use when: handle 'edge' cases",
            "model": {"provider": "openai", "id": "gpt-4o"},
        }
        out = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        # Round-trip deve dar o mesmo dict
        assert yaml.safe_load(out) == payload


# ─── #021/#115 — .env perms + atomic write ─────────────────────────


class TestEnvWritePerms:
    def test_env_module_uses_atomic_write(self):
        from alpha.wizard import env

        src = inspect.getsource(env.write_env)
        # tempfile.mkstemp + os.replace = atomico em POSIX
        assert "tempfile.mkstemp" in src
        assert "os.replace" in src

    def test_env_mode_constant_is_0o600(self):
        from alpha.wizard import env

        assert env._ENV_FILE_MODE == 0o600

    def test_write_env_creates_0o600_file(self, tmp_path, monkeypatch):
        """Test funcional: write_env cria arquivo com perms 0o600."""
        from alpha.wizard import env

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env, "ENV_PATH", env_path)
        monkeypatch.setattr(env, "_PROJECT_ROOT", tmp_path)

        result = env.write_env({"FOO": "bar"})
        assert result == env_path
        assert env_path.exists()

        mode = env_path.stat().st_mode & 0o777
        assert mode == 0o600, f"Esperado 0o600, got {oct(mode)}"
        assert env_path.read_text() == "FOO=bar\n"

    def test_write_env_is_atomic_no_partial_on_crash(
        self, tmp_path, monkeypatch
    ):
        """Se o write falhar no meio, o .env original nao deve ser tocado."""
        from alpha.wizard import env

        env_path = tmp_path / ".env"
        env_path.write_text("OLD_KEY=keep_me\n")
        env_path.chmod(0o600)
        monkeypatch.setattr(env, "ENV_PATH", env_path)
        monkeypatch.setattr(env, "_PROJECT_ROOT", tmp_path)

        # Simula crash apos escrever no tmp mas antes do replace.
        original_replace = os.replace

        def boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)

        with pytest.raises(OSError):
            env.write_env({"FOO": "bar"})

        # Original deve continuar intacto
        assert env_path.read_text() == "OLD_KEY=keep_me\n"

        # Nenhum tmp .env.* deve ter sobrado
        leftovers = list(tmp_path.glob(".env.*"))
        assert leftovers == [], f"tempfiles vazaram: {leftovers}"

        # Restaurar e fazer write valido
        monkeypatch.setattr(os, "replace", original_replace)
        env.write_env({"FOO": "bar"})
        assert "FOO=bar" in env_path.read_text()


# ─── #026/#076 — shared httpx LLM client ────────────────────────────


class TestSharedLLMClient:
    def test_get_shared_client_function_exists(self):
        from alpha import llm

        assert callable(llm._get_shared_llm_client)
        # Globals iniciam zerados; o Lazy faz init.
        assert hasattr(llm, "_shared_llm_client")
        assert hasattr(llm, "_llm_client_loop")

    def test_no_per_call_AsyncClient_in_stream(self):
        """A funcao stream_chat_with_tools NAO deve mais criar AsyncClient
        inline a cada attempt — deve usar o helper compartilhado."""
        from alpha import llm

        src = inspect.getsource(llm.stream_chat_with_tools)
        # Antes: `async with httpx.AsyncClient(...) as client:`. Agora:
        # `client = await _get_shared_llm_client()`.
        assert "_get_shared_llm_client()" in src
        assert "httpx.AsyncClient(" not in src


# ─── #D008 PERF — shared aiohttp session ───────────────────────────


class TestSharedAiohttpSession:
    def test_get_shared_session_function_exists(self):
        from alpha.tools import network_tools

        assert callable(network_tools._get_shared_aiohttp_session)
        assert hasattr(network_tools, "_shared_aiohttp_session")
        assert hasattr(network_tools, "_aiohttp_session_loop")

    def test_no_per_call_session_in_http_request(self):
        from alpha.tools import network_tools

        src = inspect.getsource(network_tools._http_request)
        # Antes: `async with aiohttp.ClientSession() as session:`.
        # Agora: `session = await _get_shared_aiohttp_session()`.
        assert "_get_shared_aiohttp_session()" in src
        assert "aiohttp.ClientSession()" not in src


# ─── #025/#026 SEC — pytest pin ─────────────────────────────────────


class TestPytestPinned:
    def test_pytest_pinned_at_least_9_0(self):
        text = (Path(__file__).parent.parent / "pyproject.toml").read_text()
        # Forma nao depende de tooling — apenas conferimos que NAO ha
        # mais `pytest>=8.0` (o pin antigo). >= 9.0.x ou >= 9.1 ambos
        # passam.
        assert "pytest>=8.0" not in text
        assert "pytest>=9" in text
