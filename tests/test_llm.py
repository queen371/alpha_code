"""Tests for LLM module — retry logic and backoff calculation.

Backoff applies full jitter (50%-100% of base on the exponential path,
80%-120% on the retry_after path) to avoid thundering-herd retries.
Tests assert the expected jitter window, not exact values.
"""

import statistics

from alpha.llm import INITIAL_BACKOFF, MAX_BACKOFF, MAX_RETRIES, _calc_backoff


class TestBackoff:
    def test_first_attempt_within_jitter_window(self):
        # Exponential path: base = INITIAL_BACKOFF, jittered to [0.5*base, 1.0*base]
        for _ in range(50):
            delay = _calc_backoff(0)
            assert 0.5 * INITIAL_BACKOFF <= delay <= INITIAL_BACKOFF

    def test_exponential_growth_on_average(self):
        # With jitter individual samples can invert; compare medians.
        samples_0 = [_calc_backoff(0) for _ in range(100)]
        samples_1 = [_calc_backoff(1) for _ in range(100)]
        samples_2 = [_calc_backoff(2) for _ in range(100)]
        assert statistics.median(samples_1) > statistics.median(samples_0)
        assert statistics.median(samples_2) > statistics.median(samples_1)

    def test_capped_at_max(self):
        # Large attempt → exponential blows up → cap dominates and wins out.
        for _ in range(50):
            delay = _calc_backoff(100)
            assert delay <= MAX_BACKOFF

    def test_retry_after_header_respected(self):
        # retry_after path: returned within ±20% of header value.
        for _ in range(50):
            delay = _calc_backoff(0, retry_after=10.0)
            assert 8.0 <= delay <= 12.0

    def test_retry_after_capped(self):
        # Header above MAX_BACKOFF gets clamped before jitter is applied.
        # Window: [MAX_BACKOFF*0.8, MAX_BACKOFF*1.2]
        for _ in range(50):
            delay = _calc_backoff(0, retry_after=999.0)
            assert MAX_BACKOFF * 0.8 <= delay <= MAX_BACKOFF * 1.2

    def test_max_retries_is_3(self):
        assert MAX_RETRIES == 3
