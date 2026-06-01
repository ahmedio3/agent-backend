"""
Per-key rate limiter for Vire Max (gemini-3.5-flash).
Tracks each API key independently: 5 req/min, 20 req/day.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class KeyUsage:
    index: int            # 1-based display index
    used_per_minute: int
    remaining_per_minute: int
    used_per_day: int
    remaining_per_day: int
    reset_minute_ts: float   # unix timestamp
    reset_day_ts: float
    status: str              # "available" | "minute_limited" | "day_exhausted"


class SingleKeyLimiter:
    """Rate limiter for one API key."""

    def __init__(self, per_minute: int = 5, per_day: int = 20) -> None:
        self.per_minute = per_minute
        self.per_day = per_day
        self._min_ts: deque[float] = deque()
        self._day_ts: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _evict(self, now: float) -> None:
        while self._min_ts and self._min_ts[0] < now - 60:
            self._min_ts.popleft()
        while self._day_ts and self._day_ts[0] < now - 86400:
            self._day_ts.popleft()

    def is_available(self) -> bool:
        now = time.time()
        self._evict(now)
        return len(self._min_ts) < self.per_minute and len(self._day_ts) < self.per_day

    def is_day_exhausted(self) -> bool:
        now = time.time()
        self._evict(now)
        return len(self._day_ts) >= self.per_day

    async def try_acquire(self) -> bool:
        """Non-blocking acquire. Returns True if acquired, False if rate-limited."""
        async with self._lock:
            now = time.time()
            self._evict(now)
            if len(self._day_ts) >= self.per_day:
                return False
            if len(self._min_ts) >= self.per_minute:
                return False
            self._min_ts.append(now)
            self._day_ts.append(now)
            return True

    def force_mark_minute_exhausted(self) -> None:
        """Force-fill the minute bucket (called when Gemini API returns quota error)."""
        now = time.time()
        self._min_ts.clear()
        for _ in range(self.per_minute):
            self._min_ts.append(now)

    def force_mark_day_exhausted(self) -> None:
        """Force-fill the day bucket (called when Gemini API returns day quota error)."""
        now = time.time()
        self._day_ts.clear()
        for _ in range(self.per_day):
            self._day_ts.append(now)

    def usage(self, index: int) -> KeyUsage:
        now = time.time()
        self._evict(now)
        used_min = len(self._min_ts)
        used_day = len(self._day_ts)
        rem_min = self.per_minute - used_min
        rem_day = self.per_day - used_day

        reset_min = (self._min_ts[0] + 60) if self._min_ts else now + 60
        reset_day = (self._day_ts[0] + 86400) if self._day_ts else now + 86400

        if used_day >= self.per_day:
            status = "day_exhausted"
        elif used_min >= self.per_minute:
            status = "minute_limited"
        else:
            status = "available"

        return KeyUsage(
            index=index,
            used_per_minute=used_min,
            remaining_per_minute=max(0, rem_min),
            used_per_day=used_day,
            remaining_per_day=max(0, rem_day),
            reset_minute_ts=reset_min,
            reset_day_ts=reset_day,
            status=status,
        )


class PerKeyRateLimiter:
    """Manages rate limits for a list of API keys with round-robin selection."""

    def __init__(self, keys: list[str], per_minute: int = 5, per_day: int = 20) -> None:
        self._keys = keys
        self._limiters: list[SingleKeyLimiter] = [
            SingleKeyLimiter(per_minute, per_day) for _ in keys
        ]
        self._current_idx = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> tuple[str, int] | None:
        """
        Try to get an available key.
        Returns (api_key, key_index) or None if all keys are exhausted/limited.
        Raises RateLimitExceeded if all day-exhausted.
        """
        async with self._lock:
            n = len(self._keys)
            # Try all keys starting from current
            for i in range(n):
                idx = (self._current_idx + i) % n
                acquired = await self._limiters[idx].try_acquire()
                if acquired:
                    self._current_idx = (idx + 1) % n
                    return self._keys[idx], idx
            return None

    def all_usage(self) -> list[KeyUsage]:
        return [lim.usage(i + 1) for i, lim in enumerate(self._limiters)]

    def overall_status(self) -> str:
        usages = self.all_usage()
        if all(u.status == "day_exhausted" for u in usages):
            return "day_exhausted"
        if all(u.status in ("day_exhausted", "minute_limited") for u in usages):
            return "minute_limited"
        return "available"


# ── Singletons ────────────────────────────────────────────────────────────────

_max_limiter: PerKeyRateLimiter | None = None
_core_limiter: SingleKeyLimiter | None = None


def get_max_limiter() -> PerKeyRateLimiter:
    global _max_limiter
    if _max_limiter is None:
        import os
        raw = os.getenv("VIRE_MAX_KEYS", "")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            # Fall back to GEMINI_KEYS if no dedicated Max keys
            raw = os.getenv("GEMINI_KEYS", "")
            keys = [k.strip() for k in raw.split(",") if k.strip()]
        rpm = int(os.getenv("MAX_RPM", "5"))
        rpd = int(os.getenv("MAX_RPD", "20"))
        _max_limiter = PerKeyRateLimiter(keys, per_minute=rpm, per_day=rpd)
    return _max_limiter


def get_core_limiter() -> SingleKeyLimiter:
    global _core_limiter
    if _core_limiter is None:
        import os
        rpm = int(os.getenv("CORE_RPM", "5"))
        rpd = int(os.getenv("CORE_RPD", "20"))
        _core_limiter = SingleKeyLimiter(per_minute=rpm, per_day=rpd)
    return _core_limiter
