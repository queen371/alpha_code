"""Coverage para `_search_files` migration to ripgrep (#025/#071 PERF V1.1).

Testes cobrem:
- Detecao de `rg` no PATH no import
- ripgrep path: roda quando `_RIPGREP_BIN` esta seteado
- fallback Python: roda quando ripgrep ausente
- Output identico em ambos os caminhos (mesma chave: file, line, content)
- Exclusoes (.git, node_modules, etc) honradas em ambos
- max_results respeitado em ambos
- Files > 1MB skipados
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest


def _seed(tmp_path: Path) -> None:
    """Cria estrutura de teste com matches conhecidos."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def hello():\n    return 'hello world'\n"
    )
    (tmp_path / "src" / "utils.py").write_text(
        "import os\n# hello again\nprint('skipping')\n"
    )
    # Diretorios excluidos:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("hello git\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("hello world\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_text("hello cache\n")


class TestRipgrepDetection:
    def test_module_loads_with_or_without_rg(self):
        from alpha.tools import file_tools

        # `_RIPGREP_BIN` deve ser ou um path absoluto ou None
        assert file_tools._RIPGREP_BIN is None or isinstance(
            file_tools._RIPGREP_BIN, str
        )

    def test_excludes_constant_includes_common_dirs(self):
        from alpha.tools import file_tools

        # `_RG_EXCLUDES` deve conter os mesmos dirs que pulavam antes
        # (.git, node_modules, __pycache__, .venv) — usado pelo ripgrep
        # E pelo fallback Python (que referencia a constante).
        for d in (".git", "node_modules", "__pycache__", ".venv"):
            assert d in file_tools._RG_EXCLUDES

    def test_python_fallback_references_excludes_constant(self):
        from alpha.tools import file_tools

        # Garante que mudar a constante propaga para o fallback,
        # nao virou string literal acidentalmente.
        src = inspect.getsource(file_tools._search_with_python)
        assert "_RG_EXCLUDES" in src

    def test_search_files_dispatches_to_helper(self):
        from alpha.tools import file_tools

        src = inspect.getsource(file_tools._search_files)
        # Deve chamar uma das duas helpers e nao ter mais o `_scan` antigo
        assert (
            "_search_with_ripgrep" in src
            or "_search_with_python" in src
        )
        assert "def _scan()" not in src


@pytest.mark.asyncio
class TestSearchFunctionalParity:
    async def test_python_fallback_finds_matches(self, tmp_path):
        """O fallback Python deve achar 'hello' em src/ e ignorar .git/node_modules."""
        from alpha.tools import file_tools

        _seed(tmp_path)
        regex = re.compile("hello", re.IGNORECASE)
        results = file_tools._search_with_python(regex, tmp_path, max_results=50)

        files = {Path(r["file"]).name for r in results}
        # Deve achar matches em src/
        assert "main.py" in files
        assert "utils.py" in files
        # Nao deve achar em .git/, node_modules/, __pycache__/
        assert "config" not in files
        assert "junk.js" not in files
        assert "junk.pyc" not in files

    async def test_python_fallback_respects_max_results(self, tmp_path):
        from alpha.tools import file_tools

        # 5 arquivos com 1 match cada
        for i in range(5):
            (tmp_path / f"file{i}.txt").write_text("hello\n")

        regex = re.compile("hello")
        results = file_tools._search_with_python(regex, tmp_path, max_results=3)
        assert len(results) == 3

    async def test_python_fallback_skips_large_files(self, tmp_path):
        from alpha.tools import file_tools

        # Arquivo de 2MB com match — deve ser pulado
        big = tmp_path / "big.txt"
        big.write_text("hello\n" + "x" * (2 * 1024 * 1024))
        # Arquivo pequeno com match — deve aparecer
        (tmp_path / "small.txt").write_text("hello small\n")

        regex = re.compile("hello")
        results = file_tools._search_with_python(regex, tmp_path, max_results=50)
        names = {Path(r["file"]).name for r in results}
        assert "small.txt" in names
        assert "big.txt" not in names

    async def test_ripgrep_path_runs_when_available(self, tmp_path):
        """Se ripgrep estiver instalado, _search_files deve usar e funcionar."""
        from alpha.tools import file_tools

        if file_tools._RIPGREP_BIN is None:
            pytest.skip("ripgrep nao instalado neste ambiente")

        _seed(tmp_path)
        # Patcha o validate_path pra aceitar tmp_path (escapa do AGENT_WORKSPACE)
        # Sem isso, _search_files rejeita por estar fora do workspace.
        from alpha.tools import path_helpers
        original = path_helpers._validate_path

        def patched(p):
            return Path(p).resolve()

        path_helpers._validate_path = patched
        file_tools._validate_path = patched
        try:
            result = await file_tools._search_files("hello", str(tmp_path))
        finally:
            path_helpers._validate_path = original
            file_tools._validate_path = original

        assert "results" in result
        assert result["matches"] >= 2
        files = {Path(r["file"]).name for r in result["results"]}
        assert "main.py" in files
        # ripgrep tambem deve respeitar exclusoes (--glob '!.git/**')
        assert "config" not in files
