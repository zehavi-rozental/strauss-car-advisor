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
    """The rate limits of the representative API (live QA 22/10).

    The rep screen's polling and the rep's answers used to share one limit per address, so ~7 answers in a
    minute were refused, and reps on one network (a venue's Wi-Fi) shared one budget. Now nothing is shared
    between reading and answering:
    - `failures` (per address): wrong or missing codes; past it the address gets nothing at all until the window
      frees one. Checked before the code, so guessing stays limited (10 a minute, stricter than the old 60);
    - `reads` (per address): the screens' polling. A refused read pauses a screen, never an answer;
    - `writes` (per rep session: the address and the screen's session id, sent after login): each rep's answers,
      so reps on one network do not share them. Counted only after the code was verified, so a session id is
      never created by a stranger;
    - `address_writes` (per address): every answer from the address, whatever session ids a code holder sends.
    A request refused by one limit is not counted in the others.
    """

    def __init__(self, reads: int, writes: int, address_writes: int, failures: int, window_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self.reads = RateLimiter(reads, window_s, clock)
        self.writes = RateLimiter(writes, window_s, clock)
        self.address_writes = RateLimiter(address_writes, window_s, clock)
        self.failures = RateLimiter(failures, window_s, clock)
        self._lock = threading.Lock()

    def locked(self, address: str) -> Optional[int]:
        """Too many wrong codes from this address: the seconds to wait. None otherwise."""
        with self._lock:
            return self.failures.retry_after(address) if self.failures.full(address) else None

    def failed(self, address: str) -> None:
        """A wrong or missing access code from this address."""
        self.failures.hit(address)

    def admit(self, address: str, kind: RepKind, session: Optional[str] = None) -> Optional[int]:
        """An authorized request. None: admitted (and counted). Otherwise the seconds to wait (Retry-After).
        Without a session id (an older screen, a script) the answers are counted for the address."""
        checks = ([(self.reads, address)] if kind == "read"
                  else [(self.address_writes, address), (self.writes, f"{address}|{session or ''}")])
        with self._lock:
            for limiter, key in checks:
                if limiter.full(key):
                    return limiter.retry_after(key)
            for limiter, key in checks:
                limiter.hit(key)
            return None
