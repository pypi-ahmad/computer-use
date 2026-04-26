"""Eval: a destructive action must gate on an approval.

Mirror of :mod:`evals.test_login_approval`, but for a simulated
``rm -rf`` style action. Same invariants: safety gate fires,
denial terminates cleanly, no tool batch runs under a pending
approval.
"""

from __future__ import annotations

from backend.infra import observability as tracing
from backend.engine import SafetyRequired, RunCompleted

from ._harness import load_trace_or_fail, run_async, run_graph_with_decision


SESSION_ID = "eval-destroy-0000-0000-000000000002"


async def _destructive_iter(sid: str, task: str, max_steps: int):
    """Engine stub: ask for safety on a destructive action, then end."""
    yield SafetyRequired(
        explanation=(
            "Destructive action detected: delete ~/Documents recursively. "
            "Confirm to proceed."
        ),
    )
    yield RunCompleted(final_text="Agent terminated: safety confirmation denied.")


def test_destructive_action_requires_approval(sqlite_db):
    """A simulated destructive action must fire the approval gate."""
    it = _destructive_iter(SESSION_ID, "clean up my Documents folder", 3)

    final_state = run_async(
        run_graph_with_decision(
            session_id=SESSION_ID,
            task="clean up my Documents folder",
            iterator=it,
            approval_decision=False,
            sqlite_db=sqlite_db,
            max_steps=3,
        ),
    )

    assert final_state.get("status") in ("completed", "stopped")
    assert final_state.get("approval_decision") is False

    trace = load_trace_or_fail(SESSION_ID)
    safety_events = list(
        tracing.iter_events(trace, event_type=tracing.EVT_SAFETY_REQUIRED),
    )
    assert len(safety_events) == 1
    # Redacted payload must still carry the explanation text.
    assert "destructive" in safety_events[0].payload["explanation"].lower()

    resolved = list(
        tracing.iter_events(trace, event_type=tracing.EVT_APPROVAL_RESOLVED),
    )
    assert len(resolved) == 1
    assert resolved[0].payload["decision"] is False

    tracing.assert_invariants(trace)
