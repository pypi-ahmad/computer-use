from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from backend.agent.memory_layers import normalize_evidence_entries, read_long_term_memory
from backend.models.schemas import load_allowed_models_json

logger = logging.getLogger(__name__)

_MAX_PLANNER_TOKENS = 1200
_VALID_SUBGOAL_STATUSES = {"pending", "active", "done", "blocked"}

PLANNER_SYSTEM_PROMPT = """\
You are the planner subgraph for a desktop computer-use agent.

Your job is to turn the user goal into a short executable plan for a separate
computer-use executor. You must not emit tool calls, desktop actions, browser
actions, or verification steps. Return strict JSON only.

Return an object with exactly these keys:
- subgoals: ordered list of objects with title and status. Use statuses from
  [\"pending\", \"active\", \"done\", \"blocked\"]. If work remains, exactly one
  subgoal should be \"active\".
- active_plan: object with summary, steps, active_subgoal, and optional pinned_evidence_ids.
- completion_criteria: list of observable on-screen completion conditions.

Planning rules:
- Use the provided capability snapshot to keep the plan grounded in the tools
  that are actually available.
- Replans must carry forward existing evidence and recent observations.
- Keep subgoals concrete, ordered, and directly useful for execution.
- Do not plan destructive or irrelevant work that the user did not ask for.
"""


class PlannerProviderCapabilities(TypedDict, total=False):
    computer_use: bool
    web_search: bool
    model_id: str
    beta_headers: list[str]
    search_allowed_domains: list[str]
    search_blocked_domains: list[str]
    allowed_callers: Optional[list[str]]


class PlannerSubgoalState(TypedDict, total=False):
    title: str
    status: str


class PlannerGraphState(TypedDict, total=False):
    goal: str
    provider: str
    model: str
    planner_model: str
    api_key: str
    provider_capabilities: PlannerProviderCapabilities
    evidence: list[dict[str, Any]]
    recovery_context: Optional[dict[str, Any]]
    session_data: dict[str, Any]
    subgoals: list[PlannerSubgoalState]
    active_plan: Optional[dict[str, Any]]
    completion_criteria: list[str]
    memory_context: Optional[dict[str, list[dict[str, Any]]]]
    replan: bool
    route: str
    status: str
    error: Optional[str]
    retry_reason: str
    last_error_classification: Optional[str]


PlannerLog = Callable[[str, str, Optional[dict[str, Any]]], None]


def _noop_log(_level: str, _msg: str, _data: dict[str, Any] | None = None) -> None:
    return None


def _copy_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _resolve_planner_model(state: PlannerGraphState) -> str:
    return str(
        state.get("planner_model")
        or os.getenv("CUA_PLANNER_MODEL")
        or state.get("model")
        or ""
    )


def _lookup_provider_for_model(model_id: str) -> str | None:
    try:
        for item in load_allowed_models_json():
            if item.get("model_id") == model_id:
                provider = str(item.get("provider") or "").strip().lower()
                if provider:
                    return provider
    except Exception:
        logger.debug("Could not resolve planner model provider", exc_info=True)
    return None


def _api_key_env_names(provider: str) -> tuple[str, ...]:
    if provider == "anthropic":
        return ("ANTHROPIC_API_KEY",)
    if provider == "google":
        return ("GOOGLE_API_KEY", "GEMINI_API_KEY")
    return ("OPENAI_API_KEY",)


def _resolve_planner_target(state: PlannerGraphState) -> tuple[str, str, str]:
    model = _resolve_planner_model(state)
    if not model:
        raise ValueError("Planner model is missing")
    provider = _lookup_provider_for_model(model) or str(state.get("provider") or "openai").lower()
    state_provider = str(state.get("provider") or "").lower()
    api_key = ""
    if provider == state_provider:
        api_key = str(state.get("api_key") or "")
    if not api_key:
        for env_name in _api_key_env_names(provider):
            api_key = str(os.getenv(env_name) or "")
            if api_key:
                break
    if not api_key:
        raise ValueError(f"No API key configured for planner provider {provider!r}")
    return provider, model, api_key


def _recent_observations(state: PlannerGraphState) -> list[dict[str, Any]]:
    session_data = state.get("session_data")
    if not isinstance(session_data, dict):
        return []
    observations: list[dict[str, Any]] = []
    for step in (session_data.get("steps") or [])[-5:]:
        if not isinstance(step, dict):
            continue
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        observations.append(
            {
                "step_number": step.get("step_number"),
                "action": action.get("action"),
                "reasoning": step.get("raw_model_response"),
                "error": step.get("error"),
            }
        )
    return observations


def _build_planner_user_prompt(state: PlannerGraphState) -> str:
    payload = {
        "goal": str(state.get("goal") or ""),
        "provider_capabilities": copy.deepcopy(state.get("provider_capabilities") or {}),
        "existing_plan": copy.deepcopy(state.get("active_plan")) if isinstance(state.get("active_plan"), dict) else None,
        "existing_subgoals": copy.deepcopy(state.get("subgoals") or []),
        "evidence": normalize_evidence_entries(state.get("evidence"), assign_ids=True),
        "long_term_memory": copy.deepcopy(state.get("memory_context") or {}),
        "recovery_context": copy.deepcopy(state.get("recovery_context") or None),
        "recent_observations": _recent_observations(state),
        "replan": bool(state.get("replan")),
    }
    return (
        "Create or refresh the execution plan for this desktop task. Return strict JSON only.\n\n"
        f"Context:\n{json.dumps(payload, indent=2, ensure_ascii=True)}"
    )


async def _call_openai_planner(model: str, api_key: str, user_prompt: str) -> str:
    from openai import AsyncOpenAI

    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    client = AsyncOpenAI(**kwargs)
    response = await client.responses.create(
        model=model,
        instructions=PLANNER_SYSTEM_PROMPT,
        input=user_prompt,
    )
    return str(getattr(response, "output_text", "") or "")


async def _call_anthropic_planner(model: str, api_key: str, user_prompt: str) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=_MAX_PLANNER_TOKENS,
        system=PLANNER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return " ".join(parts)


async def _call_gemini_planner(model: str, api_key: str, user_prompt: str) -> str:
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model=model,
        contents=user_prompt,
        config=genai_types.GenerateContentConfig(
            system_instruction=PLANNER_SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )
    return str(getattr(response, "text", "") or "")


async def _request_planner_text(state: PlannerGraphState) -> tuple[str, str]:
    provider, model, api_key = _resolve_planner_target(state)
    user_prompt = _build_planner_user_prompt(state)
    if provider == "anthropic":
        text = await _call_anthropic_planner(model, api_key, user_prompt)
    elif provider == "google":
        text = await _call_gemini_planner(model, api_key, user_prompt)
    else:
        text = await _call_openai_planner(model, api_key, user_prompt)
    return model, text


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = str(text or "").strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end < start:
            raise
        payload = json.loads(candidate[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Planner output must be a JSON object")
    return payload


def _normalize_subgoals(value: Any) -> list[PlannerSubgoalState]:
    if not isinstance(value, list):
        raise ValueError("Planner output must include a subgoals list")
    normalized: list[PlannerSubgoalState] = []
    for item in value:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("objective") or "").strip()
            status = str(item.get("status") or "pending").lower()
        else:
            title = str(item).strip()
            status = "pending"
        if not title:
            continue
        if status not in _VALID_SUBGOAL_STATUSES:
            status = "pending"
        normalized.append({"title": title, "status": status})
    if not normalized:
        raise ValueError("Planner returned no subgoals")

    active_index: int | None = None
    for idx, item in enumerate(normalized):
        if item["status"] == "active":
            if active_index is None:
                active_index = idx
            else:
                item["status"] = "pending"
    if active_index is None:
        for idx, item in enumerate(normalized):
            if item["status"] == "pending":
                item["status"] = "active"
                active_index = idx
                break
    if active_index is None:
        normalized[0]["status"] = "active"
    return normalized


def _active_subgoal_title(subgoals: list[PlannerSubgoalState]) -> str:
    for item in subgoals:
        if item.get("status") == "active":
            return str(item.get("title") or "")
    return str(subgoals[0].get("title") or "")


def _normalize_active_plan(value: Any, goal: str, subgoals: list[PlannerSubgoalState]) -> dict[str, Any]:
    base = copy.deepcopy(value) if isinstance(value, dict) else {}
    summary = str(base.get("summary") or goal).strip() or goal
    steps = _copy_str_list(base.get("steps")) or [item["title"] for item in subgoals]
    active_subgoal = str(base.get("active_subgoal") or _active_subgoal_title(subgoals)).strip()
    base["summary"] = summary
    base["steps"] = steps
    base["active_subgoal"] = active_subgoal
    if "pinned_evidence_ids" in base:
        base["pinned_evidence_ids"] = _copy_str_list(base.get("pinned_evidence_ids"))
    return base


def _normalize_completion_criteria(value: Any, goal: str) -> list[str]:
    criteria = _copy_str_list(value)
    if criteria:
        return criteria
    return [f"The requested on-screen goal is complete: {goal}"]


async def generate_plan_output(state: PlannerGraphState) -> dict[str, Any]:
    model, raw_text = await _request_planner_text(state)
    payload = _extract_json_object(raw_text)
    goal = str(state.get("goal") or "")
    subgoals = _normalize_subgoals(payload.get("subgoals"))
    active_plan = _normalize_active_plan(payload.get("active_plan"), goal, subgoals)
    completion_criteria = _normalize_completion_criteria(payload.get("completion_criteria"), goal)
    return {
        "planner_model": model,
        "subgoals": subgoals,
        "active_plan": active_plan,
        "completion_criteria": completion_criteria,
    }


def _make_planner_turn(emit_log: PlannerLog):
    async def planner_turn(state: PlannerGraphState, runtime: Runtime[Any] | None = None) -> dict[str, Any]:
        prepared_state = copy.deepcopy(state)
        prepared_state["memory_context"] = await read_long_term_memory(getattr(runtime, "store", None), prepared_state)
        try:
            plan = await generate_plan_output(prepared_state)
        except Exception as exc:
            emit_log("warning", f"Planner failed: {type(exc).__name__}: {exc}", None)
            return {
                "route": "retry",
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "retry_reason": "planner",
                "last_error_classification": type(exc).__name__,
            }

        emit_log(
            "info",
            f"Planner produced {len(plan['subgoals'])} subgoal(s) using {plan['planner_model']}",
            None,
        )
        return {
            **plan,
            "memory_context": copy.deepcopy(prepared_state.get("memory_context") or {}),
            "route": "model_turn",
            "status": "running",
            "error": None,
            "retry_reason": "",
            "last_error_classification": None,
            "replan": False,
        }

    return planner_turn


def build_planner_subgraph(*, emit_log: PlannerLog = _noop_log, store: Any = None):
    sg: StateGraph = StateGraph(PlannerGraphState)
    sg.add_node("planner_turn", _make_planner_turn(emit_log))
    sg.add_edge(START, "planner_turn")
    sg.add_edge("planner_turn", END)
    return sg.compile(store=store)