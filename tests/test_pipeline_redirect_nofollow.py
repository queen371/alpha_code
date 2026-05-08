"""Regression tests for pipeline redirect TOCTOU/symlink protection.

Cobre AUDIT_V1.2 #003 (BUGS ALTO) + DEEP_SECURITY V3.0 #D117 (SEC BAIXO) —
mesma issue: `_open_redirect_files` em `pipeline_tools.py` abria com
`open(path, "w")` sem `O_NOFOLLOW`. Atacante local que conseguisse trocar
o path por symlink entre validacao e open escrevia em alvo arbitrario.

Fix: usa `_validate_path_no_symlink` (rejeita symlink no input + parents)
+ `os.open(O_NOFOLLOW)` no open final (defesa em camadas — fecha TOCTOU
mesmo se symlink for criado depois da validacao).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(monkeypatch):
    """Aponta AGENT_WORKSPACE para tmpdir do teste."""
    tmp = Path(tempfile.mkdtemp(prefix="alpha-pipe-test-"))
    monkeypatch.setattr(
        "alpha.tools.pipeline_tools.AGENT_WORKSPACE", tmp
    )
    monkeypatch.setattr(
        "alpha.tools.workspace.AGENT_WORKSPACE", tmp
    )
    monkeypatch.setattr(
        "alpha.tools.path_helpers.AGENT_WORKSPACE", tmp
    )
    yield tmp
    # Cleanup
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks only")
class TestRedirectSymlinkRejection:
    """Symlinks devem ser rejeitados em todos os modos de redirect."""

    def test_existing_symlink_rejected_for_stdout(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        victim = tmp_workspace / "victim.txt"
        victim.write_text("IMPORTANT")
        sym = tmp_workspace / "sym.txt"
        sym.symlink_to(victim)

        with pytest.raises(ValueError, match="(?i)symlink"):
            _open_redirect_files({"stdout": str(sym)})

        # Conteudo nao foi tocado.
        assert victim.read_text() == "IMPORTANT"

    def test_existing_symlink_rejected_for_stderr(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        victim = tmp_workspace / "stderr_victim.txt"
        victim.write_text("STDERR_IMPORTANT")
        sym = tmp_workspace / "sym_err.txt"
        sym.symlink_to(victim)

        with pytest.raises(ValueError, match="(?i)symlink"):
            _open_redirect_files({"stderr": str(sym)})

        assert victim.read_text() == "STDERR_IMPORTANT"

    def test_existing_symlink_rejected_for_append(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        victim = tmp_workspace / "append_victim.txt"
        victim.write_text("a\n")
        sym = tmp_workspace / "sym_append.txt"
        sym.symlink_to(victim)

        with pytest.raises(ValueError, match="(?i)symlink"):
            _open_redirect_files({"stdout_append": str(sym)})

        assert victim.read_text() == "a\n"

    def test_existing_symlink_rejected_for_stdin(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        # stdin: leitura — tambem rejeita symlink (defense-in-depth contra
        # leitura de /etc/passwd via redirect injection).
        secret = tmp_workspace / "secret.txt"
        secret.write_text("API_KEY=topsecret")
        sym = tmp_workspace / "sym_in.txt"
        sym.symlink_to(secret)

        with pytest.raises(ValueError, match="(?i)symlink"):
            _open_redirect_files({"stdin": str(sym)})

    def test_parent_component_symlink_rejected(self, tmp_workspace):
        """Mesmo se o filename final nao e symlink, parent component pode ser."""
        from alpha.tools.pipeline_tools import _open_redirect_files

        # /tmp/secret_real/ tem o conteudo real.
        secret_dir = Path(tempfile.mkdtemp(prefix="alpha-secret-"))
        try:
            (secret_dir / "secret.txt").write_text("SECRETS")
            # workspace/path/ -> /tmp/secret_real/ (symlink no parent)
            sym_dir = tmp_workspace / "path"
            sym_dir.symlink_to(secret_dir)

            with pytest.raises(ValueError, match="(?i)symlink"):
                _open_redirect_files({"stdout": str(sym_dir / "out.txt")})
        finally:
            import shutil
            shutil.rmtree(secret_dir, ignore_errors=True)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths only")
class TestRedirectWorkspaceContainment:
    """Path traversal `> ../../etc/passwd` deve ser rejeitado."""

    def test_dotdot_traversal_blocked(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        # `../../etc/passwd` relativo ao workspace — sai dele.
        with pytest.raises(ValueError, match="(?i)workspace"):
            _open_redirect_files({"stdout": "../../etc/passwd_inj"})

    def test_absolute_path_outside_workspace_blocked(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        with pytest.raises(ValueError, match="(?i)workspace"):
            _open_redirect_files({"stdout": "/etc/passwd_inj"})


class TestRedirectLegitimatePaths:
    """Regression: caminhos validos continuam funcionando."""

    def test_simple_relative_write(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        target = tmp_workspace / "out.txt"
        handles = _open_redirect_files({"stdout": str(target)})
        try:
            handles["stdout"].write("hello")
        finally:
            handles["stdout"].close()
        assert target.read_text() == "hello"

    def test_append_mode_preserves_content(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        target = tmp_workspace / "log.txt"
        target.write_text("first\n")
        handles = _open_redirect_files({"stdout_append": str(target)})
        try:
            handles["stdout"].write("second\n")
        finally:
            handles["stdout"].close()
        assert target.read_text() == "first\nsecond\n"

    def test_stdin_reads_existing_file(self, tmp_workspace):
        from alpha.tools.pipeline_tools import _open_redirect_files

        src = tmp_workspace / "in.txt"
        src.write_text("input")
        handles = _open_redirect_files({"stdin": str(src)})
        try:
            assert handles["stdin"].read() == "input"
        finally:
            handles["stdin"].close()

    def test_creates_nonexistent_target(self, tmp_workspace):
        """Redirect `>` cria arquivo se nao existe."""
        from alpha.tools.pipeline_tools import _open_redirect_files

        target = tmp_workspace / "subdir" / "new.txt"
        target.parent.mkdir(parents=True)
        handles = _open_redirect_files({"stdout": str(target)})
        try:
            handles["stdout"].write("created")
        finally:
            handles["stdout"].close()
        assert target.read_text() == "created"


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"),
    reason="O_NOFOLLOW unavailable",
)
class TestONofollowDefenseInDepth:
    """Mesmo se passar `_validate_path_no_symlink`, `O_NOFOLLOW` no open
    pega TOCTOU race onde atacante cria symlink ENTRE validacao e open.

    Simulamos via mock: validador nao detecta symlink (retorna o path
    como se fosse arquivo regular), mas no open o O_NOFOLLOW dispara.
    """

    def test_open_with_nofollow_traps_symlink_at_open_time(self, tmp_workspace):
        """Verifica que `_open_redirect_target` (helper de baixo nivel)
        rejeita symlink mesmo quando a validacao ja passou."""
        from alpha.tools.pipeline_tools import _open_redirect_target

        victim = tmp_workspace / "race_victim.txt"
        victim.write_text("PROTECTED")
        sym = tmp_workspace / "race_sym.txt"
        sym.symlink_to(victim)

        # Chama direto sem passar pelo validator — simula o caso onde
        # validator passou (e.g. arquivo regular criado e depois substituido
        # por symlink no race window).
        with pytest.raises(OSError) as exc_info:
            _open_redirect_target("stdout", sym)
        # ELOOP em Linux = errno 40, em macOS = 62. Aceitamos qualquer um.
        assert exc_info.value.errno in (40, 62), (
            f"Expected ELOOP, got errno {exc_info.value.errno}"
        )
        assert victim.read_text() == "PROTECTED"
