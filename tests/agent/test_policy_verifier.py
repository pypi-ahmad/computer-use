from __future__ import annotations

import asyncio
import copy
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agent.graph import NodeBundle, _make_policy_gate, build_agent_graph, init_runtime, shutdown_runtime
from backend.agent.verifier import build_verifier_subgraph


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
        "session_id": f"pv-{provider}",
        "task": "Complete the banner check.",
        "goal": "Complete the banner check.",
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
        "reasoning_effort": "medium" if provider == "openai" else None,
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
        "subgoals": [{"title": "Save the settings", "status": "active"}],
        "active_plan": {
            "summary": "Save the settings and confirm the banner.",
            "steps": ["Click Save", "Confirm the status banner"],
            "active_subgoal": "Save the settings",
        },
        "completion_criteria": ["Status banner is visible"],
        "evidence": [],
        "risk_level": "low",
        "session_data": {
            "session_id": f"pv-{provider}",
            "task": "Complete the banner check.",
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


def test_verifier_routes_to_finalize_when_complete():
    async def _go():
        graph = build_verifier_subgraph()
        state = {
            **_base_state(),
            "latest_executor_output": {
                "turn": 1,
                "model_text": "The status banner is visible.",
                "terminal_text": "done",
                "results": [],
                "native_actions": [],
                "screenshot_ref": None,
            },
        }
        with patch(
            "backend.agent.verifier._request_verifier_text",
            return_value=(
                "gpt-5.5",
                '{"verdict":"complete","unmet_criteria":[],"rationale":"The status banner is visible."}',
            ),
        ):
            result = await graph.ainvoke(state)
        assert result["route"] == "finalize"
        assert result["verification_status"] == "complete"
        assert result["final_text"] == "done"

    asyncio.run(_go())


def test_verifier_routes_back_to_executor_with_unmet_criteria_in_prompt(tmp_sqlite_path):
    async def _go():
        call_count = {"advance": 0, "dispatch": 0}
        try:
            await init_runtime(tmp_sqlite_path)
            graph = build_agent_graph(_make_bundle())
            original_native_actions = [
                {
                    "type": "computer_call",
                    "call_id": "call-1",
                    "actions": [{"type": "click", "x": 10, "y": 20}],
                }
            ]

            async def _fake_advance(execution_state, on_log=None):
                del on_log
                call_count["advance"] += 1
                if call_count["advance"] == 1:
                    return {
                        "route": "tool_batch",
                        "status": "running",
                        "pending_action_batch": {
                            "turn": 1,
                            "model_text": "Clicked Save.",
                            "results": [],
                            "screenshot_ref": None,
                            "native_actions": copy.deepcopy(original_native_actions),
                        },
                        "session_data": copy.deepcopy(execution_state["session_data"]),
                    }
                assert "Verifier says more work is required. Unmet criteria: Status banner is visible" in execution_state["system_instruction"]
                return {
                    "route": "tool_batch",
                    "status": "running",
                    "pending_action_batch": {
                        "turn": 2,
                        "model_text": "Banner is now visible.",
                        "results": [],
                        "screenshot_ref": None,
                        "native_actions": [],
                        "terminal_text": "done",
                    },
                    "session_data": copy.deepcopy(execution_state["session_data"]),
                }

            async def _fake_dispatch(dispatch_state, on_log=None):
                del on_log
                call_count["dispatch"] += 1
                payload = copy.deepcopy(dispatch_state["pending_action_batch"])
                if call_count["dispatch"] == 1:
                    return {
                        "route": "verifier",
                        "status": "running",
                        "latest_executor_output": {
                            **payload,
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
                        },
                        "provider_state": copy.deepcopy(dispatch_state.get("provider_state") or {}),
                        "session_data": copy.deepcopy(dispatch_state["session_data"]),
                    }
                return {
                    "route": "verifier",
                    "status": "running",
                    "latest_executor_output": payload,
                    "provider_state": copy.deepcopy(dispatch_state.get("provider_state") or {}),
                    "session_data": copy.deepcopy(dispatch_state["session_data"]),
                }

            with patch("backend.agent.graph.advance_provider_turn", side_effect=_fake_advance), patch(
                "backend.agent.graph.dispatch_pending_action_batch",
                side_effect=_fake_dispatch,
            ), patch(
                "backend.agent.verifier._request_verifier_text",
                side_effect=[
                    (
                        "gpt-5.5",
                        '{"verdict":"needs_more_work","unmet_criteria":["Status banner is visible"],"rationale":"The save action happened but the banner is not visible yet."}',
                    ),
                    (
                        "gpt-5.5",
                        '{"verdict":"complete","unmet_criteria":[],"rationale":"The status banner is visible now."}',
                    ),
                ],
            ):
                result = await graph.ainvoke(
                    _base_state(),
                    config={"configurable": {"thread_id": "verifier-feedback"}},
                )

            assert result["status"] == "completed"
            assert call_count == {"advance": 2, "dispatch": 2}
        finally:
            await shutdown_runtime()

    asyncio.run(_go())


def test_policy_gate_escalates_high_risk_actions_to_interrupt():
    async def _go():
        node = _make_policy_gate(_make_bundle())
        result = await node(
            {
                **_base_state(),
                "pending_action_batch": {
                    "turn": 1,
                    "model_text": "Send the email confirmation to the customer now.",
                    "results": [],
                    "screenshot_ref": None,
                    "native_actions": [
                        {
                            "type": "computer_call",
                            "actions": [{"type": "click", "target": "Send email"}],
                        }
                    ],
                },
            }
        )
        assert result["route"] == "approval"
        assert result["status"] == "awaiting_approval"
        assert result["pending_approval"]["origin"] == "policy"

    asyncio.run(_go())


def test_provider_payloads_pass_through_policy_unchanged(tmp_sqlite_path):
    async def _go():
        captured_payloads: list[list[dict[str, object]]] = []
        try:
            await init_runtime(tmp_sqlite_path)
            graph = build_agent_graph(_make_bundle())
            original_native_actions = [
                {
                    "type": "computer_call",
                    "call_id": "call-low-risk",
                    "actions": [{"type": "click", "x": 22, "y": 44}],
                }
            ]

            async def _fake_advance(execution_state, on_log=None):
                del on_log
                return {
                    "route": "tool_batch",
                    "status": "running",
                    "pending_action_batch": {
                        "turn": 1,
                        "model_text": "Click the Save button.",
                        "results": [],
                        "screenshot_ref": None,
                        "native_actions": copy.deepcopy(original_native_actions),
                    },
                    "session_data": copy.deepcopy(execution_state["session_data"]),
                }

            async def _fake_dispatch(dispatch_state, on_log=None):
                del on_log
                captured_payloads.append(copy.deepcopy(dispatch_state["pending_action_batch"]["native_actions"]))
                return {
                    "route": "verifier",
                    "status": "running",
                    "latest_executor_output": {
                        **copy.deepcopy(dispatch_state["pending_action_batch"]),
                        "results": [
                            {
                                "name": "click_at",
                                "success": True,
                                "error": None,
                                "safety_decision": None,
                                "safety_explanation": None,
                                "extra": {"pixel_x": 22, "pixel_y": 44},
                            }
                        ],
                        "terminal_text": "done",
                    },
                    "provider_state": copy.deepcopy(dispatch_state.get("provider_state") or {}),
                    "session_data": copy.deepcopy(dispatch_state["session_data"]),
                }

            with patch("backend.agent.graph.advance_provider_turn", side_effect=_fake_advance), patch(
                "backend.agent.graph.dispatch_pending_action_batch",
                side_effect=_fake_dispatch,
            ), patch(
                "backend.agent.verifier._request_verifier_text",
                return_value=(
                    "gpt-5.5",
                    '{"verdict":"complete","unmet_criteria":[],"rationale":"The save action completed."}',
                ),
            ):
                result = await graph.ainvoke(
                    _base_state(),
                    config={"configurable": {"thread_id": "payload-pass-through"}},
                )

            assert result["status"] == "completed"
            assert captured_payloads == [original_native_actions]
        finally:
            await shutdown_runtime()

    asyncio.run(_go())


def test_policy_gate_consults_capability_probe_snapshot():
    async def _go():
        node = _make_policy_gate(_make_bundle())
        result = await node(
            {
                **_base_state(),
                "provider_capabilities": {
                    **_base_state()["provider_capabilities"],
                    "provider": "openai",
                    "verified": True,
                    "computer_use": False,
                },
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
        assert result["route"] == "approval"
        assert "did not verify computer_use support" in result["pending_approval"]["explanation"]

    asyncio.run(_go())