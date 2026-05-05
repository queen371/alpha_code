"""Tests for per-agent scratch workspace helpers in delegate_tools."""

import re
from pathlib import Path

import pytest

from alpha.tools.delegate_tools import (
    _create_scratch_dir,
    _new_agent_id,
    _snapshot_dir,
)


class TestAgentId:
    def test_format_is_timestamp_hex(self):
        agent_id = _new_agent_id()
        assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{8}$", agent_id)

    def test_ids_are_unique(self):
        ids = {_new_agent_id() for _ in range(50)}
        assert len(ids) == 50


class TestScratchDir:
    def test_creates_nested_path(self, tmp_path: Path):
        agent_id = "20260424-120000-abcdef01"
        scratch = _create_scratch_dir(str(tmp_path), agent_id)
        assert scratch.exists() and scratch.is_dir()
        assert scratch == tmp_path / ".alpha" / "runs" / agent_id

    def test_collision_raises(self, tmp_path: Path):
        agent_id = "20260424-120000-abcdef01"
        _create_scratch_dir(str(tmp_path), agent_id)
        with pytest.raises(FileExistsError):
            _create_scratch_dir(str(tmp_path), agent_id)

    def test_parallel_agents_get_separate_dirs(self, tmp_path: Path):
        a = _create_scratch_dir(str(tmp_path), _new_agent_id())
        b = _create_scratch_dir(str(tmp_path), _new_agent_id())
        assert a != b


class TestSnapshot:
    def test_snapshot_empty_dir(self, tmp_path: Path):
        assert _snapshot_dir(tmp_path) == []

    def test_snapshot_missing_dir(self, tmp_path: Path):
        assert _snapshot_dir(tmp_path / "nope") == []

    def test_snapshot_lists_files_recursively_sorted(self, tmp_path: Path):
        (tmp_path / "out.txt").write_text("hello")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested.log").write_text("x")
        assert _snapshot_dir(tmp_path) == ["out.txt", "sub/nested.log"]
