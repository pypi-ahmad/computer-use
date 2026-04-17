"""LangGraph orchestration wrapper for the Computer Use agent loop.

Surgical adoption: the engine, provider clients, desktop executor,
prompts, and docker service stay untouched. Only the outer
``preflight → execute → finalize`` orchestration inside
:class:`backend.agent.loop.AgentLoop` is expressed as a LangGraph
``StateGraph``.

Session registry migration
--------------------------
A single process-wide :class:`GraphRuntime` owns an
:class:`AsyncSqliteSaver` checkpointer. Every agent run uses its
``session_id`` as the LangGraph ``thread_id``. The ``finalize`` node
writes a full :class:`backend.models.AgentSession` dump into graph
state, so after a run the checkpointer — not a bespoke in-memory TTL
dict — is the source of truth for status/history lookups of recently
finished sessions. Sessions also survive restarts.

What this is NOT: a replacement for the per-run ``AgentLoop`` instance
held in ``_active_loops`` while a run is in flight; callbacks and
stop-requests still need that reference. The checkpointer takes over
only once the loop completes and the in-memory entry is cleaned up.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph


class AgentGraphState(TypedDict, total=False):
    """State passed between graph nodes and persisted by the checkpointer.

    Kept deliberately minimal — the authoritative in-memory session
    state still lives on :class:`backend.models.AgentSession`. The
    ``session_snapshot`` field is written once by the ``finalize`` node
    so a completed run can be reconstructed from the checkpointer.
    """

    session_id: str
    task: str
    max_steps: int
    healthy: bool
    status: str  # "running" | "completed" | "error"
    error: Optional[str]
    session_snapshot: dict[str, Any]


NodeFn = Callable[[AgentGraphState], Awaitable[dict[str, Any]]]


class GraphRuntime:
    """Owns the shared AsyncSqliteSaver checkpointer for the app's lifetime."""

    def __init__(self, db_path: str | Path):
        """Configure the runtime with a sqlite database path."""
        self._db_path = str(db_path)
        self._stack: AsyncExitStack | None = None
        self.checkpointer: AsyncSqliteSaver | None = None

    async def __aenter__(self) -> "GraphRuntime":
        """Open the sqlite checkpointer and ensure its tables exist."""
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        self.checkpointer = await self._stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(self._db_path)
        )
        await self.checkpointer.setup()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Close the sqlite checkpointer."""
        if self._stack is not None:
            try:
                await self._stack.__aexit__(*exc)
            finally:
                self._stack = None
                self.checkpointer = None


_runtime: GraphRuntime | None = None


async def init_runtime(db_path: str | Path) -> GraphRuntime:
    """Initialise the process-wide graph runtime. Idempotent."""
    global _runtime
    if _runtime is not None:
        return _runtime
    rt = GraphRuntime(db_path)
    await rt.__aenter__()
    _runtime = rt
    return _runtime


async def shutdown_runtime() -> None:
    """Close the process-wide graph runtime if one was initialised."""
    global _runtime
    if _runtime is None:
        return
    try:
        await _runtime.__aexit__(None, None, None)
    finally:
        _runtime = None


def get_runtime() -> GraphRuntime:
    """Return the initialised runtime or raise."""
    if _runtime is None or _runtime.checkpointer is None:
        raise RuntimeError("Graph runtime not initialised — call init_runtime() first")
    return _runtime


def build_agent_graph(
    preflight: NodeFn,
    execute: NodeFn,
    finalize: NodeFn,
):
    """Compile a 3-node agent graph: preflight → execute → finalize.

    Uses the process-wide :class:`GraphRuntime` checkpointer so every
    run is persisted under its ``thread_id`` (the session id).
    """
    sg: StateGraph = StateGraph(AgentGraphState)
    sg.add_node("preflight", preflight)
    sg.add_node("execute", execute)
    sg.add_node("finalize", finalize)
    sg.add_edge(START, "preflight")
    sg.add_edge("preflight", "execute")
    sg.add_edge("execute", "finalize")
    sg.add_edge("finalize", END)
    return sg.compile(checkpointer=get_runtime().checkpointer)


async def load_session_snapshot(session_id: str) -> dict[str, Any] | None:
    """Return the persisted ``session_snapshot`` for a finished run, if any."""
    if _runtime is None or _runtime.checkpointer is None:
        return None
    tup = await _runtime.checkpointer.aget_tuple(
        {"configurable": {"thread_id": session_id}}
    )
    if tup is None:
        return None
    values = tup.checkpoint.get("channel_values") or {}
    snapshot = values.get("session_snapshot")
    return snapshot if isinstance(snapshot, dict) else None
