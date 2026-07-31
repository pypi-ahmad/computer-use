"""Bridge between v2 sessions and the existing Computer Use execution engine."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionRequest:
    task: str
    provider: str
    model_id: str
    api_key: str
    max_steps: int
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ExecutionOutcome:
    session_id: str
    status: str
    actions: tuple[dict[str, Any], ...]
    duration_ms: float


ExecutionStarter = Callable[[ExecutionRequest], Awaitable[ExecutionOutcome]]


class V2Orchestrator:
    def __init__(self) -> None:
        self._starter: ExecutionStarter | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def configure(self, starter: ExecutionStarter) -> None:
        self._starter = starter

    async def start(self, request: ExecutionRequest) -> ExecutionOutcome:
        if self._starter is None:
            raise RuntimeError("v2 execution bridge is not configured")
        return await self._starter(request)

    def track(self, session_id: str, task: asyncio.Task[None]) -> None:
        self._tasks[session_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(session_id, None))

    def stop(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


orchestrator = V2Orchestrator()
