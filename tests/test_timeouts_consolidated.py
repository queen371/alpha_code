"""Coverage para consolidacao de TOOL_TIMEOUTS (#D003 V1.0 MAINT).

Antes:
- `executor.TOOL_EXECUTION_TIMEOUT=120` e `_SLOW_TOOL_TIMEOUT=300` viviam
  em `executor.py` (independentes de config).
- Hard caps inline: shell_tools=300, code_tools=60, network_tools=60,
  pipeline_tools=120 — divergentes da config.

Agora:
- `config.TOOL_EXECUTION_TIMEOUT`, `config.SLOW_TOOL_TIMEOUT` sao a fonte.
- `config.TOOL_TIMEOUT_CAPS` substitui os literais inline.
- `executor.py` re-exporta por retro-compat (composite_tools, tests).
"""

from __future__ import annotations

import inspect


class TestConfigCentralization:
    def test_executor_timeouts_in_config(self):
        from alpha import config

        assert hasattr(config, "TOOL_EXECUTION_TIMEOUT")
        assert hasattr(config, "SLOW_TOOL_TIMEOUT")
        assert config.TOOL_EXECUTION_TIMEOUT == 120
        assert config.SLOW_TOOL_TIMEOUT == 300

    def test_caps_in_config(self):
        from alpha.config import TOOL_TIMEOUT_CAPS

        # Todas as categorias com hard cap conhecida
        assert TOOL_TIMEOUT_CAPS["shell"] == 300
        assert TOOL_TIMEOUT_CAPS["code"] == 60
        assert TOOL_TIMEOUT_CAPS["network"] == 60
        assert TOOL_TIMEOUT_CAPS["pipeline"] == 120

    def test_executor_reexports_for_compat(self):
        """`composite_tools` e tests legacy importam de executor — manter."""
        from alpha import executor

        assert executor.TOOL_EXECUTION_TIMEOUT == 120
        assert executor._SLOW_TOOL_TIMEOUT == 300


class TestToolFilesUseConfigCaps:
    def test_shell_tools_uses_config_cap(self):
        from alpha.tools import shell_tools

        # Localiza o body de _execute_shell e checa que NAO tem `min(timeout, 300)` literal
        src = inspect.getsource(shell_tools)
        # O literal antigo nao deve mais aparecer
        assert "min(timeout, 300)" not in src
        # Deve usar TOOL_TIMEOUT_CAPS
        assert "TOOL_TIMEOUT_CAPS" in src

    def test_code_tools_uses_config_cap(self):
        from alpha.tools import code_tools

        src = inspect.getsource(code_tools._execute_python)
        assert "min(timeout, 60)" not in src
        assert "TOOL_TIMEOUT_CAPS" in src

    def test_network_tools_uses_config_cap(self):
        from alpha.tools import network_tools

        src = inspect.getsource(network_tools._http_request)
        # `min(timeout, 60)` literal nao deve mais existir
        assert "min(timeout, 60)" not in src
        assert "TOOL_TIMEOUT_CAPS" in src

    def test_pipeline_tools_uses_config_cap(self):
        from alpha.tools import pipeline_tools

        src = inspect.getsource(pipeline_tools)
        assert "min(timeout, 120)" not in src
        assert "TOOL_TIMEOUT_CAPS" in src


class TestExecutorReadFromConfig:
    def test_executor_imports_from_config(self):
        from alpha import executor

        src = inspect.getsource(executor)
        # O executor agora le config; nao tem mais `TOOL_EXECUTION_TIMEOUT = 120` hardcoded
        assert "TOOL_EXECUTION_TIMEOUT = 120" not in src
        assert "_SLOW_TOOL_TIMEOUT = 300" not in src
        assert "from .config import" in src
        assert "TOOL_EXECUTION_TIMEOUT" in src
