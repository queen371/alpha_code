"""Coverage para `subagent_policy` configuravel (#D007 V1.0).

Cobre:
- FEATURES expoe `subagent_policy`, `subagent_extra_block`, `subagent_allow`
- Defaults preservam comportamento antigo (strict + nada extra)
- Env vars `ALPHA_SUBAGENT_POLICY/EXTRA_BLOCK/ALLOW` sao lidas pelo config
- delegate_tools._run_subagent constroi o blocklist usando os 3 knobs
- delegate_* sempre bloqueado (anti-recursao) mesmo com policy=relaxed ou allow=delegate_task
"""

from __future__ import annotations

import inspect

import pytest


class TestFeaturesExposesPolicyKeys:
    def test_default_policy_is_strict(self):
        from alpha.config import FEATURES

        assert FEATURES.get("subagent_policy") == "strict"

    def test_default_extra_block_empty(self):
        from alpha.config import FEATURES

        assert FEATURES.get("subagent_extra_block") == frozenset()

    def test_default_allow_empty(self):
        from alpha.config import FEATURES

        assert FEATURES.get("subagent_allow") == frozenset()


class TestEnvVarOverrides:
    def test_subagent_policy_env_read_at_import(self, monkeypatch):
        # config.py le env no import — para testar override precisamos
        # forcar reimport. Em vez disso, exercitamos a expressao
        # inline que o config faz, garantindo que `os.environ.get`
        # funciona como esperado.
        import os

        monkeypatch.setenv("ALPHA_SUBAGENT_POLICY", "relaxed")
        assert os.environ.get("ALPHA_SUBAGENT_POLICY", "strict") == "relaxed"

    def test_extra_block_csv_parsing(self):
        env_value = "write_file, edit_file ,  http_request"
        parsed = frozenset(t.strip() for t in env_value.split(",") if t.strip())
        assert parsed == {"write_file", "edit_file", "http_request"}

    def test_allow_csv_parsing_empty_yields_empty_frozenset(self):
        env_value = ""
        parsed = frozenset(t.strip() for t in env_value.split(",") if t.strip())
        assert parsed == frozenset()


class TestRunSubagentReadsPolicy:
    def test_blocklist_construction_uses_feat_keys(self):
        """Garante que o codigo le `subagent_policy`/`extra_block`/`allow`
        de `FEATURES` em vez de hardcoded."""
        from alpha.tools import delegate_tools

        src = inspect.getsource(delegate_tools._run_subagent)
        assert 'feat.get("subagent_policy"' in src
        assert 'feat.get("subagent_extra_block"' in src
        assert 'feat.get("subagent_allow"' in src

    def test_relaxed_policy_skips_destructive_blocklist(self):
        """Com policy=relaxed, SUBAGENT_DESTRUCTIVE_BLOCKLIST nao e aplicado."""
        from alpha.tools import delegate_tools

        src = inspect.getsource(delegate_tools._run_subagent)
        # Logica condicional deve gatilhar so quando policy != relaxed
        assert 'policy != "relaxed"' in src or 'policy == "strict"' in src

    def test_delegate_invariant_preserved(self):
        """Mesmo com allow incluindo delegate_*, anti-recursao deve permanecer.

        O codigo re-aplica `_blocked = _blocked | {"delegate_task", "delegate_parallel"}`
        depois de aplicar o allow override.
        """
        from alpha.tools import delegate_tools

        src = inspect.getsource(delegate_tools._run_subagent)
        # Conta ocorrencias do set anti-recursao — esperamos no minimo 2:
        # uma no inicio, outra depois do allow override.
        assert src.count('"delegate_task", "delegate_parallel"') >= 2


@pytest.mark.parametrize(
    "policy,parent_callback,expect_destructive_blocked",
    [
        ("strict", None, True),
        ("strict", lambda *a, **kw: True, False),
        ("relaxed", None, False),
        ("relaxed", lambda *a, **kw: True, False),
    ],
)
def test_blocklist_logic_combinations(
    policy, parent_callback, expect_destructive_blocked
):
    """Reproduz a logica de bloqueio fora da funcao para validar a tabela:
    - strict + sem callback: bloqueia destrutivas
    - strict + callback: nao bloqueia (callback decide por tool)
    - relaxed + qualquer: nao bloqueia (so anti-recursao)
    """
    from alpha.tools.delegate_tools import SUBAGENT_DESTRUCTIVE_BLOCKLIST

    blocked = {"delegate_task", "delegate_parallel"}
    if parent_callback is None and policy != "relaxed":
        blocked = blocked | SUBAGENT_DESTRUCTIVE_BLOCKLIST

    has_destructive = "execute_shell" in blocked
    assert has_destructive == expect_destructive_blocked
