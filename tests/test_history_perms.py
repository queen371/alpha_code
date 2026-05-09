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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX perms only")
class TestLegacySessionPermsMigration:
    """DEEP_SECURITY V3.0 #D113: sessoes pre-fix #D109 (criadas com umask 022
    -> 0o644) permanecem world/group-readable indefinidamente porque
    `_atomic_write` so chmoda arquivos quando re-escritos. `_ensure_dir`
    deve elevar perms de TODOS os filhos *.json existentes para 0o600,
    fechando o gap de migracao em hosts upgradeed de versao pre-fix."""

    def test_legacy_0o664_sessions_chmoded_on_ensure_dir(self, tmp_history_dir):
        tmp_history_dir.mkdir(parents=True, mode=0o700)
        legacy = tmp_history_dir / "20260418_211136.json"
        legacy.write_text('{"session_id": "20260418_211136", "messages": []}')
        os.chmod(legacy, 0o664)
        # Pre-condicao: leak.
        assert stat.S_IMODE(os.stat(legacy).st_mode) == 0o664

        history._ensure_dir()

        # Pos: 0o600.
        assert stat.S_IMODE(os.stat(legacy).st_mode) == 0o600

    def test_legacy_0o644_world_readable_chmoded(self, tmp_history_dir):
        tmp_history_dir.mkdir(parents=True, mode=0o700)
        legacy = tmp_history_dir / "old.json"
        legacy.write_text("{}")
        os.chmod(legacy, 0o644)

        history._ensure_dir()

        assert stat.S_IMODE(os.stat(legacy).st_mode) == 0o600

    def test_already_0o600_files_untouched(self, tmp_history_dir):
        """Idempotencia: arquivo ja correto nao recebe chmod redundante.

        Verifica via mock que `os.chmod` nao e chamado em arquivo ja a 0o600.
        Importante porque #D113 roda a cada start — chmod desnecessario e
        stat-thrash em hosts saudaveis.
        """
        tmp_history_dir.mkdir(parents=True, mode=0o700)
        already_safe = tmp_history_dir / "safe.json"
        already_safe.write_text("{}")
        os.chmod(already_safe, 0o600)

        with mock.patch.object(history.os, "chmod", wraps=history.os.chmod) as ch:
            history._ensure_dir()
            # chmod em _HISTORY_DIR (0o700) sempre roda; chmod em arquivo nao.
            file_chmod_calls = [
                c for c in ch.call_args_list
                if str(c.args[0]) == str(already_safe)
            ]
            assert file_chmod_calls == [], "chmod called on already-0o600 file"

    def test_non_json_files_ignored(self, tmp_history_dir):
        """Arquivos sem `.json` nao sao migrados — fora do escopo do helper.

        Evita corromper perms de README, .DS_Store, etc., que o usuario
        possa ter colocado no diretorio.
        """
        tmp_history_dir.mkdir(parents=True, mode=0o700)
        readme = tmp_history_dir / "README.md"
        readme.write_text("notes")
        os.chmod(readme, 0o644)

        history._ensure_dir()

        assert stat.S_IMODE(os.stat(readme).st_mode) == 0o644

    def test_chmod_failure_does_not_raise(self, tmp_history_dir):
        """Erro em chmod (ex: arquivo de outro usuario em /tmp share) e
        silenciado via logger.debug — nao derruba startup."""
        tmp_history_dir.mkdir(parents=True, mode=0o700)
        legacy = tmp_history_dir / "fail.json"
        legacy.write_text("{}")
        os.chmod(legacy, 0o644)

        # Patch os.chmod para simular EPERM em arquivo (mas deixar dir passar).
        original_chmod = history.os.chmod

        def selective_chmod(path, mode):
            if str(path).endswith(".json"):
                raise PermissionError("simulated cross-user file")
            return original_chmod(path, mode)

        with mock.patch.object(history.os, "chmod", side_effect=selective_chmod):
            # Nao deve raise.
            result = history._ensure_dir()
            assert result == tmp_history_dir
