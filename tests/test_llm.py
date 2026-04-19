"""Tests for LLM module — retry logic and backoff calculation."""

from alpha.llm import MAX_BACKOFF, MAX_RETRIES, _calc_backoff


class TestBackoff:
    def test_first_attempt(self):
        delay = _calc_backoff(0)
        assert delay == 1.0  # INITIAL_BACKOFF

    def test_exponential_growth(self):
        d0 = _calc_backoff(0)
        d1 = _calc_backoff(1)
        d2 = _calc_backoff(2)
        assert d1 > d0
        assert d2 > d1

    def test_capped_at_max(self):
        delay = _calc_backoff(100)
        assert delay == MAX_BACKOFF

    def test_retry_after_header_respected(self):
        delay = _calc_backoff(0, retry_after=10.0)
        assert delay == 10.0

    def test_retry_after_capped(self):
        delay = _calc_backoff(0, retry_after=999.0)
        assert delay == MAX_BACKOFF

    def test_max_retries_is_3(self):
        assert MAX_RETRIES == 3
