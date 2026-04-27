from __future__ import annotations

import asyncio
import copy
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.store.memory import InMemoryStore

from backend.agent.graph import (
    AgentGraphState,
    NodeBundle,
    _make_approval_interrupt,
    _make_finalize,
    _make_model_turn,
    _make_policy_gate,
    _make_preflight,
    _make_tool_batch,
    build_agent_graph,
    get_runtime,
    init_runtime,
    load_session_snapshot,
    shutdown_runtime,
)
from backend.agent.memory_layers import (
    JsonFileStore,
    build_graph_store,
    operator_preferences_namespace,
    prior_workflows_namespace,
    reusable_ui_patterns_namespace,
)
from backend.engine import CUActionResult, ToolBatchCompleted


def _png_bytes() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (32, 32), color=(12, 34, 56))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


PNG_BYTES = _png_bytes()


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


def _base_state(provider: str = "openai") -> AgentGraphState:
    model = {
        "openai": "gpt-5.5",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-3-flash-preview",
    }[provider]
    return {
        "session_id": "s1",
        "task": "do the thing",
        "max_steps": 4,
        "provider": provider,
        "model": model,
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
        "session_data": {
            "session_id": "s1",
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


class TestPreflight:
    def test_preflight_marks_session_running(self):
        async def _go():
            node = _make_preflight(_make_bundle())
            delta = await node(_base_state())
            assert delta["healthy"] is True
            assert delta["route"] == "model_turn"
            assert delta["session_data"]["status"] == "running"
            assert delta["goal"] == "do the thing"
            assert delta["planner_model"] == "gpt-5.5"
            assert delta["subgoals"] == []
            assert delta["active_plan"] is None
            assert delta["evidence"] == []
            assert delta["completion_criteria"] == []
            assert delta["provider_capabilities"] == {
                "provider": "openai",
                "verified": False,
                "computer_use": False,
                "web_search": False,
                "web_search_version": None,
                "tool_combination_supported": False,
                "search_filtering_supported": False,
                "allowed_callers_supported": False,
                "reasoning_effort_default": None,
                "tool_version": None,
                "beta_flag": None,
                "model_id": "gpt-5.5",
                "beta_headers": [],
                "search_allowed_domains": [],
                "search_blocked_domains": [],
                "allowed_callers": None,
            }
            assert delta["risk_level"] == "low"
            assert delta["recovery_context"] is None
            assert delta["replan"] is False

        asyncio.run(_go())

    def test_graph_state_callback_receives_panel_snapshot(self):
        async def _go():
            emitted: list[dict[str, object]] = []
            node = _make_preflight(_make_bundle(emit_graph_state=emitted.append))
            state = {
                **_base_state(),
                "active_plan": {
                    "summary": "Open the dashboard.",
                    "steps": ["Open dashboard"],
                    "active_subgoal": "Open dashboard",
                },
                "completion_criteria": [
                    "Dashboard title visible",
                    "User menu visible",
                ],
                "verification_status": "needs_more_work",
                "unmet_completion_criteria": ["User menu visible"],
                "verification_rationale": "Dashboard title is visible, but the user menu is not.",
                "recovery_context": {
                    "classification": "transient",
                    "retry_reason": "tool_batch",
                    "error": "Click missed the menu button.",
                    "retry_count": 2,
                    "replan_count": 1,
                    "latest_turn": 3,
                    "failure_context": {
                        "verification_rationale": "Need another screenshot before deciding.",
                        "evidence_brief": "Dashboard page is visible.",
                    },
                },
            }
            await node(state)

            assert [item["phase"] for item in emitted] == ["running", "completed"]
            assert emitted[-1]["node"] == "intake"
            assert emitted[-1]["route"] == "model_turn"
            assert emitted[-1]["retry_count"] == 2
            assert emitted[-1]["replan_count"] == 1
            assert emitted[-1]["verifier_verdict"] == "needs_more_work"
            assert emitted[-1]["verification_rationale"] == "Dashboard title is visible, but the user menu is not."
            assert emitted[-1]["completion_criteria"] == [
                "Dashboard title visible",
                "User menu visible",
            ]
            assert emitted[-1]["unmet_completion_criteria"] == ["User menu visible"]
            assert emitted[-1]["recovery"]["classification"] == "transient"
            assert emitted[-1]["recovery"]["replan_reason"] == "Need another screenshot before deciding."
            assert emitted[-1]["replan_reason"] == "Need another screenshot before deciding."
            assert emitted[-1]["active_subgoal"] == "Open dashboard"
            assert emitted[-1]["pending_approval"] is None

        asyncio.run(_go())


class TestModelTurn:
    def test_stop_requested_short_circuits(self):
        async def _go():
            node = _make_model_turn(_make_bundle(stop_requested=lambda: True))
            delta = await node(_base_state())
            assert delta["route"] == "completed"
            assert delta["final_text"] == "Stopped by user."
            assert delta["session_data"]["status"] == "stopped"

        asyncio.run(_go())

    def test_pending_action_batch_routes_without_provider_call(self):
        async def _go():
            node = _make_model_turn(_make_bundle())
            with patch("backend.agent.graph.advance_provider_turn", side_effect=AssertionError("must not run")):
                delta = await node(
                    {
                        **_base_state(),
                        "pending_action_batch": {
                            "turn": 1,
                            "model_text": "thinking",
                            "results": [],
                            "screenshot_ref": None,
                        },
                    }
                )
            assert delta["route"] == "policy"

        asyncio.run(_go())

    def test_provider_delta_is_forwarded(self):
        async def _go():
            node = _make_model_turn(_make_bundle())
            with patch(
                "backend.agent.graph.advance_provider_turn",
                return_value={
                    "route": "approval",
                    "status": "awaiting_approval",
                    "pending_approval": {"explanation": "confirm?"},
                    "provider_state": {"provider": "openai"},
                    "session_data": {**_base_state()["session_data"], "status": "paused"},
                },
            ):
                delta = await node(_base_state())
            assert delta["route"] == "approval"
            assert delta["pending_approval"] == {"explanation": "confirm?"}
            assert delta["status"] == "awaiting_approval"

        asyncio.run(_go())

    @pytest.mark.parametrize(
        ("provider", "history_key", "history_value"),
        [
            ("openai", "next_input", [{"role": "assistant", "content": [{"type": "output_text", "text": "latest turn"}]}]),
            ("anthropic", "messages", [{"role": "assistant", "content": [{"type": "text", "text": "latest turn"}]}]),
            ("google", "contents", [{"role": "model", "parts": [{"text": "latest turn"}]}]),
        ],
    )
    def test_memory_context_preserves_provider_history_payload(self, provider, history_key, history_value):
        async def _go():
            node = _make_model_turn(_make_bundle())
            captured: list[dict[str, object]] = []

            async def _fake_advance(execution_state, on_log=None):
                del on_log
                captured.append(copy.deepcopy(execution_state))
                return {
                    "route": "completed",
                    "status": "completed",
                    "final_text": "done",
                    "session_data": {
                        **execution_state["session_data"],
                        "status": "completed",
                        "final_text": "done",
                    },
                }

            base_state = {
                **_base_state(provider),
                "provider_state": {history_key: copy.deepcopy(history_value)},
                "subgoals": [{"title": "Open dashboard", "status": "active"}],
                "active_plan": {
                    "summary": "Open the dashboard.",
                    "steps": ["Open dashboard"],
                    "active_subgoal": "Open dashboard",
                },
                "completion_criteria": ["Dashboard is open"],
            }
            memory_state = {
                **base_state,
                "evidence": [{"kind": "evidence_summary", "summary": "Use the dashboard before retrying."}],
                "memory_context": {
                    "prior_workflows": [{"workflow_summary": "Open the dashboard before taking action."}],
                    "operator_preferences": [],
                    "ui_patterns": [],
                },
                "recovery_context": {
                    "classification": "stuck",
                    "retry_reason": "model_turn",
                    "error": "Validation error",
                    "error_classification": "ValidationError",
                    "retry_count": 1,
                    "replan_count": 0,
                    "had_pending_action_batch": False,
                    "verification_status": None,
                    "latest_turn": 1,
                    "latest_model_text": "",
                    "failure_context": {},
                },
            }

            with patch("backend.agent.graph.advance_provider_turn", side_effect=_fake_advance):
                await node(base_state)
                await node(memory_state)

            assert json.dumps(captured[0]["provider_state"], sort_keys=True) == json.dumps(captured[1]["provider_state"], sort_keys=True)
            assert captured[1]["provider_state"][history_key][-1] == history_value[-1]
            assert captured[0]["system_instruction"] != captured[1]["system_instruction"]

        asyncio.run(_go())


class TestToolBatch:
    def test_emits_step_and_clears_pending_batch(self):
        async def _go():
            emitted: list[ToolBatchCompleted] = []
            node = _make_tool_batch(_make_bundle(emit_step=lambda ev: emitted.append(ev)))
            delta = await node(
                {
                    **_base_state(),
                    "pending_action_batch": {
                        "turn": 2,
                        "model_text": "thinking",
                        "results": [
                            {
                                "name": "click_at",
                                "success": True,
                                "error": None,
                                "safety_decision": None,
                                "safety_explanation": None,
                                "extra": {"pixel_x": 10, "pixel_y": 20},
                            }
                        ],
                        "screenshot_ref": None,
                    },
                }
            )
            assert delta["route"] == "verifier"
            assert delta["pending_action_batch"] is None
            assert len(emitted) == 1
            assert emitted[0].turn == 2
            assert len(delta["session_data"]["steps"]) == 1

        asyncio.run(_go())

    def test_terminal_after_batch_finishes(self):
        async def _go():
            node = _make_tool_batch(_make_bundle())
            delta = await node(
                {
                    **_base_state(),
                    "pending_action_batch": {
                        "turn": 1,
                        "model_text": "done",
                        "results": [],
                        "screenshot_ref": None,
                        "native_actions": [],
                        "terminal_text": "final answer",
                    },
                }
            )
            assert delta["route"] == "verifier"
            assert delta["latest_executor_output"]["terminal_text"] == "final answer"
            assert delta["session_data"]["status"] == "running"

        asyncio.run(_go())

    def test_failed_dispatch_preserves_batch_for_recovery(self):
        async def _go():
            node = _make_tool_batch(_make_bundle())
            with patch(
                "backend.agent.graph.dispatch_pending_action_batch",
                return_value={
                    "latest_executor_output": {
                        "turn": 1,
                        "model_text": "Click Save.",
                        "results": [
                            {
                                "name": "click_at",
                                "success": False,
                                "error": "Timed out while clicking Save.",
                                "safety_decision": None,
                                "safety_explanation": None,
                                "extra": {"pixel_x": 10, "pixel_y": 20},
                            }
                        ],
                        "screenshot_ref": None,
                    },
                    "provider_state": {},
                },
            ):
                delta = await node(
                    {
                        **_base_state(),
                        "pending_action_batch": {
                            "turn": 1,
                            "model_text": "Click Save.",
                            "results": [],
                            "screenshot_ref": None,
                            "native_actions": [{"type": "computer_call", "actions": [{"type": "click", "x": 10, "y": 20}]}],
                        },
                    }
                )
            assert delta["route"] == "retry"
            assert delta["pending_action_batch"] is not None
            assert delta["pending_action_batch"]["native_actions"][0]["type"] == "computer_call"

        asyncio.run(_go())


class TestPolicyGate:
    def test_low_risk_batch_passes_through(self):
        async def _go():
            node = _make_policy_gate(_make_bundle())
            delta = await node(
                {
                    **_base_state(),
                    "pending_action_batch": {
                        "turn": 1,
                        "model_text": "Click Save.",
                        "results": [],
                        "screenshot_ref": None,
                        "native_actions": [
                            {
                                "type": "computer_call",
                                "actions": [{"type": "click", "x": 10, "y": 20}],
                            }
                        ],
                    },
                }
            )
            assert delta["route"] == "tool_batch"

        asyncio.run(_go())


class TestApprovalInterrupt:
    def test_interrupt_clears_pending_and_sets_decision(self):
        async def _go():
            node = _make_approval_interrupt(_make_bundle())
            with patch("backend.agent.graph.interrupt", return_value=True):
                delta = await node(
                    {
                        **_base_state(),
                        "pending_approval": {"explanation": "confirm?"},
                        "session_data": {**_base_state()["session_data"], "status": "paused"},
                    }
                )
            assert delta["route"] == "model_turn"
            assert delta["approval_decision"] is True
            assert delta["pending_approval"] is None
            assert delta["session_data"]["status"] == "running"

        asyncio.run(_go())

    def test_recovery_payload_is_forwarded_to_interrupt(self):
        async def _go():
            node = _make_approval_interrupt(_make_bundle())
            with patch("backend.agent.graph.interrupt", return_value=True) as interrupt_mock:
                delta = await node(
                    {
                        **_base_state(),
                        "pending_approval": {
                            "origin": "recovery",
                            "explanation": "Need human review.",
                            "recovery_context": {
                                "classification": "blocked",
                                "retry_reason": "policy",
                                "error": "Policy approval denied.",
                                "error_classification": "PolicyDenied",
                                "retry_count": 0,
                                "replan_count": 1,
                                "had_pending_action_batch": True,
                                "verification_status": None,
                                "latest_turn": 2,
                                "latest_model_text": "Attempted to submit payment.",
                                "failure_context": {"pending_action_batch": {"turn": 2}},
                            },
                        },
                        "session_data": {**_base_state()["session_data"], "status": "paused"},
                    }
                )
            payload = interrupt_mock.call_args.args[0]
            assert payload["origin"] == "recovery"
            assert payload["recovery_context"]["classification"] == "blocked"
            assert delta["route"] == "recovery"

        asyncio.run(_go())


class TestFinalize:
    def test_finalize_persists_session_snapshot(self):
        async def _go():
            node = _make_finalize(_make_bundle())
            with patch("backend.agent.graph.cleanup_provider_resources") as cleanup:
                delta = await node(_base_state())
            assert delta["session_snapshot"]["session_id"] == "s1"
            cleanup.assert_called_once()

        asyncio.run(_go())

    def test_finalize_writes_long_term_memory_only_for_complete_and_reusable(self):
        async def _go():
            node = _make_finalize(_make_bundle())

            non_complete_store = InMemoryStore()
            with patch("backend.agent.graph.cleanup_provider_resources"), patch(
                "backend.agent.memory_layers.assess_reusable_workflow",
                return_value={
                    "reusable": True,
                    "workflow_summary": "Reusable workflow",
                    "ui_patterns": ["Top-right Save button"],
                    "operator_preferences": {"notes": ["Prefer dashboard first"]},
                    "rationale": "Reusable",
                },
            ):
                await node(
                    {
                        **_base_state(),
                        "verification_status": "needs_more_work",
                        "active_plan": {"summary": "Open dashboard", "steps": ["Open dashboard"], "active_subgoal": "Open dashboard"},
                        "completion_criteria": ["Dashboard is open"],
                    },
                    SimpleNamespace(store=non_complete_store),
                )
            assert non_complete_store.search(prior_workflows_namespace()) == []

            not_reusable_store = InMemoryStore()
            with patch("backend.agent.graph.cleanup_provider_resources"), patch(
                "backend.agent.memory_layers.assess_reusable_workflow",
                return_value={
                    "reusable": False,
                    "workflow_summary": "",
                    "ui_patterns": [],
                    "operator_preferences": {},
                    "rationale": "One-off flow",
                },
            ):
                await node(
                    {
                        **_base_state(),
                        "verification_status": "complete",
                        "active_plan": {"summary": "Open dashboard", "steps": ["Open dashboard"], "active_subgoal": "Open dashboard"},
                        "completion_criteria": ["Dashboard is open"],
                    },
                    SimpleNamespace(store=not_reusable_store),
                )
            assert not_reusable_store.search(prior_workflows_namespace()) == []

            reusable_store = InMemoryStore()
            with patch("backend.agent.graph.cleanup_provider_resources"), patch(
                "backend.agent.memory_layers.assess_reusable_workflow",
                return_value={
                    "reusable": True,
                    "workflow_summary": "Open the dashboard before acting.",
                    "ui_patterns": ["The dashboard link is in the top navigation."],
                    "operator_preferences": {"notes": ["Check dashboards before login"]},
                    "rationale": "Stable navigation pattern.",
                },
            ):
                await node(
                    {
                        **_base_state(),
                        "verification_status": "complete",
                        "active_plan": {
                            "summary": "Open dashboard",
                            "steps": ["Open dashboard"],
                            "active_subgoal": "Open dashboard",
                        },
                        "completion_criteria": ["Dashboard is open"],
                        "evidence": [{"kind": "evidence_summary", "summary": "Open the dashboard before taking action."}],
                    },
                    SimpleNamespace(store=reusable_store),
                )
            assert len(reusable_store.search(prior_workflows_namespace())) == 1
            assert len(reusable_store.search(reusable_ui_patterns_namespace())) == 1
            assert len(reusable_store.search(operator_preferences_namespace())) == 1

        asyncio.run(_go())


class TestGraphMemoryStore:
    def test_default_store_persists_between_instances(self, tmp_sqlite_path, monkeypatch):
        monkeypatch.delenv("CUA_MEMORY_STORE_MODE", raising=False)
        monkeypatch.delenv("CUA_GRAPH_STORE_MODE", raising=False)
        monkeypatch.delenv("CUA_MEMORY_STORE_PATH", raising=False)
        monkeypatch.delenv("CUA_GRAPH_STORE_PATH", raising=False)

        store = build_graph_store(tmp_sqlite_path)
        assert isinstance(store, JsonFileStore)

        store.put(
            prior_workflows_namespace(),
            "workflow-one",
            {"workflow_summary": "Open Settings, then choose Privacy."},
            index=False,
        )

        reloaded = build_graph_store(tmp_sqlite_path)
        matches = reloaded.search(prior_workflows_namespace(), limit=10)
        assert len(matches) == 1
        assert matches[0].value == {"workflow_summary": "Open Settings, then choose Privacy."}

    def test_memory_mode_keeps_explicit_ephemeral_opt_out(self, tmp_sqlite_path, monkeypatch):
        monkeypatch.setenv("CUA_MEMORY_STORE_MODE", "memory")
        monkeypatch.delenv("CUA_GRAPH_STORE_MODE", raising=False)

        assert isinstance(build_graph_store(tmp_sqlite_path), InMemoryStore)


class TestGraphRuntime:
    def test_get_runtime_raises_before_init(self):
        asyncio.run(shutdown_runtime())
        with pytest.raises(RuntimeError):
            get_runtime()

    def test_init_then_shutdown_is_idempotent(self, tmp_sqlite_path):
        async def _go():
            await init_runtime(tmp_sqlite_path)
            await init_runtime(tmp_sqlite_path)
            assert get_runtime().checkpointer is not None
            await shutdown_runtime()
            await shutdown_runtime()

        asyncio.run(_go())

    def test_load_session_snapshot_without_init_returns_none(self):
        async def _go():
            await shutdown_runtime()
            assert await load_session_snapshot("missing") is None

        asyncio.run(_go())

    def test_checkpoint_round_trip_preserves_expanded_graph_state(self, tmp_sqlite_path):
        async def _go():
            await init_runtime(tmp_sqlite_path)
            graph = build_agent_graph(_make_bundle())
            config = {"configurable": {"thread_id": "round-trip"}}
            session_data = {
                **_base_state()["session_data"],
                "status": "completed",
                "final_text": "done",
            }
            state = {
                **_base_state("anthropic"),
                "goal": "complete the checkout flow",
                "subgoals": [
                    {"title": "Open the cart", "status": "done"},
                    {"title": "Confirm the order", "status": "active"},
                ],
                "active_plan": {
                    "summary": "Open cart, review order, then confirm checkout.",
                    "steps": ["Open cart", "Confirm order"],
                },
                "evidence": [
                    {"kind": "url", "value": "https://example.test/cart"},
                    {"kind": "ocr", "value": "Order total: $42.00"},
                ],
                "completion_criteria": [
                    "Cart page opened",
                    "Order confirmation visible",
                ],
                "provider_capabilities": {
                    "provider": "anthropic",
                    "verified": True,
                    "computer_use": True,
                    "web_search": False,
                    "web_search_version": "web_search_20250305",
                    "tool_combination_supported": False,
                    "search_filtering_supported": True,
                    "allowed_callers_supported": True,
                    "reasoning_effort_default": None,
                    "tool_version": "computer_20251124",
                    "beta_flag": "computer-use-2025-11-24",
                    "model_id": "claude-sonnet-4-6",
                    "beta_headers": ["computer-use-2025-11-24"],
                    "search_allowed_domains": [],
                    "search_blocked_domains": [],
                    "allowed_callers": None,
                },
                "risk_level": "medium",
                "recovery_context": {
                    "classification": "",
                    "retry_reason": "tool_batch",
                    "error": "Temporary DOM mismatch",
                    "error_classification": "RuntimeError",
                    "retry_count": 1,
                    "replan_count": 0,
                    "had_pending_action_batch": True,
                    "verification_status": None,
                    "latest_turn": 0,
                    "latest_model_text": "",
                    "failure_context": {},
                },
            }
            with patch(
                "backend.agent.graph.advance_provider_turn",
                return_value={
                    "route": "completed",
                    "status": "completed",
                    "final_text": "done",
                    "session_data": session_data,
                },
            ):
                await graph.ainvoke(state, config=config)
            await shutdown_runtime()

            await init_runtime(tmp_sqlite_path)
            tup = await get_runtime().checkpointer.aget_tuple(config)
            values = tup.checkpoint.get("channel_values") or {}
            assert values["goal"] == "complete the checkout flow"
            assert values["planner_model"] == "claude-sonnet-4-6"
            assert values["subgoals"] == [
                {"title": "Open the cart", "status": "done"},
                {"title": "Confirm the order", "status": "active"},
            ]
            assert values["active_plan"] == {
                "summary": "Open cart, review order, then confirm checkout.",
                "steps": ["Open cart", "Confirm order"],
            }
            assert values["evidence"] == [
                {"kind": "url", "value": "https://example.test/cart"},
                {"kind": "ocr", "value": "Order total: $42.00"},
            ]
            assert values["completion_criteria"] == [
                "Cart page opened",
                "Order confirmation visible",
            ]
            assert values["provider_capabilities"] == {
                "provider": "anthropic",
                "verified": True,
                "computer_use": True,
                "web_search": False,
                "web_search_version": "web_search_20250305",
                "tool_combination_supported": False,
                "search_filtering_supported": True,
                "allowed_callers_supported": True,
                "reasoning_effort_default": None,
                "tool_version": "computer_20251124",
                "beta_flag": "computer-use-2025-11-24",
                "model_id": "claude-sonnet-4-6",
                "beta_headers": ["computer-use-2025-11-24"],
                "search_allowed_domains": [],
                "search_blocked_domains": [],
                "allowed_callers": None,
            }
            assert values["risk_level"] == "medium"
            assert values["recovery_context"] == {
                "classification": "",
                "retry_reason": "tool_batch",
                "error": "Temporary DOM mismatch",
                "error_classification": "RuntimeError",
                "retry_count": 1,
                "replan_count": 0,
                "had_pending_action_batch": True,
                "verification_status": None,
                "latest_turn": 0,
                "latest_model_text": "",
                "failure_context": {},
            }
            assert values["replan"] is False
            await shutdown_runtime()

        asyncio.run(_go())

    def test_legacy_checkpoint_load_defaults_new_graph_fields(self, tmp_sqlite_path):
        async def _go():
            try:
                await init_runtime(tmp_sqlite_path)
                runtime = get_runtime()
                config = {"configurable": {"thread_id": "legacy-state", "checkpoint_ns": ""}}
                checkpoint = empty_checkpoint()
                checkpoint["channel_values"] = {
                    "session_id": "legacy-state",
                    "task": "legacy task",
                    "max_steps": 4,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key": "test-api-key",
                    "system_instruction": "system",
                    "screen_width": 1440,
                    "screen_height": 900,
                    "container_name": "cua-environment",
                    "agent_service_url": "http://127.0.0.1:9222",
                    "reasoning_effort": None,
                    "use_builtin_search": True,
                    "search_max_uses": None,
                    "search_allowed_domains": ["example.test"],
                    "search_blocked_domains": ["blocked.test"],
                    "allowed_callers": ["direct"],
                    "attached_files": [],
                    "status": "running",
                    "route": "model_turn",
                    "retry_count": 0,
                    "turn_count": 0,
                    "session_data": {
                        "session_id": "legacy-state",
                        "task": "legacy task",
                        "status": "running",
                        "model": "claude-sonnet-4-6",
                        "engine": "computer_use",
                        "steps": [],
                        "max_steps": 4,
                        "created_at": "2026-04-27T00:00:00+00:00",
                        "final_text": None,
                        "gemini_grounding": None,
                    },
                }
                stored_config = await runtime.checkpointer.aput(
                    config,
                    checkpoint,
                    {"source": "legacy-test"},
                    {},
                )
                await shutdown_runtime()

                await init_runtime(tmp_sqlite_path)
                tup = await get_runtime().checkpointer.aget_tuple(stored_config)
                legacy_state = tup.checkpoint.get("channel_values") or {}
                node = _make_model_turn(_make_bundle())
                with patch(
                    "backend.agent.graph.advance_provider_turn",
                    return_value={
                        "route": "completed",
                        "status": "completed",
                        "final_text": "legacy done",
                        "session_data": {
                            **legacy_state["session_data"],
                            "status": "completed",
                            "final_text": "legacy done",
                        },
                    },
                ):
                    delta = await node(legacy_state)
                assert delta["goal"] == "legacy task"
                assert delta["planner_model"] == "claude-sonnet-4-6"
                assert delta["subgoals"] == []
                assert delta["active_plan"] is None
                assert delta["evidence"] == []
                assert delta["completion_criteria"] == []
                assert delta["provider_capabilities"] == {
                    "provider": "anthropic",
                    "verified": False,
                    "computer_use": False,
                    "web_search": False,
                    "web_search_version": None,
                    "tool_combination_supported": False,
                    "search_filtering_supported": False,
                    "allowed_callers_supported": False,
                    "reasoning_effort_default": None,
                    "tool_version": None,
                    "beta_flag": None,
                    "model_id": "claude-sonnet-4-6",
                    "beta_headers": [],
                    "search_allowed_domains": ["example.test"],
                    "search_blocked_domains": ["blocked.test"],
                    "allowed_callers": ["direct"],
                }
                assert delta["risk_level"] == "low"
                assert delta["recovery_context"] is None
                assert delta["replan"] is False
            finally:
                await shutdown_runtime()

        asyncio.run(_go())


class _FakeExecutor:
    def __init__(self, stats: dict[str, object] | None = None):
        self.screen_width = 1440
        self.screen_height = 900
        self._stats = stats or {}

    async def capture_screenshot(self) -> bytes:
        return PNG_BYTES

    async def execute(self, name: str, args: dict | None = None):
        payload = args or {}
        action_id = str(payload.get("action_id") or "")
        if action_id:
            seen_action_ids = self._stats.setdefault("seen_action_ids", set())
            if action_id in seen_action_ids:
                self._stats["deduped_replays"] = int(self._stats.get("deduped_replays", 0) or 0) + 1
                return CUActionResult(
                    name=name,
                    extra={
                        "pixel_x": int(payload.get("x", 10) or 10),
                        "pixel_y": int(payload.get("y", 20) or 20),
                        **({"text": payload.get("text")} if payload.get("text") else {}),
                    },
                )
            seen_action_ids.add(action_id)
            executed_action_ids = self._stats.setdefault("executed_action_ids", [])
            executed_action_ids.append(action_id)
        self._stats["executed_actions"] = int(self._stats.get("executed_actions", 0) or 0) + 1
        return CUActionResult(
            name=name,
            extra={
                "pixel_x": int(payload.get("x", 10) or 10),
                "pixel_y": int(payload.get("y", 20) or 20),
                **({"text": payload.get("text")} if payload.get("text") else {}),
            },
        )

    def get_current_url(self) -> str:
        return "https://example.test"

    async def aclose(self):
        return None


class _FakeGeminiTypes:
    class FunctionCall:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FunctionResponseBlob:
        def __init__(self, mime_type: str, data: bytes):
            self.mime_type = mime_type
            self.data = data

    class FunctionResponsePart:
        def __init__(self, inline_data):
            self.inline_data = inline_data

    class FunctionResponse:
        def __init__(self, name: str, response: dict, parts=None):
            self.name = name
            self.response = response
            self.parts = parts or []

    class Part:
        def __init__(self, text=None, function_call=None, function_response=None, inline_data=None):
            self.text = text
            self.function_call = function_call
            self.function_response = function_response
            self.inline_data = inline_data

        @classmethod
        def from_bytes(cls, *, data: bytes, mime_type: str):
            return cls(inline_data={"data": data, "mime_type": mime_type})

    class Content:
        def __init__(self, role: str, parts: list):
            self.role = role
            self.parts = parts


def _fake_engine_factory(provider: str, stats: dict[str, int]):
    executor = _FakeExecutor(stats)

    class FakeOpenAIClient:
        def __init__(self):
            self._model = "gpt-5.5"
            self._system_prompt = "system"
            self._reasoning_effort = "medium"
            self._vector_store_id = None
            self._current_screenshot_scale = 1.0
            self._use_builtin_search = True

        async def _ensure_vector_store(self, on_log=None):
            return None

        async def _cleanup_vector_store(self, on_log=None):
            return None

        def _build_tools(self, *_args, **_kwargs):
            return [{"type": "computer"}]

        async def _create_response(self, on_log=None, **kwargs):
            stats["model_calls"] += 1
            turn = int(stats.get("openai_turn_counter", 0) or 0) + 1
            stats["openai_turn_counter"] = turn
            if turn < 4:
                return SimpleNamespace(
                    error=None,
                    output_text=f"openai turn {turn}",
                    output=[
                        {
                            "type": "message",
                            "role": "assistant",
                            "phase": "commentary",
                            "content": [{"type": "output_text", "text": f"openai turn {turn}"}],
                        },
                        {
                            "type": "computer_call",
                            "call_id": f"call-{turn}",
                            "actions": [{"type": "click", "x": turn * 10, "y": turn * 20}],
                        },
                    ],
                )
            return SimpleNamespace(
                error=None,
                output_text="openai done",
                output=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": "openai done"}],
                    }
                ],
            )

        async def _execute_openai_action(self, action, executor_obj):
            if action.get("type") == "click":
                return await executor_obj.execute("click_at", {"x": action["x"], "y": action["y"]})
            return CUActionResult(name=action.get("type", "unknown"))

    class FakeClaudeMessages:
        async def create(self, **kwargs):
            stats["model_calls"] += 1
            assistant_turns = sum(1 for msg in kwargs["messages"] if msg.get("role") == "assistant")
            turn = assistant_turns + 1
            if turn < 4:
                return SimpleNamespace(
                    content=[
                        {"type": "text", "text": f"claude turn {turn}"},
                        {
                            "type": "tool_use",
                            "id": f"tu-{turn}",
                            "input": {"action": "click_at", "x": turn * 10, "y": turn * 20},
                        },
                    ],
                    stop_reason="tool_use",
                )
            return SimpleNamespace(content=[{"type": "text", "text": "claude done"}], stop_reason="end_turn")

    class FakeClaudeClient:
        def __init__(self):
            self._model = "claude-sonnet-4-6"
            self._system_prompt = "system"
            self._tool_version = "computer_20251124"
            self._beta_flag = "computer-use-2025-11-24"
            self._use_builtin_search = True
            self._allowed_callers = None
            self._client = SimpleNamespace(beta=SimpleNamespace(messages=FakeClaudeMessages()))

        async def _ensure_anthropic_web_search_enabled(self, on_log=None):
            return None

        def _build_tools(self, *_args, **_kwargs):
            return []

        async def _prepare_attached_files(self, on_log=None):
            return [], []

        async def _execute_claude_action(self, action_input, executor_obj, *, scale_factor=1.0):
            del scale_factor
            return await executor_obj.execute(action_input.get("action", "click_at"), action_input)

    class FakeGeminiClient:
        def __init__(self):
            self._model = "gemini-3-flash-preview"
            self._types = _FakeGeminiTypes
            self._last_completion_payload = None
            self._max_history_turns = 10
            self._use_builtin_search = True

        def _compose_initial_goal_text(self, goal: str) -> str:
            return goal

        def _build_config(self):
            return object()

        async def _generate(self, *, contents, config):
            del config
            stats["model_calls"] += 1
            model_turns = sum(1 for item in contents if getattr(item, "role", None) == "model")
            turn = model_turns + 1
            if turn < 4:
                return SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            content={
                                "role": "model",
                                "parts": [
                                    {"text": f"gemini turn {turn}"},
                                    {"function_call": {"name": "click_at", "args": {"x": turn * 10, "y": turn * 20}}},
                                ],
                            },
                            grounding_metadata=None,
                        )
                    ]
                )
            return SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content={"role": "model", "parts": [{"text": "gemini done"}]},
                        grounding_metadata=None,
                    )
                ]
            )

    class FakeEngine:
        def __init__(self):
            if provider == "openai":
                self._client = FakeOpenAIClient()
            elif provider == "anthropic":
                self._client = FakeClaudeClient()
            elif provider == "google":
                self._client = FakeGeminiClient()
            else:
                raise AssertionError(provider)

        def _build_executor(self):
            return executor

    return FakeEngine()


async def _run_graph_to_completion(db_path: str, provider: str, *, crash_after_second_step: bool):
    stats: dict[str, object] = {
        "model_calls": 0,
        "steps": 0,
        "crashed": False,
        "executed_actions": 0,
        "deduped_replays": 0,
        "executed_action_ids": [],
        "seen_action_ids": set(),
    }

    def _emit_step(_event):
        stats["steps"] += 1
        if crash_after_second_step and not stats["crashed"] and stats["steps"] == 2:
            stats["crashed"] = True
            raise asyncio.CancelledError()

    bundle = _make_bundle(emit_step=_emit_step)
    state = {
        **_base_state(provider),
        "planner_model": {
            "openai": "gpt-5.5",
            "anthropic": "claude-sonnet-4-6",
            "google": "gemini-3-flash-preview",
        }[provider],
        "subgoals": [{"title": "do the thing", "status": "active"}],
        "active_plan": {
            "summary": "Do the thing.",
            "steps": ["do the thing"],
            "active_subgoal": "do the thing",
        },
        "completion_criteria": ["The task is done"],
    }
    thread_id = f"thread-{provider}"

    async def _fake_verifier_text(verifier_state):
        latest = verifier_state.get("latest_executor_output") or {}
        terminal_text = str(latest.get("terminal_text") or "").strip()
        if terminal_text:
            return (
                str(verifier_state.get("planner_model") or verifier_state.get("model") or "verifier-model"),
                '{"verdict":"complete","unmet_criteria":[],"rationale":"The terminal text indicates the task is complete."}',
            )
        return (
            str(verifier_state.get("planner_model") or verifier_state.get("model") or "verifier-model"),
            '{"verdict":"needs_more_work","unmet_criteria":["The task is done"],"rationale":"Another executor turn is still required."}',
        )

    with patch("backend.agent.persisted_runtime._build_engine", side_effect=lambda current_state: _fake_engine_factory(str(current_state.get("provider")), stats)), patch("backend.agent.capability_probe._build_engine", side_effect=lambda current_state: _fake_engine_factory(str(current_state.get("provider")), stats)), patch("backend.agent.persisted_runtime._extract_gemini_grounding_payload", return_value=None), patch("backend.agent.verifier._request_verifier_text", side_effect=_fake_verifier_text):
        await init_runtime(db_path)
        graph = build_agent_graph(bundle)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            await graph.ainvoke(state, config=config)
        except asyncio.CancelledError:
            assert crash_after_second_step
        if stats["crashed"]:
            await shutdown_runtime()
            await init_runtime(db_path)
            graph = build_agent_graph(_make_bundle())
            await graph.ainvoke(None, config=config)
        runtime = get_runtime()
        tup = await runtime.checkpointer.aget_tuple(config)
        values = tup.checkpoint.get("channel_values") or {}
        snapshot = values.get("session_snapshot")
        provider_state = values.get("provider_state")
        await shutdown_runtime()
    return stats, snapshot, provider_state


async def _run_supervisor_end_to_end(db_path: str, provider: str):
    stats: dict[str, object] = {
        "model_calls": 0,
        "steps": 0,
        "crashed": False,
        "executed_actions": 0,
        "deduped_replays": 0,
        "executed_action_ids": [],
        "seen_action_ids": set(),
    }

    def _emit_step(_event):
        stats["steps"] = int(stats.get("steps", 0) or 0) + 1

    bundle = _make_bundle(emit_step=_emit_step)
    state = {
        **_base_state(provider),
        "planner_model": {
            "openai": "gpt-5.5",
            "anthropic": "claude-sonnet-4-6",
            "google": "gemini-3-flash-preview",
        }[provider],
        "use_builtin_search": True,
    }
    thread_id = f"supervisor-{provider}"

    async def _fake_planner_text(planner_state):
        return (
            str(planner_state.get("planner_model") or planner_state.get("model") or "planner-model"),
            json.dumps(
                {
                    "subgoals": [
                        {"title": "Find the official status page", "status": "active"},
                        {"title": "Open the page", "status": "pending"},
                    ],
                    "active_plan": {
                        "summary": "Find the official status page before opening it.",
                        "steps": ["Find the official status page", "Open the page"],
                        "grounding_query": "official status page",
                    },
                    "completion_criteria": ["The official status page is open"],
                }
            ),
        )

    async def _fake_grounding_evidence(current_state, *, subgoal, plan_summary, query, on_log=None):
        del current_state, on_log
        return {
            "kind": "grounding",
            "subgoal": subgoal,
            "plan_summary": plan_summary,
            "query": query,
            "summary": "The official status page is status.example.test.",
            "sources": [{"title": "Status Page", "url": "https://status.example.test"}],
            "confidence": "high",
        }

    async def _fake_verifier_text(verifier_state):
        latest = verifier_state.get("latest_executor_output") or {}
        terminal_text = str(latest.get("terminal_text") or "").strip()
        if terminal_text:
            return (
                str(verifier_state.get("planner_model") or verifier_state.get("model") or "verifier-model"),
                '{"verdict":"complete","unmet_criteria":[],"rationale":"The terminal text indicates the task is complete."}',
            )
        return (
            str(verifier_state.get("planner_model") or verifier_state.get("model") or "verifier-model"),
            '{"verdict":"needs_more_work","unmet_criteria":["The official status page is open"],"rationale":"Another executor turn is still required."}',
        )

    with patch("backend.agent.persisted_runtime._build_engine", side_effect=lambda current_state: _fake_engine_factory(str(current_state.get("provider")), stats)), patch("backend.agent.capability_probe._build_engine", side_effect=lambda current_state: _fake_engine_factory(str(current_state.get("provider")), stats)), patch("backend.agent.persisted_runtime._extract_gemini_grounding_payload", return_value=None), patch("backend.agent.planner._request_planner_text", side_effect=_fake_planner_text), patch("backend.agent.grounding_subgraph.collect_grounding_evidence", side_effect=_fake_grounding_evidence), patch("backend.agent.verifier._request_verifier_text", side_effect=_fake_verifier_text):
        await init_runtime(db_path)
        graph = build_agent_graph(bundle)
        config = {"configurable": {"thread_id": thread_id}}
        await graph.ainvoke(state, config=config)
        runtime = get_runtime()
        tup = await runtime.checkpointer.aget_tuple(config)
        values = tup.checkpoint.get("channel_values") or {}
        snapshot = values.get("session_snapshot")
        provider_state = values.get("provider_state")
        await shutdown_runtime()

    return stats, snapshot, provider_state, values

    def test_compiled_graph_uses_supervisor_node_names(self, tmp_sqlite_path):
        async def _go():
            await init_runtime(tmp_sqlite_path)
            try:
                graph = build_agent_graph(_make_bundle())
                nodes = set(graph.get_graph().nodes.keys())
                assert {
                    "intake",
                    "capability_probe",
                    "planner",
                    "grounding",
                    "executor",
                    "policy",
                    "desktop_dispatcher",
                    "verifier",
                    "escalate_interrupt",
                    "recovery",
                    "finalize",
                }.issubset(nodes)
            finally:
                await shutdown_runtime()

        asyncio.run(_go())


@pytest.mark.parametrize(
    ("provider", "history_key"),
    [
        ("openai", "next_input"),
        ("anthropic", "messages"),
        ("google", "contents"),
    ],
)
def test_restart_resume_preserves_provider_history(tmp_sqlite_path, provider, history_key):
    async def _go():
        interrupted_stats, interrupted_snapshot, interrupted_provider_state = await _run_graph_to_completion(
            tmp_sqlite_path,
            provider,
            crash_after_second_step=True,
        )
        uninterrupted_db = str(Path(tmp_sqlite_path).with_name(f"{provider}-uninterrupted.sqlite"))
        uninterrupted_stats, uninterrupted_snapshot, uninterrupted_provider_state = await _run_graph_to_completion(
            uninterrupted_db,
            provider,
            crash_after_second_step=False,
        )

        assert interrupted_stats["model_calls"] == 4
        assert uninterrupted_stats["model_calls"] == 4
        assert interrupted_snapshot["final_text"] == uninterrupted_snapshot["final_text"]
        assert interrupted_snapshot["steps"] == uninterrupted_snapshot["steps"]
        assert interrupted_provider_state[history_key] == uninterrupted_provider_state[history_key]

    asyncio.run(_go())


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
def test_supervisor_graph_runs_end_to_end_for_each_provider(tmp_sqlite_path, provider):
    async def _go():
        stats, snapshot, provider_state, values = await _run_supervisor_end_to_end(tmp_sqlite_path, provider)

        assert stats["model_calls"] == 4
        assert stats["steps"] == 4
        assert stats["executed_actions"] == 3
        assert snapshot["status"] == "completed"
        assert snapshot["final_text"] in {"openai done", "claude done", "gemini done"}
        assert values["provider_capabilities"]["verified"] is True
        assert values["active_plan"]["grounding_query"] == "official status page"
        assert any(item.get("kind") == "grounding" for item in values["evidence"])
        assert provider_state is not None

    asyncio.run(_go())


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
def test_desktop_dispatcher_restart_dedupes_replayed_actions(tmp_sqlite_path, provider):
    async def _go():
        interrupted_stats, interrupted_snapshot, interrupted_provider_state = await _run_graph_to_completion(
            tmp_sqlite_path,
            provider,
            crash_after_second_step=True,
        )
        uninterrupted_db = str(Path(tmp_sqlite_path).with_name(f"{provider}-dispatcher-uninterrupted.sqlite"))
        uninterrupted_stats, uninterrupted_snapshot, uninterrupted_provider_state = await _run_graph_to_completion(
            uninterrupted_db,
            provider,
            crash_after_second_step=False,
        )

        assert interrupted_snapshot["final_text"] == uninterrupted_snapshot["final_text"]
        assert interrupted_provider_state == uninterrupted_provider_state
        assert interrupted_stats["executed_actions"] == uninterrupted_stats["executed_actions"]
        assert interrupted_stats["executed_action_ids"] == uninterrupted_stats["executed_action_ids"]
        assert interrupted_stats["deduped_replays"] >= 1

    asyncio.run(_go())
