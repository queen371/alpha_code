"""Global LLM rate limiter — token bucket per provider (#D008).

Prevents sub-agents in delegate_parallel from exhausting provider rate limits.
Each provider gets its own bucket; all LLM calls (main agent + sub-agents)
acquire a token before making an HTTP request.

Configurable via env vars:
  ALPHA_RATE_LIMIT_<PROVIDER>=<req_per_minute>,<burst>
  Default: 60 req/min, burst=10 (generous — doesn't affect single-agent use).
"""

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket rate limiter — thread-safe via asyncio.Lock."""

    __slots__ = ("rate", "burst", "tokens", "last_refill", "_lock")

    def __init__(self, rate: float, burst: int):
        self.rate = rate  # tokens per second
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire one token, waiting if necessary."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(float(self.burst), self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return

            # Calculate wait time for 1 token
            wait = (1.0 - self.tokens) / self.rate
            self.tokens = 0.0

        logger.debug(f"Rate limiter: waiting {wait:.2f}s for token")
        await asyncio.sleep(wait)


# ─── Provider defaults (requests per minute, burst) ───

_DEFAULT_LIMITS: dict[str, tuple[float, int]] = {
    # DeepSeek free tier: very restrictive. Default: 5 req/min, burst=2.
    "deepseek": (5.0, 2),
    # OpenAI free tier: ~3 req/min, burst=2.
    "openai": (3.0, 2),
    # Anthropic: varies. Default: 10 req/min, burst=3.
    "anthropic": (10.0, 3),
    # Grok: generous. Default: 30 req/min, burst=5.
    "grok": (30.0, 5),
    # Ollama (local): no limit needed. Default: 600 req/min, burst=30.
    "ollama": (600.0, 30),
    # Fallback for unknown providers.
    "_default": (60.0, 10),
}

# Global bucket cache: provider_name -> TokenBucket
_buckets: dict[str, TokenBucket] = {}


def _parse_limit(provider: str) -> tuple[float, int]:
    """Parse ALPHA_RATE_LIMIT_<PROVIDER> env var or use default."""
    env_key = f"ALPHA_RATE_LIMIT_{provider.upper()}"
    raw = os.environ.get(env_key, "")
    if raw:
        try:
            parts = raw.split(",")
            rpm = float(parts[0].strip())
            burst = int(parts[1].strip()) if len(parts) > 1 else max(1, int(rpm / 6))
            return (rpm / 60.0, burst)
        except (ValueError, IndexError):
            logger.warning(f"Invalid {env_key}='{raw}', using default")
    return _DEFAULT_LIMITS.get(provider.lower(), _DEFAULT_LIMITS["_default"])


def get_provider_limiter(provider: str) -> TokenBucket:
    """Get or create the TokenBucket for a provider."""
    key = provider.lower()
    if key not in _buckets:
        rate, burst = _parse_limit(key)
        _buckets[key] = TokenBucket(rate=rate, burst=burst)
        logger.info(
            f"Rate limiter for '{key}': {rate * 60:.0f} req/min, burst={burst}"
        )
    return _buckets[key]


async def acquire_llm_token(provider: str) -> None:
    """Acquire a rate-limit token before making an LLM API call."""
    bucket = get_provider_limiter(provider)
    await bucket.acquire()
