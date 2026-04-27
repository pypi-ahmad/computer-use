from __future__ import annotations

import asyncio
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langgraph.store.memory import InMemoryStore

from backend.agent.graph import NodeBundle, build_agent_graph, init_runtime, shutdown_runtime
from backend.agent.memory_layers import prior_workflows_namespace
from backend.agent.persisted_runtime import _build_engine
from backend.agent.planner import build_planner_subgraph


@pytest.fixture
def tmp_sqlite_path():
    with tempfile.TemporaryDirectory() as d:
        yield str(Path(d) / "sessions.sqlite")


def _make_bundle(**overrides):
    async def _health():
        return True

    defaults = dict(check_health=_health)
    defaults.update(overrides)
    return NodeBundle(**defaults)


def _base_state(provider: str = "openai") -> dict:
    model = {
        "openai": "gpt-5.5",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-3-flash-preview",
    }[provider]
    return {
        "session_id": "planner-s1",
        "task": "do the thing",
        "goal": "do the thing",
        "max_steps": 4,
        "provider": provider,
        "model": model,
        "planner_model": model,
        "api_key": "test-api-key",
        "system_instruction": "system",
        "screen_width": 1440,
        "screen_height": 900,
        "container_name": "cua-environment",
        "agent_service_url": "http://127.0.0.1:9222",
        "reasoning_effort": None,
        "use_builtin_search": False,
        "search_max_uses": None,
        "search_allowed_domains": None,
        "search_blocked_domains": None,
        "allowed_callers": None,
        "attached_files": [],
        "provider_capabilities": {
            "computer_use": True,
            "web_search": False,
            "model_id": model,
            "beta_headers": [],
            "search_allowed_domains": [],
            "search_blocked_domains": [],
            "allowed_callers": None,
        },
        "session_data": {
            "session_id": "planner-s1",
            "task": "do the thing",
            "status": "running",
            "model": model,
            "engine": "computer_use",
            "steps": [],
            "max_steps": 4,
            "created_at": "2026-04-27T00:00:00+00:00",
            "final_text": None,
            "gemini_grounding": None,
        },
    }


def test_planner_produces_non_empty_subgoals_for_simple_task():
    async def _go():
        graph = build_planner_subgraph()
        with patch(
            "backend.agent.planner._request_planner_text",
            return_value=(
                "gpt-5.5",
                """{
                    \"subgoals\": [
                        {\"title\": \"Open Calculator\", \"status\": \"active\"},
                        {\"title\": \"Type 2+2\", \"status\": \"pending\"}
                    ],
                    \"active_plan\": {
                        \"summary\": \"Open Calculator and enter the expression.\",
                        \"steps\": [\"Open Calculator\", \"Type 2+2\"]
                    },
                    \"completion_criteria\": [
                        \"Calculator is visible\",
                        \"The expression has been entered\"
                    ]
                }""",
            ),
        ):
            result = await graph.ainvoke(
                {
                    **_base_state(),
                    "goal": "Open Calculator and type 2+2",
                }
            )
        assert result["planner_model"] == "gpt-5.5"
        assert len(result["subgoals"]) == 2
        assert result["subgoals"][0]["status"] == "active"
        assert result["active_plan"]["active_subgoal"] == "Open Calculator"
        assert result["completion_criteria"] == [
            "Calculator is visible",
            "The expression has been entered",
        ]
        assert result["route"] == "model_turn"
        assert result["replan"] is False

    asyncio.run(_go())


def test_planner_reads_long_term_memory_and_seeds_plan():
    async def _go():
        store = InMemoryStore()
        store.put(
            prior_workflows_namespace(),
            "workflow-1",
            {
                "goal": "Open invoices",
                "workflow_summary": "Use the Billing menu before opening invoices.",
                "steps": ["Open Billing", "Open Invoices"],
            },
            index=False,
        )
        graph = build_planner_subgraph(store=store)
        captured: dict[str, object] = {}

        async def _fake_request(state):
            captured["memory_context"] = copy.deepcopy(state.get("memory_context") or {})
            return (
                "gpt-5.5",
                """{
                    \"subgoals\": [{\"title\": \"Open Billing\", \"status\": \"active\"}],
                    \"active_plan\": {
                        \"summary\": \"Use the Billing menu before opening invoices.\",
                        \"steps\": [\"Open Billing\", \"Open Invoices\"]
                    },
                    \"completion_criteria\": [\"Invoices are visible\"]
                }""",
            )

        with patch("backend.agent.planner._request_planner_text", side_effect=_fake_request):
            result = await graph.ainvoke({**_base_state(), "goal": "Open invoices from the billing menu"})

        assert captured["memory_context"]["prior_workflows"][0]["workflow_summary"] == "Use the Billing menu before opening invoices."
        assert result["memory_context"]["prior_workflows"][0]["workflow_summary"] == "Use the Billing menu before opening invoices."

    asyncio.run(_go())


def test_planner_is_skipped_when_active_plan_exists(tmp_sqlite_path):
    async def _go():
        try:
            await init_runtime(tmp_sqlite_path)
            graph = build_agent_graph(_make_bundle())
            state = {
                **_base_state(),
                "subgoals": [{"title": "Open app", "status": "active"}],
                "active_plan": {
                    "summary": "Open the app.",
                    "steps": ["Open app"],
                    "active_subgoal": "Open app",
                },
                "completion_criteria": ["App is open"],
            }
            with patch(
                "backend.agent.planner._request_planner_text",
                side_effect=AssertionError("planner should be skipped"),
            ), patch(
                "backend.agent.graph.advance_provider_turn",
                return_value={
                    "route": "completed",
                    "status": "completed",
                    "final_text": "done",
                    "session_data": {
                        **state["session_data"],
                        "status": "completed",
                        "final_text": "done",
                    },
                },
            ):
                result = await graph.ainvoke(
                    state,
                    config={"configurable": {"thread_id": "skip-planner"}},
                )
            assert result["status"] == "completed"
        finally:
            await shutdown_runtime()

    asyncio.run(_go())


def test_replan_reinvocation_preserves_evidence(tmp_sqlite_path):
    async def _go():
        captured: dict[str, object] = {}

        async def _fake_generate_plan(state):
            captured["evidence"] = copy.deepcopy(state.get("evidence"))
            captured["steps"] = copy.deepcopy((state.get("session_data") or {}).get("steps"))
            return {
                "planner_model": "gpt-5.5",
                "subgoals": [{"title": "Retry login", "status": "active"}],
                "active_plan": {
                    "summary": "Retry the login flow using the observed state.",
                    "steps": ["Retry login"],
                    "active_subgoal": "Retry login",
                },
                "completion_criteria": ["Login is complete"],
            }

        try:
            await init_runtime(tmp_sqlite_path)
            graph = build_agent_graph(_make_bundle())
            state = {
                **_base_state(),
                "subgoals": [{"title": "Old plan", "status": "active"}],
                "active_plan": {
                    "summary": "Old plan",
                    "steps": ["Old plan"],
                    "active_subgoal": "Old plan",
                },
                "completion_criteria": ["Old complete"],
                "evidence": [{"kind": "ocr", "value": "Login failed"}],
                "replan": True,
                "session_data": {
                    **_base_state()["session_data"],
                    "steps": [
                        {
                            "step_number": 1,
                            "raw_model_response": "Tried to submit the form",
                            "action": {"action": "click_at", "coordinates": [100, 200]},
                            "error": "Validation error",
                        }
                    ],
                },
            }
            with patch(
                "backend.agent.planner.generate_plan_output",
                side_effect=_fake_generate_plan,
            ), patch(
                "backend.agent.graph.advance_provider_turn",
                return_value={
                    "route": "completed",
                    "status": "completed",
                    "final_text": "done",
                    "session_data": {
                        **state["session_data"],
                        "status": "completed",
                        "final_text": "done",
                    },
                },
            ):
                result = await graph.ainvoke(
                    state,
                    config={"configurable": {"thread_id": "replan-with-evidence"}},
                )
            assert captured["evidence"] == [{"kind": "ocr", "value": "Login failed"}]
            assert captured["steps"] == [
                {
                    "step_number": 1,
                    "raw_model_response": "Tried to submit the form",
                    "action": {"action": "click_at", "coordinates": [100, 200]},
                    "error": "Validation error",
                }
            ]
            assert result["replan"] is False
            assert result["active_plan"]["active_subgoal"] == "Retry login"
        finally:
            await shutdown_runtime()

    asyncio.run(_go())


def test_provider_adapters_receive_unchanged_payload_shape():
    state = {
        **_base_state(),
        "subgoals": [{"title": "Open app", "status": "active"}],
        "active_plan": {"summary": "Open app", "steps": ["Open app"]},
        "completion_criteria": ["App is open"],
        "evidence": [{"kind": "note", "value": "already loaded"}],
        "replan": True,
    }
    with patch("backend.agent.persisted_runtime.ComputerUseEngine", return_value=SimpleNamespace()) as engine_cls:
        _build_engine(state)

    _, kwargs = engine_cls.call_args
    assert sorted(kwargs) == [
        "agent_service_url",
        "allowed_callers",
        "api_key",
        "attached_files",
        "container_name",
        "environment",
        "model",
        "provider",
        "reasoning_effort",
        "screen_height",
        "screen_width",
        "search_allowed_domains",
        "search_blocked_domains",
        "search_max_uses",
        "system_instruction",
        "use_builtin_search",
    ]
    assert "planner_model" not in kwargs
    assert "subgoals" not in kwargs
    assert "active_plan" not in kwargs
    assert "completion_criteria" not in kwargs
    assert "evidence" not in kwargs
    assert "provider_capabilities" not in kwargs
    assert "replan" not in kwargs