"""Shared helpers for the eval harness.

The evals drive :func:`backend.agent.graph.build_agent_graph` with a
fake engine iterator (no network, no SDKs). These helpers build that
shape, install tracing on the bundle, and run the graph to a terminal
state while threading an approval decision through the interrupt if
one is raised.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from backend.agent.graph import (
    NodeBundle,
    _register_iterator,
    build_agent_graph,
    get_runtime,
    init_runtime,
    shutdown_runtime,
)
from backend.infra import observability as tracing


async def run_graph_with_decision(
    *,
    session_id: str,
    task: str,
    iterator: AsyncIterator,
    approval_decision: bool,
    sqlite_db: str,
    max_steps: int = 5,
) -> dict[str, Any]:
    """Drive the agent graph to terminal state for *session_id*.

    Owns the LangGraph runtime lifecycle (init + shutdown) inside the
    caller's event loop. This is important because
    :class:`AsyncSqliteSaver` binds its connection to the loop it is
    opened in, so the init, graph runs, and teardown must share one
    loop. A fixture-scoped runtime would break that contract on any
    test that calls ``asyncio.run``.

    Parameters
    ----------
    session_id:
        LangGraph thread id — must also key the iterator registry.
    task:
        Task string forwarded to the graph state.
    iterator:
        Pre-built async iterator yielding :class:`TurnEvent` values.
    approval_decision:
        Decision delivered on the first ``interrupt()``. Tests that
        don't hit a safety gate pass any value; it's ignored.
    sqlite_db:
        Path to a per-test sqlite DB (from the ``sqlite_db`` fixture).
    max_steps:
        Forwarded to the graph state; unused by the fake iterator but
        required by the state schema.

    Returns
    -------
    The final graph state dict.
    """
    await init_runtime(sqlite_db)
    try:
        tracing.start_session(session_id, task=task)

        async def _health() -> bool:
            return True

        async def _start_iter(sid: str, _task: str, _max: int):
            return iterator

        bundle = NodeBundle(
            check_health=_health,
            start_iter=_start_iter,
            build_snapshot=lambda: {"eval": True},
        )
        bundle = tracing.install_bundle(bundle, session_id)
        _register_iterator(
            session_id, tracing.wrap_iterator(iterator, session_id),
        )

        graph = build_agent_graph(bundle)
        config = {"configurable": {"thread_id": session_id}}

        try:
            state = await graph.ainvoke(
                {"session_id": session_id, "task": task, "max_steps": max_steps},
                config=config,
            )
        except Exception:
            state = {}

        rt = get_runtime()
        tup = await rt.checkpointer.aget_tuple(config)
        values = (tup.checkpoint.get("channel_values") or {}) if tup else {}

        if values.get("pending_approval"):
            from langgraph.types import Command
            try:
                state = await graph.ainvoke(
                    Command(resume=bool(approval_decision)), config=config,
                )
            except Exception:
                state = {}
            tup = await rt.checkpointer.aget_tuple(config)
            values = (tup.checkpoint.get("channel_values") or {}) if tup else {}

        status = str(values.get("status", state.get("status", "completed")))
        tracing.finalize_session(session_id, status=status)
        return dict(values)
    finally:
        await shutdown_runtime()


def load_trace_or_fail(session_id: str):
    """Load and return the persisted trace, or :class:`AssertionError`."""
    trace = tracing.load_trace(session_id)
    assert trace is not None, f"no trace persisted for {session_id!r}"
    return trace


def run_async(coro):
    """Run *coro* on a fresh event loop. Convenience for sync tests."""
    return asyncio.run(coro)
