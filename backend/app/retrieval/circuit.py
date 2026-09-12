from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


class CircuitOpenError(RuntimeError):
    pass


@dataclass(frozen=True)
class CircuitSnapshot:
    state: str
    opened_at: float | None = None


class CircuitBreaker:
    """In-process breaker for the development deployment.

    A multi-worker deployment should replace this registry with a shared state
    implementation, but the state transition contract remains the same.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        failure_window_seconds: float = 30.0,
        open_seconds: float = 15.0,
        half_open_max_calls: int = 2,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.failure_window_seconds = failure_window_seconds
        self.open_seconds = open_seconds
        self.half_open_max_calls = half_open_max_calls
        self._failures: deque[float] = deque()
        self._state = "CLOSED"
        self._opened_at: float | None = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    async def before_call(self) -> CircuitSnapshot:
        async with self._lock:
            now = time.monotonic()
            if self._state == "OPEN":
                if self._opened_at is None or now - self._opened_at < self.open_seconds:
                    raise CircuitOpenError("circuit is open")
                self._state = "HALF_OPEN"
                self._half_open_calls = 0
            if self._state == "HALF_OPEN":
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError("half-open probe limit reached")
                self._half_open_calls += 1
            return CircuitSnapshot(self._state, self._opened_at)

    async def record_success(self) -> None:
        async with self._lock:
            self._failures.clear()
            self._state = "CLOSED"
            self._opened_at = None
            self._half_open_calls = 0

    async def record_failure(self) -> CircuitSnapshot:
        async with self._lock:
            now = time.monotonic()
            while self._failures and now - self._failures[0] > self.failure_window_seconds:
                self._failures.popleft()
            if self._state == "HALF_OPEN":
                self._state = "OPEN"
                self._opened_at = now
            else:
                self._failures.append(now)
                if len(self._failures) >= self.failure_threshold:
                    self._state = "OPEN"
                    self._opened_at = now
            return CircuitSnapshot(self._state, self._opened_at)


class CircuitRegistry:
    def __init__(self, **breaker_options: float | int) -> None:
        self._options = breaker_options
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()

    async def get(self, dependency: str) -> CircuitBreaker:
        async with self._lock:
            breaker = self._breakers.get(dependency)
            if breaker is None:
                breaker = CircuitBreaker(**self._options)
                self._breakers[dependency] = breaker
            return breaker


_registry: CircuitRegistry | None = None
_registry_options: tuple[tuple[str, float | int], ...] | None = None


async def get_circuit_registry(**options: float | int) -> CircuitRegistry:
    global _registry, _registry_options
    normalized = tuple(sorted(options.items()))
    if _registry is None or _registry_options != normalized:
        _registry = CircuitRegistry(**options)
        _registry_options = normalized
    return _registry
