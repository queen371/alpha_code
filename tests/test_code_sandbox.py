"""Regression tests for execute_python static-analysis sandbox.

Cobre DEEP_SECURITY #D101 — pickle.loads / marshal.loads / runpy bypassam
o blocklist se nao estiverem listados explicitamente. Falha em CI quando
alguem afrouxar o regex sem decisao consciente.
"""

import pytest

from alpha.tools.code_tools import _validate_code_safety


class TestBlockedDeserializationModules:
    """#D101: modulos de desserializacao/runtime que dao RCE em sandbox."""

    @pytest.mark.parametrize("code", [
        "import pickle",
        "from pickle import loads",
        "import marshal",
        "from marshal import loads",
        "import runpy",
        "from runpy import run_path",
        "import inspect",
        "from inspect import getframe",
        "import gc",
        "from gc import get_objects",
        "import platform",
        "from platform import node",
        "import dis",
        "from dis import Bytecode",
    ])
    def test_blocked(self, code):
        result = _validate_code_safety(code)
        assert result is not None, f"Expected block: {code!r}"
        assert "bloqueado" in result.lower() or "blocked" in result.lower()


class TestStillBlocksKnownDangerousModules:
    """Regression: modulos antigamente bloqueados continuam bloqueados."""

    @pytest.mark.parametrize("code", [
        "import os",
        "import subprocess",
        "import socket",
        "import urllib.request",
        "import httpx",
    ])
    def test_blocked(self, code):
        assert _validate_code_safety(code) is not None


class TestSafeCodeStillPasses:
    """Codigo sem dependencias perigosas continua passando."""

    @pytest.mark.parametrize("code", [
        "x = 1 + 1",
        "import math\nprint(math.sqrt(2))",
        "data = [1, 2, 3]\nprint(sum(data))",
        "from collections import Counter\nprint(Counter('hello'))",
    ])
    def test_passes(self, code):
        assert _validate_code_safety(code) is None
