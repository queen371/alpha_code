"""Tests for conversation history persistence."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from alpha.history import (
    InvalidSessionId,
    _session_path,
    generate_session_id,
    list_sessions,
    load_session,
    load_session_summary,
    save_session,
)


class TestHistory:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self._patcher = patch("alpha.history._HISTORY_DIR", Path(self.tmpdir))
        self._patcher.start()

    def teardown_method(self):
        self._patcher.stop()

    def test_generate_session_id(self):
        sid = generate_session_id()
        # YYYYMMDD_HHMMSS_xxxxxxxx (15 + 1 + 8 = 24)
        assert len(sid) == 24
        # Dois underscores: timestamp <-> hex suffix
        assert sid.count("_") == 2

    def test_generate_session_id_unique_in_same_second(self):
        # Sufixo aleatorio garante unicidade mesmo com mesma timestamp.
        ids = {generate_session_id() for _ in range(50)}
        assert len(ids) == 50

    def test_save_and_load(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        save_session("test_001", messages)

        loaded = load_session("test_001")
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["role"] == "user"
        assert loaded[1]["content"] == "hi there"

    def test_load_nonexistent(self):
        assert load_session("does_not_exist") is None

    def test_system_messages_excluded(self):
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ]
        save_session("test_002", messages)
        loaded = load_session("test_002")
        assert len(loaded) == 1
        assert loaded[0]["role"] == "user"

    def test_tool_results_truncated(self):
        big_content = "x" * 5000
        # Tool messages precisam ter um assistant.tool_calls correspondente
        # (DEEP_LOGIC #DL019). Sanitizer drop-aria caso contrario.
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "fake", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "c1", "content": big_content},
        ]
        save_session("test_003", messages)
        loaded = load_session("test_003")
        # encontrar a tool message e verificar truncamento
        tool_msg = next(m for m in loaded if m["role"] == "tool")
        assert len(tool_msg["content"]) < 5000

    def test_list_sessions(self):
        save_session("test_a", [{"role": "user", "content": "[CWD: /tmp]\nhello"}])
        save_session("test_b", [{"role": "user", "content": "world"}])

        sessions = list_sessions()
        assert len(sessions) == 2
        # Should have preview
        previews = [s["preview"] for s in sessions]
        assert any("hello" in p for p in previews)
        assert any("world" in p for p in previews)

    def test_metadata_saved(self):
        save_session("test_meta", [{"role": "user", "content": "hi"}], {"provider": "grok"})
        path = Path(self.tmpdir) / "test_meta.json"
        data = json.loads(path.read_text())
        assert data["metadata"]["provider"] == "grok"


class TestSessionIdValidation:
    """Path-traversal guard on session_id (regression for /load <id>)."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self._patcher = patch("alpha.history._HISTORY_DIR", Path(self.tmpdir))
        self._patcher.start()

    def teardown_method(self):
        self._patcher.stop()

    def test_traversal_id_rejected_by_session_path(self):
        import pytest
        for bad in ("../etc/passwd", "../../etc/passwd", "a/b",
                    "a\\b", " ../foo", "id with space"):
            with pytest.raises(InvalidSessionId):
                _session_path(bad)

    def test_dot_only_id_rejected(self):
        import pytest
        for bad in ("", ".", "..", "...", ".hidden"):
            with pytest.raises(InvalidSessionId):
                _session_path(bad)

    def test_valid_ids_accepted(self):
        # Real ids from generate_session_id + simple alphanum/hyphen/underscore.
        for good in ("20260507_153045_abcdef12", "test_001", "ABC-123",
                     generate_session_id()):
            p = _session_path(good)
            assert p.suffix == ".json"
            assert p.is_relative_to(Path(self.tmpdir).resolve())

    def test_load_session_returns_none_for_traversal(self):
        # Public API must not raise — REPL `/load ../x` should fail soft.
        assert load_session("../etc/passwd") is None
        assert load_session_summary("../etc/passwd") is None

    def test_save_session_refuses_traversal_silently(self):
        # save_session swallows the error and logs (consistent with the
        # OSError-on-disk-full handler). The malicious file must NOT be
        # created anywhere.
        save_session("../leaked", [{"role": "user", "content": "x"}])
        # Nothing under tmpdir, and nothing one level above either.
        assert not list(Path(self.tmpdir).rglob("*leaked*"))
        assert not (Path(self.tmpdir).parent / "leaked.json").exists()
