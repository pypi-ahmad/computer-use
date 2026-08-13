"""Bridge between v2 sessions and the existing Computer Use execution engine."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionRequest:
    session_id: str
    task: str
    provider: str
    model_id: str
    api_key: str | None
    max_steps: int
    reasoning_effort: str | None = None
    oauth_credentials: Any | None = None
    quota_project_id: str | None = None
    safety_policy: str = "provider_default"
    use_builtin_search: bool = False
    attached_files: tuple[str, ...] = ()
    on_event: Callable[[dict[str, Any]], None] | None = None


@dataclass(frozen=True)
class ExecutionOutcome:
    session_id: str
    status: str
    actions: tuple[dict[str, Any], ...]
    duration_ms: float
    input_tokens: int = 0
    output_tokens: int = 0


ExecutionStarter = Callable[[ExecutionRequest], Awaitable[ExecutionOutcome]]


class V2Orchestrator:
    def __init__(self) -> None:
        self._starter: ExecutionStarter | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._execution_ids: dict[str, str] = {}

    def configure(self, starter: ExecutionStarter) -> None:
        self._starter = starter

    async def start(self, request: ExecutionRequest) -> ExecutionOutcome:
        if self._starter is None:
            raise RuntimeError("v2 execution bridge is not configured")
        return await self._starter(request)

    def track(self, session_id: str, task: asyncio.Task[None]) -> None:
        self._tasks[session_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(session_id, None))

    def publish(self, session_id: str, event: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers.get(session_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(session_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(session_id, None)

    def bind_execution(self, session_id: str, execution_id: str) -> None:
        self._execution_ids[session_id] = execution_id

    def execution_id(self, session_id: str) -> str | None:
        return self._execution_ids.get(session_id)

    def stop(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


orchestrator = V2Orchestrator()
