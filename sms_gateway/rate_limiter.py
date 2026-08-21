"""Token-bucket rate limiter for per-provider throttling."""
import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: float            # max tokens (burst size)
    refill_rate: float         # tokens per second
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    def try_acquire(self) -> bool:
        """Non-blocking: return True if a token was consumed, False if empty."""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    async def acquire(self, timeout: float = 30.0) -> bool:
        """Blocking (async) acquire — waits until a token is available or timeout."""
        deadline = time.monotonic() + timeout
        while not self.try_acquire():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True