from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from backend.engine import (
    CUActionResult,
    ComputerUseEngine,
    Environment,
    Provider,
    SafetyDecision,
    ToolBatchCompleted,
    _append_source_footer,
    _build_openai_computer_call_output,
    _call_with_retry,
    _sanitize_openai_response_item_for_replay,
    _to_plain_dict,
    get_claude_scale_factor,
    resize_screenshot_for_claude,
)
from backend.engine.claude import (
    _CLAUDE_MAX_TOKENS,
    _CLAUDE_OPUS_47_MAX_LONG_EDGE,
    _CONTEXT_PRUNE_KEEP_RECENT,
    _extract_claude_sources,
    _is_opus_47,
    _prune_claude_context,
)
from backend.engine.grounding import (
    _build_grounding_evidence_entry,
    _extract_claude_grounding_result,
    _extract_gemini_grounding_result,
    _extract_openai_grounding_result,
)
from backend.engine.gemini import _extract_gemini_grounding_payload, _prune_gemini_context
from backend.engine.openai import _extract_openai_sources, _prepare_openai_computer_screenshot

_IMAGE_PNG = "image/png"
_SCREENSHOT_ROOT = Path(tempfile.gettempdir()) / "cua-graph-screenshots"
_SCREENSHOT_ROOT.mkdir(parents=True, exist_ok=True)


def _action_payload_digest(payload: Any) -> str:
    plain = _to_plain_dict(payload) if not isinstance(payload, dict) else copy.deepcopy(payload)
    encoded = json.dumps(plain, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.blake2b(encoded, digest_size=8).hexdigest()


def _openai_action_id(
    *,
    turn_number: int,
    call_index: int,
    computer_call: dict[str, Any],
    action_index: int,
    action: dict[str, Any],
) -> str:
    call_id = str(computer_call.get("call_id") or "").strip()
    if call_id:
        return f"openai:{turn_number}:{call_id}:{action_index}"
    return f"openai:{turn_number}:{call_index}:{action_index}:{_action_payload_digest(action)}"


def _claude_action_id(*, turn_number: int, tool_index: int, tool_use: dict[str, Any]) -> str:
    tool_use_id = str(tool_use.get("id") or "").strip()
    if tool_use_id:
        return f"anthropic:{turn_number}:{tool_use_id}"
    return f"anthropic:{turn_number}:{tool_index}:{_action_payload_digest(tool_use.get('input') or tool_use)}"


def _gemini_action_id(*, turn_number: int, call_index: int, function_call: dict[str, Any]) -> str:
    function_call_id = str(function_call.get("id") or function_call.get("call_id") or "").strip()
    if function_call_id:
        return f"google:{turn_number}:{function_call_id}"
    stable_payload = {
        "name": function_call.get("name"),
        "args": function_call.get("args") or {},
    }
    return f"google:{turn_number}:{call_index}:{_action_payload_digest(stable_payload)}"


class _IdempotentActionExecutor:
    def __init__(self, inner: Any, action_id: str):
        self._inner = inner
        self._action_id = action_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult:
        payload = dict(args or {})
        payload["action_id"] = self._action_id
        return await self._inner.execute(name, payload)

    async def capture_screenshot(self) -> bytes:
        return await self._inner.capture_screenshot()

    def get_current_url(self) -> str:
        return self._inner.get_current_url()

    async def aclose(self) -> None:
        if hasattr(self._inner, "aclose"):
            await self._inner.aclose()


def _session_data(state: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(state.get("session_data") or {})
    if data:
        return data
    return {
        "session_id": str(state.get("session_id", "")),
        "task": str(state.get("task", "")),
        "status": "idle",
        "model": str(state.get("model", "")),
        "engine": "computer_use",
        "steps": [],
        "max_steps": int(state.get("max_steps", 25) or 25),
        "created_at": str(state.get("created_at", "")),
        "final_text": None,
        "gemini_grounding": None,
    }


def _store_image_bytes(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    path = _SCREENSHOT_ROOT / digest
    if not path.exists():
        path.write_bytes(data)
    return digest


def _load_image_bytes(ref: str) -> bytes:
    return (_SCREENSHOT_ROOT / str(ref)).read_bytes()


def _store_image_b64(image_b64: str) -> str:
    return _store_image_bytes(base64.standard_b64decode(image_b64))


def _load_image_b64(ref: str) -> str:
    return base64.standard_b64encode(_load_image_bytes(ref)).decode()


def _externalize_png_blobs(value: Any) -> Any:
    if isinstance(value, list):
        return [_externalize_png_blobs(item) for item in value]
    if isinstance(value, tuple):
        return [_externalize_png_blobs(item) for item in value]
    if not isinstance(value, dict):
        return value

    if isinstance(value.get("image_url"), str) and value["image_url"].startswith("data:image/png;base64,"):
        out = {k: _externalize_png_blobs(v) for k, v in value.items() if k != "image_url"}
        out["image_ref"] = _store_image_b64(value["image_url"].split(",", 1)[1])
        return out

    if value.get("type") == "base64" and value.get("media_type") == _IMAGE_PNG and isinstance(value.get("data"), str):
        out = {k: _externalize_png_blobs(v) for k, v in value.items() if k != "data"}
        out["image_ref"] = _store_image_b64(value["data"])
        return out

    inline = value.get("inline_data")
    if isinstance(inline, dict) and inline.get("mime_type") == _IMAGE_PNG and inline.get("data") is not None:
        out = {k: _externalize_png_blobs(v) for k, v in value.items()}
        raw = inline.get("data")
        if isinstance(raw, (bytes, bytearray)):
            out["inline_data"] = {
                **{k: _externalize_png_blobs(v) for k, v in inline.items() if k != "data"},
                "image_ref": _store_image_bytes(bytes(raw)),
            }
            return out
        if isinstance(raw, str):
            out["inline_data"] = {
                **{k: _externalize_png_blobs(v) for k, v in inline.items() if k != "data"},
                "image_ref": _store_image_b64(raw),
            }
            return out

    return {k: _externalize_png_blobs(v) for k, v in value.items()}


def _rehydrate_png_blobs(value: Any) -> Any:
    if isinstance(value, list):
        return [_rehydrate_png_blobs(item) for item in value]
    if isinstance(value, tuple):
        return [_rehydrate_png_blobs(item) for item in value]
    if not isinstance(value, dict):
        return value

    inline = value.get("inline_data")
    if isinstance(inline, dict) and inline.get("image_ref"):
        out = {k: _rehydrate_png_blobs(v) for k, v in value.items()}
        ref = str(inline["image_ref"])
        out["inline_data"] = {
            **{k: _rehydrate_png_blobs(v) for k, v in inline.items() if k != "image_ref"},
            "data": _load_image_bytes(ref),
        }
        return out

    if value.get("image_ref"):
        ref = str(value["image_ref"])
        if value.get("type") in {"base64", "ref"} and value.get("media_type") == _IMAGE_PNG:
            out = {k: _rehydrate_png_blobs(v) for k, v in value.items() if k != "image_ref"}
            out["type"] = "base64"
            out["data"] = _load_image_b64(ref)
            return out
        out = {k: _rehydrate_png_blobs(v) for k, v in value.items() if k != "image_ref"}
        out["image_url"] = f"data:image/png;base64,{_load_image_b64(ref)}"
        return out

    return {k: _rehydrate_png_blobs(v) for k, v in value.items()}


def serialize_action_result(result: CUActionResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "success": result.success,
        "error": result.error,
        "safety_decision": result.safety_decision.value if result.safety_decision else None,
        "safety_explanation": result.safety_explanation,
        "extra": copy.deepcopy(result.extra),
    }


def deserialize_action_result(payload: dict[str, Any]) -> CUActionResult:
    safety_decision = payload.get("safety_decision")
    decision = None
    if isinstance(safety_decision, str):
        try:
            decision = SafetyDecision(safety_decision)
        except Exception:
            decision = None
    return CUActionResult(
        name=str(payload.get("name", "unknown")),
        success=bool(payload.get("success", True)),
        error=payload.get("error"),
        safety_decision=decision,
        safety_explanation=payload.get("safety_explanation"),
        extra=copy.deepcopy(payload.get("extra") or {}),
    )


def serialize_tool_batch(turn: int, model_text: str, results: list[CUActionResult], screenshot_ref: str | None) -> dict[str, Any]:
    return {
        "turn": int(turn),
        "model_text": model_text,
        "results": [serialize_action_result(result) for result in results],
        "screenshot_ref": screenshot_ref,
    }


def serialize_pending_action_batch(
    turn: int,
    model_text: str,
    native_actions: list[dict[str, Any]],
    *,
    screenshot_ref: str | None = None,
    terminal_text: str | None = None,
) -> dict[str, Any]:
    payload = serialize_tool_batch(turn, model_text, [], screenshot_ref)
    payload["native_actions"] = copy.deepcopy(native_actions)
    if terminal_text is not None:
        payload["terminal_text"] = terminal_text
    return payload


def deserialize_tool_batch(payload: dict[str, Any]) -> ToolBatchCompleted:
    screenshot_ref = payload.get("screenshot_ref")
    screenshot_b64 = _load_image_b64(str(screenshot_ref)) if screenshot_ref else None
    return ToolBatchCompleted(
        turn=int(payload.get("turn", 0)),
        model_text=str(payload.get("model_text", "")),
        results=[deserialize_action_result(item) for item in (payload.get("results") or [])],
        screenshot_b64=screenshot_b64,
    )


def append_step(session_data: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(session_data)
    steps = list(data.get("steps") or [])
    first = (payload.get("results") or [None])[0]
    action = None
    if isinstance(first, dict) and first.get("name"):
        action = {
            "action": first.get("name"),
            "coordinates": None,
            "text": first.get("extra", {}).get("text") if isinstance(first.get("extra"), dict) else None,
            "reasoning": str(payload.get("model_text") or "")[:500] or None,
        }
        px = (first.get("extra") or {}).get("pixel_x") if isinstance(first.get("extra"), dict) else None
        py = (first.get("extra") or {}).get("pixel_y") if isinstance(first.get("extra"), dict) else None
        if px is not None and py is not None:
            action["coordinates"] = [px, py]
    steps.append(
        {
            "step_number": int(payload.get("turn", 0)),
            "raw_model_response": payload.get("model_text"),
            "action": action,
            "error": None,
        }
    )
    data["steps"] = steps
    return data


def snapshot_session_data(session_data: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(session_data)
    for step in data.get("steps") or []:
        if isinstance(step, dict):
            step.pop("screenshot_b64", None)
    return data


def _provider_enum(name: str) -> Provider:
    key = str(name or "").lower()
    if key == "openai":
        return Provider.OPENAI
    if key == "anthropic":
        return Provider.CLAUDE
    if key == "google":
        return Provider.GEMINI
    raise ValueError(f"Unsupported provider {name!r}")


def _build_engine(state: dict[str, Any]) -> ComputerUseEngine:
    return ComputerUseEngine(
        provider=_provider_enum(str(state.get("provider", ""))),
        api_key=str(state.get("api_key", "")),
        model=str(state.get("model") or ""),
        environment=Environment.DESKTOP,
        screen_width=int(state.get("screen_width", 1440) or 1440),
        screen_height=int(state.get("screen_height", 900) or 900),
        system_instruction=str(state.get("system_instruction") or ""),
        container_name=str(state.get("container_name") or "cua-environment"),
        agent_service_url=str(state.get("agent_service_url") or "http://127.0.0.1:9222"),
        reasoning_effort=state.get("reasoning_effort"),
        use_builtin_search=bool(state.get("use_builtin_search", False)),
        search_max_uses=state.get("search_max_uses"),
        search_allowed_domains=copy.deepcopy(state.get("search_allowed_domains") or None),
        search_blocked_domains=copy.deepcopy(state.get("search_blocked_domains") or None),
        allowed_callers=copy.deepcopy(state.get("allowed_callers") or None),
        attached_files=copy.deepcopy(state.get("attached_files") or None),
    )


async def _call_claude_messages_create(client: Any, **kwargs: Any) -> Any:
    return await _call_with_retry(
        lambda: client._client.beta.messages.create(**kwargs),
        provider="anthropic",
        on_log=kwargs.pop("on_log", None),
    )


def _plain_claude_tool_uses(content: list[Any]) -> list[dict[str, Any]]:
    tool_uses: list[dict[str, Any]] = []
    for block in content:
        block_dict = _to_plain_dict(block) if not isinstance(block, dict) else dict(block)
        if block_dict.get("type") == "tool_use":
            tool_uses.append(block_dict)
    return tool_uses


def _plain_claude_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        block_dict = _to_plain_dict(block) if not isinstance(block, dict) else dict(block)
        text = block_dict.get("text")
        if text:
            parts.append(str(text))
    return " ".join(parts)


def _scale_for_claude(client: Any, executor: Any, on_log) -> float:
    scale = get_claude_scale_factor(
        executor.screen_width,
        executor.screen_height,
        client._model,
        tool_version=getattr(client, "_tool_version", None),
    )
    if os.environ.get("CUA_OPUS47_HIRES") == "1" and _is_opus_47(client._model):
        long_edge = max(executor.screen_width, executor.screen_height)
        scale = min(1.0, _CLAUDE_OPUS_47_MAX_LONG_EDGE / long_edge)
        if on_log:
            on_log("info", "CUA_OPUS47_HIRES=1: long-edge-only scaling for Opus 4.7")
    return scale


def _rehydrate_gemini_contents(client: Any, contents: list[dict[str, Any]]) -> list[Any]:
    types = client._types
    out: list[Any] = []
    for item in contents:
        plain = _rehydrate_png_blobs(copy.deepcopy(item))
        parts: list[Any] = []
        for part in plain.get("parts", []) or []:
            if part.get("text") is not None:
                parts.append(types.Part(text=part["text"]))
                continue
            if part.get("inline_data") is not None:
                inline = part["inline_data"]
                parts.append(types.Part.from_bytes(data=inline["data"], mime_type=inline.get("mime_type", _IMAGE_PNG)))
                continue
            if part.get("function_call") is not None:
                parts.append(types.Part(function_call=types.FunctionCall(**part["function_call"])))
                continue
            if part.get("function_response") is not None:
                fr_plain = copy.deepcopy(part["function_response"])
                fr_parts: list[Any] = []
                for fr_part in fr_plain.get("parts", []) or []:
                    inline = fr_part.get("inline_data") or {}
                    fr_parts.append(
                        types.FunctionResponsePart(
                            inline_data=types.FunctionResponseBlob(
                                mime_type=inline.get("mime_type", _IMAGE_PNG),
                                data=inline.get("data", b""),
                            )
                        )
                    )
                if fr_parts:
                    fr_plain["parts"] = fr_parts
                parts.append(types.Part(function_response=types.FunctionResponse(**fr_plain)))
                continue
            parts.append(types.Part(**part))
        out.append(types.Content(role=plain.get("role", "user"), parts=parts))
    return out


def _serialize_gemini_contents(contents: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in contents:
        plain = _to_plain_dict(item) if not isinstance(item, dict) else copy.deepcopy(item)
        out.append(_externalize_png_blobs(plain))
    return out


def _session_running(data: dict[str, Any], *, status: str = "running", final_text: str | None = None, gemini_grounding: dict[str, Any] | None = None) -> dict[str, Any]:
    updated = copy.deepcopy(data)
    updated["status"] = status
    if final_text is not None:
        updated["final_text"] = final_text
    if gemini_grounding is not None:
        updated["gemini_grounding"] = gemini_grounding
    return updated


def _openai_search_only_tools(client: Any, on_log) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(tool)
        for tool in client._build_tools(0, 0, on_log=on_log)
        if str(tool.get("type") or "") == "web_search"
    ]


def _claude_search_only_tools(client: Any, state: dict[str, Any]) -> list[dict[str, Any]]:
    tools = client._build_tools(
        int(state.get("screen_width", 1440) or 1440),
        int(state.get("screen_height", 900) or 900),
    )
    return [
        copy.deepcopy(tool)
        for tool in tools
        if str(tool.get("name") or "") == "web_search"
        or str(tool.get("type") or "").startswith("web_search")
    ]


def _gemini_search_only_config(client: Any) -> Any:
    config = client._build_config()
    tools = list(getattr(config, "tools", []) or [])
    search_tools = []
    for tool in tools:
        plain = _to_plain_dict(tool) if not isinstance(tool, dict) else copy.deepcopy(tool)
        if plain.get("google_search") is not None or plain.get("googleSearch") is not None:
            search_tools.append(tool)
    if not search_tools:
        raise ValueError("Gemini grounding requested without google_search enabled")
    try:
        config.tools = search_tools
        return config
    except Exception:
        kwargs: dict[str, Any] = {"tools": search_tools}
        for key in (
            "thinking_config",
            "include_server_side_tool_invocations",
            "tool_config",
            "safety_settings",
            "system_instruction",
        ):
            value = getattr(config, key, None)
            if value is not None:
                kwargs[key] = value
        return client._genai.types.GenerateContentConfig(**kwargs)


async def _run_openai_grounding_query(client: Any, query: str, on_log) -> dict[str, Any]:
    tools = _openai_search_only_tools(client, on_log)
    if not tools:
        raise ValueError("OpenAI grounding requested without web_search enabled")
    request: dict[str, Any] = {
        "model": client._model,
        "input": query,
        "tools": tools,
        "parallel_tool_calls": False,
        "include": ["web_search_call.action.sources"],
        "reasoning": {"effort": client._reasoning_effort},
        "store": False,
        "truncation": "auto",
    }
    if client._system_prompt:
        request["instructions"] = client._system_prompt
    response = await client._create_response(on_log=on_log, **request)
    response_error = getattr(response, "error", None)
    if response_error:
        raise RuntimeError(getattr(response_error, "message", str(response_error)))
    return _extract_openai_grounding_result(response)


async def _run_claude_grounding_query(client: Any, state: dict[str, Any], query: str, on_log) -> dict[str, Any]:
    await client._ensure_anthropic_web_search_enabled(on_log)
    tools = _claude_search_only_tools(client, state)
    if not tools:
        raise ValueError("Anthropic grounding requested without web_search enabled")
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": query}]}
    ]
    if client._tool_version == "computer_20251124":
        thinking_cfg: dict[str, Any] = {"type": "adaptive"}
    else:
        thinking_cfg = {"type": "enabled", "budget_tokens": 4096}
    while True:
        request: dict[str, Any] = {
            "model": client._model,
            "max_tokens": _CLAUDE_MAX_TOKENS,
            "system": client._system_prompt,
            "tools": tools,
            "messages": messages,
            "thinking": thinking_cfg,
        }
        beta_flag = getattr(client, "_beta_flag", None)
        if beta_flag:
            request["betas"] = [beta_flag]
        response = await _call_claude_messages_create(client, on_log=on_log, **request)
        assistant_plain = [
            copy.deepcopy(block) if isinstance(block, dict) else _to_plain_dict(block)
            for block in list(getattr(response, "content", []) or [])
        ]
        messages.append({"role": "assistant", "content": assistant_plain})
        if getattr(response, "stop_reason", None) == "pause_turn":
            continue
        return _extract_claude_grounding_result(response)


async def _run_gemini_grounding_query(client: Any, query: str, on_log) -> dict[str, Any]:
    config = _gemini_search_only_config(client)
    response = await _call_with_retry(
        lambda: client._generate(
            contents=[
                client._types.Content(
                    role="user",
                    parts=[client._types.Part(text=query)],
                )
            ],
            config=config,
        ),
        provider="google",
        on_log=on_log,
    )
    return _extract_gemini_grounding_result(response)


async def collect_grounding_evidence(
    state: dict[str, Any],
    *,
    subgoal: str,
    plan_summary: str,
    query: str,
    on_log=None,
) -> dict[str, Any]:
    provider = str(state.get("provider") or "").lower()
    if not query.strip():
        raise ValueError("Grounding query is empty")
    if not bool((state.get("provider_capabilities") or {}).get("web_search")):
        raise ValueError("Grounding requires provider web search to be enabled")

    engine = _build_engine(state)
    client = engine._client
    if provider == "openai":
        result = await _run_openai_grounding_query(client, query, on_log)
    elif provider == "anthropic":
        result = await _run_claude_grounding_query(client, state, query, on_log)
    elif provider == "google":
        result = await _run_gemini_grounding_query(client, query, on_log)
    else:
        raise ValueError(f"Unsupported grounding provider {provider!r}")

    return _build_grounding_evidence_entry(
        provider=provider,
        subgoal=subgoal,
        plan_summary=plan_summary,
        query=query,
        result=result,
    )


async def _ensure_openai_state(state: dict[str, Any], engine: Any, executor: Any, on_log) -> dict[str, Any]:
    client = engine._client
    ps = copy.deepcopy(state.get("provider_state") or {})
    if ps.get("provider") != "openai":
        ps = {
            "provider": "openai",
            "turn_index": 0,
            "next_input": None,
            "vector_store_id": None,
            "current_screenshot_scale": 1.0,
            "saw_computer_action": False,
            "nudged_for_computer_use": False,
            "pending_turn": None,
        }
    client._vector_store_id = ps.get("vector_store_id")
    client._current_screenshot_scale = float(ps.get("current_screenshot_scale", 1.0) or 1.0)
    if ps.get("next_input") is not None:
        return ps

    await client._ensure_vector_store(on_log=on_log)
    ps["vector_store_id"] = client._vector_store_id
    screenshot_bytes = await executor.capture_screenshot()
    if not screenshot_bytes or len(screenshot_bytes) < 100:
        raise RuntimeError("Error: Could not capture initial screenshot")
    prepared, scale = _prepare_openai_computer_screenshot(screenshot_bytes, on_log=on_log)
    ref = _store_image_bytes(prepared)
    ps["current_screenshot_scale"] = scale
    ps["next_input"] = _externalize_png_blobs(
        [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": str(state.get("task", ""))},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{_load_image_b64(ref)}",
                        "detail": "original",
                    },
                ],
            }
        ]
    )
    return ps


async def _continue_openai_turn(state: dict[str, Any], engine: Any, executor: Any, ps: dict[str, Any], on_log) -> dict[str, Any]:
    session_data = _session_data(state)
    pending = copy.deepcopy(ps.get("pending_turn") or {})
    decision = state.get("approval_decision")
    turn_number = int(pending.get("turn", 0) or 0)
    turn_text = str(pending.get("turn_text") or "")
    computer_calls = copy.deepcopy(pending.get("computer_calls") or [])

    if decision is False:
        ps["pending_turn"] = None
        ps["turn_index"] = max(int(ps.get("turn_index", 0) or 0), turn_number)
        return {
            "provider_state": ps,
            "route": "completed",
            "status": "completed",
            "final_text": "Agent terminated: safety confirmation denied.",
            "session_data": _session_running(session_data, status="completed", final_text="Agent terminated: safety confirmation denied."),
        }

    acknowledgements_by_call: list[list[dict[str, Any]] | None] = []
    approval_reasons: list[str] = []
    for computer_call in computer_calls:
        pending_checks = [
            _to_plain_dict(check)
            for check in (computer_call.get("pending_safety_checks") or [])
        ]
        if pending_checks:
            acknowledged_safety_checks = []
            for check in pending_checks:
                ack = {"id": check["id"]}
                if check.get("code") is not None:
                    ack["code"] = check["code"]
                if check.get("message") is not None:
                    ack["message"] = check["message"]
                acknowledged_safety_checks.append(ack)
            approval_reasons.extend(
                check.get("message") or check.get("code") or "Safety acknowledgement required"
                for check in pending_checks
            )
            acknowledgements_by_call.append(acknowledged_safety_checks)
            continue
        acknowledgements_by_call.append(None)

    if approval_reasons and decision is None:
        ps["pending_turn"] = pending
        return {
            "provider_state": ps,
            "route": "approval",
            "status": "awaiting_approval",
            "pending_approval": {
                "origin": "safety",
                "explanation": " | ".join(str(reason) for reason in approval_reasons),
            },
            "session_data": _session_running(session_data, status="paused"),
        }

    pending["acknowledged_safety_checks_by_call"] = acknowledgements_by_call
    ps["pending_turn"] = pending
    return {
        "provider_state": ps,
        "pending_action_batch": serialize_pending_action_batch(turn_number, turn_text, computer_calls),
        "route": "tool_batch",
        "status": "running",
        "turn_count": turn_number,
        "last_model_text": turn_text,
        "session_data": _session_running(session_data),
    }


async def _dispatch_openai_pending_turn(state: dict[str, Any], engine: Any, executor: Any, on_log) -> dict[str, Any]:
    client = engine._client
    session_data = _session_data(state)
    ps = copy.deepcopy(state.get("provider_state") or {})
    pending = copy.deepcopy(ps.get("pending_turn") or {})
    if not pending:
        latest_output = copy.deepcopy(state.get("pending_action_batch") or {})
        return {
            "provider_state": ps,
            "latest_executor_output": latest_output,
            "route": "verifier",
            "status": "running",
            "session_data": _session_running(session_data),
        }

    turn_number = int(pending.get("turn", 0) or 0)
    turn_text = str(pending.get("turn_text") or "")
    response_output = copy.deepcopy(pending.get("response_output") or [])
    computer_calls = copy.deepcopy(pending.get("computer_calls") or [])
    tool_outputs = copy.deepcopy(pending.get("tool_outputs") or [])
    next_scale = float(pending.get("next_screenshot_scale", ps.get("current_screenshot_scale", 1.0)) or 1.0)
    acknowledgements_by_call = list(pending.get("acknowledged_safety_checks_by_call") or [])

    results: list[CUActionResult] = []
    last_screenshot_ref: str | None = None
    for call_index, computer_call in enumerate(computer_calls):
        actions = list(computer_call.get("actions") or [])
        if not actions and computer_call.get("action") is not None:
            actions = [computer_call["action"]]
        for index, action in enumerate(actions):
            action_id = _openai_action_id(
                turn_number=turn_number,
                call_index=call_index,
                computer_call=computer_call,
                action_index=index,
                action=action,
            )
            result = await client._execute_openai_action(
                action,
                _IdempotentActionExecutor(executor, action_id),
            )
            results.append(result)
            if index != len(actions) - 1:
                await asyncio.sleep(0.12)

        screenshot_bytes = await executor.capture_screenshot()
        prepared, next_scale = _prepare_openai_computer_screenshot(screenshot_bytes, on_log=on_log)
        last_screenshot_ref = _store_image_bytes(prepared)
        tool_outputs.append(
            _externalize_png_blobs(
                _build_openai_computer_call_output(
                    computer_call.get("call_id"),
                    _load_image_b64(last_screenshot_ref),
                    acknowledged_safety_checks=(acknowledgements_by_call[call_index] if call_index < len(acknowledgements_by_call) else None),
                )
            )
        )

    ps["pending_turn"] = None
    ps["current_screenshot_scale"] = next_scale
    ps["saw_computer_action"] = True
    ps["next_input"] = _externalize_png_blobs(response_output + tool_outputs)
    latest_output = serialize_tool_batch(turn_number, turn_text, results, last_screenshot_ref)
    latest_output["native_actions"] = copy.deepcopy(computer_calls)
    return {
        "provider_state": ps,
        "latest_executor_output": latest_output,
        "route": "verifier",
        "status": "running",
        "turn_count": turn_number,
        "last_model_text": turn_text,
        "last_screenshot_ref": last_screenshot_ref,
        "session_data": _session_running(session_data),
    }


async def _step_openai(state: dict[str, Any], engine: Any, executor: Any, on_log) -> dict[str, Any]:
    client = engine._client
    session_data = _session_data(state)
    ps = await _ensure_openai_state(state, engine, executor, on_log)
    if ps.get("pending_turn"):
        return await _continue_openai_turn(state, engine, executor, ps, on_log)

    turn_index = int(ps.get("turn_index", 0) or 0)
    turn_limit = int(state.get("max_steps", 25) or 25)
    if turn_index >= turn_limit:
        text = f"OpenAI CU reached the turn limit ({turn_limit}) without a final response."
        return {
            "provider_state": ps,
            "route": "completed",
            "status": "completed",
            "final_text": text,
            "session_data": _session_running(session_data, status="completed", final_text=text),
        }

    next_input = _rehydrate_png_blobs(copy.deepcopy(ps.get("next_input") or []))
    client._vector_store_id = ps.get("vector_store_id")
    client._current_screenshot_scale = float(ps.get("current_screenshot_scale", 1.0) or 1.0)
    include_fields = ["reasoning.encrypted_content"]
    if state.get("use_builtin_search"):
        include_fields.append("web_search_call.action.sources")
    request = {
        "model": client._model,
        "input": next_input,
        "tools": client._build_tools(getattr(executor, "screen_width", 0) or 0, getattr(executor, "screen_height", 0) or 0, on_log=on_log if turn_index == 0 else None),
        "parallel_tool_calls": False,
        "include": include_fields,
        "reasoning": {"effort": client._reasoning_effort},
        "store": False,
        "truncation": "auto",
    }
    if client._system_prompt:
        request["instructions"] = client._system_prompt
    response = await client._create_response(on_log=on_log, **request)
    response_error = getattr(response, "error", None)
    if response_error:
        raise RuntimeError(getattr(response_error, "message", str(response_error)))
    output_items = list(getattr(response, "output", []) or [])
    output_plain = [copy.deepcopy(item) if isinstance(item, dict) else _to_plain_dict(item) for item in output_items]
    turn_text = getattr(response, "output_text", "") or ""
    if not turn_text:
        for item in output_plain:
            if item.get("type") != "message":
                continue
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text" and part.get("text"):
                    turn_text = "\n\n".join(filter(None, [turn_text, str(part["text"]).strip()]))
    turn_number = turn_index + 1
    ps["turn_index"] = turn_number
    computer_calls = [item for item in output_plain if item.get("type") == "computer_call"]

    if not computer_calls:
        if (state.get("use_builtin_search") or ps.get("vector_store_id") is not None) and not ps.get("saw_computer_action") and not ps.get("nudged_for_computer_use"):
            refreshed = await executor.capture_screenshot()
            prepared, scale = _prepare_openai_computer_screenshot(refreshed, on_log=on_log)
            ref = _store_image_bytes(prepared)
            next_items = [_sanitize_openai_response_item_for_replay(item) for item in output_plain]
            next_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Use any retrieved search/file context to continue, but do not stop yet. "
                                "This app's purpose is computer use: the task is not complete until you perform "
                                "the requested action with the computer tool on the current screen. "
                                "Continue with computer actions now."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{_load_image_b64(ref)}",
                            "detail": "original",
                        },
                    ],
                }
            )
            ps["current_screenshot_scale"] = scale
            ps["next_input"] = _externalize_png_blobs(next_items)
            ps["nudged_for_computer_use"] = True
            return {
                "provider_state": ps,
                "route": "model_turn",
                "status": "running",
                "session_data": _session_running(session_data),
            }
        final_text = _append_source_footer(
            turn_text or "OpenAI completed without a final message.",
            _extract_openai_sources(output_plain),
        )
        ps["next_input"] = _externalize_png_blobs([_sanitize_openai_response_item_for_replay(item) for item in output_plain])
        return {
            "provider_state": ps,
            "pending_action_batch": serialize_pending_action_batch(turn_number, turn_text, [], terminal_text=final_text),
            "route": "tool_batch",
            "status": "running",
            "turn_count": turn_number,
            "last_model_text": turn_text,
            "session_data": _session_running(session_data),
        }

    ps["pending_turn"] = {
        "turn": turn_number,
        "turn_text": turn_text,
        "response_output": [_sanitize_openai_response_item_for_replay(item) for item in output_plain],
        "computer_calls": computer_calls,
        "tool_outputs": [],
        "next_screenshot_scale": float(ps.get("current_screenshot_scale", 1.0) or 1.0),
    }
    return await _continue_openai_turn(state, engine, executor, ps, on_log)


async def _step_claude(state: dict[str, Any], engine: Any, executor: Any, on_log) -> dict[str, Any]:
    client = engine._client
    session_data = _session_data(state)
    ps = copy.deepcopy(state.get("provider_state") or {})
    if ps.get("provider") != "anthropic":
        ps = {
            "provider": "anthropic",
            "turn_index": 0,
            "messages": None,
            "saw_computer_action": False,
            "nudged_for_computer_use": False,
        }

    scale = _scale_for_claude(client, executor, on_log)
    scaled_w = int(executor.screen_width * scale)
    scaled_h = int(executor.screen_height * scale)
    await client._ensure_anthropic_web_search_enabled(on_log)
    tools = client._build_tools(scaled_w, scaled_h)

    if ps.get("messages") is None:
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            text = "Error: Could not capture initial screenshot"
            return {
                "provider_state": ps,
                "route": "completed",
                "status": "completed",
                "final_text": text,
                "session_data": _session_running(session_data, status="completed", final_text=text),
            }
        screenshot_bytes, _, _ = resize_screenshot_for_claude(screenshot_bytes, scale)
        screenshot_ref = _store_image_bytes(screenshot_bytes)
        document_blocks, inline_pairs = await client._prepare_attached_files(on_log)
        goal_text = str(state.get("task", ""))
        if inline_pairs:
            inline_sections = "\n\n".join(
                f"<attached_file name=\"{name}\">\n{text}\n</attached_file>"
                for name, text in inline_pairs
            )
            goal_text = (
                f"{inline_sections}\n\n"
                f"The above attached files are provided as plain-text context. User goal:\n\n{goal_text}"
            )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": goal_text},
                    *document_blocks,
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _IMAGE_PNG,
                            "data": _load_image_b64(screenshot_ref),
                        },
                    },
                ],
            }
        ]
        ps["messages"] = _externalize_png_blobs(messages)

    turn_index = int(ps.get("turn_index", 0) or 0)
    turn_limit = int(state.get("max_steps", 25) or 25)
    if turn_index >= turn_limit:
        text = f"Claude CU reached the turn limit ({turn_limit}) without a final response."
        return {
            "provider_state": ps,
            "route": "completed",
            "status": "completed",
            "final_text": text,
            "session_data": _session_running(session_data, status="completed", final_text=text),
        }

    messages = _rehydrate_png_blobs(copy.deepcopy(ps.get("messages") or []))
    _prune_claude_context(messages, _CONTEXT_PRUNE_KEEP_RECENT)
    if client._tool_version == "computer_20251124":
        thinking_cfg: dict[str, Any] = {"type": "adaptive"}
    else:
        thinking_cfg = {"type": "enabled", "budget_tokens": 4096}
    betas = [client._beta_flag]
    if state.get("attached_files"):
        betas.append("files-api-2025-04-14")
    response = await _call_with_retry(
        lambda: client._client.beta.messages.create(
            model=client._model,
            max_tokens=_CLAUDE_MAX_TOKENS,
            system=client._system_prompt,
            tools=tools,
            messages=messages,
            betas=betas,
            thinking=thinking_cfg,
        ),
        provider="anthropic",
        on_log=on_log,
    )

    assistant_content = list(getattr(response, "content", []) or [])
    assistant_plain = [copy.deepcopy(block) if isinstance(block, dict) else _to_plain_dict(block) for block in assistant_content]
    messages.append({"role": "assistant", "content": assistant_plain})
    tool_uses = [block for block in assistant_plain if block.get("type") == "tool_use"]
    turn_text = _plain_claude_text(assistant_plain)
    stop = getattr(response, "stop_reason", None)
    turn_number = turn_index + 1
    ps["turn_index"] = turn_number

    if stop == "pause_turn":
        ps["messages"] = _externalize_png_blobs(messages)
        return {
            "provider_state": ps,
            "route": "model_turn",
            "status": "running",
            "session_data": _session_running(session_data),
        }

    if stop in {"refusal", "model_context_window_exceeded", "max_tokens", "stop_sequence"} or stop == "end_turn" or not tool_uses:
        if (state.get("use_builtin_search") or state.get("attached_files")) and not ps.get("saw_computer_action") and not ps.get("nudged_for_computer_use") and (stop == "end_turn" or not tool_uses):
            try:
                refreshed = await executor.capture_screenshot()
            except Exception:
                refreshed = b""
            if refreshed:
                refreshed, _, _ = resize_screenshot_for_claude(refreshed, scale)
                screenshot_ref = _store_image_bytes(refreshed)
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Use any retrieved search context to continue, but do not stop yet. "
                                    "This app's purpose is computer use: the task is not complete until you perform "
                                    "the requested action with the computer tool on the current screen. "
                                    "Continue with computer actions now."
                                ),
                            },
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": _IMAGE_PNG,
                                    "data": _load_image_b64(screenshot_ref),
                                },
                            },
                        ],
                    }
                )
                ps["messages"] = _externalize_png_blobs(messages)
                ps["nudged_for_computer_use"] = True
                return {
                    "provider_state": ps,
                    "route": "model_turn",
                    "status": "running",
                    "session_data": _session_running(session_data),
                }
        if stop == "refusal":
            final_text = turn_text or "Model refused to continue (safety refusal)."
        elif stop == "model_context_window_exceeded":
            final_text = "Error: context window exceeded. Task too long."
        elif stop in {"max_tokens", "stop_sequence"}:
            final_text = turn_text or f"Response truncated (stop_reason={stop})."
        else:
            final_text = _append_source_footer(turn_text, _extract_claude_sources(assistant_plain))
        ps["messages"] = _externalize_png_blobs(messages)
        return {
            "provider_state": ps,
            "pending_action_batch": serialize_pending_action_batch(turn_number, turn_text or final_text, [], terminal_text=final_text),
            "route": "tool_batch",
            "status": "running",
            "turn_count": turn_number,
            "last_model_text": turn_text or final_text,
            "session_data": _session_running(session_data),
        }

    ps["pending_turn"] = {
        "turn": turn_number,
        "turn_text": turn_text,
        "tool_uses": copy.deepcopy(tool_uses),
        "scale_factor": scale,
    }
    ps["messages"] = _externalize_png_blobs(messages)
    return {
        "provider_state": ps,
        "pending_action_batch": serialize_pending_action_batch(turn_number, turn_text, tool_uses),
        "route": "tool_batch",
        "status": "running",
        "turn_count": turn_number,
        "last_model_text": turn_text,
        "session_data": _session_running(session_data),
    }


async def _dispatch_claude_pending_turn(state: dict[str, Any], engine: Any, executor: Any, on_log) -> dict[str, Any]:
    del on_log
    client = engine._client
    session_data = _session_data(state)
    ps = copy.deepcopy(state.get("provider_state") or {})
    pending = copy.deepcopy(ps.get("pending_turn") or {})
    if not pending:
        return {
            "provider_state": ps,
            "latest_executor_output": copy.deepcopy(state.get("pending_action_batch") or {}),
            "route": "verifier",
            "status": "running",
            "session_data": _session_running(session_data),
        }

    tool_uses = copy.deepcopy(pending.get("tool_uses") or [])
    scale = float(pending.get("scale_factor", 1.0) or 1.0)
    turn_number = int(pending.get("turn", 0) or 0)
    turn_text = str(pending.get("turn_text") or "")
    messages = _rehydrate_png_blobs(copy.deepcopy(ps.get("messages") or []))

    results: list[CUActionResult] = []
    tool_result_parts: list[dict[str, Any]] = []
    screenshot_ref: str | None = None
    for tool_index, tool_use in enumerate(tool_uses):
        action_id = _claude_action_id(turn_number=turn_number, tool_index=tool_index, tool_use=tool_use)
        result = await client._execute_claude_action(
            tool_use.get("input") or {},
            _IdempotentActionExecutor(executor, action_id),
            scale_factor=scale,
        )
        results.append(result)
        screenshot_bytes = await executor.capture_screenshot()
        screenshot_bytes, _, _ = resize_screenshot_for_claude(screenshot_bytes, scale)
        screenshot_ref = _store_image_bytes(screenshot_bytes)
        content: list[dict[str, Any]] = []
        if result.error:
            content.append({"type": "text", "text": f"Error: {result.error}"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "ref",
                    "media_type": _IMAGE_PNG,
                    "image_ref": screenshot_ref,
                },
            }
        )
        tool_result_parts.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_use.get("id"),
                "content": content,
            }
        )

    messages.append({"role": "user", "content": tool_result_parts})
    ps["messages"] = _externalize_png_blobs(messages)
    ps["pending_turn"] = None
    ps["saw_computer_action"] = True
    latest_output = serialize_tool_batch(turn_number, turn_text, results, screenshot_ref)
    latest_output["native_actions"] = copy.deepcopy(tool_uses)
    return {
        "provider_state": ps,
        "latest_executor_output": latest_output,
        "route": "verifier",
        "status": "running",
        "turn_count": turn_number,
        "last_model_text": turn_text,
        "last_screenshot_ref": screenshot_ref,
        "session_data": _session_running(session_data),
    }


async def _step_gemini(state: dict[str, Any], engine: Any, executor: Any, on_log) -> dict[str, Any]:
    client = engine._client
    session_data = _session_data(state)
    ps = copy.deepcopy(state.get("provider_state") or {})
    if ps.get("provider") != "google":
        ps = {
            "provider": "google",
            "turn_index": 0,
            "contents": None,
            "last_completion_payload": None,
            "saw_computer_action": False,
            "nudged_for_computer_use": False,
            "pending_turn": None,
        }

    client._last_completion_payload = ps.get("last_completion_payload")

    if ps.get("contents") is None:
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            text = "Error: Could not capture initial screenshot"
            return {
                "provider_state": ps,
                "route": "completed",
                "status": "completed",
                "final_text": text,
                "session_data": _session_running(session_data, status="completed", final_text=text),
            }
        contents = [
            client._types.Content(
                role="user",
                parts=[
                    client._types.Part(text=client._compose_initial_goal_text(str(state.get("task", "")))),
                    client._types.Part.from_bytes(data=screenshot_bytes, mime_type=_IMAGE_PNG),
                ],
            )
        ]
        ps["contents"] = _serialize_gemini_contents(contents)

    turn_index = int(ps.get("turn_index", 0) or 0)
    turn_limit = int(state.get("max_steps", 25) or 25)
    if turn_index >= turn_limit:
        text = f"Gemini CU reached the turn limit ({turn_limit}) without a final response."
        return {
            "provider_state": ps,
            "route": "completed",
            "status": "completed",
            "final_text": text,
            "session_data": _session_running(session_data, status="completed", final_text=text),
        }

    if ps.get("pending_turn"):
        return await _continue_gemini_turn(state, engine, executor, ps, on_log)

    contents = _rehydrate_gemini_contents(client, copy.deepcopy(ps.get("contents") or []))
    _prune_gemini_context(contents, client._max_history_turns)
    config = client._build_config()
    try:
        response = await _call_with_retry(
            lambda: client._generate(contents=contents, config=config),
            provider="google",
            on_log=on_log,
        )
    except Exception as api_err:
        text = f"Gemini API error: {api_err}"
        return {
            "provider_state": ps,
            "route": "completed",
            "status": "completed",
            "final_text": text,
            "session_data": _session_running(session_data, status="completed", final_text=text),
        }
    if not getattr(response, "candidates", None):
        try:
            retry_ss = await executor.capture_screenshot()
        except Exception:
            retry_ss = b""
        if retry_ss:
            contents.append(
                client._types.Content(
                    role="user",
                    parts=[
                        client._types.Part(text="Please continue using the computer_use tools to complete the task. Here is the current screen."),
                        client._types.Part.from_bytes(data=retry_ss, mime_type=_IMAGE_PNG),
                    ],
                )
            )
        response = await _call_with_retry(
            lambda: client._generate(contents=contents, config=config),
            provider="google",
            on_log=on_log,
            attempts=2,
        )
        if not getattr(response, "candidates", None):
            text = "Error: Gemini returned no candidates (after retry)"
            return {
                "provider_state": ps,
                "route": "completed",
                "status": "completed",
                "final_text": text,
                "session_data": _session_running(session_data, status="completed", final_text=text),
            }

    candidate = response.candidates[0]
    contents.append(candidate.content)
    candidate_plain = _to_plain_dict(candidate.content)
    function_calls = []
    for part in candidate_plain.get("parts", []) or []:
        fc = part.get("function_call")
        if isinstance(fc, dict):
            function_calls.append(fc)
    turn_text = " ".join(str(part.get("text")) for part in candidate_plain.get("parts", []) or [] if part.get("text"))
    turn_number = turn_index + 1
    ps["turn_index"] = turn_number

    if not function_calls:
        if state.get("use_builtin_search") and not ps.get("saw_computer_action") and not ps.get("nudged_for_computer_use"):
            try:
                retry_ss = await executor.capture_screenshot()
            except Exception:
                retry_ss = b""
            if retry_ss:
                contents.append(
                    client._types.Content(
                        role="user",
                        parts=[
                            client._types.Part(
                                text=(
                                    "Use any retrieved search/file context to continue, but do not stop yet. "
                                    "This app's purpose is computer use: the task is not complete until you perform "
                                    "the requested action with the computer_use tool on the current screen. "
                                    "Continue with computer actions now."
                                )
                            ),
                            client._types.Part.from_bytes(data=retry_ss, mime_type=_IMAGE_PNG),
                        ],
                    )
                )
            ps["contents"] = _serialize_gemini_contents(contents)
            ps["nudged_for_computer_use"] = True
            return {
                "provider_state": ps,
                "route": "model_turn",
                "status": "running",
                "session_data": _session_running(session_data),
            }
        grounding_payload = _extract_gemini_grounding_payload(response)
        ps["contents"] = _serialize_gemini_contents(contents)
        ps["last_completion_payload"] = {"gemini_grounding": grounding_payload} if grounding_payload else None
        final_text = turn_text
        return {
            "provider_state": ps,
            "pending_action_batch": serialize_pending_action_batch(turn_number, turn_text, [], terminal_text=final_text),
            "route": "tool_batch",
            "status": "running",
            "turn_count": turn_number,
            "last_model_text": turn_text,
            "session_data": _session_running(session_data, gemini_grounding=grounding_payload),
        }

    ps["contents"] = _serialize_gemini_contents(contents)
    ps["pending_turn"] = {
        "turn": turn_number,
        "turn_text": turn_text,
        "function_calls": function_calls,
        "approved_safety_indices": [],
    }
    return await _continue_gemini_turn({**state, "provider_state": ps, "approval_decision": state.get("approval_decision")}, engine, executor, ps, on_log)


async def _continue_gemini_turn(state: dict[str, Any], engine: Any, executor: Any, ps: dict[str, Any], on_log) -> dict[str, Any]:
    del executor, on_log
    session_data = _session_data(state)
    pending = copy.deepcopy(ps.get("pending_turn") or {})
    decision = state.get("approval_decision")
    turn_number = int(pending.get("turn", 0) or 0)
    turn_text = str(pending.get("turn_text") or "")
    function_calls = copy.deepcopy(pending.get("function_calls") or [])

    approval_messages: list[str] = []
    approved_indices: list[int] = []
    for idx, fc in enumerate(function_calls):
        args = dict(fc.get("args") or {})
        safety_decision = args.get("safety_decision")
        if not isinstance(safety_decision, dict) or safety_decision.get("decision") != "require_confirmation":
            continue
        approval_messages.append(str(safety_decision.get("explanation", "")).strip() or "Safety acknowledgement required")
        approved_indices.append(idx)

    if approval_messages and decision is None:
        ps["pending_turn"] = pending
        return {
            "provider_state": ps,
            "route": "approval",
            "status": "awaiting_approval",
            "pending_approval": {
                "origin": "safety",
                "explanation": " | ".join(approval_messages),
            },
            "session_data": _session_running(session_data, status="paused"),
        }
    if approval_messages and decision is False:
        ps["pending_turn"] = None
        return {
            "provider_state": ps,
            "route": "completed",
            "status": "completed",
            "final_text": "Agent terminated: safety confirmation denied.",
            "session_data": _session_running(session_data, status="completed", final_text="Agent terminated: safety confirmation denied."),
        }

    pending["approved_safety_indices"] = approved_indices if approval_messages else []
    ps["pending_turn"] = pending
    return {
        "provider_state": ps,
        "pending_action_batch": serialize_pending_action_batch(turn_number, turn_text, function_calls),
        "route": "tool_batch",
        "status": "running",
        "turn_count": turn_number,
        "last_model_text": turn_text,
        "session_data": _session_running(session_data),
    }


async def _dispatch_gemini_pending_turn(state: dict[str, Any], engine: Any, executor: Any, on_log) -> dict[str, Any]:
    del on_log
    client = engine._client
    session_data = _session_data(state)
    ps = copy.deepcopy(state.get("provider_state") or {})
    pending = copy.deepcopy(ps.get("pending_turn") or {})
    if not pending:
        return {
            "provider_state": ps,
            "latest_executor_output": copy.deepcopy(state.get("pending_action_batch") or {}),
            "route": "verifier",
            "status": "running",
            "session_data": _session_running(session_data),
        }

    turn_number = int(pending.get("turn", 0) or 0)
    turn_text = str(pending.get("turn_text") or "")
    function_calls = copy.deepcopy(pending.get("function_calls") or [])
    approved_indices = set(int(value) for value in (pending.get("approved_safety_indices") or []))
    contents = _rehydrate_gemini_contents(client, copy.deepcopy(ps.get("contents") or []))

    results: list[CUActionResult] = []
    for idx, fc in enumerate(function_calls):
        args = dict(fc.get("args") or {})
        safety_confirmed = False
        if idx in approved_indices and "safety_decision" in args:
            args.pop("safety_decision", None)
            safety_confirmed = True
        elif "safety_decision" in args:
            args.pop("safety_decision", None)
        action_id = _gemini_action_id(turn_number=turn_number, call_index=idx, function_call=fc)
        result = await _IdempotentActionExecutor(executor, action_id).execute(fc.get("name"), args)
        if safety_confirmed:
            result.safety_decision = SafetyDecision.REQUIRE_CONFIRMATION
        results.append(result)

    try:
        screenshot_bytes = await executor.capture_screenshot()
    except Exception:
        screenshot_bytes = b""
    screenshot_ref = _store_image_bytes(screenshot_bytes) if screenshot_bytes else None

    current_url = executor.get_current_url()
    function_responses = []
    for result in results:
        resp_data: dict[str, Any] = {"url": current_url}
        if result.error:
            resp_data["error"] = result.error
        if result.safety_decision == SafetyDecision.REQUIRE_CONFIRMATION:
            resp_data["safety_acknowledgement"] = "true"
        for key, value in result.extra.items():
            if isinstance(value, tuple):
                resp_data[key] = list(value)
            elif isinstance(value, (str, int, float, bool, type(None), list, dict)):
                resp_data[key] = value
            else:
                resp_data[key] = str(value)
        fr_kwargs: dict[str, Any] = {"name": result.name, "response": resp_data}
        if screenshot_bytes and len(screenshot_bytes) >= 100:
            fr_kwargs["parts"] = [
                client._types.FunctionResponsePart(
                    inline_data=client._types.FunctionResponseBlob(
                        mime_type=_IMAGE_PNG,
                        data=screenshot_bytes,
                    )
                )
            ]
        function_responses.append(client._types.FunctionResponse(**fr_kwargs))

    contents.append(
        client._types.Content(
            role="user",
            parts=[client._types.Part(function_response=fr) for fr in function_responses],
        )
    )
    ps["contents"] = _serialize_gemini_contents(contents)
    ps["pending_turn"] = None
    ps["turn_index"] = turn_number
    ps["saw_computer_action"] = True

    latest_output = serialize_tool_batch(turn_number, turn_text, results, screenshot_ref)
    latest_output["native_actions"] = copy.deepcopy(function_calls)
    return {
        "provider_state": ps,
        "latest_executor_output": latest_output,
        "route": "verifier",
        "status": "running",
        "turn_count": turn_number,
        "last_model_text": turn_text,
        "last_screenshot_ref": screenshot_ref,
        "session_data": _session_running(session_data),
    }


async def advance_provider_turn(state: dict[str, Any], *, on_log=None) -> dict[str, Any]:
    engine = _build_engine(state)
    executor = engine._build_executor()
    try:
        provider = str(state.get("provider", "")).lower()
        if provider == "openai":
            return await _step_openai(state, engine, executor, on_log)
        if provider == "anthropic":
            return await _step_claude(state, engine, executor, on_log)
        if provider == "google":
            return await _step_gemini(state, engine, executor, on_log)
        raise ValueError(f"Unsupported provider {provider!r}")
    finally:
        if hasattr(executor, "aclose"):
            try:
                await executor.aclose()
            except Exception:
                pass


async def dispatch_pending_action_batch(state: dict[str, Any], *, on_log=None) -> dict[str, Any]:
    engine = _build_engine(state)
    executor = engine._build_executor()
    try:
        provider = str(state.get("provider", "")).lower()
        if provider == "openai":
            return await _dispatch_openai_pending_turn(state, engine, executor, on_log)
        if provider == "anthropic":
            return await _dispatch_claude_pending_turn(state, engine, executor, on_log)
        if provider == "google":
            return await _dispatch_gemini_pending_turn(state, engine, executor, on_log)
        raise ValueError(f"Unsupported provider {provider!r}")
    finally:
        if hasattr(executor, "aclose"):
            try:
                await executor.aclose()
            except Exception:
                pass


async def cleanup_provider_resources(state: dict[str, Any], *, on_log=None) -> None:
    engine = _build_engine(state)
    client = engine._client
    ps = state.get("provider_state") or {}
    provider = str(state.get("provider", "")).lower()
    if provider == "openai":
        client._vector_store_id = ps.get("vector_store_id")
        await client._cleanup_vector_store(on_log=on_log)
      