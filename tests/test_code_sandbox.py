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


class TestAuditV12Bypasses:
    """AUDIT V1.2 #009: blocklist anterior cobria `os` e `subprocess` mas
    deixava as implementacoes low-level expostas. `import posix; posix.
    system("...")` virava RCE sem prompt porque execute_python esta em
    AUTO_APPROVE_TOOLS. Este test bloqueia toda a familia."""

    @pytest.mark.parametrize("code", [
        # The actual exploit reported by the audit.
        "import posix",
        "import posix; posix.system('id')",
        "from posix import system",
        # CPython subprocess uses _posixsubprocess.fork_exec for the heavy
        # lifting on POSIX. Importing it directly skips the subprocess
        # module wrapper entirely.
        "import _posixsubprocess",
        "from _posixsubprocess import fork_exec",
        # Windows equivalents — same shape, same risk.
        "import nt",
        "import _winapi",
        "import msvcrt",
        # Memory mapping — can read /proc/self/mem on Linux to dump
        # process memory including secrets.
        "import mmap",
        "from mmap import ACCESS_READ",
        # File-control / TTY primitives.
        "import fcntl",
        "import termios",
        "import tty",
        # Direct socket access through the C extension bypasses `socket`
        # being blocked by name (audit #018 V1.2 follow-up).
        "import _socket",
        # builtins module exposes eval/exec/__import__ even when the
        # bare names are blocked.
        "import builtins",
        "from builtins import eval",
        # Pickle's C accelerator.
        "import _pickle",
        # Threading lets you spawn workers that execute code outside the
        # main interpreter loop.
        "import _thread",
        "import threading",
        # ctypes private name was missed by the original list.
        "import _ctypes",
    ])
    def test_low_level_modules_blocked(self, code):
        result = _validate_code_safety(code)
        assert result is not None, f"Expected block: {code!r}"


class TestAuditV12ReflectionBypasses:
    """AUDIT V1.2 #012: dunder access that lets a payload reach blocked
    callables indirectly."""

    @pytest.mark.parametrize("code", [
        # __getattribute__ as gadget to fish out a class's bases.
        "x = ().__getattribute__('__class__')",
        "y = ''.__getattribute__('__class__').__bases__",
        # __getattr__ same idea.
        "obj.__getattr__('something')",
        # __code__ / __closure__ / __globals__ on a function let a caller
        # rebuild the function with new bytecode.
        "f.__code__",
        "f.__closure__",
        "f.__globals__",
        # delattr/setattr via builtin call — both newly added.
        "setattr(x, 'y', 1)",
        "delattr(x, 'y')",
        "locals()",
        "input('prompt')",
    ])
    def test_reflection_blocked(self, code):
        result = _validate_code_safety(code)
        assert result is not None, f"Expected block: {code!r}"


class TestSafeCodeStillPassesAfterV12:
    """Regression: blocklist expansion did not break legitimate code that
    happens to use common builtins or modules."""

    @pytest.mark.parametrize("code", [
        "x = list(range(10))",
        "from dataclasses import dataclass\n@dataclass\nclass A: x: int",
        "from typing import Optional\nfrom collections import defaultdict",
        "import json\nprint(json.dumps({'a': 1}))",
        "import re\nm = re.match(r'\\d+', 'abc123')",
        "import math, statistics\nprint(math.pi, statistics.mean([1,2,3]))",
        # __class__ access alone (without chaining to bases/subclasses) is
        # ubiquitous and not a real escape — should still pass.
        "type(x).__name__",
    ])
    def test_passes(self, code):
        assert _validate_code_safety(code) is None, (
            f"Legitimate code blocked: {code!r}"
        )
