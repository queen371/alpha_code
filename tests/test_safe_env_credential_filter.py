"""Regression tests for safe_env credential filter (AUDIT_V1.2 #011).

The filter removes secrets from os.environ before subprocess execution
(execute_shell, execute_python, install_package). The previous regex
missed `AWS_ACCESS_KEY_ID`, `GITHUB_PAT`, `OPENAI_KEY`, `KAGGLE_KEY`,
`BASIC_AUTH`, `AZURE_OPENAI_KEY`, `MY_DSN` — these tests fail loud if
anyone narrows the regex without consciously deciding to ship a leak.

Boundary nota: regex `\\b` treats `_` as a word char in Python, so
`\\bKEY\\b` does NOT match in `OPENAI_KEY` (no word boundary between
`_` and `K`). The filter uses `(?:^|_)KEY(?:_|$)` instead — explicit
underscore separators that match the typical SCREAMING_SNAKE env var.
"""

from __future__ import annotations

import os
import pytest

from alpha.tools import safe_env
from alpha.tools.safe_env import (
    _EXPLICIT_KEYS,
    _SENSITIVE_PATTERNS,
    get_safe_env,
    invalidate_safe_env_cache,
)


def _is_filtered(key: str) -> bool:
    """Mirrors the predicate in `_build_safe_env`."""
    return bool(_SENSITIVE_PATTERNS.search(key)) or key in _EXPLICIT_KEYS


class TestAuditV12CredentialLeaks:
    """AUDIT_V1.2 #011: vars que vazavam por gaps no regex anterior."""

    @pytest.mark.parametrize("var", [
        # Cloud providers — common shapes the original regex missed.
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GITHUB_PAT",
        "GH_TOKEN",
        "GH_KEY",
        "OPENAI_KEY",
        "OPENAI_API_KEY",
        "KAGGLE_KEY",
        "AZURE_OPENAI_KEY",
        "AZURE_API_KEY",
        # Generic auth — `_AUTH` suffix bug from `\b` boundary.
        "BASIC_AUTH",
        "BASIC_AUTH_TOKEN",
        # DSN suffix — same `\b` issue.
        "MY_DSN",
        "PG_DSN",
        # PyPI / NPM — no `_API_KEY` per se but provider prefix.
        "NPM_TOKEN",
        "PYPI_API_TOKEN",
        "PYPI_TOKEN",
        # SaaS providers covered by prefix list.
        "STRIPE_SECRET_KEY",
        "TWILIO_AUTH_TOKEN",
        "SENDGRID_API_KEY",
        "SLACK_BOT_TOKEN",
        "DISCORD_TOKEN",
        "SUPABASE_KEY",
        "CLOUDFLARE_API_TOKEN",
        "VERCEL_TOKEN",
        # MASTER_KEY/SECRET pattern.
        "MASTER_KEY",
        "MASTER_TOKEN",
        # Connection URLs.
        "DATABASE_URL",
        "REDIS_URL",
        "MONGO_URL",
        "POSTGRES_URL",
        "MYSQL_URL",
        "ANYTHING_URL",
        # Crypto.
        "PRIVATE_KEY",
        "PUBLIC_KEY",
        "SIGNING_KEY",
        "ENCRYPTION_KEY",
        # Passwords.
        "PASSWORD",
        "PASSWD",
        "DB_PASSWORD",
        # Tor / debug ports.
        "TOR_CONTROL_PORT",
    ])
    def test_var_is_filtered(self, var):
        assert _is_filtered(var), (
            f"{var!r} leaked through safe_env filter — "
            "this is the exact regression of AUDIT_V1.2 #011"
        )


class TestExplicitKeysFiltered:
    """Vars que nao casam o regex mas vazam por contexto."""

    @pytest.mark.parametrize("var", [
        "GOOGLE_APPLICATION_CREDENTIALS",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GPG_AGENT_INFO",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "KUBECONFIG",
        "NETRC",
    ])
    def test_explicit_key_filtered(self, var):
        assert _is_filtered(var)


class TestLegitimateVarsNotFiltered:
    """Regression: nao over-filtrar vars normais que tem KEY/TOKEN no nome."""

    @pytest.mark.parametrize("var", [
        # System / locale.
        "PATH", "HOME", "USER", "LOGNAME", "PWD",
        "LANG", "LC_ALL", "TERM", "SHELL",
        "XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY",
        # Toolchain.
        "PYTHONDONTWRITEBYTECODE", "PYTHONPATH",
        "NODE_ENV", "NODE_PATH", "GO_PATH", "CARGO_HOME",
        # False-positive substring traps.
        "MONKEY_KEYBOARD",       # KEY inside word — must not match.
        "KEYCHAIN_NAME",         # KEY at start of compound — not standalone token.
        "TOKENIZER",             # TOKEN as prefix of larger word.
        "NUMBER_OF_PROCESSORS",  # PROCESSORS contains nothing risky.
        "TERMINAL_EMULATOR",
        # Display / WM stuff that contains substrings.
        "GTK_THEME",
        "QT_QPA_PLATFORM",
    ])
    def test_var_passes(self, var):
        assert not _is_filtered(var), (
            f"{var!r} was over-filtered by safe_env — "
            "user code expecting this env var would break"
        )


class TestSafeEnvIntegration:
    """End-to-end: get_safe_env() actually strips and keeps right vars."""

    def setup_method(self):
        invalidate_safe_env_cache()

    def teardown_method(self):
        invalidate_safe_env_cache()

    def test_aws_keys_stripped(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA1234567890")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret/secret")
        monkeypatch.setenv("HOME", "/home/test")
        invalidate_safe_env_cache()
        env = get_safe_env()
        assert "AWS_ACCESS_KEY_ID" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert env.get("HOME") == "/home/test"

    def test_github_pat_stripped(self, monkeypatch):
        monkeypatch.setenv("GITHUB_PAT", "ghp_xxx")
        monkeypatch.setenv("GH_TOKEN", "ghs_xxx")
        invalidate_safe_env_cache()
        env = get_safe_env()
        assert "GITHUB_PAT" not in env
        assert "GH_TOKEN" not in env

    def test_pythondontwritebytecode_added(self, monkeypatch):
        invalidate_safe_env_cache()
        env = get_safe_env()
        assert env.get("PYTHONDONTWRITEBYTECODE") == "1"

    def test_legitimate_var_preserved(self, monkeypatch):
        monkeypatch.setenv("MONKEY_KEYBOARD", "yes")
        monkeypatch.setenv("KEYCHAIN_NAME", "default")
        invalidate_safe_env_cache()
        env = get_safe_env()
        assert env.get("MONKEY_KEYBOARD") == "yes"
        assert env.get("KEYCHAIN_NAME") == "default"
