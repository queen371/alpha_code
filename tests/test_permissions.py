"""Tests for user-defined permission rules in approval.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha import approval


@pytest.fixture(autouse=True)
def _reset_cache():
    approval.reset_permission_cache()
    yield
    approval.reset_permission_cache()


@pytest.fixture
def settings_in_cwd(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    alpha_dir = tmp_path / ".alpha"
    alpha_dir.mkdir()
    return alpha_dir / "settings.json"


def _write_perms(path: Path, allow=None, deny=None) -> None:
    block = {}
    if allow is not None:
        block["allow"] = allow
    if deny is not None:
        block["deny"] = deny
    path.write_text(json.dumps({"permissions": block}), encoding="utf-8")


# ── Defaults preserved when no settings ──


class TestDefaultsUnchanged:
    def test_no_settings_file_uses_defaults(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # read_file is auto-approve by default
        assert approval.needs_approval("read_file", {"path": "/tmp/x"}) is False
        # delegate_task requires approval by default
        assert approval.needs_approval("delegate_task", {"task": "x"}) is True


# ── Allow rules ──


class TestAllowRules:
    def test_allow_overrides_default_require(self, settings_in_cwd: Path):
        _write_perms(settings_in_cwd, allow=["delegate_task"])
        assert approval.needs_approval("delegate_task", {"task": "x"}) is False

    def test_allow_with_literal_match(self, settings_in_cwd: Path):
        _write_perms(settings_in_cwd, allow=["execute_shell(npm test)"])
        assert approval.needs_approval("execute_shell", {"command": "npm test"}) is False
        # Different command falls through to default (unsafe → require)
        assert approval.needs_approval("execute_shell", {"command": "rm -rf /"}) is True

    def test_allow_with_regex_match(self, settings_in_cwd: Path):
        # `dangercmd` is not in SAFE_SHELL_COMMANDS, so by default it requires approval.
        _write_perms(settings_in_cwd, allow=[r"execute_shell:^dangercmd ok"])
        assert approval.needs_approval(
            "execute_shell", {"command": "dangercmd ok run"}
        ) is False
        # Same tool, command doesn't match regex → falls through to default → require.
        assert approval.needs_approval(
            "execute_shell", {"command": "dangercmd bad run"}
        ) is True


# ── Deny rules ──


class TestDenyRules:
    def test_is_denied_blocks_with_reason(self, settings_in_cwd: Path):
        _write_perms(settings_in_cwd, deny=["execute_shell(rm -rf /)"])
        denied, reason = approval.is_denied(
            "execute_shell", {"command": "rm -rf /"}
        )
        assert denied is True
        assert "rm -rf /" in reason

    def test_deny_regex(self, settings_in_cwd: Path):
        _write_perms(settings_in_cwd, deny=["execute_shell:sudo"])
        denied, _ = approval.is_denied(
            "execute_shell", {"command": "sudo apt install"}
        )
        assert denied is True

    def test_deny_takes_precedence_over_allow(self, settings_in_cwd: Path):
        # Deny rules are enforced by the executor via `is_denied`, not by
        # `needs_approval`. The contract: if a command matches both lists,
        # `is_denied` reports True so the executor short-circuits before
        # `needs_approval` is even consulted.
        _write_perms(
            settings_in_cwd,
            allow=["execute_shell:^sudo "],
            deny=["execute_shell:apt"],
        )
        denied, _ = approval.is_denied(
            "execute_shell", {"command": "sudo apt install"}
        )
        assert denied is True

    def test_no_deny_match_returns_false(self, settings_in_cwd: Path):
        _write_perms(settings_in_cwd, deny=["execute_shell:rm"])
        denied, _ = approval.is_denied("execute_shell", {"command": "ls"})
        assert denied is False


# ── Rule parsing ──


class TestRuleParsing:
    def test_invalid_rule_skipped(self, settings_in_cwd: Path):
        _write_perms(settings_in_cwd, allow=["this is not a tool name!!!"])
        # No rules loaded → behaves like no allow rules
        assert approval.needs_approval("read_file", {"path": "x"}) is False  # default

    def test_invalid_regex_skipped(self, settings_in_cwd: Path):
        _write_perms(settings_in_cwd, allow=["execute_shell:[unclosed"])
        # The bad rule is dropped; nothing matches; default applies.
        assert approval.needs_approval(
            "execute_shell", {"command": "rm -rf /"}
        ) is True


# ── Primary arg fallback ──


class TestPrimaryArg:
    def test_unknown_tool_uses_first_string_arg(self, settings_in_cwd: Path):
        _write_perms(settings_in_cwd, allow=["my_custom_tool(target1)"])
        assert approval.needs_approval("my_custom_tool", {"target": "target1"}) is False
        assert approval.needs_approval("my_custom_tool", {"target": "target2"}) is True
