"""הגבלת קצב בזיכרון: חלון נע של דקה לכל מפתח (שיחה / כתובת IP)."""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Literal, Optional


class RateLimiter:
    def __init__(self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _current(self, key: str) -> deque[float]:
        hits, now = self._hits[key], self.clock()
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        return hits

    def allow(self, key: str) -> bool:
        with self._lock:
            hits = self._current(key)
            if len(hits) >= self.limit:
                return False
            hits.append(self.clock())
            return True

    def full(self, key: str) -> bool:
        with self._lock:
            return len(self._current(key)) >= self.limit

    def hit(self, key: str) -> None:
        with self._lock:
            self._current(key).append(self.clock())

    def retry_after(self, key: str) -> int:
        """Whole seconds until the window frees one place (at least 1): the Retry-After of a 429."""
        with self._lock:
            hits = self._current(key)
            if len(hits) < self.limit:
                return 1
            return max(1, math.ceil(hits[0] + self.window_s - self.clock()))


RepKind = Literal["read", "write"]


class RepGate:
    """The rate limits of the representative API, per client address (live QA 22/10).

    One limit for everything used to be shared by the screen's own polling (40 a minute) and the rep's
    actions, so ~7 answers in a minute were refused. Now:
    - `total` is the old limit and still counts every admitted request: mixing kinds never gets past it;
    - `reads` stays below `total`, so polling (however many tabs) always leaves room for answers;
    - `writes` caps answers on their own;
    - `failures` counts wrong access codes; past it the address gets nothing at all until the window
      frees one (stricter than before against guessing: 10 a minute instead of 60).
    A request refused by one limit is not counted in the others, so a flood of one kind never uses up another.
    """

    def __init__(self, total: int, reads: int, writes: int, failures: int, window_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self.total = RateLimiter(total, window_s, clock)
        self.reads = RateLimiter(reads, window_s, clock)
        self.writes = RateLimiter(writes, window_s, clock)
        self.failures = RateLimiter(failures, window_s, clock)
        self._lock = threading.Lock()

    def admit(self, key: str, kind: RepKind) -> Optional[int]:
        """None: admitted (and counted). Otherwise the seconds to wait (Retry-After)."""
        own = self.reads if kind == "read" else self.writes
        with self._lock:
            for limiter in (self.failures, own, self.total):
                if limiter.full(key):
                    return limiter.retry_after(key)
            own.hit(key)
            self.total.hit(key)
            return None

    def failed(self, key: str) -> None:
        """A wrong or missing access code from this address."""
        self.failures.hit(key)
