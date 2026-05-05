"""Tests for the declarative hook system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha import hooks


@pytest.fixture(autouse=True)
def _reset_hook_cache():
    hooks.reset_cache()
    yield
    hooks.reset_cache()


@pytest.fixture
def settings_in_cwd(tmp_path: Path, monkeypatch):
    """Point the hook loader at a settings file inside `tmp_path`."""
    monkeypatch.chdir(tmp_path)
    alpha_dir = tmp_path / ".alpha"
    alpha_dir.mkdir()
    return alpha_dir / "settings.json"


def _write_settings(path: Path, hooks_block: dict) -> None:
    path.write_text(json.dumps({"hooks": hooks_block}), encoding="utf-8")


# ── Loader ──


class TestLoader:
    def test_no_settings_returns_empty(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        loaded = hooks.load_hooks(force=True)
        assert all(loaded[ev] == [] for ev in hooks.VALID_EVENTS)

    def test_invalid_json_returns_empty(self, settings_in_cwd: Path):
        settings_in_cwd.write_text("{bad json", encoding="utf-8")
        loaded = hooks.load_hooks(force=True)
        assert all(loaded[ev] == [] for ev in hooks.VALID_EVENTS)

    def test_unknown_event_skipped(self, settings_in_cwd: Path):
        _write_settings(
            settings_in_cwd,
            {"made_up_event": [{"command": "true"}]},
        )
        loaded = hooks.load_hooks(force=True)
        assert all(loaded[ev] == [] for ev in hooks.VALID_EVENTS)

    def test_invalid_matcher_falls_back_to_no_matcher(self, settings_in_cwd: Path):
        _write_settings(
            settings_in_cwd,
            {"pre_tool": [{"matcher": "[unclosed", "command": "true"}]},
        )
        loaded = hooks.load_hooks(force=True)
        assert len(loaded["pre_tool"]) == 1
        assert loaded["pre_tool"][0].matcher is None  # invalid regex was dropped

    def test_command_required(self, settings_in_cwd: Path):
        _write_settings(
            settings_in_cwd,
            {"pre_tool": [{"matcher": "x", "command": ""}]},
        )
        loaded = hooks.load_hooks(force=True)
        assert loaded["pre_tool"] == []


# ── Firing / matching ──


class TestFire:
    def test_no_hooks_returns_noop(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        outcome = hooks.fire("pre_tool", tool_name="read_file", tool_args={})
        assert outcome.blocked is False

    def test_matcher_filters_tools(self, settings_in_cwd: Path, tmp_path: Path):
        marker_a = tmp_path / "ran_a.txt"
        marker_b = tmp_path / "ran_b.txt"
        _write_settings(
            settings_in_cwd,
            {
                "pre_tool": [
                    {"matcher": "write_file", "command": f"touch {marker_a}"},
                    {"matcher": "read_file", "command": f"touch {marker_b}"},
                ]
            },
        )
        hooks.fire("pre_tool", tool_name="write_file", tool_args={})
        assert marker_a.exists()
        assert not marker_b.exists()

    def test_no_matcher_fires_for_every_tool(self, settings_in_cwd: Path, tmp_path: Path):
        marker = tmp_path / "ran.txt"
        _write_settings(
            settings_in_cwd,
            {"pre_tool": [{"command": f"touch {marker}"}]},
        )
        hooks.fire("pre_tool", tool_name="anything", tool_args={})
        assert marker.exists()

    def test_blocking_hook_vetoes(self, settings_in_cwd: Path):
        _write_settings(
            settings_in_cwd,
            {
                "pre_tool": [
                    {"command": "echo 'no way' >&2; exit 7", "blocking": True}
                ]
            },
        )
        outcome = hooks.fire("pre_tool", tool_name="write_file", tool_args={})
        assert outcome.blocked is True
        assert "no way" in outcome.block_reason

    def test_non_blocking_failure_does_not_veto(self, settings_in_cwd: Path):
        _write_settings(
            settings_in_cwd,
            {"pre_tool": [{"command": "exit 1"}]},
        )
        outcome = hooks.fire("pre_tool", tool_name="x", tool_args={})
        assert outcome.blocked is False

    def test_payload_via_env_vars(self, settings_in_cwd: Path, tmp_path: Path):
        marker = tmp_path / "captured.txt"
        _write_settings(
            settings_in_cwd,
            {
                "pre_tool": [
                    {
                        "command": (
                            f"echo \"$ALPHA_TOOL_NAME|$ALPHA_HOOK_EVENT\" > {marker}"
                        )
                    }
                ]
            },
        )
        hooks.fire("pre_tool", tool_name="write_file", tool_args={"path": "/tmp/x"})
        captured = marker.read_text(encoding="utf-8").strip()
        assert captured == "write_file|pre_tool"

    def test_payload_via_stdin(self, settings_in_cwd: Path, tmp_path: Path):
        marker = tmp_path / "stdin.txt"
        _write_settings(
            settings_in_cwd,
            {"pre_tool": [{"command": f"cat > {marker}"}]},
        )
        hooks.fire("pre_tool", tool_name="t", tool_args={"k": "v"})
        payload = json.loads(marker.read_text(encoding="utf-8"))
        assert payload["event"] == "pre_tool"
        assert payload["tool_name"] == "t"
        assert payload["tool_args"] == {"k": "v"}

    def test_first_blocking_hook_short_circuits(
        self, settings_in_cwd: Path, tmp_path: Path
    ):
        """A blocking hook stops subsequent hooks from running."""
        late_marker = tmp_path / "late.txt"
        _write_settings(
            settings_in_cwd,
            {
                "pre_tool": [
                    {"command": "exit 1", "blocking": True},
                    {"command": f"touch {late_marker}"},
                ]
            },
        )
        outcome = hooks.fire("pre_tool", tool_name="x", tool_args={})
        assert outcome.blocked is True
        assert not late_marker.exists()
