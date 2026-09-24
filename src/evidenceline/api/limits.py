"""In-memory rate limits: rolling windows per key, safe to call from several threads.

Kept in the process's memory on purpose: one small service, no database. A restart forgets the counts, which errs
towards letting people in; the global daily brake on model calls is the spending guard.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

HOUR = 3600
DAY = 86400
_SWEEP_EVERY = 1000
"""Forget idle keys every this many checks, so memory does not grow with every address ever seen."""


@dataclass(frozen=True, slots=True)
class Window:
    limit: int
    seconds: int
    name: str
    """How the limit is described to a person, for example '10 questions per hour'."""


@dataclass(frozen=True, slots=True)
class Refusal:
    """A request over a limit: which limit, and how long until a slot frees up (whole seconds, at least 1)."""

    window: Window
    retry_after: int


class RateLimiter:
    """Allows a request for a key only when it is under every window; records it only when it is allowed."""

    def __init__(self, windows: Sequence[Window], clock: Callable[[], float] = time.monotonic) -> None:
        self._windows = tuple(windows)
        self._longest = max((w.seconds for w in self._windows), default=0)
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._checks = 0

    def check(self, key: str) -> Refusal | None:
        """None and the request is counted; or the first window it would break, and nothing is counted."""
        with self._lock:
            now = self._clock()
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self._longest:
                hits.popleft()
            for window in self._windows:
                recent = [t for t in hits if t > now - window.seconds]
                if len(recent) >= window.limit:
                    wait = int(recent[0] + window.seconds - now) + 1
                    return Refusal(window, max(wait, 1))
            hits.append(now)
            self._sweep(now)
            return None

    def forget(self, key: str) -> None:
        """Remove the latest request recorded for ``key``: it turned out to be counted by another limit."""
        with self._lock:
            hits = self._hits.get(key)
            if hits:
                hits.pop()

    def count(self, key: str) -> int:
        """Requests recorded for ``key`` within the longest window."""
        with self._lock:
            now = self._clock()
            return sum(1 for t in self._hits.get(key, ()) if t > now - self._longest)

    def _sweep(self, now: float) -> None:
        self._checks += 1
        if self._checks % _SWEEP_EVERY:
            return
        idle = [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - self._longest]
        for key in idle:
            del self._hits[key]
