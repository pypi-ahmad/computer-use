# Operator Migration Note: Supervisor Graph Rollout

## What Changed

New sessions now choose their graph implementation at session start:

- legacy graph: default path
- supervisor graph: enabled only when `CUA_USE_SUPERVISOR_GRAPH=1` and the
  kill switch is not active

The provider-native Anthropic, OpenAI, and Gemini adapters are unchanged on
both paths. The frontend WebSocket event contract is also unchanged.

## Required Operator Settings

- `CUA_USE_SUPERVISOR_GRAPH`
  - `0`: force legacy graph for all new sessions
  - `1`: request supervisor graph for all new sessions unless the kill switch
    forces fallback
- `CUA_SUPERVISOR_FAILURE_RATE_THRESHOLD`
  - default `0.20`
- `CUA_SUPERVISOR_FAILURE_RATE_MIN_SESSIONS`
  - default `100`

## Metrics Dashboard Input

Use `GET /api/agent/graph-rollout` as the dashboard feed for rollout status.
Track these fields during rollout:

- `graphs.supervisor.nodes.*.latency_histogram_ms`
- `graphs.supervisor.nodes.*.invocation_failure_rate`
- `graphs.supervisor.nodes.*.session_failure_rate`
- `graphs.supervisor.verifier_verdicts`
- `graphs.supervisor.policy.escalation_rate`
- `graphs.supervisor.recovery_classifications`
- `graphs.supervisor.planner_memory.hit_rate`
- `kill_switch`
- `alerts`

## Kill Switch Behavior

- The kill switch evaluates supervisor node failures over the configured
  rolling session window.
- Once tripped, all new sessions automatically fall back to the legacy graph.
- The backend emits an alert log and exposes the trip in the rollout snapshot.
- Because rollout state is in memory, clearing a trip currently requires a
  backend restart after the incident is resolved.

## Recommended Operating Procedure

1. Confirm the rollout flag value before changing traffic allocation.
2. Watch the rollout snapshot continuously during each rollout phase.
3. If the kill switch trips, stop the ramp, investigate the offending node,
   and restart the backend only after mitigation is in place.
4. Keep the legacy path available until Phase D is complete.