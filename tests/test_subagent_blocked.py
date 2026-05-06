"""Regression tests for the sub-agent destructive-tool blocklist.

Cobre AUDIT V1.1 #018 (browser_execute_js + git_operation write actions
escapando da policy) e #102 (cobertura inadequada). Quebra explicitamente
se alguem adicionar uma tool DESTRUCTIVE nova sem decidir conscientemente
se ela deve viver no sub-agent sem callback.
"""

import pytest

from alpha.approval import AUTO_APPROVE_TOOLS
from alpha.tools import TOOL_REGISTRY, ToolSafety, load_all_tools
from alpha.tools.delegate_tools import (
    GIT_READ_ACTIONS,
    SUBAGENT_DESTRUCTIVE_BLOCKLIST,
    _auto_approve_no_callback,
)


@pytest.fixture(scope="module", autouse=True)
def _ensure_tools_loaded():
    load_all_tools()


class TestSubagentBlocklistContents:
    """O conjunto exposto cobre os vetores conhecidos."""

    def test_browser_execute_js_blocked(self):
        # Vetor explicito do #018: JS arbitrario em sessao logada.
        assert "browser_execute_js" in SUBAGENT_DESTRUCTIVE_BLOCKLIST

    def test_browser_interaction_blocked(self):
        for name in (
            "browser_click", "browser_fill",
            "browser_select_option", "browser_press_key",
        ):
            assert name in SUBAGENT_DESTRUCTIVE_BLOCKLIST, name

    def test_classic_destructive_blocked(self):
        for name in (
            "execute_shell", "execute_pipeline", "http_request",
            "query_database", "clipboard_write", "install_package",
        ):
            assert name in SUBAGENT_DESTRUCTIVE_BLOCKLIST, name

    def test_auto_approved_destructive_not_blocked(self):
        # write_file/edit_file/execute_python sao DESTRUCTIVE mas
        # auto-aprovadas por design — nao devem aparecer na blocklist.
        for name in ("write_file", "edit_file", "execute_python",
                     "search_and_replace", "run_tests"):
            assert name not in SUBAGENT_DESTRUCTIVE_BLOCKLIST, name


class TestBlocklistCoversAllDestructiveExceptPolicyExceptions:
    """Garante que toda tool DESTRUCTIVE nova force decisao explicita."""

    # Tools DESTRUCTIVE com tratamento especial fora da blocklist:
    # - delegate_*: bloqueadas separadamente (anti-recursao)
    # - present_plan: ferramenta de planning, sem efeito real
    # - git_operation: gating dinamico via _auto_approve_no_callback
    POLICY_EXCEPTIONS = frozenset({
        "delegate_task", "delegate_parallel",
        "present_plan", "git_operation",
    })

    def test_every_destructive_is_classified(self):
        destructive = {
            n for n, t in TOOL_REGISTRY.items()
            if t.safety == ToolSafety.DESTRUCTIVE
        }
        unclassified = (
            destructive
            - SUBAGENT_DESTRUCTIVE_BLOCKLIST
            - AUTO_APPROVE_TOOLS
            - self.POLICY_EXCEPTIONS
        )
        assert not unclassified, (
            "Tools DESTRUCTIVE sem decisao explicita de policy: "
            f"{sorted(unclassified)}. Adicione a SUBAGENT_DESTRUCTIVE_BLOCKLIST, "
            "AUTO_APPROVE_TOOLS, ou POLICY_EXCEPTIONS deste teste."
        )


class TestGitOperationGate:
    """O gate dinamico de git_operation distingue read de write."""

    def test_read_actions_auto_approved(self):
        for action in ("status", "diff", "log", "branch", "show",
                       "blame", "stash_list", "remote", "tag"):
            assert _auto_approve_no_callback(
                "git_operation", {"action": action}
            ) is True, action

    def test_write_actions_rejected(self):
        # Vetor #018: git push/merge/rebase para remote arbitrario.
        for action in ("push", "merge", "rebase", "reset", "clean",
                       "commit", "checkout"):
            assert _auto_approve_no_callback(
                "git_operation", {"action": action}
            ) is False, action

    def test_missing_action_rejected(self):
        assert _auto_approve_no_callback("git_operation", {}) is False
        assert _auto_approve_no_callback("git_operation", None) is False

    def test_non_git_tool_default_approves(self):
        # Tools que nao sao git_operation seguem o default (True).
        # As destrutivas perigosas ja foram removidas do toolset antes
        # do gate ser consultado.
        assert _auto_approve_no_callback("read_file", {"path": "x"}) is True
        assert _auto_approve_no_callback("write_file", {"path": "x"}) is True

    def test_git_read_actions_set_aligned(self):
        # Defesa contra typo: o set publico bate com o que o gate aceita.
        for action in GIT_READ_ACTIONS:
            assert _auto_approve_no_callback(
                "git_operation", {"action": action}
            ) is True
