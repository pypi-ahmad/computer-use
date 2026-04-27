from __future__ import annotations

import copy
import logging
import re
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agent.memory_layers import build_evidence_brief, build_memory_context_brief

logger = logging.getLogger(__name__)

_TRANSIENT_RE = re.compile(r"\b(timeout|timed out|temporary|temporarily|transient|busy|rate limit|429|503|network|connection|unavailable|try again)\b", re.IGNORECASE)
_FATAL_RE = re.compile(r"\b(unsupported|forbidden|permission denied|invalid api key|authentication failed|context window exceeded|fatal|unrecoverable)\b", re.IGNORECASE)
_STUCK_RE = re.compile(r"\b(not found|no such|missing|stuck|regression|wrong page|unexpected page|validation error|could not locate)\b", re.IGNORECASE)


class RecoveryContextState(TypedDict, total=False):
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


class RecoveryGraphState(TypedDict, total=False):
    retry_reason: str
    retry_count: int
    error: Optional[str]
    last_error_classification: Optional[str]
    pending_action_batch: Optional[dict[str, Any]]
    latest_executor_output: Optional[dict[str, Any]]
    verification_status: str
    verification_rationale: Optional[str]
    evidence: list[dict[str, Any]]
    memory_context: Optional[dict[str, list[dict[str, Any]]]]
    approval_decision: Optional[bool]
    pending_approval: Optional[dict[str, Any]]
    replan: bool
    final_text: str
    status: str
    route: str
    session_data: dict[str, Any]
    recovery_context: Optional[RecoveryContextState]


RecoveryLog = Callable[[str, str, Optional[dict[str, Any]]], None]


def _noop_log(_level: str, _msg: str, _data: dict[str, Any] | None = None) -> None:
    return None


def _failed_results(state: RecoveryGraphState) -> list[dict[str, Any]]:
    latest = state.get("latest_executor_output")
    if not isinstance(latest, dict):
        return []
    failed: list[dict[str, Any]] = []
    for item in latest.get("results") or []:
        if not isinstance(item, dict):
            continue
        if item.get("success", True) is False or item.get("error"):
            failed.append(copy.deepcopy(item))
    return failed


def _latest_turn(state: RecoveryGraphState) -> int:
    latest = state.get("latest_executor_output")
    if isinstance(latest, dict):
        try:
            return int(latest.get("turn") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _failure_text(state: RecoveryGraphState) -> str:
    parts: list[str] = []
    for value in (
        state.get("retry_reason"),
        state.get("error"),
        state.get("last_error_classification"),
        state.get("verification_status"),
        state.get("verification_rationale"),
    ):
        text = str(value or "").strip()
        if text:
            parts.append(text)
    latest = state.get("latest_executor_output")
    if isinstance(latest, dict):
        for key in ("model_text", "terminal_text"):
            text = str(latest.get(key) or "").strip()
            if text:
                parts.append(text)
    for item in _failed_results(state):
        text = str(item.get("error") or item.get("name") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def classify_recovery_failure(state: RecoveryGraphState) -> str:
    text = _failure_text(state)
    retry_reason = str(state.get("retry_reason") or "").lower()
    error_classification = str(state.get("last_error_classification") or "").lower()
    verification_status = str(state.get("verification_status") or "").lower()

    if state.get("approval_decision") is False and str((state.get("recovery_context") or {}).get("classification") or "") == "blocked":
        return "fatal"
    if retry_reason == "policy" or error_classification == "policydenied":
        return "blocked"
    if verification_status == "regression" or error_classification == "regression":
        return "stuck"
    if _FATAL_RE.search(text):
        return "fatal"
    if _TRANSIENT_RE.search(text):
        return "transient"
    if _failed_results(state):
        if _STUCK_RE.search(text):
            return "stuck"
        return "transient"
    if _STUCK_RE.search(text):
        return "stuck"
    return "stuck"


def _recovery_context(state: RecoveryGraphState, *, classification: str, retry_count: int, replan_count: int) -> RecoveryContextState:
    latest = state.get("latest_executor_output") if isinstance(state.get("latest_executor_output"), dict) else {}
    evidence_brief = build_evidence_brief(state.get("evidence"), limit=2)
    memory_brief = build_memory_context_brief(state.get("memory_context"), limit=2)
    return {
        "classification": classification,
        "retry_reason": str(state.get("retry_reason") or ""),
        "error": state.get("error"),
        "error_classification": state.get("last_error_classification"),
        "retry_count": int(retry_count),
        "replan_count": int(replan_count),
        "had_pending_action_batch": state.get("pending_action_batch") is not None,
        "verification_status": str(state.get("verification_status") or "") or None,
        "latest_turn": _latest_turn(state),
        "latest_model_text": str(latest.get("model_text") or "") if isinstance(latest, dict) else "",
        "failure_context": {
            "latest_executor_output": copy.deepcopy(state.get("latest_executor_output")),
            "pending_action_batch": copy.deepcopy(state.get("pending_action_batch")),
            "verification_rationale": state.get("verification_rationale"),
            "failed_results": _failed_results(state),
            "evidence_brief": evidence_brief,
            "memory_context_brief": memory_brief,
        },
    }


def _approval_payload(state: RecoveryGraphState, context: RecoveryContextState) -> dict[str, Any]:
    explanation = (
        f"Recovery blocked on {context.get('retry_reason') or 'task failure'}: "
        f"{context.get('error') or context.get('error_classification') or context.get('verification_status') or 'human review required.'}"
    )
    return {
        "origin": "recovery",
        "explanation": explanation,
        "recovery_context": copy.deepcopy(context),
    }


def _make_recovery_turn(*, max_transient_retries: int, max_replans: int, emit_log: RecoveryLog):
    async def recovery_turn(state: RecoveryGraphState) -> dict[str, Any]:
        previous = copy.deepcopy(state.get("recovery_context") or {})
        previous_classification = str(previous.get("classification") or "")
        previous_retry_count = int(previous.get("retry_count", state.get("retry_count", 0)) or 0)
        previous_replan_count = int(previous.get("replan_count", 0) or 0)

        if previous_classification == "blocked" and state.get("approval_decision") is False:
            context = _recovery_context(state, classification="fatal", retry_count=previous_retry_count, replan_count=previous_replan_count)
            emit_log("warning", "Recovery escalation denied; finalizing with failure.", None)
            session_data = copy.deepcopy(state.get("session_data") or {})
            session_data["status"] = "error"
            session_data["final_text"] = state.get("error") or "Recovery escalation denied."
            return {
                "route": "completed",
                "status": "error",
                "final_text": session_data["final_text"],
                "recovery_context": context,
                "approval_decision": None,
                "session_data": session_data,
            }

        if previous_classification == "blocked" and state.get("approval_decision") is True:
            replan_count = previous_replan_count + 1
            context = _recovery_context(state, classification="stuck", retry_count=previous_retry_count, replan_count=replan_count)
            emit_log("info", "Recovery escalation approved; forcing a replan.", None)
            return {
                "route": "planner",
                "status": "running",
                "replan": True,
                "approval_decision": None,
                "error": None,
                "retry_reason": "",
                "last_error_classification": None,
                "recovery_context": context,
            }

        classification = classify_recovery_failure(state)
        retry_count = previous_retry_count
        replan_count = previous_replan_count

        if classification == "transient":
            retry_count += 1
            if retry_count >= int(max_transient_retries):
                classification = "stuck"
            else:
                context = _recovery_context(state, classification="transient", retry_count=retry_count, replan_count=replan_count)
                emit_log("warning", f"Recovery classified failure as transient ({retry_count}/{max_transient_retries}); retrying.", None)
                return {
                    "route": "tool_batch" if state.get("pending_action_batch") is not None else "model_turn",
                    "status": "running",
                    "retry_count": retry_count,
                    "error": None,
                    "replan": False,
                    "approval_decision": None,
                    "recovery_context": context,
                }

        if classification == "stuck":
            replan_count += 1
            context = _recovery_context(state, classification="stuck", retry_count=retry_count, replan_count=replan_count)
            if replan_count >= int(max_replans):
                emit_log("warning", f"Recovery exceeded replan budget ({max_replans}); escalating.", None)
                return {
                    "route": "approval",
                    "status": "awaiting_approval",
                    "pending_approval": _approval_payload(state, context),
                    "approval_decision": None,
                    "recovery_context": {**context, "classification": "blocked"},
                }
            emit_log("warning", f"Recovery classified failure as stuck; replanning ({replan_count}/{max_replans}).", None)
            return {
                "route": "planner",
                "status": "running",
                "replan": True,
                "retry_count": retry_count,
                "error": None,
                "approval_decision": None,
                "recovery_context": context,
            }

        if classification == "blocked":
            context = _recovery_context(state, classification="blocked", retry_count=retry_count, replan_count=replan_count)
            emit_log("warning", "Recovery classified failure as blocked; escalating to approval interrupt.", None)
            return {
                "route": "approval",
                "status": "awaiting_approval",
                "pending_approval": _approval_payload(state, context),
                "approval_decision": None,
                "recovery_context": context,
            }

        context = _recovery_context(state, classification="fatal", retry_count=retry_count, replan_count=replan_count)
        emit_log("error", "Recovery classified failure as fatal; finalizing with error.", None)
        session_data = copy.deepcopy(state.get("session_data") or {})
        session_data["status"] = "error"
        session_data["final_text"] = state.get("error") or "Run failed."
        return {
            "route": "completed",
            "status": "error",
            "final_text": session_data["final_text"],
            "recovery_context": context,
            "approval_decision": None,
            "session_data": session_data,
        }

    return recovery_turn


def build_recovery_subgraph(
    *,
    max_transient_retries: int = 3,
    max_replans: int = 2,
    emit_log: RecoveryLog = _noop_log,
):
    sg: StateGraph = StateGraph(RecoveryGraphState)
    sg.add_node(
        "recovery_turn",
        _make_recovery_turn(
            max_transient_retries=max_transient_retries,
            max_replans=max_replans,
            emit_log=emit_log,
        ),
    )
    sg.add_edge(START, "recovery_turn")
    sg.add_edge("recovery_turn", END)
    return sg.compile()