"""Tests for conversation history persistence."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from alpha.history import generate_session_id, list_sessions, load_session, save_session


class TestHistory:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self._patcher = patch("alpha.history._HISTORY_DIR", Path(self.tmpdir))
        self._patcher.start()

    def teardown_method(self):
        self._patcher.stop()

    def test_generate_session_id(self):
        sid = generate_session_id()
        assert len(sid) == 15  # YYYYMMDD_HHMMSS
        assert "_" in sid

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
        messages = [
            {"role": "tool", "tool_call_id": "c1", "content": big_content},
        ]
        save_session("test_003", messages)
        loaded = load_session("test_003")
        assert len(loaded[0]["content"]) < 5000

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
