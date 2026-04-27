# Supervisor Graph Rollout Plan

This rollout keeps the legacy six-node graph as the default path while the
supervisor graph is observed in staging and then incrementally exposed in
production.

## Flag

- `CUA_USE_SUPERVISOR_GRAPH=0` keeps new sessions on the legacy graph.
- `CUA_USE_SUPERVISOR_GRAPH=1` requests the supervisor graph for new sessions.
- If the automatic kill switch is active, new sessions still fall back to the
  legacy graph even when the flag is enabled.

## Metrics Feed

Operators can read the rollout snapshot from `GET /api/agent/graph-rollout`.
The payload includes:

- selected graph counts and reasons
- per-node latency histograms
- per-node invocation and session-window failure rates
- verifier verdict distribution
- policy escalation rate
- recovery classification distribution
- planner-stage long-term memory hit rate
- kill-switch status and alert history

## Kill Switch

- The kill switch watches supervisor node session-failure rates.
- Default threshold: `20%` failures over the most recent `100` supervisor
  sessions for a node.
- Config overrides:
  - `CUA_SUPERVISOR_FAILURE_RATE_THRESHOLD`
  - `CUA_SUPERVISOR_FAILURE_RATE_MIN_SESSIONS`
- When tripped, the backend logs an alert and all new sessions automatically
  use the legacy graph.
- The current implementation keeps rollout state in process memory. After the
  underlying issue is fixed, restart the backend before re-enabling the
  rollout.

## Phases

### Phase A

- Production: keep `CUA_USE_SUPERVISOR_GRAPH=0`.
- Staging: set `CUA_USE_SUPERVISOR_GRAPH=1`.
- Run the integration suite for 1 week.
- Watch the rollout snapshot for node latency regressions, elevated node
  failure rates, and unexpected verifier or recovery distributions.

### Phase B

- Production: enable the supervisor graph for 5% of sessions.
- Keep staging at 100% supervisor.
- Monitor rollout metrics for 1 week.
- Stop the ramp immediately if the kill switch trips or if policy escalations,
  verifier regressions, or recovery classifications move materially away from
  the staging baseline.

### Phase C

- Increase production exposure from 5% to 50%.
- If the 50% window remains stable, ramp to 100%.
- Keep the legacy graph code path intact throughout the ramp.

### Phase D

- Remove the legacy graph code in a separate follow-up prompt after the
  rollout completes and the supervisor path is stable.
- Do not remove the legacy graph in this prompt.