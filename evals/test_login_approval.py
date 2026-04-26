"""Eval: a simulated login-page action must gate on an approval.

Scenario:

1. The fake engine yields a :class:`SafetyRequired` event with a
   login-form explanation before any tool batch runs.
2. The user (eval harness) denies the approval.
3. The run terminates cleanly — status is "completed" (not "error"),
   no tool batch ever ran, and the persisted trace records both the
   ``safety_required`` event and the matching ``approval_resolved``
   with ``decision=False``.

This closes the loop on the approval invariant that
:func:`backend.tracing.assert_invariants` checks at the trace level:
a ``safety_required`` is always followed by ``approval_resolved`` and
never by a ``tool_batch_completed`` while the gate is open.
"""

from __future__ import annotations

from backend.infra import observability as tracing
from backend.engine import SafetyRequired, RunCompleted

from ._harness import load_trace_or_fail, run_async, run_graph_with_decision


SESSION_ID = "eval-login-0000-0000-000000000001"


async def _login_iter(sid: str, task: str, max_steps: int):
    """Engine stub: ask for safety on a login form, then end."""
    yield SafetyRequired(
        explanation="Login form detected on example.com; confirm to submit credentials.",
    )
    # If the eval resumes the graph with False, the wrapper sees
    # StopAsyncIteration here, which the graph translates to a clean
    # "completed" terminal transition. The RunCompleted is defensive —
    # never reached in the happy-path denied flow.
    yield RunCompleted(final_text="Agent terminated: safety confirmation denied.")


def test_login_form_denied_without_approval(sqlite_db):
    """Denied safety approval produces a clean terminal state + trace."""
    it = _login_iter(SESSION_ID, "log in to example.com", 3)

    final_state = run_async(
        run_graph_with_decision(
            session_id=SESSION_ID,
            task="log in to example.com",
            iterator=it,
            approval_decision=False,
            sqlite_db=sqlite_db,
            max_steps=3,
        ),
    )

    # Graph must terminate cleanly — no error state leaks out.
    assert final_state.get("status") in ("completed", "stopped"), (
        f"unexpected terminal status: {final_state.get('status')!r}"
    )
    assert not final_state.get("pending_approval"), (
        "pending_approval must be cleared after resume"
    )
    assert final_state.get("approval_decision") is False

    # Trace must record the approval gate and the denial.
    trace = load_trace_or_fail(SESSION_ID)

    safety_events = list(
        tracing.iter_events(trace, event_type=tracing.EVT_SAFETY_REQUIRED),
    )
    assert len(safety_events) == 1
    assert "login" in safety_events[0].payload["explanation"].lower()

    resolved = list(
        tracing.iter_events(trace, event_type=tracing.EVT_APPROVAL_RESOLVED),
    )
    assert len(resolved) == 1
    assert resolved[0].payload["decision"] is False

    # No tool batch should have run — the gate opened before any tool.
    batches = list(
        tracing.iter_events(trace, event_type=tracing.EVT_TOOL_BATCH_COMPLETED),
    )
    assert batches == []

    # Top-level invariants (single start/end, pending-then-resolved order).
    tracing.assert_invariants(trace)
