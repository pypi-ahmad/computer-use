from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agent.persisted_runtime import _build_engine
from backend.engine import default_openai_reasoning_effort_for_model, _get_gemini_builtin_search_sdk_error
from backend.models.registry import EngineCapabilities
from backend.models.schemas import load_allowed_models_json

logger = logging.getLogger(__name__)

_OPENAI_COMPUTER_USE_DOC_URL = "https://developers.openai.com/api/docs/guides/tools-computer-use"
_OPENAI_WEB_SEARCH_DOC_URL = "https://developers.openai.com/api/docs/guides/tools-web-search"
_ANTHROPIC_COMPUTER_USE_DOC_URL = "https://docs.anthropic.com/en/docs/build-with-claude/computer-use"
_ANTHROPIC_WEB_SEARCH_DOC_URL = "https://docs.anthropic.com/en/docs/build-with-claude/tool-use/web-search-tool"
_GEMINI_COMPUTER_USE_DOC_URL = "https://ai.google.dev/gemini-api/docs/computer-use"
_GEMINI_TOOL_COMBINATION_DOC_URL = "https://ai.google.dev/gemini-api/docs/tool-combination"


class ProviderCapabilitiesState(TypedDict, total=False):
    provider: str
    model_id: str
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
    beta_headers: list[str]
    search_allowed_domains: list[str]
    search_blocked_domains: list[str]
    allowed_callers: Optional[list[str]]


class CapabilityProbeGraphState(TypedDict, total=False):
    provider: str
    model: str
    api_key: str
    reasoning_effort: Optional[str]
    use_builtin_search: bool
    search_max_uses: Optional[int]
    search_allowed_domains: Optional[list[str]]
    search_blocked_domains: Optional[list[str]]
    allowed_callers: Optional[list[str]]
    provider_capabilities: ProviderCapabilitiesState
    system_instruction: str
    screen_width: int
    screen_height: int
    container_name: str
    agent_service_url: str
    attached_files: list[str]
    route: str
    status: str
    error: Optional[str]
    final_text: str
    session_data: dict[str, Any]


CapabilityProbeLog = Callable[[str, str, Optional[dict[str, Any]]], None]


def _noop_log(_level: str, _msg: str, _data: dict[str, Any] | None = None) -> None:
    return None


def _copy_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _copy_optional_str_list(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    return _copy_str_list(value)


def _lookup_model_metadata(provider: str, model_id: str) -> dict[str, Any]:
    for item in load_allowed_models_json():
        if str(item.get("provider") or "").strip().lower() == provider and str(item.get("model_id") or "") == model_id:
            return copy.deepcopy(item)
    return {}


def _supports_search_filtering(provider: str) -> bool:
    return provider in {"anthropic", "openai"}


def _supports_allowed_callers(provider: str) -> bool:
    return provider == "anthropic"


def _supports_gemini_tool_combination(model_id: str) -> bool:
    return model_id.startswith("gemini-3") and _get_gemini_builtin_search_sdk_error() is None


def _doc_url_for_failure(provider: str, message: str) -> str:
    text = str(message or "").lower()
    if provider == "anthropic":
        if "web search" in text or "allowed_callers" in text or "search_allowed_domains" in text or "search_blocked_domains" in text:
            return _ANTHROPIC_WEB_SEARCH_DOC_URL
        return _ANTHROPIC_COMPUTER_USE_DOC_URL
    if provider == "google":
        if "google_search" in text or "tool_config" in text or "validated" in text or "include_server_side_tool_invocations" in text:
            return _GEMINI_TOOL_COMBINATION_DOC_URL
        return _GEMINI_COMPUTER_USE_DOC_URL
    if "web_search" in text or "reasoning" in text or "search" in text:
        return _OPENAI_WEB_SEARCH_DOC_URL
    return _OPENAI_COMPUTER_USE_DOC_URL


def _fail_fast(provider: str, message: str) -> ValueError:
    url = _doc_url_for_failure(provider, message)
    if url in message:
        return ValueError(message)
    return ValueError(f"Capability probe rejected the selected model/config: {message} See: {url}")


def _base_capabilities(state: CapabilityProbeGraphState, metadata: dict[str, Any]) -> ProviderCapabilitiesState:
    provider = str(state.get("provider") or "").strip().lower()
    model_id = str(state.get("model") or "")
    beta_flag = None
    betas = metadata.get("cu_betas")
    if isinstance(betas, list) and betas:
        beta_flag = str(betas[0] or "") or None
    return {
        "provider": provider,
        "model_id": model_id,
        "verified": False,
        "computer_use": False,
        "web_search": False,
        "web_search_version": None,
        "tool_combination_supported": False,
        "search_filtering_supported": _supports_search_filtering(provider),
        "allowed_callers_supported": _supports_allowed_callers(provider),
        "reasoning_effort_default": default_openai_reasoning_effort_for_model(model_id) if provider == "openai" and model_id else None,
        "tool_version": str(metadata.get("cu_tool_version") or "") or None,
        "beta_flag": beta_flag,
        "beta_headers": [beta_flag] if beta_flag else [],
        "search_allowed_domains": _copy_str_list(state.get("search_allowed_domains")),
        "search_blocked_domains": _copy_str_list(state.get("search_blocked_domains")),
        "allowed_callers": _copy_optional_str_list(state.get("allowed_callers")),
    }


def _has_verified_capabilities(state: CapabilityProbeGraphState) -> bool:
    capabilities = state.get("provider_capabilities") or {}
    if not isinstance(capabilities, dict):
        return False
    required_keys = {
        "computer_use",
        "web_search",
        "tool_combination_supported",
        "search_filtering_supported",
        "allowed_callers_supported",
        "reasoning_effort_default",
        "tool_version",
        "beta_flag",
    }
    if capabilities.get("verified") is not True:
        return False
    return required_keys.issubset(capabilities.keys())


async def probe_session_capabilities(state: CapabilityProbeGraphState, *, on_log: CapabilityProbeLog = _noop_log) -> ProviderCapabilitiesState:
    provider = str(state.get("provider") or "").strip().lower()
    model_id = str(state.get("model") or "")
    metadata = _lookup_model_metadata(provider, model_id)
    capabilities = _base_capabilities(state, metadata)

    engine_schema = EngineCapabilities().get_engine("computer_use")
    if engine_schema is None or not engine_schema.allowed_actions:
        raise _fail_fast(provider, "computer_use engine capabilities are unavailable in backend/models/engine_capabilities.json.")

    if not metadata:
        raise _fail_fast(provider, f"Model {model_id!r} is not present in backend/models/allowed_models.json.")

    if not bool(metadata.get("supports_computer_use")):
        raise _fail_fast(provider, f"Model {model_id!r} does not support computer use.")

    capabilities["computer_use"] = True
    if provider == "google":
        capabilities["tool_combination_supported"] = _supports_gemini_tool_combination(model_id)

    try:
        engine = _build_engine(state)
    except Exception as exc:
        raise _fail_fast(provider, str(exc)) from exc

    client = engine._client
    if provider == "anthropic" and bool(state.get("use_builtin_search")):
        try:
            await client._ensure_anthropic_web_search_enabled(on_log)
        except Exception as exc:
            raise _fail_fast(provider, str(exc)) from exc

    capabilities["web_search"] = bool(getattr(client, "_use_builtin_search", False))
    if provider == "anthropic":
        allowed_callers = getattr(client, "_allowed_callers", None)
        capabilities["web_search_version"] = (
            "web_search_20260209" if allowed_callers is not None else "web_search_20250305"
        )
        capabilities["tool_version"] = str(getattr(client, "_tool_version", capabilities.get("tool_version") or "")) or None
        capabilities["beta_flag"] = str(getattr(client, "_beta_flag", capabilities.get("beta_flag") or "")) or None
        capabilities["beta_headers"] = [capabilities["beta_flag"]] if capabilities.get("beta_flag") else []
    elif provider == "openai":
        capabilities["reasoning_effort_default"] = default_openai_reasoning_effort_for_model(model_id)
    elif provider == "google":
        capabilities["tool_combination_supported"] = _supports_gemini_tool_combination(model_id)

    capabilities["verified"] = True
    on_log(
        "info",
        f"Capability probe verified provider/model {provider}/{model_id}",
        {"provider_capabilities": copy.deepcopy(capabilities)},
    )
    return capabilities


def _make_capability_probe_turn(emit_log: CapabilityProbeLog):
    async def capability_probe_turn(state: CapabilityProbeGraphState) -> dict[str, Any]:
        if _has_verified_capabilities(state):
            return {
                "route": "planner",
                "status": state.get("status") or "running",
            }
        try:
            capabilities = await probe_session_capabilities(state, on_log=emit_log)
        except Exception as exc:
            session_data = copy.deepcopy(state.get("session_data") or {})
            session_data["status"] = "error"
            session_data["final_text"] = str(exc)
            emit_log("warning", f"Capability probe failed: {type(exc).__name__}: {exc}", None)
            return {
                "route": "completed",
                "status": "error",
                "error": str(exc),
                "final_text": str(exc),
                "provider_capabilities": _base_capabilities(state, {}),
                "session_data": session_data,
            }
        return {
            "route": "planner",
            "status": "running",
            "error": None,
            "provider_capabilities": capabilities,
        }

    return capability_probe_turn


def build_capability_probe_subgraph(*, emit_log: CapabilityProbeLog = _noop_log):
    sg: StateGraph = StateGraph(CapabilityProbeGraphState)
    sg.add_node("capability_probe_turn", _make_capability_probe_turn(emit_log))
    sg.add_edge(START, "capability_probe_turn")
    sg.add_edge("capability_probe_turn", END)
    return sg.compile()