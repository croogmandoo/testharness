"""In-process sliding-window rate limiting for unauthenticated endpoints.

State lives in process memory, so limits are per worker process and reset
on restart. That is enough to blunt online password guessing against
``/auth/login`` and ``/setup``; it is not a distributed limiter.

Keys are usually the client IP (``request.client.host``). Behind a reverse
proxy every client shares the proxy's IP unless uvicorn is told to trust
``X-Forwarded-For`` (``forwarded_allow_ips``).
"""
import threading
import time
from collections import deque
from typing import Callable, Dict, Deque


class SlidingWindowLimiter:
    """Allow at most ``max_events`` events per key in any ``window_seconds``."""

    def __init__(self, max_events: int, window_seconds: float,
                 clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000):
        self.max_events = max_events
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Deque[float]:
        q = self._events.get(key)
        if q is None:
            return deque()
        cutoff = now - self.window_seconds
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del self._events[key]
        return q

    def _sweep(self, now: float) -> None:
        """Drop every key whose events have all expired."""
        for k in list(self._events):
            self._prune(k, now)

    def hit(self, key: str) -> None:
        """Record one event for ``key``.

        Memory stays bounded at ``max_keys`` tracked keys: when a new key
        would exceed it, expired keys are swept first and, failing that, the
        key with the stalest latest event is evicted."""
        with self._lock:
            now = self._clock()
            self._prune(key, now)
            if key not in self._events and len(self._events) >= self.max_keys:
                self._sweep(now)
                # Make room for the new key if the sweep freed nothing.
                if len(self._events) >= self.max_keys:
                    stalest = min(self._events, key=lambda k: self._events[k][-1])
                    del self._events[stalest]
            self._events.setdefault(key, deque()).append(now)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def is_limited(self, key: str) -> bool:
        """True once ``key`` has ``max_events`` events inside the window."""
        with self._lock:
            return len(self._prune(key, self._clock())) >= self.max_events

    def retry_after(self, key: str) -> int:
        """Seconds until ``key`` drops below the limit (0 if not limited)."""
        with self._lock:
            now = self._clock()
            q = self._prune(key, now)
            if len(q) < self.max_events:
                return 0
            oldest_relevant = q[len(q) - self.max_events]
            return max(1, int(oldest_relevant + self.window_seconds - now) + 1)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


# 20 failed logins per IP per 5 minutes.
login_limiter = SlidingWindowLimiter(max_events=20, window_seconds=300)
# 10 /setup submissions per IP per 5 minutes.
setup_limiter = SlidingWindowLimiter(max_events=10, window_seconds=300)


def client_ip(request) -> str:
    proxy_info = getattr(getattr(request, "state", None), "proxy", None)
    if isinstance(proxy_info, dict) and proxy_info.get("effective_client_ip"):
        return proxy_info["effective_client_ip"]
    client = getattr(request, "client", None)
    return (client.host if client else None) or "unknown"
