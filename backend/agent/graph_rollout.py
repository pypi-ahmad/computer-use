from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from backend.infra.config import config

logger = logging.getLogger(__name__)

LEGACY_GRAPH_MODE = "legacy"
SUPERVISOR_GRAPH_MODE = "supervisor"

_LATENCY_BUCKETS_MS = (50, 100, 250, 500, 1000, 2500, 5000)
_ALERT_LIMIT = 50


@dataclass(frozen=True)
class GraphSelection:
    requested_mode: str
    selected_mode: str
    reason: str
    kill_switch_active: bool
    alert: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "selected_mode": self.selected_mode,
            "reason": self.reason,
            "kill_switch_active": self.kill_switch_active,
            "alert": self.alert,
        }


@dataclass
class _SessionStats:
    graph_mode: str
    requested_supervisor: bool
    reason: str
    started_at: float = field(default_factory=time.time)
    invoked_nodes: set[str] = field(default_factory=set)
    failed_nodes: set[str] = field(default_factory=set)


_lock = threading.Lock()
_active_sessions: dict[str, _SessionStats] = {}
_node_latency_histograms: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
_node_invocations: dict[str, Counter[str]] = defaultdict(Counter)
_node_failures: dict[str, Counter[str]] = defaultdict(Counter)
_selection_counts: Counter[str] = Counter()
_selection_reasons: Counter[str] = Counter()
_verifier_verdicts: Counter[str] = Counter()
_recovery_classifications: Counter[str] = Counter()
_policy_evaluations = 0
_policy_escalations = 0
_planner_memory_reads = 0
_planner_memory_hits = 0
_supervisor_node_session_windows: dict[str, deque[bool]] = defaultdict(deque)
_alerts: deque[dict[str, Any]] = deque(maxlen=_ALERT_LIMIT)
_kill_switch_state: dict[str, Any] = {
    "active": False,
    "node": None,
    "failure_rate": 0.0,
    "window_size": 0,
    "tripped_at": None,
    "alert": None,
}


def _threshold() -> float:
    return float(config.supervisor_failure_rate_threshold)


def _min_sessions() -> int:
    return int(config.supervisor_failure_rate_min_sessions)


def _latency_bucket(duration_ms: float) -> str:
    for upper in _LATENCY_BUCKETS_MS:
        if duration_ms <= upper:
            return f"<={upper}ms"
    return f">{_LATENCY_BUCKETS_MS[-1]}ms"


def _append_alert_locked(level: str, message: str, **payload: Any) -> None:
    _alerts.append(
        {
            "ts": time.time(),
            "level": level,
            "message": message,
            **payload,
        }
    )


def reset_state() -> None:
    with _lock:
        _active_sessions.clear()
        _node_latency_histograms.clear()
        _node_invocations.clear()
        _node_failures.clear()
        _selection_counts.clear()
        _selection_reasons.clear()
        _verifier_verdicts.clear()
        _recovery_classifications.clear()
        _supervisor_node_session_windows.clear()
        _alerts.clear()
        global _policy_evaluations, _policy_escalations
        global _planner_memory_reads, _planner_memory_hits
        _policy_evaluations = 0
        _policy_escalations = 0
        _planner_memory_reads = 0
        _planner_memory_hits = 0
        _kill_switch_state.update(
            {
                "active": False,
                "node": None,
                "failure_rate": 0.0,
                "window_size": 0,
                "tripped_at": None,
                "alert": None,
            }
        )


def begin_session(session_id: str, *, requested_supervisor: bool) -> GraphSelection:
    requested_mode = SUPERVISOR_GRAPH_MODE if requested_supervisor else LEGACY_GRAPH_MODE
    with _lock:
        if requested_supervisor and _kill_switch_state["active"]:
            selection = GraphSelection(
                requested_mode=requested_mode,
                selected_mode=LEGACY_GRAPH_MODE,
                reason="kill_switch",
                kill_switch_active=True,
                alert=str(_kill_switch_state.get("alert") or "Supervisor kill switch active."),
            )
        elif requested_supervisor:
            selection = GraphSelection(
                requested_mode=requested_mode,
                selected_mode=SUPERVISOR_GRAPH_MODE,
                reason="flag_enabled",
                kill_switch_active=False,
            )
        else:
            selection = GraphSelection(
                requested_mode=requested_mode,
                selected_mode=LEGACY_GRAPH_MODE,
                reason="flag_disabled",
                kill_switch_active=bool(_kill_switch_state["active"]),
                alert=str(_kill_switch_state.get("alert") or "") or None,
            )
        _active_sessions[session_id] = _SessionStats(
            graph_mode=selection.selected_mode,
            requested_supervisor=requested_supervisor,
            reason=selection.reason,
        )
        _selection_counts[selection.selected_mode] += 1
        _selection_reasons[selection.reason] += 1

    if selection.reason == "kill_switch":
        logger.error(
            "Supervisor kill switch active; session %s falling back to legacy graph: %s",
            session_id,
            selection.alert,
        )
    else:
        logger.info(
            "Graph selection for session %s: requested=%s selected=%s reason=%s",
            session_id,
            selection.requested_mode,
            selection.selected_mode,
            selection.reason,
        )
    return selection


def record_node_result(
    session_id: str,
    *,
    graph_mode: str,
    node_name: str,
    duration_ms: float,
    failed: bool,
) -> None:
    with _lock:
        _node_invocations[graph_mode][node_name] += 1
        _node_latency_histograms[graph_mode][node_name][_latency_bucket(duration_ms)] += 1
        if failed:
            _node_failures[graph_mode][node_name] += 1
        session = _active_sessions.get(session_id)
        if session is not None:
            session.invoked_nodes.add(node_name)
            if failed:
                session.failed_nodes.add(node_name)


def record_verifier_verdict(verdict: str) -> None:
    verdict_key = str(verdict or "unknown").strip().lower() or "unknown"
    with _lock:
        _verifier_verdicts[verdict_key] += 1


def record_policy_evaluation(*, escalated: bool) -> None:
    global _policy_evaluations, _policy_escalations
    with _lock:
        _policy_evaluations += 1
        if escalated:
            _policy_escalations += 1


def record_recovery_classification(classification: str | None) -> None:
    key = str(classification or "unknown").strip().lower() or "unknown"
    with _lock:
        _recovery_classifications[key] += 1


def record_planner_memory_read(*, hit: bool) -> None:
    global _planner_memory_reads, _planner_memory_hits
    with _lock:
        _planner_memory_reads += 1
        if hit:
            _planner_memory_hits += 1


def finalize_session(session_id: str, *, status: str) -> None:
    del status
    with _lock:
        session = _active_sessions.pop(session_id, None)
        if session is None or session.graph_mode != SUPERVISOR_GRAPH_MODE:
            return
        window_size = _min_sessions()
        for node_name in session.invoked_nodes:
            window = _supervisor_node_session_windows[node_name]
            window.append(node_name in session.failed_nodes)
            while len(window) > window_size:
                window.popleft()
        _evaluate_kill_switch_locked()


def _evaluate_kill_switch_locked() -> None:
    threshold = _threshold()
    min_sessions = _min_sessions()
    hottest_node = None
    hottest_rate = 0.0
    hottest_window = 0
    for node_name, window in _supervisor_node_session_windows.items():
        if len(window) < min_sessions:
            continue
        failure_rate = sum(1 for item in window if item) / float(len(window))
        if failure_rate > threshold and failure_rate >= hottest_rate:
            hottest_node = node_name
            hottest_rate = failure_rate
            hottest_window = len(window)
    if hottest_node is None:
        return
    if _kill_switch_state["active"] and _kill_switch_state.get("node") == hottest_node:
        return
    message = (
        f"Supervisor kill switch tripped on node '{hottest_node}' "
        f"at {hottest_rate:.1%} failures over {hottest_window} sessions."
    )
    _kill_switch_state.update(
        {
            "active": True,
            "node": hottest_node,
            "failure_rate": hottest_rate,
            "window_size": hottest_window,
            "tripped_at": time.time(),
            "alert": message,
        }
    )
    _append_alert_locked(
        "error",
        message,
        node=hottest_node,
        failure_rate=hottest_rate,
        window_size=hottest_window,
    )
    logger.error(message)


def _node_metrics_snapshot(graph_mode: str) -> dict[str, Any]:
    nodes = set(_node_invocations[graph_mode]) | set(_node_latency_histograms[graph_mode])
    if graph_mode == SUPERVISOR_GRAPH_MODE:
        nodes |= set(_supervisor_node_session_windows)
    snapshot: dict[str, Any] = {}
    for node_name in sorted(nodes):
        invocations = int(_node_invocations[graph_mode].get(node_name, 0))
        failures = int(_node_failures[graph_mode].get(node_name, 0))
        invocation_failure_rate = failures / float(invocations) if invocations else 0.0
        window = list(_supervisor_node_session_windows.get(node_name, ())) if graph_mode == SUPERVISOR_GRAPH_MODE else []
        session_failure_rate = (
            sum(1 for item in window if item) / float(len(window)) if window else 0.0
        )
        snapshot[node_name] = {
            "invocations": invocations,
            "failures": failures,
            "invocation_failure_rate": invocation_failure_rate,
            "session_window_size": len(window),
            "session_failure_rate": session_failure_rate,
            "latency_histogram_ms": dict(_node_latency_histograms[graph_mode].get(node_name, {})),
        }
    return snapshot


def get_snapshot() -> dict[str, Any]:
    with _lock:
        policy_escalation_rate = (
            _policy_escalations / float(_policy_evaluations) if _policy_evaluations else 0.0
        )
        planner_memory_hit_rate = (
            _planner_memory_hits / float(_planner_memory_reads) if _planner_memory_reads else 0.0
        )
        return {
            "config": {
                "flag_enabled": bool(config.use_supervisor_graph),
                "failure_rate_threshold": _threshold(),
                "failure_rate_min_sessions": _min_sessions(),
            },
            "selection_counts": dict(_selection_counts),
            "selection_reasons": dict(_selection_reasons),
            "kill_switch": dict(_kill_switch_state),
            "alerts": list(_alerts),
            "graphs": {
                LEGACY_GRAPH_MODE: {
                    "nodes": _node_metrics_snapshot(LEGACY_GRAPH_MODE),
                },
                SUPERVISOR_GRAPH_MODE: {
                    "nodes": _node_metrics_snapshot(SUPERVISOR_GRAPH_MODE),
                    "verifier_verdicts": dict(_verifier_verdicts),
                    "policy": {
                        "evaluations": _policy_evaluations,
                        "escalations": _policy_escalations,
                        "escalation_rate": policy_escalation_rate,
                    },
                    "recovery_classifications": dict(_recovery_classifications),
                    "planner_memory": {
                        "reads": _planner_memory_reads,
                        "hits": _planner_memory_hits,
                        "hit_rate": planner_memory_hit_rate,
                    },
                },
            },
        }