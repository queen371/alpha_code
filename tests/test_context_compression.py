"""Tests for context compression fallback (AUDIT V1.1 #062).

Cobre o fallback de truncacao crua quando o LLM falha em produzir sumario
em N tentativas consecutivas — sem isso, sessoes quebram com HTTP 400 em
provider outage.
"""

import pytest

import alpha.context as ctx
from alpha.context import _hard_truncate, compress_context


@pytest.fixture(autouse=True)
def _reset_failure_counter():
    """Garante isolamento entre testes — counter agora e ContextVar."""
    ctx._compress_consecutive_failures.set(0)
    yield
    ctx._compress_consecutive_failures.set(0)


class TestHardTruncate:
    def test_keeps_system_and_recent(self):
        msgs = [{"role": "system", "content": "sys"}]
        msgs += [{"role": "user", "content": f"u{i}"} for i in range(20)]
        result = _hard_truncate(msgs, start=1, end=15, keep_recent=5)
        assert result[0] == {"role": "system", "content": "sys"}
        assert len(result) == 6  # system + 5 recent
        assert result[-1]["content"] == "u19"

    def test_drops_orphan_tool_messages(self):
        msgs = [{"role": "system", "content": "sys"}]
        # Tail comeca com role=tool sem assistant correspondente — proibido
        # pela API. _hard_truncate deve podar.
        msgs += [
            {"role": "tool", "tool_call_id": "1", "content": "result"},
            {"role": "user", "content": "next"},
        ]
        result = _hard_truncate(msgs, start=1, end=2, keep_recent=2)
        # tail era [tool, user]; tool foi removida
        assert all(m.get("role") != "tool" or i > 1 for i, m in enumerate(result))
        assert result[-1]["content"] == "next"

    def test_drops_consecutive_orphan_tools(self):
        msgs = [{"role": "system", "content": "sys"}]
        msgs += [
            {"role": "tool", "tool_call_id": "1", "content": "r1"},
            {"role": "tool", "tool_call_id": "2", "content": "r2"},
            {"role": "user", "content": "u"},
        ]
        result = _hard_truncate(msgs, start=1, end=3, keep_recent=3)
        assert [m["role"] for m in result] == ["system", "user"]


class TestCompressContextFallback:
    @pytest.mark.asyncio
    async def test_empty_summary_first_failure_skips(self):
        # Primeira falha so faz skip — nao trunca ainda.
        messages = _make_long_conversation(50)
        original_len = len(messages)

        async def empty_stream(*args, **kwargs):
            if False:
                yield None  # generator vazio

        result = await compress_context(messages, "deepseek", empty_stream)
        assert len(result) == original_len  # skip, nao truncou
        assert ctx._compress_consecutive_failures.get() == 1

    @pytest.mark.asyncio
    async def test_empty_summary_threshold_triggers_truncation(self):
        messages = _make_long_conversation(50)
        original_len = len(messages)

        async def empty_stream(*args, **kwargs):
            if False:
                yield None

        # Pre-bumb pra estar 1 falha abaixo do threshold
        ctx._compress_consecutive_failures.set(ctx._COMPRESS_FAIL_TRUNCATE_THRESHOLD - 1)
        result = await compress_context(messages, "deepseek", empty_stream)
        assert len(result) < original_len  # truncou de fato
        assert result[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_exception_in_stream_counts_as_failure(self):
        messages = _make_long_conversation(50)

        async def failing_stream(*args, **kwargs):
            raise ConnectionError("provider outage")
            yield None  # unreachable

        ctx._compress_consecutive_failures.set(ctx._COMPRESS_FAIL_TRUNCATE_THRESHOLD - 1)
        result = await compress_context(messages, "deepseek", failing_stream)
        # Threshold atingido -> truncou
        assert len(result) < 50

    @pytest.mark.asyncio
    async def test_successful_summary_resets_counter(self):
        messages = _make_long_conversation(50)

        async def good_stream(*args, **kwargs):
            yield {"type": "content_token", "token": "summary text"}
            yield {"type": "final", "content": "summary text"}

        ctx._compress_consecutive_failures.set(5)
        await compress_context(messages, "deepseek", good_stream)
        assert ctx._compress_consecutive_failures.get() == 0


def _make_long_conversation(n: int) -> list[dict]:
    msgs = [{"role": "system", "content": "system prompt"}]
    for i in range(n):
        role = "assistant" if i % 2 else "user"
        msgs.append({"role": role, "content": f"message {i} " + "x" * 200})
    return msgs


class TestFailureCounterIsolation:
    """Regressao: counter migrou de global int para ContextVar pra evitar
    que sub-agents contaminem o budget de retry do parent."""

    def test_counter_isolated_across_contexts(self):
        # Em contextos copiados (Context.copy()), `set` em um nao afeta o outro.
        import contextvars
        ctx._compress_consecutive_failures.set(0)

        sub_ctx = contextvars.copy_context()

        def bump():
            ctx._compress_consecutive_failures.set(99)
            return ctx._compress_consecutive_failures.get()

        sub_value = sub_ctx.run(bump)
        parent_value = ctx._compress_consecutive_failures.get()

        assert sub_value == 99
        assert parent_value == 0, (
            "Sub-context modification leaked into parent — ContextVar "
            "isolation broken"
        )
