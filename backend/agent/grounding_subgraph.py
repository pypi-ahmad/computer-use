from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agent.memory_layers import prune_evidence_entries
from backend.agent.persisted_runtime import collect_grounding_evidence

logger = logging.getLogger(__name__)

GROUNDING_SYSTEM_PROMPT = """\
You are the grounding subgraph for a desktop computer-use agent.

Use the provider's native web search tool to gather only the external facts,
official URLs, and UI navigation clues needed before the executor acts on the
desktop. Do not describe or perform desktop actions. Return concise factual
notes backed by sources.
"""

_GROUNDING_HINT_RE = re.compile(
    r"\b("
    r"search|look up|lookup|find|website|site|url|link|portal|dashboard|docs|documentation|"
    r"help center|faq|support|latest|today|recent|version|release|pricing|price|"
    r"hours|address|phone|email|official|policy|instructions|where is|menu path|navigate to"
    r")\b",
    re.IGNORECASE,
)


class GroundingProviderCapabilities(TypedDict, total=False):
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


class GroundingSubgoalState(TypedDict, total=False):
    title: str
    status: str


class GroundingGraphState(TypedDict, total=False):
    goal: str
    provider: str
    model: str
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
    provider_capabilities: GroundingProviderCapabilities
    subgoals: list[GroundingSubgoalState]
    active_plan: Optional[dict[str, Any]]
    evidence: list[dict[str, Any]]
    route: str
    status: str
    error: Optional[str]
    retry_reason: str
    last_error_classification: Optional[str]


GroundingLog = Callable[[str, str, Optional[dict[str, Any]]], None]


def _noop_log(_level: str, _msg: str, _data: dict[str, Any] | None = None) -> None:
    return None


def _active_subgoal_title(state: GroundingGraphState) -> str:
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


def _plan_summary(state: GroundingGraphState) -> str:
    active_plan = state.get("active_plan")
    if not isinstance(active_plan, dict):
        return ""
    return str(active_plan.get("summary") or "").strip()


def _explicit_grounding_query(state: GroundingGraphState) -> str:
    active_plan = state.get("active_plan")
    if not isinstance(active_plan, dict):
        return ""
    direct = str(active_plan.get("grounding_query") or "").strip()
    if direct:
        return direct
    queries = active_plan.get("grounding_queries")
    if isinstance(queries, list):
        for item in queries:
            query = str(item or "").strip()
            if query:
                return query
    return ""


def _has_grounding_evidence(state: GroundingGraphState, *, subgoal: str, plan_summary: str) -> bool:
    for item in state.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "grounding":
            continue
        if str(item.get("subgoal") or "").strip() != subgoal:
            continue
        if str(item.get("plan_summary") or "").strip() != plan_summary:
            continue
        return True
    return False


def needs_grounding(state: GroundingGraphState) -> bool:
    active_plan = state.get("active_plan")
    if not isinstance(active_plan, dict):
        return False
    capabilities = state.get("provider_capabilities") or {}
    if not bool(capabilities.get("web_search")):
        return False
    subgoal = _active_subgoal_title(state)
    if not subgoal:
        return False
    plan_summary = _plan_summary(state)
    if _has_grounding_evidence(state, subgoal=subgoal, plan_summary=plan_summary):
        return False
    if _explicit_grounding_query(state):
        return True
    search_text = " ".join(
        [
            str(state.get("goal") or "").strip(),
            plan_summary,
            subgoal,
            " ".join(
                str(step or "").strip()
                for step in (active_plan.get("steps") or [])[:4]
                if str(step or "").strip()
            ),
        ]
    ).strip()
    if not search_text:
        return False
    if re.search(r"https?://\S+", search_text) and not _GROUNDING_HINT_RE.search(search_text):
        return False
    return bool(_GROUNDING_HINT_RE.search(search_text))


def _build_grounding_query(state: GroundingGraphState, *, subgoal: str, plan_summary: str) -> str:
    explicit = _explicit_grounding_query(state)
    if explicit:
        return explicit
    active_plan = state.get("active_plan") if isinstance(state.get("active_plan"), dict) else {}
    context = {
        "goal": str(state.get("goal") or "").strip(),
        "active_subgoal": subgoal,
        "plan_summary": plan_summary,
        "plan_steps": [
            str(step or "").strip()
            for step in (active_plan.get("steps") or [])[:5]
            if str(step or "").strip()
        ],
        "existing_evidence": copy.deepcopy((state.get("evidence") or [])[-3:]),
    }
    return (
        "Use the native web search tool to gather only the external facts, official URLs, "
        "or UI navigation clues needed before computer-use execution. Return concise factual "
        "notes and cite source URLs.\n\n"
        f"Context:\n{json.dumps(context, indent=2, ensure_ascii=True)}"
    )


def _make_grounding_turn(*, emit_log: GroundingLog, evidence_limit: int):
    async def grounding_turn(state: GroundingGraphState) -> dict[str, Any]:
        if not needs_grounding(state):
            return {
                "route": "model_turn",
                "status": "running",
                "error": None,
                "retry_reason": "",
                "last_error_classification": None,
            }

        subgoal = _active_subgoal_title(state)
        plan_summary = _plan_summary(state)
        query = _build_grounding_query(state, subgoal=subgoal, plan_summary=plan_summary)
        try:
            entry = await collect_grounding_evidence(
                {**state, "system_instruction": GROUNDING_SYSTEM_PROMPT},
                subgoal=subgoal,
                plan_summary=plan_summary,
                query=query,
                on_log=emit_log,
            )
        except Exception as exc:
            emit_log("warning", f"Grounding failed: {type(exc).__name__}: {exc}", None)
            return {
                "route": "retry",
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "retry_reason": "grounding",
                "last_error_classification": type(exc).__name__,
            }

        evidence = copy.deepcopy(state.get("evidence") or [])
        evidence.append(entry)
        evidence = await prune_evidence_entries(state, evidence, evidence_limit=evidence_limit)
        emit_log(
            "info",
            f"Grounding collected {len(entry.get('sources') or [])} source(s) for subgoal: {subgoal}",
            None,
        )
        return {
            "route": "model_turn",
            "status": "running",
            "evidence": evidence,
            "error": None,
            "retry_reason": "",
            "last_error_classification": None,
        }

    return grounding_turn


def build_grounding_subgraph(*, emit_log: GroundingLog = _noop_log, evidence_limit: int = 50):
    sg: StateGraph = StateGraph(GroundingGraphState)
    sg.add_node("grounding_turn", _make_grounding_turn(emit_log=emit_log, evidence_limit=evidence_limit))
    sg.add_edge(START, "grounding_turn")
    sg.add_edge("grounding_turn", END)
    return sg.compile()