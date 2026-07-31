"""Deterministic route fallback, transient retry, and circuit breaking."""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteSpec:
    id: str
    provider: str
    model_id: str
    max_attempts: int = 3


class RouteFailure(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True)
class RouteResult[T]:
    value: T
    route_id: str
    attempts: int
    failures: tuple[str, ...]


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, recovery_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._threshold = failure_threshold
        self._recovery = recovery_seconds
        self._clock = clock
        self._state: dict[str, tuple[int, float]] = {}

    def allows(self, route_id: str) -> bool:
        failures, opened_at = self._state.get(route_id, (0, 0.0))
        if failures < self._threshold:
            return True
        if self._clock() - opened_at >= self._recovery:
            self._state[route_id] = (0, 0.0)
            return True
        return False

    def success(self, route_id: str) -> None:
        self._state.pop(route_id, None)

    def failure(self, route_id: str) -> None:
        count, _ = self._state.get(route_id, (0, 0.0))
        count += 1
        self._state[route_id] = (count, self._clock() if count >= self._threshold else 0.0)

    def state(self, route_id: str) -> str:
        return "CLOSED" if self.allows(route_id) else "OPEN"


async def run_with_fallback[T](
    routes: Sequence[RouteSpec],
    invoke: Callable[[RouteSpec], Awaitable[T]],
    breaker: CircuitBreaker,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RouteResult[T]:
    if not routes:
        raise ValueError("At least one route is required")
    failures: list[str] = []
    attempts = 0
    for route in routes:
        if not breaker.allows(route.id):
            failures.append(f"{route.id}: circuit open")
            continue
        for attempt in range(max(1, route.max_attempts)):
            attempts += 1
            try:
                value = await invoke(route)
            except RouteFailure as exc:
                failures.append(f"{route.id}: {exc}")
                breaker.failure(route.id)
                if not exc.retryable or not breaker.allows(route.id) or attempt + 1 >= route.max_attempts:
                    break
                delay = exc.retry_after if exc.retry_after is not None else min(8.0, 0.25 * (2**attempt))
                await sleep(delay + random.uniform(0.0, delay * 0.2))
            else:
                breaker.success(route.id)
                return RouteResult(value=value, route_id=route.id, attempts=attempts, failures=tuple(failures))
    raise RouteFailure("All configured routes failed: " + "; ".join(failures), retryable=False)
