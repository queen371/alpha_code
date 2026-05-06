"""Regression tests for DEEP_PERFORMANCE V2.0 ALTOs.

Cobre #D013 (loop detection complexity), #D015 (httpx client loop binding),
#D016 (message list hard cap).
"""

import asyncio

import pytest

from alpha.agent import _detect_loop
from alpha.context import MAX_MESSAGES, MIN_MESSAGES_FOR_COMPRESSION, needs_compression


# ─── #D013 ───────────────────────────────────────────────────────


class TestLoopDetectionStillWorks:
    """Counter + index by tool name nao quebra detection real."""

    def test_exact_repeat_detected(self):
        sig = "read_file:{\"path\": \"/x\"}"
        recent = [sig] * 4
        result = _detect_loop([sig], recent, [])
        assert result is not None
        assert "exact repeat" in result

    def test_below_threshold_not_flagged(self):
        sig = "read_file:{\"path\": \"/x\"}"
        recent = [sig] * 2  # menor que _MAX_REPEAT_CALLS=3
        assert _detect_loop([sig], recent, []) is None

    def test_different_tools_no_false_positive(self):
        # 5 calls, todos com nomes diferentes -> nao loop
        recent = [
            "read_file:{\"path\": \"/a\"}",
            "write_file:{\"path\": \"/b\"}",
            "execute_shell:{\"command\": \"ls\"}",
            "list_directory:{\"path\": \".\"}",
            "search_files:{\"pattern\": \"foo\"}",
        ]
        new_sig = "git_operation:{\"action\": \"status\"}"
        assert _detect_loop([new_sig], recent, []) is None

    def test_similar_calls_still_detected(self):
        # 5 calls do mesmo tool com paths similares -> deve ainda detectar
        # apesar do short-circuit de "candidates < threshold"
        recent = [
            "read_file:{\"path\": \"/long/path/file_1.py\"}",
            "read_file:{\"path\": \"/long/path/file_2.py\"}",
            "read_file:{\"path\": \"/long/path/file_3.py\"}",
            "read_file:{\"path\": \"/long/path/file_4.py\"}",
            "read_file:{\"path\": \"/long/path/file_5.py\"}",
        ]
        new_sig = "read_file:{\"path\": \"/long/path/file_6.py\"}"
        result = _detect_loop([new_sig], recent, [])
        # Pode ser similar ou exact dependendo do threshold; o ponto e que
        # detecta loop real
        assert result is not None


# ─── #D015 ───────────────────────────────────────────────────────


class TestHttpxClientLoopBinding:
    """Client deve ser recriado quando o event loop muda."""

    def test_client_recreated_across_loops(self):
        from alpha import web_search

        # Reset module state
        web_search._shared_client = None
        web_search._client_loop = None

        async def get_client():
            return await web_search._get_shared_client()

        # Loop 1: cria
        c1 = asyncio.run(get_client())
        loop1_ref = web_search._client_loop

        # Loop 2: deve detectar e recriar (loops diferentes)
        c2 = asyncio.run(get_client())
        loop2_ref = web_search._client_loop

        assert c1 is not c2, "client should be recreated across loops"
        assert loop1_ref is not loop2_ref


# ─── #D016 ───────────────────────────────────────────────────────


class TestMessageHardCap:
    def test_cap_triggers_even_when_tokens_low(self):
        # 600 messages tinychars -> nao bate threshold de tokens mas bate cap
        msgs = [{"role": "system", "content": "s"}]
        msgs += [{"role": "user", "content": "."} for _ in range(MAX_MESSAGES + 100)]
        assert needs_compression(msgs, "deepseek") is True

    def test_below_cap_and_below_token_threshold_skipped(self):
        # 50 messages tinychars: nao gatilha tokens nem count
        msgs = [{"role": "system", "content": "s"}]
        msgs += [{"role": "user", "content": "."} for _ in range(50)]
        assert needs_compression(msgs, "deepseek") is False

    def test_below_min_short_circuits(self):
        msgs = [{"role": "user", "content": "."}
                for _ in range(MIN_MESSAGES_FOR_COMPRESSION - 1)]
        assert needs_compression(msgs, "deepseek") is False
