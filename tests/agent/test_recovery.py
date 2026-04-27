from __future__ import annotations

import asyncio
from unittest.mock import patch

from backend.agent.recovery import build_recovery_subgraph


def _base_state() -> dict:
    return {
        "session_id": "recovery-1",
        "retry_reason": "tool_batch",
        "retry_count": 0,
        "error": "Timed out while clicking Save.",
        "last_error_classification": "RuntimeError",
        "verification_status": "",
        "verification_rationale": None,
        "approval_decision": None,
        "pending_approval": None,
        "replan": False,
        "final_text": "",
        "status": "error",
        "route": "retry",
        "pending_action_batch": {
            "turn": 1,
            "model_text": "Click Save.",
            "results": [],
            "screenshot_ref": None,
            "native_actions": [{"type": "computer_call", "actions": [{"type": "click", "x": 10, "y": 20}]}],
        },
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
        "session_data": {
            "session_id": "recovery-1",
            "task": "Recover from a failed action.",
            "status": "running",
            "model": "gpt-5.5",
            "engine": "computer_use",
            "steps": [],
            "max_steps": 4,
            "created_at": "2026-04-27T00:00:00+00:00",
            "final_text": None,
            "gemini_grounding": None,
        },
    }


def test_transient_classification_retries_same_executor_turn():
    async def _go():
        graph = build_recovery_subgraph(max_transient_retries=3, max_replans=2)
        with patch("backend.agent.recovery.classify_recovery_failure", return_value="transient"):
            result = await graph.ainvoke(_base_state())
        assert result["route"] == "tool_batch"
        assert result["retry_count"] == 1
        assert result["recovery_context"]["classification"] == "transient"
        assert result["recovery_context"]["retry_count"] == 1

    asyncio.run(_go())


def test_three_transient_retries_force_replan():
    async def _go():
        graph = build_recovery_subgraph(max_transient_retries=3, max_replans=2)
        with patch("backend.agent.recovery.classify_recovery_failure", return_value="transient"):
            result = await graph.ainvoke({**_base_state(), "retry_count": 2})
        assert result["route"] == "planner"
        assert result["replan"] is True
        assert result["recovery_context"]["classification"] == "stuck"
        assert result["recovery_context"]["retry_count"] == 3
        assert result["recovery_context"]["replan_count"] == 1

    asyncio.run(_go())


def test_stuck_classification_routes_to_planner():
    async def _go():
        graph = build_recovery_subgraph(max_transient_retries=3, max_replans=2)
        with patch("backend.agent.recovery.classify_recovery_failure", return_value="stuck"):
            result = await graph.ainvoke(_base_state())
        assert result["route"] == "planner"
        assert result["replan"] is True
        assert result["recovery_context"]["classification"] == "stuck"
        assert result["recovery_context"]["replan_count"] == 1

    asyncio.run(_go())


def test_blocked_classification_escalates_with_full_context():
    async def _go():
        graph = build_recovery_subgraph(max_transient_retries=3, max_replans=2)
        with patch("backend.agent.recovery.classify_recovery_failure", return_value="blocked"):
            result = await graph.ainvoke(_base_state())
        assert result["route"] == "approval"
        assert result["status"] == "awaiting_approval"
        assert result["pending_approval"]["origin"] == "recovery"
        assert result["pending_approval"]["recovery_context"]["classification"] == "blocked"
        assert result["pending_approval"]["recovery_context"]["failure_context"]["pending_action_batch"]["turn"] == 1
        assert result["pending_approval"]["recovery_context"]["failure_context"]["failed_results"][0]["name"] == "click_at"

    asyncio.run(_go())


def test_fatal_classification_routes_to_finalize():
    async def _go():
        graph = build_recovery_subgraph(max_transient_retries=3, max_replans=2)
        with patch("backend.agent.recovery.classify_recovery_failure", return_value="fatal"):
            result = await graph.ainvoke(_base_state())
        assert result["route"] == "completed"
        assert result["status"] == "error"
        assert result["final_text"] == "Timed out while clicking Save."
        assert result["recovery_context"]["classification"] == "fatal"

    asyncio.run(_go())


def test_recovery_context_captures_memory_briefs():
    async def _go():
        graph = build_recovery_subgraph(max_transient_retries=3, max_replans=2)
        state = {
            **_base_state(),
            "evidence": [{"kind": "evidence_summary", "summary": "Check the status page before retrying."}],
            "memory_context": {
                "prior_workflows": [{"workflow_summary": "Open the status page before logging in."}],
                "operator_preferences": [],
                "ui_patterns": [],
            },
        }
        with patch("backend.agent.recovery.classify_recovery_failure", return_value="blocked"):
            result = await graph.ainvoke(state)
        failure_context = result["pending_approval"]["recovery_context"]["failure_context"]
        assert "status page" in failure_context["evidence_brief"].lower()
        assert "prior workflow" in failure_context["memory_context_brief"].lower()

    asyncio.run(_go())