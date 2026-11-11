"""Small in-memory sliding-window rate limiter (per client key) for login / sign-up endpoints."""
from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_events: int, window_s: int):
        self.max, self.window = max_events, window_s
        self._events: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record an attempt; returns False once the key exceeded the allowed number of attempts in the window."""
        now = time.time() if now is None else now
        q = self._events[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max:
            return False
        q.append(now)
        return True
