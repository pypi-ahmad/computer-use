# Evals

Offline, deterministic replay evals for the Computer Use agent
runtime. Each eval exercises a whole-session invariant that unit
tests can't reach: the graph, the engine-iterator contract, the
trace recorder, and (where relevant) the FastAPI readiness gate all
participate.

## What's in here

| File | What it asserts |
| --- | --- |
| `test_login_approval.py` | A simulated login-form action triggers `SafetyRequired`; a denied approval produces a clean terminal state; the trace records `safety_required` → `approval_resolved(False)` and no tool batch ran while the gate was open. |
| `test_destructive_approval.py` | Same invariants for a simulated destructive (`rm -rf`) action. |
| `test_degraded_container_startup.py` | `POST /api/agent/start` returns **HTTP 409** when the container is up but the in-container agent is `unready`, and no session row is created. |

## Running

From the repo root:

```bash
pytest evals/
```

Evals are fully offline:

- No network, no real provider SDK calls (engines are replaced by
  tiny async-generator stubs that yield canned `TurnEvent`s).
- No real Docker container (the container-state helpers are mocked).
- Each eval gets its own sqlite checkpointer and trace directory via
  the fixtures in `conftest.py`. The operator's real `~/.computer-use`
  store is never touched.

## Tracing

Every session produces a sidecar JSON file at
`$CUA_TRACE_DIR/<session_id>.json`. Inspect it with:

```bash
python -m backend.infra.observability dump <session_id>
python -m backend.infra.observability list
```

During an eval the `conftest.py` fixture pins `$CUA_TRACE_DIR` to a
tmp directory; outside of evals it defaults to
`~/.computer-use/traces/`.

## Adding a new eval

1. Write an async generator that yields `TurnEvent`s modelling the
   scenario. For approval-driven scenarios, `yield
   SafetyRequired(...)` first; the harness delivers the decision via
   LangGraph's interrupt-resume.
2. Call `evals._harness.run_graph_with_decision(...)` to run the
   graph and finalize the trace.
3. Load the trace with `backend.infra.observability.load_trace(session_id)` and
   assert on the ordered events using `tracing.iter_events(...)`.
4. Call `tracing.assert_invariants(trace)` as a baseline.

The harness does not touch real provider SDKs — keep evals that way.
