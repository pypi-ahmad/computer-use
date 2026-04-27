"""Shared helpers for the eval harness.

The evals drive :func:`backend.agent.graph.build_agent_graph` with a
fake provider-turn adapter (no network, no SDKs). These helpers patch
``advance_provider_turn`` with a deterministic async iterator bridge,
install tracing on the bundle, and run the graph to a terminal state
while threading an approval decision through the interrupt if one is
raised.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import patch

from backend.agent.persisted_runtime import serialize_action_result
from backend.agent.graph import (
    NodeBundle,
    build_agent_graph,
    get_runtime,
    init_runtime,
    shutdown_runtime,
)
from backend.engine import (
    ModelTurnStarted,
    RunCompleted,
    RunFailed,
    SafetyRequired,
    ToolBatchCompleted,
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
        LangGraph thread id.
    task:
        Task string forwarded to the graph state.
    iterator:
        Pre-built async iterator yielding legacy eval ``TurnEvent`` values.
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

        async def _advance(state: dict[str, Any], on_log=None) -> dict[str, Any]:
            del on_log
            while True:
                try:
                    event = await iterator.__anext__()
                except StopAsyncIteration:
                    session_data = dict(state.get("session_data") or {})
                    session_data["status"] = "completed"
                    return {
                        "route": "completed",
                        "status": "completed",
                        "final_text": session_data.get("final_text") or "",
                        "session_data": session_data,
                    }

                if isinstance(event, ModelTurnStarted):
                    tracing.record(
                        session_id,
                        tracing.STAGE_MODEL_TURN,
                        tracing.EVT_MODEL_TURN_STARTED,
                        {
                            "turn": event.turn,
                            "model_text": event.model_text,
                            "pending_tool_uses": event.pending_tool_uses,
                        },
                    )
                    continue

                if isinstance(event, ToolBatchCompleted):
                    return {
                        "route": "tool_batch",
                        "status": "running",
                        "turn_count": event.turn,
                        "last_model_text": event.model_text,
                        "pending_action_batch": {
                            "turn": event.turn,
                            "model_text": event.model_text,
                            "results": [
                                serialize_action_result(result)
                                for result in (event.results or [])
                            ],
                            "screenshot_ref": None,
                        },
                        "session_data": dict(state.get("session_data") or {}),
                    }

                if isinstance(event, SafetyRequired):
                    tracing.record(
                        session_id,
                        tracing.STAGE_APPROVAL,
                        tracing.EVT_SAFETY_REQUIRED,
                        {"explanation": event.explanation},
                    )
                    session_data = dict(state.get("session_data") or {})
                    session_data["status"] = "paused"
                    return {
                        "route": "approval",
                        "status": "awaiting_approval",
                        "pending_approval": {"explanation": event.explanation},
                        "session_data": session_data,
                    }

                if isinstance(event, RunCompleted):
                    tracing.record(
                        session_id,
                        tracing.STAGE_MODEL_TURN,
                        tracing.EVT_RUN_COMPLETED,
                        {"final_text": event.final_text},
                    )
                    session_data = dict(state.get("session_data") or {})
                    session_data["status"] = "completed"
                    session_data["final_text"] = event.final_text
                    return {
                        "route": "completed",
                        "status": "completed",
                        "final_text": event.final_text,
                        "session_data": session_data,
                    }

                if isinstance(event, RunFailed):
                    tracing.record(
                        session_id,
                        tracing.STAGE_MODEL_TURN,
                        tracing.EVT_RUN_FAILED,
                        {"error": event.error},
                    )
                    session_data = dict(state.get("session_data") or {})
                    session_data["status"] = "error"
                    session_data["final_text"] = event.error
                    return {
                        "route": "retry",
                        "status": "error",
                        "error": event.error,
                        "retry_reason": "model_turn",
                        "session_data": session_data,
                    }

                raise AssertionError(
                    f"Unsupported eval event: {type(event).__name__}"
                )

        bundle = NodeBundle(check_health=_health)
        bundle = tracing.install_bundle(bundle, session_id)

        with patch("backend.agent.graph.advance_provider_turn", side_effect=_advance):
            graph = build_agent_graph(bundle)
            config = {"configurable": {"thread_id": session_id}}
            rt = get_runtime()

            try:
                state = await graph.ainvoke(
                    {
                        "session_id": session_id,
                        "task": task,
                        "max_steps": max_steps,
                        "session_data": {
                            "session_id": session_id,
                            "task": task,
                            "status": "running",
                            "model": "eval-harness",
                            "engine": "computer_use",
                            "steps": [],
                            "max_steps": max_steps,
                            "created_at": "",
                            "final_text": None,
                            "gemini_grounding": None,
                        },
                    },
                    config=config,
                )
            except Exception:
                state = {}

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

        tup = await rt.checkpointer.aget_tuple({"configurable": {"thread_id": session_id}})
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
