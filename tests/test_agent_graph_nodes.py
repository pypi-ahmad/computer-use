"""Tests for the six-node LangGraph state machine in ``backend.agent.graph``.

Each node is independently unit-testable by swapping in a fake async
iterator via :func:`backend.agent.graph._register_iterator` and a
stubbed :class:`backend.agent.graph.NodeBundle`. A separate integration
test exercises the full interrupt-resume flow including a simulated
backend restart (new runtime, same sqlite DB, same thread id).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend.agent import graph as graph_mod
from backend.agent.graph import (
    AgentGraphState,
    NodeBundle,
    _make_approval_interrupt,
    _make_finalize,
    _make_model_turn,
    _make_preflight,
    _make_recover_or_retry,
    _make_tool_batch,
    _register_iterator,
    build_agent_graph,
    init_runtime,
    shutdown_runtime,
)
from backend.engine import (
    CUActionResult,
    ModelTurnStarted,
    RunCompleted,
    RunFailed,
    SafetyRequired,
    ToolBatchCompleted,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_sqlite_path():
    with tempfile.TemporaryDirectory() as d:
        yield str(Path(d) / "sessions.sqlite")


@pytest.fixture(autouse=True)
def _reset_iterator_registry():
    """Clear the module-level iterator registry between tests."""
    graph_mod._iterators.clear()
    graph_mod._buffered_events.clear()
    yield
    graph_mod._iterators.clear()
    graph_mod._buffered_events.clear()


def _make_bundle(**overrides):
    """Build a minimal NodeBundle with no-op defaults for testing."""
    async def _health():
        return True

    async def _start(sid: str, task: str, max_steps: int):
        # Empty generator.
        if False:
            yield None

    defaults = dict(check_health=_health, start_iter=_start)
    defaults.update(overrides)
    return NodeBundle(**defaults)


async def _async_iter(events):
    """Convert a plain list into an async iterator for engine stubs."""
    for ev in events:
        yield ev


# ---------------------------------------------------------------------------
# Per-node unit tests
# ---------------------------------------------------------------------------

class TestPreflight:
    """The preflight node health-checks and registers the iterator."""

    def test_healthy_preflight_creates_iterator(self):
        """When start_iter returns a generator it is stored in the registry."""
        async def _go():
            async def _iter(sid, task, max_steps):
                async for ev in _async_iter([RunCompleted(final_text="x")]):
                    yield ev

            async def _health():
                return True

            bundle = _make_bundle(check_health=_health, start_iter=_iter)
            node = _make_preflight(bundle)
            state: AgentGraphState = {
                "session_id": "s1", "task": "t", "max_steps": 5,
            }
            delta = await node(state)
            assert delta["healthy"] is True
            assert delta["route"] == "model_turn"
            assert "s1" in graph_mod._iterators

        asyncio.run(_go())

    def test_unhealthy_preflight_still_registers_iterator(self):
        """An unhealthy service logs a warning but still registers the iter."""
        logs: list[tuple[str, str]] = []

        async def _go():
            async def _iter(sid, task, max_steps):
                async for ev in _async_iter([RunCompleted(final_text="x")]):
                    yield ev

            async def _health():
                return False

            bundle = _make_bundle(
                check_health=_health, start_iter=_iter,
                emit_log=lambda l, m, d=None: logs.append((l, m)),
            )
            node = _make_preflight(bundle)
            state: AgentGraphState = {"session_id": "s2", "task": "t", "max_steps": 3}
            delta = await node(state)
            assert delta["healthy"] is False
            assert any("not responding" in m for _, m in logs)

        asyncio.run(_go())


class TestModelTurn:
    """The model_turn node pulls one event and routes."""

    def test_ModelTurnStarted_routes_to_tool_batch(self):
        async def _go():
            ev = ModelTurnStarted(turn=1, model_text="thinking", pending_tool_uses=2)
            _register_iterator("s", _async_iter([ev]))
            node = _make_model_turn(_make_bundle())
            delta = await node({"session_id": "s"})
            assert delta["route"] == "tool_batch"
            assert delta["turn_count"] == 1
            assert delta["last_model_text"] == "thinking"

        asyncio.run(_go())

    def test_RunCompleted_routes_to_finalize(self):
        async def _go():
            _register_iterator("s", _async_iter([RunCompleted(final_text="done")]))
            node = _make_model_turn(_make_bundle())
            delta = await node({"session_id": "s"})
            assert delta["route"] == "completed"
            assert delta["final_text"] == "done"

        asyncio.run(_go())

    def test_SafetyRequired_routes_to_approval(self):
        async def _go():
            _register_iterator(
                "s", _async_iter([SafetyRequired(explanation="confirm?")])
            )
            node = _make_model_turn(_make_bundle())
            delta = await node({"session_id": "s"})
            assert delta["route"] == "approval"
            assert delta["pending_approval"] == {"explanation": "confirm?"}
            assert delta["status"] == "awaiting_approval"

        asyncio.run(_go())

    def test_RunFailed_routes_to_retry(self):
        async def _go():
            _register_iterator("s", _async_iter([RunFailed(error="boom")]))
            node = _make_model_turn(_make_bundle())
            delta = await node({"session_id": "s"})
            assert delta["route"] == "retry"
            assert delta["retry_reason"] == "model_turn"

        asyncio.run(_go())

    def test_stop_requested_short_circuits(self):
        async def _go():
            bundle = _make_bundle(stop_requested=lambda: True)
            node = _make_model_turn(bundle)
            delta = await node({"session_id": "s"})
            assert delta["route"] == "completed"
            assert "Stopped by user" in delta["final_text"]

        asyncio.run(_go())

    def test_missing_iterator_errors(self):
        async def _go():
            node = _make_model_turn(_make_bundle())
            delta = await node({"session_id": "no-such"})
            assert delta["route"] == "error"
            assert "Iterator missing" in delta["error"]

        asyncio.run(_go())


class TestToolBatch:
    """The tool_batch node emits a StepRecord and routes back to model_turn."""

    def test_ToolBatchCompleted_emits_step_and_loops_back(self):
        async def _go():
            result = CUActionResult(name="click_at", extra={"pixel_x": 10, "pixel_y": 20})
            ev = ToolBatchCompleted(turn=2, model_text="t", results=[result], screenshot_b64="b64")
            _register_iterator("s", _async_iter([ev]))
            emitted: list[ToolBatchCompleted] = []
            bundle = _make_bundle(emit_step=lambda e: emitted.append(e))
            node = _make_tool_batch(bundle)
            delta = await node({"session_id": "s"})
            assert delta["route"] == "model_turn"
            assert delta["turn_count"] == 2
            assert len(emitted) == 1
            assert emitted[0].turn == 2

        asyncio.run(_go())


class TestRecoverOrRetry:
    """The recover_or_retry node retries up to a bounded number of times."""

    def test_first_failure_retries(self):
        async def _go():
            node = _make_recover_or_retry(_make_bundle())
            state: AgentGraphState = {
                "retry_reason": "model_turn", "retry_count": 0, "error": "boom",
            }
            delta = await node(state)
            assert delta["route"] == "model_turn"
            assert delta["retry_count"] == 1
            assert delta["error"] is None

        asyncio.run(_go())

    def test_gives_up_after_max_retries(self):
        async def _go():
            node = _make_recover_or_retry(_make_bundle())
            state: AgentGraphState = {
                "retry_reason": "model_turn",
                "retry_count": graph_mod._MAX_MODEL_TURN_RETRIES,
                "error": "boom",
            }
            delta = await node(state)
            assert delta["route"] == "completed"
            assert delta["status"] == "error"

        asyncio.run(_go())


class TestFinalize:
    """The finalize node persists a snapshot and drops the iterator entry."""

    def test_writes_snapshot_and_clears_registry(self):
        async def _go():
            _register_iterator("s", _async_iter([]))
            assert "s" in graph_mod._iterators
            bundle = _make_bundle(build_snapshot=lambda: {"status": "completed"})
            node = _make_finalize(bundle)
            delta = await node({"session_id": "s"})
            assert delta["session_snapshot"] == {"status": "completed"}
            assert "s" not in graph_mod._iterators

        asyncio.run(_go())


# ---------------------------------------------------------------------------
# Approval resume buffering
# ---------------------------------------------------------------------------

class TestApprovalBufferedResume:
    """Resumed events from ``asend(decision)`` must not be dropped."""

    def test_tool_batch_returned_by_asend_is_buffered_for_next_node(self):
        async def _go():
            async def _iter():
                decision = yield SafetyRequired(explanation="confirm?")
                assert decision is True
                yield ToolBatchCompleted(
                    turn=1,
                    model_text="clicked",
                    results=[CUActionResult(name="click_at")],
                    screenshot_b64="b64",
                )
                yield RunCompleted(final_text="done")

            _register_iterator("s", _iter())
            model_node = _make_model_turn(_make_bundle())
            tool_events: list[ToolBatchCompleted] = []
            tool_node = _make_tool_batch(
                _make_bundle(emit_step=lambda e: tool_events.append(e))
            )
            approval_node = _make_approval_interrupt(_make_bundle())

            delta = await model_node({"session_id": "s"})
            assert delta["route"] == "approval"

            with patch("backend.agent.graph.interrupt", return_value=True):
                resumed = await approval_node(
                    {"session_id": "s", "pending_approval": {"explanation": "confirm?"}}
                )

            assert resumed["route"] == "tool_batch"

            after_tool = await tool_node({"session_id": "s"})
            assert after_tool["route"] == "model_turn"
            assert len(tool_events) == 1
            assert tool_events[0].turn == 1

            final_delta = await model_node({"session_id": "s"})
            assert final_delta["route"] == "completed"
            assert final_delta["final_text"] == "done"

        asyncio.run(_go())


# ---------------------------------------------------------------------------
# Integration: end-to-end interrupt & restart-resume
# ---------------------------------------------------------------------------

class TestApprovalInterruptResume:
    """The approval_interrupt node pauses and resumes across a fresh runtime.

    Simulates a backend crash between the user being prompted and the
    decision arriving: the graph pauses, the process "restarts" (fresh
    runtime, fresh graph instance, same sqlite DB file, same thread
    id), and the resume delivers the decision. Verifies that
    ``pending_approval`` is checkpointed and that the interrupt
    primitive correctly threads the decision back into the resumed
    run.
    """

    def test_pause_and_resume_across_fresh_runtime(self, tmp_sqlite_path):
        thread_id = "ac7e1d6a-2e2f-4a1a-8c1b-111111111111"

        # Decisions recorded by the bundles
        recorded: dict[str, Any] = {}

        async def _build_and_run_phase1():
            # First boot: runtime #1, iterator yields SafetyRequired.
            await init_runtime(tmp_sqlite_path)

            async def _iter(sid, task, max_steps):
                yield SafetyRequired(explanation="dangerous?")

            async def _health():
                return True

            bundle = NodeBundle(
                check_health=_health,
                start_iter=_iter,
                build_snapshot=lambda: {"phase": "phase1"},
            )
            graph = build_agent_graph(bundle)
            config = {"configurable": {"thread_id": thread_id}}
            try:
                await graph.ainvoke(
                    {"session_id": thread_id, "task": "t", "max_steps": 3},
                    config=config,
                )
            except Exception:
                # LangGraph signals interrupt via an exception in some
                # versions; other versions return normally with state
                # containing __interrupt__. Either is fine — the
                # checkpoint below is the source of truth.
                pass

            # Inspect checkpoint: pending_approval must be there.
            rt = graph_mod.get_runtime()
            tup = await rt.checkpointer.aget_tuple(config)
            assert tup is not None, "checkpoint was not written"
            values = tup.checkpoint.get("channel_values") or {}
            assert values.get("pending_approval") == {"explanation": "dangerous?"}, (
                f"expected pending_approval to be checkpointed, got "
                f"{values.get('pending_approval')!r}"
            )
            recorded["phase1_pending"] = values.get("pending_approval")
            recorded["phase1_status"] = values.get("status")

            # Phase 1 complete — shut down.
            await shutdown_runtime()
            # Simulate process restart: iterator registry is lost.
            graph_mod._iterators.clear()

        async def _build_and_run_phase2_resume():
            # Fresh runtime pointing at the SAME sqlite DB — simulates
            # a backend crash + restart.
            await init_runtime(tmp_sqlite_path)

            # A new iterator for the resumed session. In the real
            # system a new run would create a fresh engine; here we
            # supply a stub that completes immediately so the graph
            # can progress past the approval node to finalize.
            async def _iter_after_resume(sid, task, max_steps):
                yield RunCompleted(final_text="resumed ok")

            async def _health():
                return True

            bundle = NodeBundle(
                check_health=_health,
                start_iter=_iter_after_resume,
                build_snapshot=lambda: {"phase": "phase2", "resumed": True},
            )
            graph = build_agent_graph(bundle)
            config = {"configurable": {"thread_id": thread_id}}

            # Register the resume iterator manually — the graph on
            # resume re-enters approval_interrupt which calls asend
            # against the iterator in the registry. For the A1 scope,
            # Claude's iter_turns never awaits an asend value so the
            # asend is a no-op; the graph then transitions to
            # model_turn which pulls RunCompleted from the fresh iter.
            _register_iterator(thread_id, _iter_after_resume(thread_id, "t", 3))

            from langgraph.types import Command
            await graph.ainvoke(Command(resume=True), config=config)

            rt = graph_mod.get_runtime()
            tup = await rt.checkpointer.aget_tuple(config)
            values = tup.checkpoint.get("channel_values") or {}
            recorded["phase2_final_text"] = values.get("final_text")
            recorded["phase2_snapshot"] = values.get("session_snapshot")
            recorded["phase2_pending"] = values.get("pending_approval")
            recorded["phase2_decision"] = values.get("approval_decision")

            await shutdown_runtime()

        asyncio.run(_build_and_run_phase1())
        asyncio.run(_build_and_run_phase2_resume())

        # Phase 1 correctness
        assert recorded["phase1_pending"] == {"explanation": "dangerous?"}

        # Phase 2 correctness — resume delivered a decision and run
        # reached finalize with the expected final text + snapshot.
        assert recorded["phase2_decision"] is True, (
            f"expected approval_decision=True post-resume, "
            f"got {recorded['phase2_decision']!r}"
        )
        assert recorded["phase2_pending"] in (None, {}), (
            f"pending_approval must be cleared after resume, got "
            f"{recorded['phase2_pending']!r}"
        )
        assert recorded["phase2_final_text"] == "resumed ok"
        assert recorded["phase2_snapshot"] == {"phase": "phase2", "resumed": True}
