from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from time import monotonic


class TTLCache[T]:
    """Tiny in-memory TTL cache with bounded insertion-order eviction."""

    def __init__(self, ttl: float, max_entries: int = 128) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if monotonic() - stored_at > self._ttl:
            del self._entries[key]
            return None
        return value

    def put(self, key: str, value: T) -> None:
        self._entries[key] = (monotonic(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class SlidingWindowRateLimiter:
    """In-memory per-actor limit for expensive operations."""

    def __init__(self, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._requests: dict[str, deque[float]] = {}

    def allow(self, actor: str) -> bool:
        now = monotonic()
        requests = self._requests.setdefault(actor, deque())
        while requests and now - requests[0] >= self._window:
            requests.popleft()
        if len(requests) >= self._limit:
            return False
        requests.append(now)
        return True


class Throttle:
    """Serialize upstream calls to keep request rates below provider limits."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self._interval - monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = monotonic()
