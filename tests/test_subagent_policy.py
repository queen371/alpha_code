"""Coverage para `subagent_policy` configuravel via runtime getters.

Cobre:
- `get_subagent_policy()` / `get_subagent_extra_block()` / `get_subagent_allow()`
  exportados de `alpha.config`
- Defaults preservam comportamento antigo (strict + nada extra)
- AUDIT_V1.2 #014: getters leem env A CADA CALL (nao cache de import-time)
  — `monkeypatch.setenv` pos-import deve afetar comportamento
- `delegate_tools._run_subagent` usa os getters em vez de FEATURES dict
- `delegate_*` sempre bloqueado (anti-recursao) mesmo com policy=relaxed
  ou allow incluindo delegate_task
"""

from __future__ import annotations

import inspect

import pytest


class TestGettersDefaults:
    """Valores default quando env nao esta setada."""

    def test_default_policy_is_strict(self, monkeypatch):
        monkeypatch.delenv("ALPHA_SUBAGENT_POLICY", raising=False)
        from alpha.config import get_subagent_policy
        assert get_subagent_policy() == "strict"

    def test_default_extra_block_empty(self, monkeypatch):
        monkeypatch.delenv("ALPHA_SUBAGENT_EXTRA_BLOCK", raising=False)
        from alpha.config import get_subagent_extra_block
        assert get_subagent_extra_block() == frozenset()

    def test_default_allow_empty(self, monkeypatch):
        monkeypatch.delenv("ALPHA_SUBAGENT_ALLOW", raising=False)
        from alpha.config import get_subagent_allow
        assert get_subagent_allow() == frozenset()


class TestRuntimeEnvOverrides:
    """AUDIT_V1.2 #014: getters refletem mudanca runtime em os.environ.

    Antes do fix, `FEATURES["subagent_policy"]` era resolvido no IMPORT do
    modulo, congelando a flag. Hooks/scripts/testes que setassem env apos
    o import nao surtiam efeito ate reload — confusao operacional + bug
    silencioso. Agora cada call le env de novo.
    """

    def test_policy_changes_reflect_immediately(self, monkeypatch):
        from alpha.config import get_subagent_policy

        monkeypatch.setenv("ALPHA_SUBAGENT_POLICY", "relaxed")
        assert get_subagent_policy() == "relaxed"

        monkeypatch.setenv("ALPHA_SUBAGENT_POLICY", "strict")
        assert get_subagent_policy() == "strict"

    def test_extra_block_csv_parsing(self, monkeypatch):
        from alpha.config import get_subagent_extra_block

        monkeypatch.setenv(
            "ALPHA_SUBAGENT_EXTRA_BLOCK",
            "write_file, edit_file ,  http_request",
        )
        assert get_subagent_extra_block() == {
            "write_file", "edit_file", "http_request",
        }

    def test_extra_block_empty_yields_empty_frozenset(self, monkeypatch):
        from alpha.config import get_subagent_extra_block

        monkeypatch.setenv("ALPHA_SUBAGENT_EXTRA_BLOCK", "")
        assert get_subagent_extra_block() == frozenset()

    def test_allow_runtime_change(self, monkeypatch):
        from alpha.config import get_subagent_allow

        # Sem env: vazio.
        monkeypatch.delenv("ALPHA_SUBAGENT_ALLOW", raising=False)
        assert get_subagent_allow() == frozenset()

        # Set: returns parsed.
        monkeypatch.setenv("ALPHA_SUBAGENT_ALLOW", "execute_shell,http_request")
        assert get_subagent_allow() == {"execute_shell", "http_request"}

        # Unset: back to empty (NOT cached).
        monkeypatch.delenv("ALPHA_SUBAGENT_ALLOW", raising=False)
        assert get_subagent_allow() == frozenset()


class TestRunSubagentReadsGetters:
    """`_run_subagent` chama os getters runtime em vez de FEATURES dict."""

    def test_run_subagent_calls_getters(self):
        from alpha.tools import delegate_tools

        src = inspect.getsource(delegate_tools._run_subagent)
        # Os 3 getters devem ser chamados.
        assert "get_subagent_policy()" in src
        assert "get_subagent_extra_block()" in src
        assert "get_subagent_allow()" in src

    def test_does_not_use_stale_feat_dict(self):
        """Garante que o codigo NAO le mais via `feat.get("subagent_*")`,
        evitando regressao silenciosa do cache import-time."""
        from alpha.tools import delegate_tools

        src = inspect.getsource(delegate_tools._run_subagent)
        assert 'feat.get("subagent_policy"' not in src
        assert 'feat.get("subagent_extra_block"' not in src
        assert 'feat.get("subagent_allow"' not in src

    def test_relaxed_policy_skips_destructive_blocklist(self):
        from alpha.tools import delegate_tools

        src = inspect.getsource(delegate_tools._run_subagent)
        assert 'policy != "relaxed"' in src or 'policy == "strict"' in src

    def test_delegate_invariant_preserved(self):
        """Mesmo com allow incluindo delegate_*, anti-recursao permanece."""
        from alpha.tools import delegate_tools

        src = inspect.getsource(delegate_tools._run_subagent)
        # Conta ocorrencias — esperamos no minimo 2 (inicio + apos allow override).
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
