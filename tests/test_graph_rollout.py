from __future__ import annotations

import asyncio
import copy
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent import graph_rollout
from backend.agent.graph import (
    FINALIZE_NODE,
    LEGACY_APPROVAL_INTERRUPT_NODE,
    LEGACY_MODEL_TURN_NODE,
    LEGACY_POLICY_GATE_NODE,
    LEGACY_PREFLIGHT_NODE,
    LEGACY_TOOL_BATCH_NODE,
    NodeBundle,
    build_legacy_graph,
    init_runtime,
    shutdown_runtime,
)
from backend.infra.config import config

LEGACY_GRAPH_MODE = graph_rollout.LEGACY_GRAPH_MODE
SUPERVISOR_GRAPH_MODE = graph_rollout.SUPERVISOR_GRAPH_MODE


def _make_bundle() -> NodeBundle:
    async def _health() -> bool:
        return True

    return NodeBundle(check_health=_health)


@pytest.fixture
def tmp_sqlite_path():
    with tempfile.TemporaryDirectory() as d:
        yield str(Path(d) / "sessions.sqlite")


class _FakeGraph:
    async def ainvoke(self, state, config=None):
        del config
        session_data = copy.deepcopy(state["session_data"])
        session_data["status"] = "completed"
        session_data["final_text"] = "done"
        return {
            "status": "completed",
            "final_text": "done",
            "session_data": session_data,
        }


class TestGraphBuilders:
    def test_build_legacy_graph_uses_legacy_node_names(self, tmp_sqlite_path):
        async def _go():
            await init_runtime(tmp_sqlite_path)
            try:
                graph = build_legacy_graph(_make_bundle())
                nodes = set(graph.get_graph().nodes.keys())
                assert {
                    LEGACY_PREFLIGHT_NODE,
                    LEGACY_MODEL_TURN_NODE,
                    LEGACY_POLICY_GATE_NODE,
                    LEGACY_TOOL_BATCH_NODE,
                    LEGACY_APPROVAL_INTERRUPT_NODE,
                    FINALIZE_NODE,
                }.issubset(nodes)
            finally:
                await shutdown_runtime()

        asyncio.run(_go())


class TestRolloutRegistry:
    def setup_method(self):
        graph_rollout.reset_state()

    def teardown_method(self):
        graph_rollout.reset_state()

    def test_begin_session_defaults_to_legacy_when_flag_off(self, monkeypatch):
        monkeypatch.setattr(config, "use_supervisor_graph", False)

        selection = graph_rollout.begin_session(
            "session-legacy",
            requested_supervisor=config.use_supervisor_graph,
        )

        assert selection.selected_mode == LEGACY_GRAPH_MODE
        assert selection.reason == "flag_disabled"
        graph_rollout.finalize_session("session-legacy", status="completed")

    def test_kill_switch_trips_and_new_sessions_fall_back(self, monkeypatch):
        monkeypatch.setattr(config, "use_supervisor_graph", True)
        monkeypatch.setattr(config, "supervisor_failure_rate_threshold", 0.2)
        monkeypatch.setattr(config, "supervisor_failure_rate_min_sessions", 5)

        for index in range(5):
            session_id = f"session-{index}"
            selection = graph_rollout.begin_session(session_id, requested_supervisor=True)
            assert selection.selected_mode == SUPERVISOR_GRAPH_MODE
            graph_rollout.record_node_result(
                session_id,
                graph_mode=SUPERVISOR_GRAPH_MODE,
                node_name="planner",
                duration_ms=12.0,
                failed=index < 2,
            )
            graph_rollout.finalize_session(
                session_id,
                status="error" if index < 2 else "completed",
            )

        snapshot = graph_rollout.get_snapshot()
        assert snapshot["kill_switch"]["active"] is True
        assert snapshot["kill_switch"]["node"] == "planner"

        fallback = graph_rollout.begin_session("session-fallback", requested_supervisor=True)
        assert fallback.selected_mode == LEGACY_GRAPH_MODE
        assert fallback.reason == "kill_switch"
        assert fallback.alert
        graph_rollout.finalize_session("session-fallback", status="completed")

    def test_supervisor_snapshot_tracks_business_metrics(self, monkeypatch):
        monkeypatch.setattr(config, "use_supervisor_graph", True)
        graph_rollout.begin_session("session-metrics", requested_supervisor=True)
        graph_rollout.record_node_result(
            "session-metrics",
            graph_mode=SUPERVISOR_GRAPH_MODE,
            node_name="planner",
            duration_ms=25.0,
            failed=False,
        )
        graph_rollout.record_planner_memory_read(hit=True)
        graph_rollout.record_policy_evaluation(escalated=True)
        graph_rollout.record_recovery_classification("stuck")
        graph_rollout.record_verifier_verdict("complete")

        snapshot = graph_rollout.get_snapshot()
        supervisor = snapshot["graphs"][SUPERVISOR_GRAPH_MODE]

        assert supervisor["planner_memory"]["reads"] == 1
        assert supervisor["planner_memory"]["hits"] == 1
        assert supervisor["planner_memory"]["hit_rate"] == 1.0
        assert supervisor["policy"]["evaluations"] == 1
        assert supervisor["policy"]["escalations"] == 1
        assert supervisor["policy"]["escalation_rate"] == 1.0
        assert supervisor["recovery_classifications"]["stuck"] == 1
        assert supervisor["verifier_verdicts"]["complete"] == 1
        assert supervisor["nodes"]["planner"]["invocations"] == 1

        graph_rollout.finalize_session("session-metrics", status="completed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag_enabled", "expected_builder"),
    [
        (False, LEGACY_GRAPH_MODE),
        (True, SUPERVISOR_GRAPH_MODE),
    ],
)
async def test_agent_loop_selects_graph_at_session_start(
    monkeypatch,
    flag_enabled,
    expected_builder,
):
    import backend.agent.graph as graph_mod
    import backend.agent.prompts as prompts
    import backend.infra.observability as tracing
    from backend.agent.loop import AgentLoop

    graph_rollout.reset_state()
    monkeypatch.setattr(config, "use_supervisor_graph", flag_enabled)
    monkeypatch.setattr(prompts, "get_system_prompt", lambda *args, **kwargs: "system")
    monkeypatch.setattr(tracing, "start_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(tracing, "install", lambda bundle, session_id: bundle)
    monkeypatch.setattr(tracing, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(tracing, "finalize_session", lambda *args, **kwargs: None)

    calls: list[str] = []

    def _build_supervisor(_bundle):
        calls.append(SUPERVISOR_GRAPH_MODE)
        return _FakeGraph()

    def _build_legacy(_bundle):
        calls.append(LEGACY_GRAPH_MODE)
        return _FakeGraph()

    monkeypatch.setattr(graph_mod, "build_agent_graph", _build_supervisor)
    monkeypatch.setattr(graph_mod, "build_legacy_graph", _build_legacy)

    loop = AgentLoop(task="hello", api_key="k" * 16)
    session = await loop.run()

    assert calls == [expected_builder]
    assert session.final_text == "done"


def test_graph_rollout_endpoint_returns_snapshot():
    from backend import server

    graph_rollout.reset_state()
    with TestClient(server.app) as client:
        response = client.get("/api/agent/graph-rollout")

    assert response.status_code == 200
    payload = response.json()
    assert "kill_switch" in payload
    assert "graphs" in payload