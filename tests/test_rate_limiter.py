"""Tests for the token-bucket rate limiter."""
import asyncio
import time

import pytest

from sms_gateway.rate_limiter import TokenBucket


class TestTokenBucket:
    def test_initial_burst(self):
        """Bucket starts full — can consume up to capacity immediately."""
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        for _ in range(5):
            assert bucket.try_acquire() is True
        # Now empty
        assert bucket.try_acquire() is False

    def test_refill_over_time(self):
        """Tokens refill at the configured rate."""
        bucket = TokenBucket(capacity=2, refill_rate=10.0)  # 10 tokens/sec
        bucket.try_acquire()
        bucket.try_acquire()
        assert bucket.try_acquire() is False
        # Wait 0.15s → should refill ~1.5 tokens
        time.sleep(0.15)
        assert bucket.try_acquire() is True

    @pytest.mark.asyncio
    async def test_acquire_with_wait(self):
        """Async acquire waits for a token to become available."""
        bucket = TokenBucket(capacity=1, refill_rate=100.0)  # fast refill
        assert await bucket.acquire() is True  # consume the only token
        # Should wait briefly then succeed
        result = await bucket.acquire(timeout=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_timeout(self):
        """Acquire returns False on timeout with zero refill."""
        bucket = TokenBucket(capacity=1, refill_rate=0.0)  # never refills
        assert await bucket.acquire() is True  # consume the only token
        result = await bucket.acquire(timeout=0.2)
        assert result is False

    def test_partial_consume_then_refill(self):
        """Consume some, wait, consume more — check smooth refill."""
        bucket = TokenBucket(capacity=10, refill_rate=10.0)
        # Consume 3
        for _ in range(3):
            bucket.try_acquire()
        # Wait 0.1s → +1 token
        time.sleep(0.1)
        # Should have ~8 tokens now, can consume at least 7
        count = 0
        while bucket.try_acquire():
            count += 1
        assert count >= 7  # at least 7 available after refill