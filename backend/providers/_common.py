"""Shared provider-run primitives for native Computer Use loops."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderTools:
    """Provider-native tools requested for one Computer Use run."""

    web_search: bool = False


@dataclass(frozen=True)
class ProviderEvent:
    """Stream event emitted by a provider-owned run loop."""

    type: str
    data: Any = None


EventCallback = Callable[[ProviderEvent], Any]
SafetyCallback = Callable[[str], Awaitable[bool] | bool]

def normalize_tools(tools: ProviderTools | Mapping[str, Any] | None) -> ProviderTools:
    """Accept either the typed tools object or a request-style mapping."""
    if isinstance(tools, ProviderTools):
        return tools
    if not tools:
        return ProviderTools()
    return ProviderTools(
        web_search=bool(tools.get("web_search", False)),
    )


async def emit_event(event: ProviderEvent, callback: EventCallback | None) -> None:
    """Invoke a sync or async event callback."""
    if callback is None:
        return
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result


async def stream_client_run_loop(
    task: str,
    *,
    client: Any,
    executor: Any,
    turn_limit: int,
    on_event: EventCallback | None,
    on_safety: SafetyCallback | None,
    close_executor: bool,
):
    """Bridge an existing provider client's callback loop into a stream.

    Provider files own the documented run contract. The underlying SDK
    adapters still expose ``run_loop`` while the codebase is being
    slimmed, so this helper keeps events live instead of buffering them
    until the provider call returns.
    """
    queue: asyncio.Queue[ProviderEvent] = asyncio.Queue()

    def _on_turn(record: Any) -> None:
        queue.put_nowait(ProviderEvent("turn", record))

    def _on_log(level: str, message: str) -> None:
        queue.put_nowait(
            ProviderEvent("log", {"level": str(level), "message": str(message)})
        )

    async def _runner() -> None:
        try:
            final_text = await client.run_loop(
                goal=task,
                executor=executor,
                turn_limit=turn_limit,
                on_safety=on_safety,
                on_turn=_on_turn,
                on_log=_on_log,
            )
            await queue.put(
                ProviderEvent(
                    "final",
                    {
                        "text": final_text,
                        "completion_payload": (
                            getattr(client, "_last_completion_payload", None) or {}
                        ),
                    },
                )
            )
        except Exception as exc:
            await queue.put(ProviderEvent("error", exc))

    run_task = asyncio.create_task(_runner())
    try:
        while True:
            event = await queue.get()
            await emit_event(event, on_event)
            if event.type == "error":
                raise event.data
            yield event
            if event.type == "final":
                await run_task
                break
    finally:
        if not run_task.done():
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task
        if close_executor and hasattr(executor, "aclose"):
            try:
                await executor.aclose()
            except Exception:
                logger.debug("Error closing provider executor", exc_info=True)
