from __future__ import annotations
"""LangGraph state machine for the Computer Use agent loop.

The graph persists all provider-side replay state in the sqlite
checkpointer so a resumed run never depends on a pre-restart in-memory
async iterator.
"""


import copy
import logging
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import interrupt

from backend.agent import graph_rollout
from backend.agent.capability_probe import build_capability_probe_subgraph
from backend.agent.executor_prompt import build_executor_system_prompt
from backend.agent.grounding_subgraph import build_grounding_subgraph, needs_grounding
from backend.agent.memory_layers import build_graph_store, normalize_evidence_entries, write_long_term_memory
from backend.agent.planner import build_planner_subgraph
from backend.agent.policy import classify_pending_action_batch, policy_explanation
from backend.agent.recovery import build_recovery_subgraph
from backend.agent.persisted_runtime import (
    advance_provider_turn,
    append_step,
    cleanup_provider_resources,
    dispatch_pending_action_batch,
    deserialize_tool_batch,
    _session_running,
    snapshot_session_data,
)
from backend.agent.verifier import build_verifier_subgraph
from backend.engine import ToolBatchCompleted

logger = logging.getLogger(__name__)

INTAKE_NODE = "intake"
CAPABILITY_PROBE_NODE = "capability_probe"
PLANNER_NODE = "planner"
GROUNDING_NODE = "grounding"
EXECUTOR_NODE = "executor"
POLICY_NODE = "policy"
DESKTOP_DISPATCHER_NODE = "desktop_dispatcher"
VERIFIER_NODE = "verifier"
ESCALATE_INTERRUPT_NODE = "escalate_interrupt"
RECOVERY_NODE = "recovery"
FINALIZE_NODE = "finalize"

LEGACY_PREFLIGHT_NODE = "preflight"
LEGACY_MODEL_TURN_NODE = "model_turn"
LEGACY_POLICY_GATE_NODE = "policy_gate"
LEGACY_TOOL_BATCH_NODE = "tool_batch"
LEGACY_APPROVAL_INTERRUPT_NODE = "approval_interrupt"

_VALID_SUBGOAL_STATUSES = {"pending", "active", "done", "blocked"}
_VALID_RISK_LEVELS = {"low", "medium", "high"}


class SubgoalState(TypedDict, total=False):
    """Planner-owned intermediate objective tracked in graph state."""

    title: str
    status: str


class ProviderCapabilitiesState(TypedDict, total=False):
    """Snapshot of the session-level tool/config capability surface."""

    provider: str
    verified: bool
    computer_use: bool
    web_search: bool
    web_search_version: Optional[str]
    tool_combination_supported: bool
    search_filtering_supported: bool
    allowed_callers_supported: bool
    reasoning_effort_default: Optional[str]
    tool_version: Optional[str]
    beta_flag: Optional[str]
    model_id: str
    beta_headers: list[str]
    search_allowed_domains: list[str]
    search_blocked_domains: list[str]
    allowed_callers: Optional[list[str]]


class RecoveryContextState(TypedDict, total=False):
    """Graph-owned summary of the last failure routed through recovery."""

    classification: str
    retry_reason: str
    error: Optional[str]
    error_classification: Optional[str]
    retry_count: int
    replan_count: int
    had_pending_action_batch: bool
    verification_status: Optional[str]
    latest_turn: int
    latest_model_text: str
    failure_context: dict[str, Any]


class AgentGraphState(TypedDict, total=False):
    """State passed between graph nodes and persisted by the checkpointer."""

    # Request context
    session_id: str
    task: str
    goal: str
    max_steps: int
    provider: str
    model: str
    planner_model: str
    api_key: str
    system_instruction: str
    screen_width: int
    screen_height: int
    container_name: str
    agent_service_url: str
    reasoning_effort: Optional[str]
    use_builtin_search: bool
    search_max_uses: Optional[int]
    search_allowed_domains: Optional[list[str]]
    search_blocked_domains: Optional[list[str]]
    allowed_callers: Optional[list[str]]
    attached_files: list[str]
    subgoals: list[SubgoalState]
    active_plan: Optional[dict[str, Any]]
    evidence: list[dict[str, Any]]
    completion_criteria: list[str]
    memory_context: Optional[dict[str, list[dict[str, Any]]]]
    latest_executor_output: Optional[dict[str, Any]]
    verification_status: str
    unmet_completion_criteria: list[str]
    verification_rationale: Optional[str]
    provider_capabilities: ProviderCapabilitiesState
    risk_level: str
    recovery_context: Optional[RecoveryContextState]
    replan: bool

    # Lifecycle / routing
    healthy: bool
    status: str   # "preflight" | "running" | "awaiting_approval" | "completed" | "error"
    route: str    # internal router signal
    error: Optional[str]

    # Turn bookkeeping
    turn_count: int
    last_model_text: str
    provider_state: dict[str, Any]
    pending_action_batch: Optional[dict[str, Any]]
    pending_terminal_after_batch: Optional[str]
    last_screenshot_ref: Optional[str]
    session_data: dict[str, Any]

    # Approval (checkpointed across restarts)
    pending_approval: Optional[dict[str, Any]]
    approval_decision: Optional[bool]

    # Retry
    retry_count: int
    retry_reason: str
    last_error_classification: Optional[str]

    # Terminal
    final_text: str
    session_snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Node bundle — I/O closures the graph needs.
# ---------------------------------------------------------------------------

def _noop_log(_level: str, _msg: str, _data: dict[str, Any] | None = None) -> None:
    return None


def _noop_step(_ev: ToolBatchCompleted) -> None:
    return None


def _noop_snapshot() -> dict[str, Any]:
    return {}


def _noop_stop() -> bool:
    return False


def _noop_graph_state(_state: dict[str, Any]) -> None:
    return None


@dataclass
class NodeBundle:
    """Injectable I/O for the graph nodes.

    Separating these from the graph structure keeps each node pure and
    swappable in tests. ``AgentLoop`` provides the production bundle;
    tests pass a fake bundle with stubbed callables.
    """

    check_health: Callable[[], Awaitable[bool]]
    emit_step: Callable[[ToolBatchCompleted], None] = field(default=_noop_step)
    emit_log: Callable[[str, str, Optional[dict[str, Any]]], None] = field(default=_noop_log)
    emit_graph_state: Callable[[dict[str, Any]], None] = field(default=_noop_graph_state)
    stop_requested: Callable[[], bool] = field(default=_noop_stop)


def _copy_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _copy_optional_str_list(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    copied = _copy_str_list(value)
    return copied or []


def _default_provider_capabilities(state: AgentGraphState) -> ProviderCapabilitiesState:
    return {
        "provider": str(state.get("provider") or "").strip().lower(),
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
        "model_id": str(state.get("model", "")),
        "beta_headers": [],
        "search_allowed_domains": _copy_str_list(state.get("search_allowed_domains")),
        "search_blocked_domains": _copy_str_list(state.get("search_blocked_domains")),
        "allowed_callers": _copy_optional_str_list(state.get("allowed_callers")),
    }


def _normalize_subgoals(value: Any) -> list[SubgoalState]:
    if not isinstance(value, list):
        return []
    normalized: list[SubgoalState] = []
    for item in value:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("objective") or "").strip()
            if not title:
                continue
            status = str(item.get("status") or "pending").lower()
            if status not in _VALID_SUBGOAL_STATUSES:
                status = "pending"
            normalized.append({"title": title, "status": status})
            continue
        text = str(item).strip()
        if text:
            normalized.append({"title": text, "status": "pending"})
    return normalized


def _normalize_evidence(value: Any) -> list[dict[str, Any]]:
    return normalize_evidence_entries(value)


def _normalize_completion_criteria(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _normalize_latest_executor_output(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return copy.deepcopy(value)


def _normalize_recovery_context(value: Any) -> Optional[RecoveryContextState]:
    if not isinstance(value, dict):
        return None
    return {
        "classification": str(value.get("classification", "")),
        "retry_reason": str(value.get("retry_reason", "")),
        "error": value.get("error"),
        "error_classification": value.get("error_classification"),
        "retry_count": int(value.get("retry_count", 0) or 0),
        "replan_count": int(value.get("replan_count", 0) or 0),
        "had_pending_action_batch": bool(value.get("had_pending_action_batch", False)),
        "verification_status": str(value.get("verification_status") or "") or None,
        "latest_turn": int(value.get("latest_turn", 0) or 0),
        "latest_model_text": str(value.get("latest_model_text") or ""),
        "failure_context": copy.deepcopy(value.get("failure_context") or {}),
    }


def _normalize_provider_capabilities(state: AgentGraphState) -> ProviderCapabilitiesState:
    base = _default_provider_capabilities(state)
    existing = state.get("provider_capabilities")
    if not isinstance(existing, dict):
        return base
    normalized = copy.deepcopy(base)
    if "provider" in existing:
        normalized["provider"] = str(existing.get("provider") or normalized["provider"])
    if "verified" in existing:
        normalized["verified"] = bool(existing.get("verified"))
    if "computer_use" in existing:
        normalized["computer_use"] = bool(existing.get("computer_use"))
    if "web_search" in existing:
        normalized["web_search"] = bool(existing.get("web_search"))
    if "web_search_version" in existing:
        normalized["web_search_version"] = str(existing.get("web_search_version") or "") or None
    if "tool_combination_supported" in existing:
        normalized["tool_combination_supported"] = bool(existing.get("tool_combination_supported"))
    if "search_filtering_supported" in existing:
        normalized["search_filtering_supported"] = bool(existing.get("search_filtering_supported"))
    if "allowed_callers_supported" in existing:
        normalized["allowed_callers_supported"] = bool(existing.get("allowed_callers_supported"))
    if "reasoning_effort_default" in existing:
        normalized["reasoning_effort_default"] = str(existing.get("reasoning_effort_default") or "") or None
    if "tool_version" in existing:
        normalized["tool_version"] = str(existing.get("tool_version") or "") or None
    if "beta_flag" in existing:
        normalized["beta_flag"] = str(existing.get("beta_flag") or "") or None
    if "model_id" in existing:
        normalized["model_id"] = str(existing.get("model_id") or normalized["model_id"])
    if "beta_headers" in existing:
        normalized["beta_headers"] = _copy_str_list(existing.get("beta_headers"))
    if "search_allowed_domains" in existing:
        normalized["search_allowed_domains"] = _copy_str_list(existing.get("search_allowed_domains"))
    if "search_blocked_domains" in existing:
        normalized["search_blocked_domains"] = _copy_str_list(existing.get("search_blocked_domains"))
    if "allowed_callers" in existing:
        normalized["allowed_callers"] = _copy_optional_str_list(existing.get("allowed_callers"))
    return normalized


def _normalize_graph_state(state: AgentGraphState) -> AgentGraphState:
    normalized: AgentGraphState = copy.deepcopy(state)
    normalized["goal"] = str(normalized.get("goal") or normalized.get("task") or "")
    normalized["planner_model"] = str(
        normalized.get("planner_model")
        or os.getenv("CUA_PLANNER_MODEL")
        or normalized.get("model")
        or ""
    )
    normalized["subgoals"] = _normalize_subgoals(normalized.get("subgoals"))
    normalized["active_plan"] = (
        copy.deepcopy(normalized.get("active_plan"))
        if isinstance(normalized.get("active_plan"), dict)
        else None
    )
    normalized["evidence"] = _normalize_evidence(normalized.get("evidence"))
    normalized["completion_criteria"] = _normalize_completion_criteria(normalized.get("completion_criteria"))
    normalized["memory_context"] = (
        copy.deepcopy(normalized.get("memory_context"))
        if isinstance(normalized.get("memory_context"), dict)
        else {}
    )
    normalized["latest_executor_output"] = _normalize_latest_executor_output(normalized.get("latest_executor_output"))
    normalized["verification_status"] = str(normalized.get("verification_status") or "")
    normalized["unmet_completion_criteria"] = _normalize_completion_criteria(normalized.get("unmet_completion_criteria"))
    normalized["verification_rationale"] = (
        str(normalized.get("verification_rationale") or "") or None
    )
    normalized["provider_capabilities"] = _normalize_provider_capabilities(normalized)
    risk_level = str(normalized.get("risk_level") or "low").lower()
    normalized["risk_level"] = risk_level if risk_level in _VALID_RISK_LEVELS else "low"
    normalized["recovery_context"] = _normalize_recovery_context(normalized.get("recovery_context"))
    normalized["replan"] = bool(normalized.get("replan", False))
    return normalized


def _graph_state_delta(state: AgentGraphState) -> dict[str, Any]:
    return {
        "goal": state["goal"],
        "planner_model": state["planner_model"],
        "subgoals": copy.deepcopy(state["subgoals"]),
        "active_plan": copy.deepcopy(state["active_plan"]),
        "evidence": copy.deepcopy(state["evidence"]),
        "completion_criteria": list(state["completion_criteria"]),
        "memory_context": copy.deepcopy(state.get("memory_context") or {}),
        "latest_executor_output": copy.deepcopy(state["latest_executor_output"]),
        "verification_status": state["verification_status"],
        "unmet_completion_criteria": list(state["unmet_completion_criteria"]),
        "verification_rationale": state["verification_rationale"],
        "provider_capabilities": copy.deepcopy(state["provider_capabilities"]),
        "risk_level": state["risk_level"],
        "recovery_context": copy.deepcopy(state["recovery_context"]),
        "replan": state["replan"],
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _active_subgoal_title(state: AgentGraphState) -> str:
    active_plan = state.get("active_plan")
    if isinstance(active_plan, dict):
        title = str(active_plan.get("active_subgoal") or "").strip()
        if title:
            return title
    for item in state.get("subgoals") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").lower() == "active":
            title = str(item.get("title") or "").strip()
            if title:
                return title
    return ""


def _approval_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    explanation = str(value.get("explanation") or "").strip()
    origin = str(value.get("origin") or "safety").strip() or "safety"
    if not explanation and not origin:
        return None
    return {
        "origin": origin,
        "explanation": explanation,
    }


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _recovery_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    failure_context = value.get("failure_context")
    failure = failure_context if isinstance(failure_context, dict) else {}
    verification_rationale = _first_text(failure.get("verification_rationale"))
    evidence_brief = _first_text(failure.get("evidence_brief"))
    memory_context_brief = _first_text(failure.get("memory_context_brief"))
    reason = _first_text(
        verification_rationale,
        value.get("error"),
        value.get("error_classification"),
        value.get("verification_status"),
        value.get("retry_reason"),
        value.get("latest_model_text"),
    )
    return {
        "classification": str(value.get("classification") or ""),
        "retry_reason": str(value.get("retry_reason") or ""),
        "error": value.get("error"),
        "error_classification": value.get("error_classification"),
        "verification_status": value.get("verification_status"),
        "retry_count": _safe_int(value.get("retry_count")),
        "replan_count": _safe_int(value.get("replan_count")),
        "latest_turn": _safe_int(value.get("latest_turn")),
        "latest_model_text": str(value.get("latest_model_text") or ""),
        "verification_rationale": verification_rationale,
        "evidence_brief": evidence_brief,
        "memory_context_brief": memory_context_brief,
        "replan_reason": reason,
    }


def _graph_run_snapshot(
    node_name: str,
    state: AgentGraphState,
    *,
    phase: str,
) -> dict[str, Any]:
    recovery_context = state.get("recovery_context")
    recovery = recovery_context if isinstance(recovery_context, dict) else {}
    retry_count = max(
        _safe_int(state.get("retry_count")),
        _safe_int(recovery.get("retry_count")),
    )
    replan_count = _safe_int(recovery.get("replan_count"))
    verifier_verdict = str(state.get("verification_status") or "").strip()
    recovery_insight = _recovery_summary(recovery)
    return {
        "session_id": str(state.get("session_id") or ""),
        "node": node_name,
        "current_node": node_name,
        "phase": phase,
        "route": str(state.get("route") or ""),
        "status": str(state.get("status") or ""),
        "turn_count": _safe_int(state.get("turn_count")),
        "retry_count": retry_count,
        "replan_count": replan_count,
        "verifier_verdict": verifier_verdict or None,
        "verification_rationale": _first_text(state.get("verification_rationale")),
        "completion_criteria": list(state.get("completion_criteria") or []),
        "unmet_completion_criteria": list(state.get("unmet_completion_criteria") or []),
        "recovery": recovery_insight,
        "replan_reason": (
            recovery_insight.get("replan_reason")
            if isinstance(recovery_insight, dict)
            else None
        ),
        "active_subgoal": _active_subgoal_title(state) or None,
        "pending_approval": _approval_summary(state.get("pending_approval")),
        "updated_at": time.time(),
    }


def _emit_graph_state(
    bundle: NodeBundle | None,
    node_name: str,
    state: AgentGraphState,
    *,
    phase: str,
) -> None:
    if bundle is None or not node_name:
        return
    try:
        bundle.emit_graph_state(_graph_run_snapshot(node_name, state, phase=phase))
    except Exception:
        logger.debug("Graph-state callback failed for node %s", node_name, exc_info=True)


def _memory_context_has_entries(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(isinstance(entries, list) and entries for entries in value.values())


def _node_failed_for_metrics(node_name: str, state: AgentGraphState) -> bool:
    status = str(state.get("status") or "")
    route = str(state.get("route") or "")
    error = str(state.get("error") or "").strip()
    classification = str(state.get("last_error_classification") or "").strip().lower()

    if node_name in {POLICY_NODE, LEGACY_POLICY_GATE_NODE, RECOVERY_NODE}:
        return False
    if node_name == VERIFIER_NODE and classification == "regression":
        return False
    if status == "error" and error:
        return True
    return route == "retry" and bool(error)


def _record_rollout_metrics(
    *,
    graph_mode: str,
    node_name: str,
    state: AgentGraphState,
) -> None:
    if graph_mode != graph_rollout.SUPERVISOR_GRAPH_MODE:
        return
    if node_name == PLANNER_NODE:
        graph_rollout.record_planner_memory_read(
            hit=_memory_context_has_entries(state.get("memory_context")),
        )
        return
    if node_name == POLICY_NODE:
        graph_rollout.record_policy_evaluation(
            escalated=str(state.get("route") or "") == "approval",
        )
        return
    if node_name == VERIFIER_NODE:
        verdict = str(state.get("verification_status") or "").strip().lower()
        if verdict:
            graph_rollout.record_verifier_verdict(verdict)
        return
    if node_name == RECOVERY_NODE:
        recovery_context = state.get("recovery_context")
        classification = recovery_context.get("classification") if isinstance(recovery_context, dict) else None
        graph_rollout.record_recovery_classification(str(classification or "unknown"))


def _with_graph_state_defaults(
    node: Callable[..., Awaitable[dict[str, Any]]],
    *,
    bundle: NodeBundle | None = None,
    node_name: str = "",
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def wrapped(state: AgentGraphState, *args: Any, **kwargs: Any) -> dict[str, Any]:
        normalized = _normalize_graph_state(state)
        _emit_graph_state(bundle, node_name, normalized, phase="running")
        session_id = str(normalized.get("session_id") or "")
        started_at = time.perf_counter()
        try:
            delta = await node(normalized, *args, **kwargs)
        except Exception:
            if node_name:
                graph_rollout.record_node_result(
                    session_id,
                    graph_mode=graph_mode,
                    node_name=node_name,
                    duration_ms=(time.perf_counter() - started_at) * 1000.0,
                    failed=True,
                )
            raise
        merged = {**_graph_state_delta(normalized), **delta}
        final_state = {**normalized, **merged}
        _emit_graph_state(bundle, node_name, final_state, phase="completed")
        if node_name:
            graph_rollout.record_node_result(
                session_id,
                graph_mode=graph_mode,
                node_name=node_name,
                duration_ms=(time.perf_counter() - started_at) * 1000.0,
                failed=_node_failed_for_metrics(node_name, final_state),
            )
            _record_rollout_metrics(
                graph_mode=graph_mode,
                node_name=node_name,
                state=final_state,
            )
        return merged

    return wrapped


# ---------------------------------------------------------------------------
# Runtime (checkpointer lifecycle) — unchanged public API.
# ---------------------------------------------------------------------------

class GraphRuntime:
    """Owns the shared AsyncSqliteSaver checkpointer for the app's lifetime."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._stack: AsyncExitStack | None = None
        self.checkpointer: AsyncSqliteSaver | None = None
        self.store: BaseStore | None = None

    async def __aenter__(self) -> "GraphRuntime":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        self.store = build_graph_store(self._db_path)
        self.checkpointer = await self._stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(self._db_path)
        )
        await self.checkpointer.setup()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack is not None:
            try:
                await self._stack.__aexit__(*exc)
            finally:
                self._stack = None
                self.checkpointer = None
                self.store = None


_runtime: GraphRuntime | None = None


async def init_runtime(db_path: str | Path) -> GraphRuntime:
    """Initialise the process-wide graph runtime. Idempotent."""
    global _runtime
    if _runtime is not None:
        return _runtime
    rt = GraphRuntime(db_path)
    await rt.__aenter__()
    _runtime = rt
    return _runtime


async def shutdown_runtime() -> None:
    """Close the process-wide graph runtime if one was initialised."""
    global _runtime
    if _runtime is None:
        return
    try:
        await _runtime.__aexit__(None, None, None)
    finally:
        _runtime = None


def get_runtime() -> GraphRuntime:
    """Return the initialised runtime or raise."""
    if _runtime is None or _runtime.checkpointer is None or _runtime.store is None:
        raise RuntimeError("Graph runtime not initialised — call init_runtime() first")
    return _runtime


async def load_session_snapshot(session_id: str) -> dict[str, Any] | None:
    """Return the latest persisted session snapshot for *session_id*."""
    if _runtime is None or _runtime.checkpointer is None:
        return None
    tup = await _runtime.checkpointer.aget_tuple(
        {"configurable": {"thread_id": session_id}}
    )
    if tup is None:
        return None
    values = tup.checkpoint.get("channel_values") or {}
    snapshot = values.get("session_snapshot") or values.get("session_data")
    return snapshot if isinstance(snapshot, dict) else None


# ---------------------------------------------------------------------------
# Node factories — closures over the NodeBundle keep nodes pure.
# ---------------------------------------------------------------------------

def _make_preflight(
    bundle: NodeBundle,
    *,
    node_name: str = INTAKE_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    async def preflight(state: AgentGraphState) -> dict[str, Any]:
        """Health-check the desktop service and initialize persisted session state."""
        healthy = await bundle.check_health()
        if not healthy:
            bundle.emit_log(
                "warning",
                "Agent service not responding, will retry during execution",
                None,
            )
        session_data = copy.deepcopy(state.get("session_data") or {})
        if session_data:
            session_data["status"] = "running"
        return {
            "healthy": healthy,
            "status": "running",
            "turn_count": 0,
            "retry_count": 0,
            "route": "model_turn",
            "session_data": session_data,
        }

    return _with_graph_state_defaults(
        preflight,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_capability_probe(
    bundle: NodeBundle,
    *,
    node_name: str = CAPABILITY_PROBE_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    capability_probe_graph = build_capability_probe_subgraph(emit_log=bundle.emit_log)

    async def capability_probe(state: AgentGraphState) -> dict[str, Any]:
        return await capability_probe_graph.ainvoke(state)

    return _with_graph_state_defaults(
        capability_probe,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_planner(
    bundle: NodeBundle,
    *,
    store: BaseStore | None,
    node_name: str = PLANNER_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    planner_graph = build_planner_subgraph(emit_log=bundle.emit_log, store=store)

    async def planner(state: AgentGraphState) -> dict[str, Any]:
        return await planner_graph.ainvoke(state)

    return _with_graph_state_defaults(
        planner,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_grounding(
    bundle: NodeBundle,
    *,
    evidence_limit: int,
    node_name: str = GROUNDING_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    grounding_graph = build_grounding_subgraph(
        emit_log=bundle.emit_log,
        evidence_limit=evidence_limit,
    )

    async def grounding(state: AgentGraphState) -> dict[str, Any]:
        return await grounding_graph.ainvoke(state)

    return _with_graph_state_defaults(
        grounding,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_model_turn(
    bundle: NodeBundle,
    *,
    node_name: str = EXECUTOR_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    async def model_turn(state: AgentGraphState) -> dict[str, Any]:
        """Advance exactly one provider turn from persisted graph state."""
        if bundle.stop_requested():
            session_data = copy.deepcopy(state.get("session_data") or {})
            session_data["status"] = "stopped"
            session_data["final_text"] = "Stopped by user."
            return {
                "route": "completed",
                "status": "completed",
                "final_text": "Stopped by user.",
                "session_data": session_data,
            }
        if state.get("pending_action_batch") is not None:
            return {
                "route": "policy",
                "status": state.get("status", "running"),
            }
        execution_state = copy.deepcopy(state)
        execution_state["system_instruction"] = build_executor_system_prompt(
            provider=str(state.get("provider") or "google"),
            model=str(state.get("model") or "") or None,
            active_plan=state.get("active_plan"),
            subgoals=state.get("subgoals"),
            completion_criteria=state.get("completion_criteria"),
            verification_status=str(state.get("verification_status") or "") or None,
            unmet_completion_criteria=state.get("unmet_completion_criteria"),
            recovery_context=copy.deepcopy(state.get("recovery_context") or None),
            evidence=copy.deepcopy(state.get("evidence") or []),
            memory_context=copy.deepcopy(state.get("memory_context") or {}),
        )
        try:
            delta = await advance_provider_turn(execution_state, on_log=bundle.emit_log)
        except Exception as exc:
            return {
                "route": "retry",
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "retry_reason": "model_turn",
                "last_error_classification": type(exc).__name__,
            }
        delta.setdefault("system_instruction", execution_state["system_instruction"])
        delta.setdefault("approval_decision", None)
        if delta.get("pending_approval") is None and delta.get("status") != "awaiting_approval":
            delta["pending_approval"] = None
        return delta

    return _with_graph_state_defaults(
        model_turn,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_policy_gate(
    bundle: NodeBundle,
    *,
    node_name: str = POLICY_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    async def policy_gate(state: AgentGraphState) -> dict[str, Any]:
        payload = copy.deepcopy(state.get("pending_action_batch") or {})
        if not payload:
            return {
                "route": "model_turn",
                "status": "running",
            }

        classification = classify_pending_action_batch(
            provider=str(state.get("provider") or "").lower(),
            batch=payload,
            risk_level=str(state.get("risk_level") or "low"),
            provider_capabilities=copy.deepcopy(state.get("provider_capabilities") or {}),
        )
        explanation = policy_explanation(classification)
        overall = str(classification.get("overall_level") or "low")
        if overall == "high":
            if state.get("approval_decision") is False:
                bundle.emit_log("warning", f"Policy gate denied batch: {explanation}", None)
                return {
                    "route": "retry",
                    "status": "error",
                    "error": "Policy approval denied.",
                    "retry_reason": "policy",
                    "last_error_classification": "PolicyDenied",
                    "pending_action_batch": copy.deepcopy(payload),
                    "approval_decision": None,
                }
            if state.get("approval_decision") is True:
                bundle.emit_log("info", f"Policy gate approved high-risk batch: {explanation}", None)
                return {
                    "route": "tool_batch",
                    "status": "running",
                    "approval_decision": None,
                }
            return {
                "route": "approval",
                "status": "awaiting_approval",
                "pending_approval": {
                    "origin": "policy",
                    "explanation": explanation,
                },
                "session_data": _session_running(copy.deepcopy(state.get("session_data") or {}), status="paused"),
            }
        if overall == "medium":
            bundle.emit_log("info", f"Policy gate logging medium-risk batch: {explanation}", None)
        return {
            "route": "tool_batch",
            "status": "running",
            "approval_decision": None,
        }

    return _with_graph_state_defaults(
        policy_gate,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _failed_tool_results(latest_output: dict[str, Any]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for item in latest_output.get("results") or []:
        if not isinstance(item, dict):
            continue
        if item.get("success", True) is False or item.get("error"):
            failed.append(copy.deepcopy(item))
    return failed


def _make_tool_batch(
    bundle: NodeBundle,
    *,
    node_name: str = DESKTOP_DISPATCHER_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    async def tool_batch(state: AgentGraphState) -> dict[str, Any]:
        """Dispatch the pending action batch, emit the step, and forward to verification."""
        if bundle.stop_requested():
            session_data = copy.deepcopy(state.get("session_data") or {})
            session_data["status"] = "stopped"
            session_data["final_text"] = "Stopped by user."
            return {
                "route": "completed",
                "status": "completed",
                "final_text": "Stopped by user.",
                "session_data": session_data,
            }
        payload = copy.deepcopy(state.get("pending_action_batch") or {})
        if not payload:
            return {
                "route": "model_turn",
                "status": "running",
            }
        latest_output = payload
        dispatch_delta: dict[str, Any] = {}
        if isinstance(payload.get("native_actions"), list):
            try:
                dispatch_delta = await dispatch_pending_action_batch(state, on_log=bundle.emit_log)
            except Exception as exc:
                return {
                    "route": "retry",
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "retry_reason": "tool_batch",
                    "last_error_classification": type(exc).__name__,
                    "pending_action_batch": copy.deepcopy(payload),
                }
            latest_output = copy.deepcopy(dispatch_delta.get("latest_executor_output") or payload)
        event = deserialize_tool_batch(latest_output)
        try:
            bundle.emit_step(event)
        except Exception as exc:
            bundle.emit_log(
                "warning",
                f"emit_step raised: {type(exc).__name__}: {exc}",
                None,
            )
        session_data = append_step(copy.deepcopy(state.get("session_data") or {}), latest_output)
        session_data["status"] = "running"
        failed_results = _failed_tool_results(latest_output)
        if failed_results:
            first_failed = failed_results[0]
            error = str(first_failed.get("error") or f"Action {first_failed.get('name') or 'unknown'} failed.")
            return {
                "route": "retry",
                "status": "error",
                "error": error,
                "retry_reason": "tool_batch",
                "last_error_classification": str(first_failed.get("name") or "ActionFailed"),
                "pending_action_batch": copy.deepcopy(payload),
                "pending_terminal_after_batch": None,
                "latest_executor_output": latest_output,
                "provider_state": copy.deepcopy(dispatch_delta.get("provider_state") or state.get("provider_state") or {}),
                "last_model_text": dispatch_delta.get("last_model_text", event.model_text),
                "last_screenshot_ref": dispatch_delta.get("last_screenshot_ref", latest_output.get("screenshot_ref")),
                "session_data": session_data,
            }
        return {
            "route": "verifier",
            "status": "running",
            "turn_count": event.turn,
            "pending_action_batch": None,
            "pending_terminal_after_batch": None,
            "latest_executor_output": latest_output,
            "provider_state": copy.deepcopy(dispatch_delta.get("provider_state") or state.get("provider_state") or {}),
            "last_model_text": dispatch_delta.get("last_model_text", event.model_text),
            "last_screenshot_ref": dispatch_delta.get("last_screenshot_ref", latest_output.get("screenshot_ref")),
            "session_data": session_data,
        }

    return _with_graph_state_defaults(
        tool_batch,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_approval_interrupt(
    bundle: NodeBundle,
    *,
    node_name: str = ESCALATE_INTERRUPT_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    async def approval_interrupt(state: AgentGraphState) -> dict[str, Any]:
        """Pause the graph on a persisted safety approval payload."""
        sid = state["session_id"]
        pending = state.get("pending_approval") or {}
        origin = str(pending.get("origin") or "safety")
        explanation = str(pending.get("explanation", ""))

        decision = bool(interrupt({
            "type": "safety_approval",
            "session_id": sid,
            "origin": origin,
            "explanation": explanation,
            "recovery_context": copy.deepcopy(pending.get("recovery_context")) if isinstance(pending.get("recovery_context"), dict) else None,
        }))

        # OBS: record the approval resolution in the session trace.
        # Kept best-effort so a tracing-module import error cannot break
        # the graph's approval handshake.
        try:
            from backend.infra import observability as tracing
            tracing.record(
                sid, tracing.STAGE_APPROVAL, tracing.EVT_APPROVAL_RESOLVED,
                {"decision": decision, "explanation": explanation},
            )
        except Exception:
            pass

        bundle.emit_log("info", f"Safety approval resolved: {decision}", None)
        session_data = copy.deepcopy(state.get("session_data") or {})
        session_data["status"] = "running"
        return {
            "route": "policy" if origin == "policy" else "recovery" if origin == "recovery" else "model_turn",
            "status": "running",
            "approval_decision": decision,
            "pending_approval": None,
            "session_data": session_data,
        }

    return _with_graph_state_defaults(
        approval_interrupt,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_recovery(
    bundle: NodeBundle,
    *,
    max_transient_retries: int,
    max_replans: int,
    node_name: str = RECOVERY_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    recovery_graph = build_recovery_subgraph(
        max_transient_retries=max_transient_retries,
        max_replans=max_replans,
        emit_log=bundle.emit_log,
    )

    async def recovery(state: AgentGraphState) -> dict[str, Any]:
        return await recovery_graph.ainvoke(state)

    return _with_graph_state_defaults(
        recovery,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_verifier(
    bundle: NodeBundle,
    *,
    node_name: str = VERIFIER_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    verifier_graph = build_verifier_subgraph(emit_log=bundle.emit_log)

    async def verifier(state: AgentGraphState) -> dict[str, Any]:
        return await verifier_graph.ainvoke(state)

    return _with_graph_state_defaults(
        verifier,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


def _make_finalize(
    bundle: NodeBundle,
    *,
    node_name: str = FINALIZE_NODE,
    graph_mode: str = graph_rollout.SUPERVISOR_GRAPH_MODE,
):
    async def finalize(state: AgentGraphState, runtime: Runtime[Any] | None = None) -> dict[str, Any]:
        """Persist the latest session snapshot and clean provider-side resources."""
        try:
            await cleanup_provider_resources(state, on_log=bundle.emit_log)
        except Exception as exc:
            bundle.emit_log(
                "warning", f"cleanup_provider_resources raised: {exc}", None,
            )
        try:
            memory_write = await write_long_term_memory(getattr(runtime, "store", None), state)
            if int(memory_write.get("writes") or 0) > 0:
                bundle.emit_log(
                    "info",
                    f"Wrote {memory_write['writes']} long-term memory item(s).",
                    None,
                )
        except Exception as exc:
            bundle.emit_log("warning", f"long-term memory write failed: {exc}", None)
        snapshot = snapshot_session_data(copy.deepcopy(state.get("session_data") or {}))
        return {"session_snapshot": snapshot}

    return _with_graph_state_defaults(
        finalize,
        bundle=bundle,
        node_name=node_name,
        graph_mode=graph_mode,
    )


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------

def _needs_planner(state: AgentGraphState) -> bool:
    provider = str(state.get("provider") or "").strip()
    planner_model = str(state.get("planner_model") or state.get("model") or "").strip()
    if not provider or not planner_model:
        return False
    return bool(state.get("replan")) or state.get("active_plan") is None

def _after_preflight(state: AgentGraphState) -> str:
    route = state.get("route")
    if route == "completed":
        return FINALIZE_NODE
    if route == "finalize":
        return FINALIZE_NODE
    return CAPABILITY_PROBE_NODE


def _after_capability_probe(state: AgentGraphState) -> str:
    route = state.get("route")
    if route == "completed" or route == "finalize":
        return FINALIZE_NODE
    if _needs_planner(state):
        return PLANNER_NODE
    if needs_grounding(state):
        return GROUNDING_NODE
    return EXECUTOR_NODE


def _after_planner(state: AgentGraphState) -> str:
    route = state.get("route")
    if route == "retry":
        return RECOVERY_NODE
    if route == "completed":
        return FINALIZE_NODE
    if needs_grounding(state):
        return GROUNDING_NODE
    return EXECUTOR_NODE


def _after_grounding(state: AgentGraphState) -> str:
    route = state.get("route")
    if route == "retry":
        return RECOVERY_NODE
    if route == "completed":
        return FINALIZE_NODE
    return EXECUTOR_NODE


def _after_model_turn(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "model_turn":
        return EXECUTOR_NODE
    if r == "tool_batch":
        return POLICY_NODE
    if r == "approval":
        return ESCALATE_INTERRUPT_NODE
    if r == "retry":
        return RECOVERY_NODE
    if r == "completed":
        return FINALIZE_NODE
    return FINALIZE_NODE


def _after_policy(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "tool_batch":
        return DESKTOP_DISPATCHER_NODE
    if r == "approval":
        return ESCALATE_INTERRUPT_NODE
    if r == "retry":
        return RECOVERY_NODE
    if r == "completed":
        return FINALIZE_NODE
    return FINALIZE_NODE


def _after_tool_batch(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "verifier":
        return VERIFIER_NODE
    if r == "approval":
        return ESCALATE_INTERRUPT_NODE
    if r == "retry":
        return RECOVERY_NODE
    if r == "completed":
        return FINALIZE_NODE
    return FINALIZE_NODE


def _after_verifier(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "model_turn":
        return EXECUTOR_NODE
    if r == "retry":
        return RECOVERY_NODE
    if r == "completed" or r == "finalize":
        return FINALIZE_NODE
    return FINALIZE_NODE


def _after_approval(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "policy":
        return POLICY_NODE
    if r == "recovery" or r == "retry":
        return RECOVERY_NODE
    if r == "planner":
        return PLANNER_NODE
    if r == "completed":
        return FINALIZE_NODE
    return EXECUTOR_NODE


def _after_recovery(state: AgentGraphState) -> str:
    r = state.get("route")
    if r == "completed":
        return FINALIZE_NODE
    if r == "planner":
        return PLANNER_NODE
    if r == "grounding":
        return GROUNDING_NODE
    if r == "approval":
        return ESCALATE_INTERRUPT_NODE
    if r == "policy":
        return POLICY_NODE
    if r == "tool_batch":
        return DESKTOP_DISPATCHER_NODE
    return EXECUTOR_NODE


def _after_legacy_preflight(state: AgentGraphState) -> str:
    route = state.get("route")
    if route == "completed" or route == "finalize":
        return FINALIZE_NODE
    return LEGACY_MODEL_TURN_NODE


def _after_legacy_model_turn(state: AgentGraphState) -> str:
    route = str(state.get("route") or "")
    if route in {"policy", "tool_batch"}:
        return LEGACY_POLICY_GATE_NODE
    if route == "approval":
        return LEGACY_APPROVAL_INTERRUPT_NODE
    if route == "completed" or route == "finalize" or route == "retry":
        return FINALIZE_NODE
    return LEGACY_MODEL_TURN_NODE


def _after_legacy_policy(state: AgentGraphState) -> str:
    route = str(state.get("route") or "")
    if route == "tool_batch":
        return LEGACY_TOOL_BATCH_NODE
    if route == "approval":
        return LEGACY_APPROVAL_INTERRUPT_NODE
    if route == "model_turn":
        return LEGACY_MODEL_TURN_NODE
    if route == "completed" or route == "finalize" or route == "retry":
        return FINALIZE_NODE
    return FINALIZE_NODE


def _after_legacy_tool_batch(state: AgentGraphState) -> str:
    route = str(state.get("route") or "")
    if route == "verifier" or route == "model_turn":
        return LEGACY_MODEL_TURN_NODE
    if route == "approval":
        return LEGACY_APPROVAL_INTERRUPT_NODE
    if route == "completed" or route == "finalize" or route == "retry":
        return FINALIZE_NODE
    return FINALIZE_NODE


def _after_legacy_approval(state: AgentGraphState) -> str:
    route = str(state.get("route") or "")
    if route == "policy":
        return LEGACY_POLICY_GATE_NODE
    if route == "completed":
        return FINALIZE_NODE
    return LEGACY_MODEL_TURN_NODE


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_agent_graph(
    bundle: NodeBundle,
    *,
    max_transient_retries: int = 3,
    max_replans: int = 2,
    evidence_limit: int = 50,
    store: BaseStore | None = None,
):
    """Compile the supervisor graph.

    Shape: intake -> capability_probe -> planner -> grounding(optional)
    -> executor -> policy -> desktop_dispatcher -> verifier ->
    [executor | recovery | finalize], with recovery able to route back to
    executor, planner, policy, desktop_dispatcher, grounding, an interrupt,
    or finalize without replacing the provider-native executors.
    """
    runtime = get_runtime()
    compiled_store = store or runtime.store
    sg: StateGraph = StateGraph(AgentGraphState)

    sg.add_node(INTAKE_NODE, _make_preflight(bundle))
    sg.add_node(CAPABILITY_PROBE_NODE, _make_capability_probe(bundle))
    sg.add_node(PLANNER_NODE, _make_planner(bundle, store=compiled_store))
    sg.add_node(GROUNDING_NODE, _make_grounding(bundle, evidence_limit=evidence_limit))
    sg.add_node(EXECUTOR_NODE, _make_model_turn(bundle))
    sg.add_node(POLICY_NODE, _make_policy_gate(bundle))
    sg.add_node(DESKTOP_DISPATCHER_NODE, _make_tool_batch(bundle))
    sg.add_node(VERIFIER_NODE, _make_verifier(bundle))
    sg.add_node(ESCALATE_INTERRUPT_NODE, _make_approval_interrupt(bundle))
    sg.add_node(RECOVERY_NODE, _make_recovery(bundle, max_transient_retries=max_transient_retries, max_replans=max_replans))
    sg.add_node(FINALIZE_NODE, _make_finalize(bundle))

    sg.add_edge(START, INTAKE_NODE)
    sg.add_conditional_edges(INTAKE_NODE, _after_preflight, {
        CAPABILITY_PROBE_NODE: CAPABILITY_PROBE_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(CAPABILITY_PROBE_NODE, _after_capability_probe, {
        PLANNER_NODE: PLANNER_NODE,
        GROUNDING_NODE: GROUNDING_NODE,
        EXECUTOR_NODE: EXECUTOR_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(PLANNER_NODE, _after_planner, {
        GROUNDING_NODE: GROUNDING_NODE,
        EXECUTOR_NODE: EXECUTOR_NODE,
        RECOVERY_NODE: RECOVERY_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(GROUNDING_NODE, _after_grounding, {
        EXECUTOR_NODE: EXECUTOR_NODE,
        RECOVERY_NODE: RECOVERY_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(EXECUTOR_NODE, _after_model_turn, {
        EXECUTOR_NODE: EXECUTOR_NODE,
        POLICY_NODE: POLICY_NODE,
        ESCALATE_INTERRUPT_NODE: ESCALATE_INTERRUPT_NODE,
        RECOVERY_NODE: RECOVERY_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(POLICY_NODE, _after_policy, {
        DESKTOP_DISPATCHER_NODE: DESKTOP_DISPATCHER_NODE,
        ESCALATE_INTERRUPT_NODE: ESCALATE_INTERRUPT_NODE,
        RECOVERY_NODE: RECOVERY_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(DESKTOP_DISPATCHER_NODE, _after_tool_batch, {
        VERIFIER_NODE: VERIFIER_NODE,
        ESCALATE_INTERRUPT_NODE: ESCALATE_INTERRUPT_NODE,
        RECOVERY_NODE: RECOVERY_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(VERIFIER_NODE, _after_verifier, {
        EXECUTOR_NODE: EXECUTOR_NODE,
        RECOVERY_NODE: RECOVERY_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(ESCALATE_INTERRUPT_NODE, _after_approval, {
        POLICY_NODE: POLICY_NODE,
        PLANNER_NODE: PLANNER_NODE,
        EXECUTOR_NODE: EXECUTOR_NODE,
        RECOVERY_NODE: RECOVERY_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(RECOVERY_NODE, _after_recovery, {
        PLANNER_NODE: PLANNER_NODE,
        GROUNDING_NODE: GROUNDING_NODE,
        ESCALATE_INTERRUPT_NODE: ESCALATE_INTERRUPT_NODE,
        POLICY_NODE: POLICY_NODE,
        EXECUTOR_NODE: EXECUTOR_NODE,
        DESKTOP_DISPATCHER_NODE: DESKTOP_DISPATCHER_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_edge(FINALIZE_NODE, END)

    return sg.compile(checkpointer=runtime.checkpointer, store=compiled_store)


def build_legacy_graph(
    bundle: NodeBundle,
    *,
    store: BaseStore | None = None,
):
    """Compile the legacy six-node graph.

    Shape: preflight -> model_turn -> policy_gate -> tool_batch ->
    approval_interrupt -> finalize, with successful tool batches looping
    straight back to the executor path.
    """
    runtime = get_runtime()
    compiled_store = store or runtime.store
    sg: StateGraph = StateGraph(AgentGraphState)

    sg.add_node(
        LEGACY_PREFLIGHT_NODE,
        _make_preflight(
            bundle,
            node_name=LEGACY_PREFLIGHT_NODE,
            graph_mode=graph_rollout.LEGACY_GRAPH_MODE,
        ),
    )
    sg.add_node(
        LEGACY_MODEL_TURN_NODE,
        _make_model_turn(
            bundle,
            node_name=LEGACY_MODEL_TURN_NODE,
            graph_mode=graph_rollout.LEGACY_GRAPH_MODE,
        ),
    )
    sg.add_node(
        LEGACY_POLICY_GATE_NODE,
        _make_policy_gate(
            bundle,
            node_name=LEGACY_POLICY_GATE_NODE,
            graph_mode=graph_rollout.LEGACY_GRAPH_MODE,
        ),
    )
    sg.add_node(
        LEGACY_TOOL_BATCH_NODE,
        _make_tool_batch(
            bundle,
            node_name=LEGACY_TOOL_BATCH_NODE,
            graph_mode=graph_rollout.LEGACY_GRAPH_MODE,
        ),
    )
    sg.add_node(
        LEGACY_APPROVAL_INTERRUPT_NODE,
        _make_approval_interrupt(
            bundle,
            node_name=LEGACY_APPROVAL_INTERRUPT_NODE,
            graph_mode=graph_rollout.LEGACY_GRAPH_MODE,
        ),
    )
    sg.add_node(
        FINALIZE_NODE,
        _make_finalize(
            bundle,
            node_name=FINALIZE_NODE,
            graph_mode=graph_rollout.LEGACY_GRAPH_MODE,
        ),
    )

    sg.add_edge(START, LEGACY_PREFLIGHT_NODE)
    sg.add_conditional_edges(LEGACY_PREFLIGHT_NODE, _after_legacy_preflight, {
        LEGACY_MODEL_TURN_NODE: LEGACY_MODEL_TURN_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(LEGACY_MODEL_TURN_NODE, _after_legacy_model_turn, {
        LEGACY_MODEL_TURN_NODE: LEGACY_MODEL_TURN_NODE,
        LEGACY_POLICY_GATE_NODE: LEGACY_POLICY_GATE_NODE,
        LEGACY_APPROVAL_INTERRUPT_NODE: LEGACY_APPROVAL_INTERRUPT_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(LEGACY_POLICY_GATE_NODE, _after_legacy_policy, {
        LEGACY_MODEL_TURN_NODE: LEGACY_MODEL_TURN_NODE,
        LEGACY_TOOL_BATCH_NODE: LEGACY_TOOL_BATCH_NODE,
        LEGACY_APPROVAL_INTERRUPT_NODE: LEGACY_APPROVAL_INTERRUPT_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(LEGACY_TOOL_BATCH_NODE, _after_legacy_tool_batch, {
        LEGACY_MODEL_TURN_NODE: LEGACY_MODEL_TURN_NODE,
        LEGACY_APPROVAL_INTERRUPT_NODE: LEGACY_APPROVAL_INTERRUPT_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_conditional_edges(LEGACY_APPROVAL_INTERRUPT_NODE, _after_legacy_approval, {
        LEGACY_MODEL_TURN_NODE: LEGACY_MODEL_TURN_NODE,
        LEGACY_POLICY_GATE_NODE: LEGACY_POLICY_GATE_NODE,
        FINALIZE_NODE: FINALIZE_NODE,
    })
    sg.add_edge(FINALIZE_NODE, END)

    return sg.compile(checkpointer=runtime.checkpointer, store=compiled_store)
