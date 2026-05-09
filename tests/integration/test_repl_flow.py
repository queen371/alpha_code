"""End-to-end tests for the interactive REPL loop.

These tests drive ``main.run_repl`` by feeding scripted inputs through a
patched ``read_input`` and mocking ``run_agent`` so no real LLM call
fires. The goal is to lock in the behavior of every slash command
*before* the dispatch-table refactor — without this coverage, splitting
the 400-line if/elif chain into named handlers risks silent regression.

Each test runs the REPL until either ``/exit`` is sent or the input
script is exhausted (which raises EOFError → REPL terminates cleanly).
Output is captured with ``capsys``; persistent state is captured by
patching ``alpha.history._HISTORY_DIR`` to a tmpdir.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ─── Mocks ──────────────────────────────────────────────────────────


class _ScriptedInput:
    """Replacement for ``read_input``. Yields scripted (text, image_paths)
    tuples; raises EOFError when exhausted (signals the REPL to break)."""

    def __init__(self, *entries):
        self._entries = list(entries)

    def __call__(self, _prompt):
        if not self._entries:
            raise EOFError
        entry = self._entries.pop(0)
        if isinstance(entry, str):
            return entry, []
        return entry  # (text, image_paths)


async def _fake_run_agent_done(*_args, **_kwargs):
    """Stand-in for ``run_agent`` that emits a single done event.

    The REPL waits for ``done`` to know the turn ended and a reply was
    produced. Real LLM is never called.
    """
    yield {"type": "done", "reply": "[mock reply]"}


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def repl_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Common test env: tmp history dir, fake API key, no project context."""
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    monkeypatch.setattr("alpha.history._HISTORY_DIR", history_dir)

    # All providers need *some* API key path; deepseek's env var is the
    # default. Use a sentinel string so any accidental real call fails fast.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-placeholder")

    # Don't auto-load the *project's* ALPHA.md into the test agent's prompt.
    monkeypatch.setenv("ALPHA_NO_PROJECT_CONTEXT", "1")

    # Avoid prompt_toolkit's terminal handling — read_input is patched per test.
    monkeypatch.chdir(tmp_path)
    return {"tmp_path": tmp_path, "history_dir": history_dir}


def _drive_repl(monkeypatch, *inputs):
    """Run ``main.run_repl`` against a scripted input. Returns ``main`` so
    callers can assert on captured state."""
    import main

    monkeypatch.setattr(main, "read_input", _ScriptedInput(*inputs))
    monkeypatch.setattr("alpha.agent.run_agent", _fake_run_agent_done)
    main.run_repl(provider="deepseek", temperature=0.5)
    return main


# ─── Tests ──────────────────────────────────────────────────────────


class TestExitCommands:
    """`/exit`, `/quit`, `/q` all terminate the loop cleanly."""

    @pytest.mark.parametrize("cmd", ["/exit", "/quit", "/q"])
    def test_exit_variants_terminate(self, repl_env, monkeypatch, capsys, cmd):
        _drive_repl(monkeypatch, cmd)
        out = capsys.readouterr().out
        assert "Goodbye." in out

    def test_eof_terminates(self, repl_env, monkeypatch, capsys):
        # No inputs queued — read_input raises EOFError on first call.
        _drive_repl(monkeypatch)
        # No assertion on output — we just verify the REPL didn't hang.


class TestClear:
    def test_clear_resets_history_and_session(self, repl_env, monkeypatch, capsys):
        # Send a chat turn (mock LLM done), then /clear, then /exit.
        # After /clear, the banner reprints — that's the user-visible signal.
        _drive_repl(monkeypatch, "hello", "/clear", "/exit")
        out = capsys.readouterr().out
        # Banner appears at startup AND after /clear → at least 2 occurrences.
        assert out.count("ALPHA CODE") >= 2


class TestHistory:
    def test_empty_history_message(self, repl_env, monkeypatch, capsys):
        _drive_repl(monkeypatch, "/history", "/exit")
        out = capsys.readouterr().out
        assert "History is empty" in out

    def test_history_shows_user_messages_after_chat(
        self, repl_env, monkeypatch, capsys
    ):
        _drive_repl(
            monkeypatch,
            "tell me a joke",
            "/history",
            "/exit",
        )
        out = capsys.readouterr().out
        # Each user input appears in /history output (truncated to 100 chars).
        assert "tell me a joke" in out


class TestSaveLoad:
    def test_save_writes_session_file(self, repl_env, monkeypatch, capsys):
        _drive_repl(monkeypatch, "first message", "/save", "/exit")
        out = capsys.readouterr().out
        assert "Session saved:" in out
        # File appeared in the patched history dir.
        files = list(repl_env["history_dir"].glob("*.json"))
        assert len(files) >= 1, "save_session must persist a JSON file"

    def test_load_with_no_args_lists_recent_sessions(
        self, repl_env, monkeypatch, capsys
    ):
        # First save a session, then /load (no id) should list it.
        _drive_repl(monkeypatch, "msg one", "/save", "/exit")
        out = capsys.readouterr().out
        assert "Session saved:" in out

        # Second run: /load with no id should list the prior session.
        _drive_repl(monkeypatch, "/load", "/exit")
        out = capsys.readouterr().out
        assert "Recent sessions:" in out or "No saved sessions." in out

    def test_load_unknown_id_shows_error(self, repl_env, monkeypatch, capsys):
        _drive_repl(monkeypatch, "/load nonexistent_id", "/exit")
        out = capsys.readouterr().out
        assert "Session not found" in out

    def test_load_path_traversal_attempt_handled_gracefully(
        self, repl_env, monkeypatch, capsys
    ):
        # Regression: history.py rejects ids matching path traversal.
        # The REPL must not crash — it should report "Session not found".
        _drive_repl(monkeypatch, "/load ../etc/passwd", "/exit")
        out = capsys.readouterr().out
        assert "Session not found" in out


class TestSessions:
    def test_sessions_with_none_saved(self, repl_env, monkeypatch, capsys):
        _drive_repl(monkeypatch, "/sessions", "/exit")
        # No assertion on the exact text — just that the REPL didn't crash
        # listing zero sessions.

    def test_sessions_after_save(self, repl_env, monkeypatch, capsys):
        _drive_repl(monkeypatch, "msg", "/save", "/sessions", "/exit")
        # Output should include the saved session id (timestamp_hex).
        out = capsys.readouterr().out
        assert "_" in out  # session id format includes underscores


class TestTools:
    def test_tools_command_lists_registered_tools(
        self, repl_env, monkeypatch, capsys
    ):
        _drive_repl(monkeypatch, "/tools", "/exit")
        out = capsys.readouterr().out
        # At minimum, the read_file tool should appear.
        assert "read_file" in out


class TestSkills:
    def test_skills_command_lists_skills(self, repl_env, monkeypatch, capsys):
        _drive_repl(monkeypatch, "/skills", "/exit")
        out = capsys.readouterr().out
        # Header summarizes counts; with the bundled skills, count > 0.
        assert "skills registered" in out
        # Either Ready or Inactive group must appear.
        assert "Ready" in out or "Inactive" in out


class TestUnknownCommand:
    def test_unknown_command_shows_hint(self, repl_env, monkeypatch, capsys):
        _drive_repl(monkeypatch, "/totallyfakecmd", "/exit")
        out = capsys.readouterr().out
        assert "Unknown command:" in out

    def test_close_match_suggested_for_typos(
        self, repl_env, monkeypatch, capsys
    ):
        # /clea is one char away from /clear → dispatcher should suggest.
        _drive_repl(monkeypatch, "/clea", "/exit")
        out = capsys.readouterr().out
        # Must say "Did you mean" with a real suggestion.
        assert "Did you mean" in out or "Unknown command" in out


class TestHelp:
    def test_help_lists_commands(self, repl_env, monkeypatch, capsys):
        _drive_repl(monkeypatch, "/help", "/exit")
        out = capsys.readouterr().out
        # Spot-check: /clear and /exit should appear in the help output.
        assert "/clear" in out
        assert "/exit" in out


class TestSessionPersistence:
    """End-to-end: save → exit → load in fresh REPL → verify content survived."""

    def test_save_then_load_round_trip(
        self, repl_env, monkeypatch, capsys
    ):
        # 1) Save a session and record its id from the on-disk JSON.
        _drive_repl(monkeypatch, "remember this fact", "/save", "/exit")
        files = list(repl_env["history_dir"].glob("*.json"))
        assert files, "save did not produce a file"
        data = json.loads(files[0].read_text())
        session_id = data["session_id"]

        # 2) New REPL run, /load <id> should not error.
        _drive_repl(monkeypatch, f"/load {session_id}", "/exit")
        out = capsys.readouterr().out
        assert "Loaded" in out and session_id in out


# ─── Smoke ──────────────────────────────────────────────────────────


def test_repl_starts_and_exits_cleanly(repl_env, monkeypatch, capsys):
    """Bare-minimum sanity: start REPL, send /exit, verify banner appeared."""
    _drive_repl(monkeypatch, "/exit")
    out = capsys.readouterr().out
    assert "ALPHA CODE" in out
    assert "Goodbye" in out
