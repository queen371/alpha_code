"""Test fixtures shared across the suite.

AUDIT_V1.2 #046: BrowserSession e o counter de compressao tem estado de
classe/ContextVar global. Tests rodando em isolamento passam, mas em ordens
diferentes (com vs sem cache, com vs sem `-p no:cacheprovider`) o estado
sobra de um test para outro e produz falsos positivos.

Esta fixture autouse reseta todos os singletons globais antes E depois de
cada test. Custo: trivial (atribuicoes simples). Beneficio: tests
deterministicos independente da ordem de execucao.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset globais entre tests (AUDIT_V1.2 #046)."""
    # ── Helper: reset com tolerancia a ImportError (modulos opcionais) ──
    def _reset_browser_session():
        try:
            from alpha.tools import browser_session as bs
        except ImportError:
            return
        bs.BrowserSession._instance = None
        bs.BrowserSession._lock = None
        bs.BrowserSession._lock_loop = None

    def _reset_compress_counter():
        try:
            from alpha import context as ctx
        except ImportError:
            return
        # ContextVar precisa de set explicito; reset() so funciona em frame
        # do mesmo task. Atribuir 0 em cada test e seguro.
        ctx._compress_consecutive_failures.set(0)

    def _reset_pg_pools():
        try:
            from alpha.tools import database_tools as dbt
        except ImportError:
            return
        dbt._pg_pools.clear()
        dbt._pg_pools_lock = None
        dbt._pg_pools_lock_loop = None

    def _reset_safe_env_cache():
        try:
            from alpha.tools import safe_env
        except ImportError:
            return
        safe_env.invalidate_safe_env_cache()

    def _reset_permission_cache():
        try:
            from alpha import approval
        except ImportError:
            return
        approval.reset_permission_cache()

    # Pre-test reset
    _reset_browser_session()
    _reset_compress_counter()
    _reset_pg_pools()
    _reset_safe_env_cache()
    _reset_permission_cache()

    yield

    # Post-test reset (idempotente — protege o proximo test mesmo se este
    # falhou e deixou state corrompido)
    _reset_browser_session()
    _reset_compress_counter()
    _reset_pg_pools()
    _reset_safe_env_cache()
    _reset_permission_cache()
