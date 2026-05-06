"""Regression tests for history file permissions (DEEP_SECURITY #D109).

Verifica que session files sao criados com 0o600 e o diretorio com 0o700,
prevenindo leak inter-usuario em hosts compartilhados.
"""

import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from alpha import history


@pytest.fixture
def tmp_history_dir(tmp_path, monkeypatch):
    """Aponta _HISTORY_DIR para um tmpdir do teste."""
    fake_dir = tmp_path / "history"
    monkeypatch.setattr(history, "_HISTORY_DIR", fake_dir)
    yield fake_dir


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX perms only")
class TestHistoryDirPerms:
    def test_dir_created_with_0o700(self, tmp_history_dir):
        history._ensure_dir()
        assert tmp_history_dir.exists()
        mode = stat.S_IMODE(os.stat(tmp_history_dir).st_mode)
        assert mode == 0o700, f"expected 0o700, got 0o{mode:o}"

    def test_dir_perms_corrected_when_already_exists_lax(self, tmp_history_dir):
        # Simula diretorio pre-existente com perms erradas.
        tmp_history_dir.mkdir(parents=True)
        os.chmod(tmp_history_dir, 0o755)
        history._ensure_dir()
        mode = stat.S_IMODE(os.stat(tmp_history_dir).st_mode)
        assert mode == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX perms only")
class TestSessionFilePerms:
    def test_save_session_writes_0o600(self, tmp_history_dir):
        path = history.save_session("testid", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        assert path.exists()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"

    def test_atomic_write_overrides_existing_lax_perms(self, tmp_history_dir):
        history._ensure_dir()
        target = tmp_history_dir / "x.json"
        target.write_text("{}")
        os.chmod(target, 0o644)
        history._atomic_write(target, '{"x": 1}')
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o600

    def test_atomic_write_uses_nofollow_when_available(self, tmp_history_dir):
        # Defesa contra symlink-attack: arquivo vitima apontando pra fora.
        if not hasattr(os, "O_NOFOLLOW"):
            pytest.skip("O_NOFOLLOW unavailable")
        history._ensure_dir()
        target = tmp_history_dir / "victim.json"
        elsewhere = tmp_history_dir / "elsewhere"
        elsewhere.write_text("important")
        target.symlink_to(elsewhere)
        with pytest.raises(OSError):
            history._atomic_write(target, '{"x": 1}')
        # Conteudo do alvo do symlink nao foi sobrescrito.
        assert elsewhere.read_text() == "important"
