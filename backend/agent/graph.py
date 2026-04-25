"""LangGraph state machine for the Computer Use agent loop (PR 7).

The orchestration is a six-node ``StateGraph`` over :class:`AgentGraphState`
with per-session iterators held in a process-wide registry:

    preflight  →  model_turn  ⇄  tool_batch  →  finalize
                      │                  │
                      └── approval_interrupt ─┘
                      │                  │
                      └── recover_or_retry ──→ finalize

Each node pulls exactly one ``TurnEvent`` from the session's
``AsyncIterator[TurnEvent]`` (or is a pure state-reducer for routing),
which makes every node independently unit-testable by swapping in a
fake iterator via :func:`_register_iterator`.

Approval handshake
------------------
When the iterator yields :class:`~backend.engine.SafetyRequired`, the
``approval_interrupt`` node calls LangGraph's ``interrupt()`` primitive
with the explanation payload. The checkpointer snapshots
``pending_approval`` as part of channel state, so if the backend
crashes while waiting on a human the run can be fully resumed after
restart via ``graph.ainvoke(Command(resume=decision), config)``.

Scope: Claude and Gemini yield native per-turn events into the graph.
OpenAI still uses the legacy ``run_loop`` shim and yields a terminal
event stream through the adapter.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend.engine import (
    ModelTurnStarted,
    RunCompleted,
    RunFailed,
    SafetyRequired,
    ToolBatchCompleted,
    TurnEvent,
)

logger = logging.getLogger(__name__)


class AgentGraphState(TypedDict, total=False):
    """State passed between graph nodes and persisted by the checkpointer."""

    # Request context
    session_id: str
    task: str
    max_steps: int

    # Lifecycle / routing
    healthy: bool
    status: str   # "preflight" | "running" | "awaiting_approval" | "completed" | "error"
    route: str    # internal router signal
    error: Optional[str]

    # Turn bookkeeping
    turn_count: int
    last_model_text: str

    # Approval (checkpointed across restarts)
    pending_approval: Optional[dict[str, Any]]
    approval_decision: Optional[bool]

    # Retry
    retry_count: int
    retry_reason: str

    # Terminal
    final_text: str
    session_snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Per-session iterator registry (process-local).
# ---------------------------------------------------------------------------
#
# ``iter_turns`` async-generators are held in memory for the duration of a
# run. They are *not* part of checkpointed state (generators cannot be
# pickled). After a process restart the registry is empty; a resumed run
# must call :func:`_register_iterator` again before advancing the graph
# past ``preflight``.

_iterators: dict[str, AsyncIterator[TurnEvent]] = {}
_buffered_events: dict[str, TurnEvent] = {}


def _register_iterator(session_id: str, it: AsyncIterator[TurnEvent]) -> None:
    """Register (or replace) the iterator for a session."""
    _iterators[session_id] = it


def _get_iterator(session_id: str) -> AsyncIterator[TurnEvent] | None:
    """Return the registered iterator or ``None``."""
    return _iterators.get(session_id)


def _drop_iterator(session_id: str) -> None:
    """Remove a session's iterator entry. Safe to call on unknown sessions."""
    _iterators.pop(session_id, None)
    _buffered_events.pop(session_id, None)


def _push_buffered_event(session_id: str, event: TurnEvent) -> None:
    """Stash *event* for the next graph node to consume."""
    _buffered_events[session_id] = event


def _pop_buffered_event(session_id: str) -> TurnEvent | None:
    """Return and clear a previously-buffered event, if any."""
    return _buffered_events.pop(session_id, None)


# ---------------------------------------------------------------------------
# Node bundle — I/O closures the graph needs.
# ---------------------------------------------------------------------------

def _noop_log(_level: str, _msg: str, _data: dict[str, Any] | None = None) -> None:
    return None


def _noop_step(_ev: ToolBatchCompleted) -> None:
    return None


def _noop_snapshot() -> dict[str, Any]:
    return {}


def _noop_stop() -> bool:
    return False


@dataclass
class NodeBundle:
    """Injectable I/O for the graph nodes.

    Separating these from the graph structure keeps each node pure and
    swappable in tests. ``AgentLoop`` provides the production bundle;
    tests pass a fake bundle with stubbed callables.
    """

    check_health: Callable[[], Awaitable[bool]]
    start_iter: Callable[[str, str, int], Awaitable[AsyncIterator[TurnEvent]]]
    emit_step: Callable[[ToolBatchCompleted], None] = field(default=_noop_step)
    emit_log: Callable[[str, str, Optional[dict[str, Any]]], None] = field(default=_noop_log)
    build_snapshot: Callable[[], dict[str, Any]] = field(default=_noop_snapshot)
    stop_requested: Callable[[], bool] = field(default=_noop_stop)


# ---------------------------------------------------------------------------
# Runtime (checkpointer lifecycle) — unchanged public API.
# ---------------------------------------------------------------------------

class GraphRuntime:
    """Owns the shared AsyncSqliteSaver checkpointer for the app's lifetime."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._stack: AsyncExitStack | None = None
        self.checkpointer: AsyncSqliteSaver | None = None

    async def __aenter__(self) -> "GraphRuntime":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        self.checkpointer = await self._stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(self._db_path)
        )
        await self.checkpointer.setup()
        return self

    async def __aexit__(self, *exc: Any) -> None:
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


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

_MAX_MODEL_TURN_RETRIES = 2


# ---------------------------------------------------------------------------
# Node factories — closures over the NodeBundle keep nodes pure.
# ---------------------------------------------------------------------------

def _make_preflight(bundle: NodeBundle):
    async def preflight(state: AgentGraphState) -> dict[str, Any]:
        """Health-check the desktop service and create the engine iterator."""
        healthy = await bundle.check_health()
        if not healthy:
            bundle.emit_log(
                "warning",
                "Agent service not responding, will retry during execution",
                None,
            )
        sid = state["session_id"]
        if _get_iterator(sid) is None:
            result = bundle.start_iter(
                sid, state["task"], int(state.get("max_steps", 25)),
            )
            # ``start_iter`` may be an ``async def`` returning an iter
            # (production path — AgentLoop) or an async-generator
            # function that returns an iter directly (test path).
            import asyncio as _asyncio
            if _asyncio.iscoroutine(result):
                it = await result
            else:
                it = result  # type: ignore[assignment]
            _register_iterator(sid, it)
        return {
            "healthy": healthy,
            "status": "running",
            "turn_count": 0,
            "retry_count": 0,
            "route": "model_turn",
        }

    return preflight


def _make_model_turn(bundle: NodeBundle):
    async def model_turn(state: AgentGraphState) -> dict[str, Any]:
        """Pull one event from the engine iterator and route.

        This node corresponds to "the model just produced a turn". The
        event it pulls is the result of the provider's LLM call. Tool
        execution happens in the following ``tool_batch`` node so each
        boundary is a distinct graph step (and thus a distinct
        checkpoint).
        """
        sid = state["session_id"]
        if bundle.stop_requested():
            return {"route": "completed", "status": "completed",
                    "final_text": "Stopped by user."}
        it = _get_iterator(sid)
        if it is None:
            return {"route": "error", "status": "error",
                    "error": "Iterator missing — cannot resume without engine state."}
        try:
            event = _pop_buffered_event(sid)
            if event is None:
                event = await it.__anext__()
        except StopAsyncIteration:
            return {"route": "completed", "status": "completed",
                    "final_text": state.get("final_text", "")}
        except Exception as exc:
            return {"route": "retry", "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "retry_reason": "model_turn"}

        return _route_event(event, state, after="model_turn")

    return model_turn


def _make_tool_batch(bundle: NodeBundle):
    async def tool_batch(state: AgentGraphState) -> dict[str, Any]:
        """Pull the next event (expected: ``ToolBatchCompleted``) and emit a step."""
        sid = state["session_id"]
        if bundle.stop_requested():
            return {"route": "completed", "status": "completed",
                    "final_text": "Stopped by user."}
        it = _get_iterator(sid)
        if it is None:
            return {"route": "error", "status": "error",
                    "error": "Iterator missing — cannot resume tool batch."}
        try:
            event = _pop_buffered_event(sid)
            if event is None:
                event = await it.__anext__()
        except StopAsyncIteration:
            return {"route": "completed", "status": "completed",
                    "final_text": state.get("final_text", "")}
        except Exception as exc:
            return {"route": "retry", "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "retry_reason": "tool_batch"}

        if isinstance(event, ToolBatchCompleted):
            try:
                bundle.emit_step(event)
            except Exception as exc:
                bundle.emit_log(
                    "warning",
                    f"emit_step raised: {type(exc).__name__}: {exc}",
                    None,
                )
            return {
                "route": "model_turn",
                "status": "running",
                "turn_count": event.turn,
            }
        # Engine yielded an unexpected event between model_turn and
        # tool_batch — delegate to the shared router so SafetyRequired
        # / RunCompleted / RunFailed still work.
        return _route_event(event, state, after="tool_batch")

    return tool_batch


def _make_approval_interrupt(bundle: NodeBundle):
    async def approval_interrupt(state: AgentGraphState) -> dict[str, Any]:
        """Pause the graph on a ``require_confirmation`` safety prompt.

        LangGraph's ``interrupt()`` serialises ``pending_approval`` into
        checkpointed channel state and raises ``GraphInterrupt`` out of
        ``ainvoke``. External code (the safety-confirm endpoint) resumes
        via ``graph.ainvoke(Command(resume=decision), config)``; the
        ``interrupt()`` call then returns ``decision`` and the node
        forwards it into the generator.
        """
        sid = state["session_id"]
        pending = state.get("pending_approval") or {}
        explanation = str(pending.get("explanation", ""))

        decision = bool(interrupt({
            "type": "safety_approval",
            "session_id": sid,
            "explanation": explanation,
        }))

        # OBS: record the approval resolution in the session trace.
        # Kept best-effort so a tracing-module import error cannot break
        # the graph's approval handshake.
        try:
            from backend import tracing
            tracing.record(
                sid, tracing.STAGE_APPROVAL, tracing.EVT_APPROVAL_RESOLVED,
                {"decision": decision, "explanation": explanation},
            )
        except Exception:
            pass

        it = _get_iterator(sid)
        next_route = "model_turn"
        try:
            if it is not None and hasattr(it, "asend"):
                resumed_event = await it.asend(decision)  # type: ignore[func-returns-value]
                if resumed_event is not None:
                    _push_buffered_event(sid, resumed_event)
                    if isinstance(resumed_event, ToolBatchCompleted):
                        next_route = "tool_batch"
        except StopAsyncIteration:
            return {
                "route": "completed",
                "status": "completed",
                "approval_decision": decision,
                "pending_approval": None,
                "final_text": "Agent terminated: safety confirmation denied."
                if not decision else state.get("final_text", ""),
            }
        except Exception as exc:
            return {
                "route": "retry",
                "status": "error",
                "approval_decision": decision,
                "pending_approval": None,
                "error": f"{type(exc).__name__}: {exc}",
                "retry_reason": "approval_resume",
            }

        bundle.emit_log("info", f"Safety approval resolved: {decision}", None)
        return {
            "route": next_route,
            "status": "running",
            "approval_decision": decision,
            "pending_approval": None,
        }

    return approval_interrupt


def _make_recover_or_retry(bundle: NodeBundle):
    async def recover_or_retry(state: AgentGraphState) -> dict[str, Any]:
        """Decide whether to retry the failing node or give up."""
        reason = state.get("retry_reason", "")
        count = int(state.get("retry_count", 0)) + 1
        # OBS: record the retry decision in the session trace.
        try:
            from backend import tracing
            tracing.record(
                str(state.get("session_id", "")),
                tracing.STAGE_RETRY, tracing.EVT_RETRY,
                {
                    "reason": reason,
                    "attempt": count,
                    "max_attempts": _MAX_MODEL_TURN_RETRIES,
                    "error": state.get("error"),
                },
            )
        except Exception:
            pass
        if count > _MAX_MODEL_TURN_RETRIES:
            bundle.emit_log(
                "error",
                f"Giving up after {count - 1} retries on {reason}: "
                f"{state.get('error', '(unknown)')}",
                None,
            )
            return {
                "route": "completed",
                "status": "error",
                "retry_count": count,
                "final_text": state.get("error") or "Run failed.",
            }
        bundle.emit_log(
            "warning",
            f"Retrying {reason} (attempt {count}/{_MAX_MODEL_TURN_RETRIES}): "
            f"{state.get('error', '(unknown)')}",
            None,
        )
        return {
            "route": reason if reason in ("model_turn", "tool_batch") else "model_turn",
            "status": "running",
            "retry_count": count,
            "error": None,
        }

    return recover_or_retry


def _make_finalize(bundle: NodeBundle):
    async def finalize(state: AgentGraphState) -> dict[str, Any]:
        """Persist the session snapshot and clear the iterator registry."""
        sid = state.get("session_id", "")
        try:
            snapshot = bundle.build_snapshot()
        except Exception as exc:
            bundle.emit_log(
                "warning", f"build_snapshot raised: {exc}", None,
            )
            snapshot = {}
        _drop_iterator(sid)
        return {"session_snapshot": snapshot}

    return finalize


# ---------------------------------------------------------------------------
# Event router — shared by model_turn / tool_batch for unexpected events.
# ---------------------------------------------------------------------------

def _route_event(
    event: TurnEvent,
    state: AgentGraphState,
    *,
    after: str,
) -> dict[str, Any]:
    """Map a TurnEvent to a state delta with the right ``route`` field."""
    if isinstance(event, ModelTurnStarted):
        return {
            "route": "tool_batch",
            "status": "running",
            "turn_count": event.turn,
            "last_model_text": event.model_text,
        }
    if isinstance(event, ToolBatchCompleted):
        # End-turn case: Claude yields a ToolBatchCompleted with no
        # actions then a RunCompleted. Loop back to model_turn so
        # the next __anext__ picks up the RunCompleted.
        return {
            "route": "model_turn",
            "status": "running",
            "turn_count": event.turn,
        }
    if isinstance(event, SafetyRequired):
        return {
            "route": "approval",
            "status": "awaiting_approval",
            "pending_approval": {"explanation": event.explanation},
        }
    if isinstance(event, RunCompleted):
        return {
            "route": "completed",
            "status": "completed",
            "final_text": event.final_text,
        }
    if isinstance(event, RunFailed):
        return {
            "route": "retry",
            "status": "error",
            "error": event.error,
            "retry_reason": after,
        }
    return {
        "route": "retry",
        "status": "error",
        "error": f"Unknown TurnEvent: {type(event).__name__}",
        "retry_reason": after,
    }


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------

def _after_preflight(state: AgentGraphState) -> str:
    return state.get("route") or "model_turn"


def _after_model_turn(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "tool_batch":
        return "tool_batch"
    if r == "approval":
        return "approval_interrupt"
    if r == "retry":
        return "recover_or_retry"
    if r == "completed":
        return "finalize"
    return "finalize"


def _after_tool_batch(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "model_turn":
        return "model_turn"
    if r == "approval":
        return "approval_interrupt"
    if r == "retry":
        return "recover_or_retry"
    if r == "completed":
        return "finalize"
    return "finalize"


def _after_approval(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "retry":
        return "recover_or_retry"
    if r == "completed":
        return "finalize"
    return "model_turn"


def _after_retry(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "completed":
        return "finalize"
    if r == "tool_batch":
        return "tool_batch"
    return "model_turn"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_agent_graph(bundle: NodeBundle):
    """Compile the six-node agent graph with the runtime's checkpointer."""
    sg: StateGraph = StateGraph(AgentGraphState)

    sg.add_node("preflight", _make_preflight(bundle))
    sg.add_node("model_turn", _make_model_turn(bundle))
    sg.add_node("tool_batch", _make_tool_batch(bundle))
    sg.add_node("approval_interrupt", _make_approval_interrupt(bundle))
    sg.add_node("recover_or_retry", _make_recover_or_retry(bundle))
    sg.add_node("finalize", _make_finalize(bundle))

    sg.add_edge(START, "preflight")
    sg.add_conditional_edges("preflight", _after_preflight, {
        "model_turn": "model_turn",
        "finalize": "finalize",
    })
    sg.add_conditional_edges("model_turn", _after_model_turn, {
        "tool_batch": "tool_batch",
        "approval_interrupt": "approval_interrupt",
        "recover_or_retry": "recover_or_retry",
        "finalize": "finalize",
    })
    sg.add_conditional_edges("tool_batch", _after_tool_batch, {
        "model_turn": "model_turn",
        "approval_interrupt": "approval_interrupt",
        "recover_or_retry": "recover_or_retry",
        "finalize": "finalize",
    })
    sg.add_conditional_edges("approval_interrupt", _after_approval, {
        "model_turn": "model_turn",
        "recover_or_retry": "recover_or_retry",
        "finalize": "finalize",
    })
    sg.add_conditional_edges("recover_or_retry", _after_retry, {
        "model_turn": "model_turn",
        "tool_batch": "tool_batch",
        "finalize": "finalize",
    })
    sg.add_edge("finalize", END)

    return sg.compile(checkpointer=get_runtime().checkpointer)
