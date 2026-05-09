"""Tests for ``alpha.project_context``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from alpha.project_context import (
    CONTEXT_FILENAME,
    MAX_BYTES,
    find_context_file,
    inject_project_context,
    load_project_context,
)


@pytest.fixture
def project_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a tmp project root with a subdir, then chdir into the subdir.

    Returns the project root.
    """
    sub = tmp_path / "src" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    # Ensure opt-out env is not set in any test by default.
    monkeypatch.delenv("ALPHA_NO_PROJECT_CONTEXT", raising=False)
    return tmp_path


class TestFindContextFile:
    def test_finds_in_cwd(self, project_tree: Path, monkeypatch):
        target = project_tree / "src" / "deep" / CONTEXT_FILENAME
        target.write_text("hi")
        found = find_context_file()
        assert found == target.resolve()

    def test_walks_up_to_project_root(self, project_tree: Path):
        target = project_tree / CONTEXT_FILENAME
        target.write_text("hi")
        found = find_context_file()
        assert found == target.resolve()

    def test_returns_none_when_absent(self, project_tree: Path):
        assert find_context_file() is None

    def test_explicit_start_path_is_honored(self, tmp_path: Path):
        # No file in cwd, but a file at the explicit start path.
        other = tmp_path / "other"
        other.mkdir()
        target = other / CONTEXT_FILENAME
        target.write_text("explicit")
        assert find_context_file(other) == target.resolve()


class TestLoadProjectContext:
    def test_reads_body(self, project_tree: Path):
        (project_tree / CONTEXT_FILENAME).write_text(
            "# Project\n\nUse pytest for tests.\n"
        )
        ctx = load_project_context()
        assert ctx is not None
        assert "Use pytest for tests" in ctx.body
        assert ctx.truncated is False

    def test_truncates_oversized_file(self, project_tree: Path):
        big = "A" * (MAX_BYTES + 4096)
        (project_tree / CONTEXT_FILENAME).write_text(big)
        ctx = load_project_context()
        assert ctx is not None
        assert ctx.truncated is True
        assert ctx.raw_size == MAX_BYTES + 4096
        # Truncation notice should mention the cap and original size.
        assert str(MAX_BYTES) in ctx.body
        assert str(MAX_BYTES + 4096) in ctx.body
        # Truncated body must be no longer than MAX_BYTES + the notice.
        assert len(ctx.body) <= MAX_BYTES + 400

    def test_disabled_via_env(self, project_tree: Path, monkeypatch):
        (project_tree / CONTEXT_FILENAME).write_text("ignored")
        monkeypatch.setenv("ALPHA_NO_PROJECT_CONTEXT", "1")
        assert load_project_context() is None

    def test_disabled_env_zero_does_not_disable(
        self, project_tree: Path, monkeypatch
    ):
        # Treat "0" / "false" / empty as NOT disabled — only truthy values disable.
        (project_tree / CONTEXT_FILENAME).write_text("loaded")
        for falsy in ("0", "false", "no", ""):
            monkeypatch.setenv("ALPHA_NO_PROJECT_CONTEXT", falsy)
            ctx = load_project_context()
            assert ctx is not None, f"expected load with env={falsy!r}"

    def test_returns_none_when_no_file(self, project_tree: Path):
        assert load_project_context() is None

    def test_handles_non_utf8_bytes(self, project_tree: Path):
        # Latin-1 encoded text — must not crash, must produce a body.
        (project_tree / CONTEXT_FILENAME).write_bytes(b"hello \xe9 world")
        ctx = load_project_context()
        assert ctx is not None
        assert "hello" in ctx.body
        assert "world" in ctx.body


class TestInject:
    def test_no_context_returns_prompt_unchanged(self):
        prompt = "# IDENTITY\nYou are ALPHA."
        assert inject_project_context(prompt, None) == prompt

    def test_appends_section_with_filename_label(
        self, project_tree: Path
    ):
        (project_tree / CONTEXT_FILENAME).write_text("project rules")
        ctx = load_project_context()
        result = inject_project_context("# IDENTITY\nbase", ctx)
        assert result.startswith("# IDENTITY\nbase")
        assert f"# PROJECT CONTEXT (from {CONTEXT_FILENAME})" in result
        assert "project rules" in result

    def test_does_not_double_blank_line(self, project_tree: Path):
        (project_tree / CONTEXT_FILENAME).write_text("rules")
        ctx = load_project_context()
        # Base ends with trailing whitespace — must be normalized.
        result = inject_project_context("base   \n\n\n", ctx)
        assert "base   \n\n\n" not in result
        assert "base\n\n# PROJECT CONTEXT" in result
