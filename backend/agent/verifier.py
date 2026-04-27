from __future__ import annotations

import base64
import copy
import json
import logging
import os
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agent.persisted_runtime import _load_image_bytes, _session_running
from backend.models.schemas import load_allowed_models_json

logger = logging.getLogger(__name__)

_MAX_VERIFIER_TOKENS = 1200
_IMAGE_PNG = "image/png"
_VALID_VERDICTS = {"complete", "needs_more_work", "regression"}

VERIFIER_SYSTEM_PROMPT = """\
You are the completion verifier for a desktop computer-use agent.

Decide whether the latest executor turn satisfies the completion criteria.
Return strict JSON only with exactly these keys:
- verdict: one of ["complete", "needs_more_work", "regression"]
- unmet_criteria: list of criteria strings that are still unmet
- rationale: short explanation grounded in the visible UI state and evidence

Use "complete" only when the criteria are visibly satisfied.
Use "needs_more_work" when the task may still succeed with another executor turn.
Use "regression" when the UI moved away from the expected state or the latest turn made progress worse.
"""


class VerifierGraphState(TypedDict, total=False):
    goal: str
    provider: str
    model: str
    planner_model: str
    api_key: str
    completion_criteria: list[str]
    evidence: list[dict[str, Any]]
    latest_executor_output: Optional[dict[str, Any]]
    verification_status: str
    unmet_completion_criteria: list[str]
    session_data: dict[str, Any]
    final_text: str
    route: str
    status: str
    error: Optional[str]
    retry_reason: str
    last_error_classification: Optional[str]


VerifierLog = Callable[[str, str, Optional[dict[str, Any]]], None]


def _noop_log(_level: str, _msg: str, _data: dict[str, Any] | None = None) -> None:
    return None


def _copy_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _lookup_provider_for_model(model_id: str) -> str | None:
    try:
        for item in load_allowed_models_json():
            if item.get("model_id") == model_id:
                provider = str(item.get("provider") or "").strip().lower()
                if provider:
                    return provider
    except Exception:
        logger.debug("Could not resolve verifier model provider", exc_info=True)
    return None


def _api_key_env_names(provider: str) -> tuple[str, ...]:
    if provider == "anthropic":
        return ("ANTHROPIC_API_KEY",)
    if provider == "google":
        return ("GOOGLE_API_KEY", "GEMINI_API_KEY")
    return ("OPENAI_API_KEY",)


def _resolve_verifier_target(state: VerifierGraphState) -> tuple[str, str, str]:
    model = str(
        os.getenv("CUA_VERIFIER_MODEL")
        or state.get("planner_model")
        or state.get("model")
        or ""
    )
    if not model:
        raise ValueError("Verifier model is missing")
    provider = _lookup_provider_for_model(model) or str(state.get("provider") or "openai").lower()
    state_provider = str(state.get("provider") or "").lower()
    api_key = str(state.get("api_key") or "") if provider == state_provider else ""
    if not api_key:
        for env_name in _api_key_env_names(provider):
            api_key = str(os.getenv(env_name) or "")
            if api_key:
                break
    if not api_key:
        raise ValueError(f"No API key configured for verifier provider {provider!r}")
    return provider, model, api_key


def _prompt_latest_output(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "turn": value.get("turn"),
        "model_text": value.get("model_text"),
        "terminal_text": value.get("terminal_text"),
        "results": copy.deepcopy(value.get("results") or []),
        "native_actions": copy.deepcopy(value.get("native_actions") or []),
    }


def _build_verifier_user_prompt(state: VerifierGraphState) -> str:
    payload = {
        "goal": str(state.get("goal") or "").strip(),
        "completion_criteria": _copy_str_list(state.get("completion_criteria")),
        "evidence": copy.deepcopy((state.get("evidence") or [])[-5:]),
        "latest_executor_output": _prompt_latest_output(state.get("latest_executor_output")),
    }
    return (
        "Verify whether the latest executor turn completed the task. Return strict JSON only.\n\n"
        f"Context:\n{json.dumps(payload, indent=2, ensure_ascii=True)}"
    )


def _verifier_screenshot_bytes(state: VerifierGraphState) -> bytes | None:
    latest = state.get("latest_executor_output")
    if isinstance(latest, dict):
        ref = str(latest.get("screenshot_ref") or "").strip()
        if ref:
            try:
                return _load_image_bytes(ref)
            except Exception:
                logger.debug("Could not load verifier screenshot ref %s", ref, exc_info=True)
    for item in reversed(state.get("evidence") or []):
        if not isinstance(item, dict):
            continue
        ref = str(item.get("screenshot_ref") or "").strip()
        if not ref:
            continue
        try:
            return _load_image_bytes(ref)
        except Exception:
            logger.debug("Could not load evidence screenshot ref %s", ref, exc_info=True)
    return None


async def _call_openai_verifier(model: str, api_key: str, user_prompt: str, screenshot_bytes: bytes | None) -> str:
    from openai import AsyncOpenAI

    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    client = AsyncOpenAI(**kwargs)
    content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
    if screenshot_bytes:
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{base64.standard_b64encode(screenshot_bytes).decode()}",
                "detail": "original",
            }
        )
    response = await client.responses.create(
        model=model,
        instructions=VERIFIER_SYSTEM_PROMPT,
        input=[{"role": "user", "content": content}],
    )
    return str(getattr(response, "output_text", "") or "")


async def _call_anthropic_verifier(model: str, api_key: str, user_prompt: str, screenshot_bytes: bytes | None) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)
    content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    if screenshot_bytes:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _IMAGE_PNG,
                    "data": base64.standard_b64encode(screenshot_bytes).decode(),
                },
            }
        )
    response = await client.messages.create(
        model=model,
        max_tokens=_MAX_VERIFIER_TOKENS,
        system=VERIFIER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return " ".join(parts)


async def _call_gemini_verifier(model: str, api_key: str, user_prompt: str, screenshot_bytes: bytes | None) -> str:
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=api_key)
    parts: list[Any] = [genai_types.Part(text=user_prompt)]
    if screenshot_bytes:
        parts.append(genai_types.Part.from_bytes(data=screenshot_bytes, mime_type=_IMAGE_PNG))
    response = await client.aio.models.generate_content(
        model=model,
        contents=[genai_types.Content(role="user", parts=parts)],
        config=genai_types.GenerateContentConfig(
            system_instruction=VERIFIER_SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )
    return str(getattr(response, "text", "") or "")


async def _request_verifier_text(state: VerifierGraphState) -> tuple[str, str]:
    provider, model, api_key = _resolve_verifier_target(state)
    user_prompt = _build_verifier_user_prompt(state)
    screenshot_bytes = _verifier_screenshot_bytes(state)
    if provider == "anthropic":
        text = await _call_anthropic_verifier(model, api_key, user_prompt, screenshot_bytes)
    elif provider == "google":
        text = await _call_gemini_verifier(model, api_key, user_prompt, screenshot_bytes)
    else:
        text = await _call_openai_verifier(model, api_key, user_prompt, screenshot_bytes)
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
        raise ValueError("Verifier output must be a JSON object")
    return payload


async def verify_completion_output(state: VerifierGraphState) -> dict[str, Any]:
    model, raw_text = await _request_verifier_text(state)
    payload = _extract_json_object(raw_text)
    verdict = str(payload.get("verdict") or "needs_more_work").strip().lower()
    if verdict not in _VALID_VERDICTS:
        verdict = "needs_more_work"
    all_criteria = _copy_str_list(state.get("completion_criteria"))
    unmet = _copy_str_list(payload.get("unmet_criteria"))
    if verdict == "needs_more_work" and not unmet:
        unmet = all_criteria
    return {
        "verifier_model": model,
        "verdict": verdict,
        "unmet_criteria": unmet,
        "rationale": str(payload.get("rationale") or "").strip(),
    }


def _final_text_from_output(state: VerifierGraphState) -> str:
    latest = state.get("latest_executor_output")
    if isinstance(latest, dict):
        terminal_text = str(latest.get("terminal_text") or "").strip()
        if terminal_text:
            return terminal_text
        model_text = str(latest.get("model_text") or "").strip()
        if model_text:
            return model_text
    return str(state.get("final_text") or "Task complete.").strip() or "Task complete."


def _make_verifier_turn(emit_log: VerifierLog):
    async def verifier_turn(state: VerifierGraphState) -> dict[str, Any]:
        try:
            result = await verify_completion_output(state)
        except Exception as exc:
            emit_log("warning", f"Verifier failed: {type(exc).__name__}: {exc}", None)
            return {
                "route": "retry",
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "retry_reason": "verification",
                "last_error_classification": type(exc).__name__,
            }

        verdict = result["verdict"]
        rationale = result["rationale"]
        emit_log("info", f"Verifier verdict: {verdict}", None)
        if verdict == "complete":
            final_text = _final_text_from_output(state)
            session_data = _session_running(
                copy.deepcopy(state.get("session_data") or {}),
                status="completed",
                final_text=final_text,
            )
            return {
                "route": "finalize",
                "status": "completed",
                "final_text": final_text,
                "verification_status": "complete",
                "unmet_completion_criteria": [],
                "session_data": session_data,
                "error": None,
                "retry_reason": "",
                "last_error_classification": None,
                "verification_rationale": rationale,
            }
        if verdict == "regression":
            return {
                "route": "retry",
                "status": "error",
                "error": rationale or "Verifier detected regression.",
                "retry_reason": "verification",
                "last_error_classification": "regression",
                "verification_status": "regression",
                "unmet_completion_criteria": _copy_str_list(result.get("unmet_criteria")),
                "verification_rationale": rationale,
            }
        return {
            "route": "model_turn",
            "status": "running",
            "verification_status": "needs_more_work",
            "unmet_completion_criteria": _copy_str_list(result.get("unmet_criteria")),
            "verification_rationale": rationale,
            "error": None,
            "retry_reason": "",
            "last_error_classification": None,
            "session_data": _session_running(copy.deepcopy(state.get("session_data") or {}), status="running"),
        }

    return verifier_turn


def build_verifier_subgraph(*, emit_log: VerifierLog = _noop_log):
    sg: StateGraph = StateGraph(VerifierGraphState)
    sg.add_node("verifier_turn", _make_verifier_turn(emit_log))
    sg.add_edge(START, "verifier_turn")
    sg.add_edge("verifier_turn", END)
    return sg.compile()