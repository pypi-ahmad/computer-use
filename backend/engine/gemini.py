"""Gemini Computer Use client — split out of ``backend.engine`` (Q2).

The class body lives here; ``backend.engine`` re-exports it so imports
like ``from backend.engine import GeminiCUClient`` keep working.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from io import BytesIO
from typing import Any

from backend.engine import (
    _IMAGE_PNG,
    DEFAULT_TURN_LIMIT,
    CUTurnRecord,
    Environment,
    ModelTurnStarted,
    RunCompleted,
    SafetyRequired,
    ToolBatchCompleted,
    TurnEvent,
    _call_with_retry,
    _invoke_safety,
    _to_plain_dict,
    validate_builtin_search_config,
)
from backend.executor import ActionExecutor, CUActionResult, SafetyDecision
from backend.infra.mcp_fetch import (
    MCP_FETCH_TOOL_NAME,
    fetch_url_for_model,
    gemini_mcp_fetch_tool,
    mcp_fetch_instruction,
)

logger = logging.getLogger(__name__)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_GEMINI_UI_ACTION_TASK_RE = re.compile(
    r"\b(open|launch|start|search|click|type|enter|press|navigate|visit|"
    r"scroll|fill|select|choose|upload|download|create|delete|rename|"
    r"move|drag|paste|copy)\b|\bgo\s+to\b",
    re.IGNORECASE,
)


def _gemini_final_needs_computer_use(goal: str, final_text: str) -> bool:
    """Return True when a UI task ended before any computer_use action."""
    _ = final_text
    return bool(_GEMINI_UI_ACTION_TASK_RE.search(goal or ""))


# ---------------------------------------------------------------------------
# Gemini Computer Use Client
# ---------------------------------------------------------------------------


# Q2: ``_to_plain_dict`` was duplicated here verbatim; it now lives once in
# backend.engine and is imported above.


def _extract_gemini_grounding_payload(response: Any) -> dict[str, Any] | None:
    """Normalize Gemini Google Search grounding metadata for the UI."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None

    candidate = _to_plain_dict(candidates[0])
    grounding = candidate.get("grounding_metadata") or candidate.get("groundingMetadata") or {}
    if not isinstance(grounding, dict):
        grounding = _to_plain_dict(grounding)
    if not grounding:
        return None

    search_entry = grounding.get("search_entry_point") or grounding.get("searchEntryPoint") or {}
    if not isinstance(search_entry, dict):
        search_entry = _to_plain_dict(search_entry)
    rendered_content = str(
        search_entry.get("renderedContent") or search_entry.get("rendered_content") or ""
    ).strip()

    normalized_chunks: list[dict[str, Any]] = []
    for raw_chunk in grounding.get("grounding_chunks") or grounding.get("groundingChunks") or []:
        chunk = raw_chunk if isinstance(raw_chunk, dict) else _to_plain_dict(raw_chunk)
        web = chunk.get("web") or {}
        if not isinstance(web, dict):
            web = _to_plain_dict(web)
        uri = str(web.get("uri") or "").strip()
        if not uri:
            continue
        title = str(web.get("title") or uri).strip() or uri
        normalized_chunks.append({"web": {"uri": uri, "title": title}})

    normalized_supports: list[dict[str, Any]] = []
    for raw_support in (
        grounding.get("grounding_supports") or grounding.get("groundingSupports") or []
    ):
        support = raw_support if isinstance(raw_support, dict) else _to_plain_dict(raw_support)
        segment = support.get("segment") or {}
        if not isinstance(segment, dict):
            segment = _to_plain_dict(segment)

        start_index = segment.get("startIndex")
        if start_index is None:
            start_index = segment.get("start_index")
        end_index = segment.get("endIndex")
        if end_index is None:
            end_index = segment.get("end_index")
        try:
            start_index = int(start_index) if start_index is not None else None
        except (TypeError, ValueError):
            start_index = None
        try:
            end_index = int(end_index) if end_index is not None else None
        except (TypeError, ValueError):
            end_index = None

        indices_raw = support.get("grounding_chunk_indices")
        if indices_raw is None:
            indices_raw = support.get("groundingChunkIndices")
        indices: list[int] = []
        for value in indices_raw or []:
            try:
                idx = int(value)
            except (TypeError, ValueError):
                continue
            if idx >= 0 and idx not in indices:
                indices.append(idx)

        segment_payload: dict[str, Any] = {}
        if start_index is not None:
            segment_payload["startIndex"] = start_index
        if end_index is not None:
            segment_payload["endIndex"] = end_index
        segment_text = str(segment.get("text") or "").strip()
        if segment_text:
            segment_payload["text"] = segment_text

        if segment_payload or indices:
            normalized_supports.append(
                {
                    "segment": segment_payload,
                    "groundingChunkIndices": indices,
                }
            )

    web_search_queries = [
        str(query).strip()
        for query in grounding.get("web_search_queries") or grounding.get("webSearchQueries") or []
        if str(query).strip()
    ]

    payload: dict[str, Any] = {}
    if rendered_content:
        payload["renderedContent"] = rendered_content
    if normalized_chunks:
        payload["groundingChunks"] = normalized_chunks
    if normalized_supports:
        payload["groundingSupports"] = normalized_supports
    if web_search_queries:
        payload["webSearchQueries"] = web_search_queries
    return payload or None


def _prune_gemini_context(contents: list[Any], max_history_turns: int) -> None:
    """Drop old Gemini history turns atomically while keeping kept turns intact.

    Gemini tool-calling replay docs require returning all parts, including
    all fields they contain, on each turn:
    https://ai.google.dev/gemini-api/docs/tool-combination

    To preserve ``toolCall``, ``toolResponse``, ``functionCall``,
    ``functionResponse``, ``thoughtSignature``, ``id``, and ``tool_type``
    inside any retained turn, pruning drops entire older turns instead of
    rewriting individual parts. The most recent assistant turn is always
    preserved in full because its ``thoughtSignature`` is what the next
    prediction binds to. Callers running long sessions should tune
    ``max_history_turns`` upward and observe the latency/quality tradeoff.
    """
    if len(contents) <= max_history_turns:
        return

    keep_from = max(0, len(contents) - max_history_turns)
    keep_indexes: set[int] = set(range(keep_from, len(contents)))

    for idx in range(len(contents) - 1, -1, -1):
        if getattr(contents[idx], "role", None) == "model":
            keep_indexes.add(idx)
            break

    if len(keep_indexes) == len(contents):
        return

    contents[:] = [content for idx, content in enumerate(contents) if idx in keep_indexes]


class GeminiCUClient:
    """Gemini Interactions API Computer Use client."""

    def __init__(
        self,
        api_key: str | None = None,
        # Lifecycle watchdog/checklist: see .github/workflows/gemini-changelog-watchdog.yml and docs/gemini-successor-evaluation.md before changing this model.
        model: str = "gemini-3.7-flash",
        environment: Environment = Environment.DESKTOP,
        excluded_actions: list[str] | None = None,
        system_instruction: str | None = None,
        use_builtin_search: bool = False,
        # Bound replay depth without stripping any fields from retained turns.
        # Gemini tool-calling replay docs require replaying all parts/fields
        # intact on each kept turn:
        # https://ai.google.dev/gemini-api/docs/tool-combination
        max_history_turns: int = 10,
        # Reference files are intentionally unsupported for Gemini CU:
        # Google's File Search docs say File Search cannot be combined
        # with other tools, and Computer Use is another tool.
        attached_file_ids: list[str] | None = None,
        credentials: Any | None = None,
        quota_project_id: str | None = None,
        thinking_level: str | None = None,
    ):
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError as exc:
            raise ImportError(
                "google-genai is required. Install: pip install google-genai"
            ) from exc

        self._genai = genai
        self._types = genai_types
        if bool(api_key) == bool(credentials):
            raise ValueError("Gemini requires exactly one of api_key or credentials.")
        self._client = genai.Client(api_key=api_key) if api_key else None
        self._oauth_credentials = credentials
        self._quota_project_id = quota_project_id
        self._model = model
        self._usage = {"input_tokens": 0, "output_tokens": 0}
        self._environment = environment
        self._excluded = excluded_actions or []
        self._system_instruction = system_instruction
        level = (thinking_level or "").strip().lower()
        self._thinking_level = level if level in {"low", "medium", "high"} else None
        max_history_turns = int(max_history_turns)
        if max_history_turns < 1:
            raise ValueError("Gemini max_history_turns must be >= 1.")
        self._max_history_turns = max_history_turns
        validate_builtin_search_config(
            provider="gemini",
            model=model,
            use_builtin_search=use_builtin_search,
        )
        # Toggle On: model may call mcp_fetch; host runs uvx mcp-server-fetch.
        self._use_builtin_search = bool(use_builtin_search)
        if attached_file_ids:
            raise ValueError(
                "Reference files are supported for OpenAI and Anthropic computer-use "
                "sessions only; Gemini File Search cannot be combined with Computer Use.",
            )
        self._last_completion_payload: dict[str, Any] | None = None

    async def _create_interaction(
        self,
        input_items: list[dict[str, Any]],
        *,
        previous_interaction_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        raw_env = getattr(self, "_environment", None)
        environment = getattr(raw_env, "value", raw_env)
        if environment not in {"desktop", "browser", "mobile"}:
            environment = "desktop"
        tool: dict[str, Any] = {
            "type": "computer_use",
            "environment": environment,
            "enable_prompt_injection_detection": True,
        }
        if self._excluded:
            tool["excluded_predefined_functions"] = self._excluded
        request: dict[str, Any] = {
            "model": self._model,
            "input": input_items,
            "tools": tools or self._interaction_tools(tool),
        }
        if self._system_instruction:
            request["system_instruction"] = self._system_instruction
        if previous_interaction_id:
            request["previous_interaction_id"] = previous_interaction_id
        thinking_level = getattr(self, "_thinking_level", None)
        if thinking_level:
            request["generation_config"] = {"thinking_level": thinking_level}
        if self._client is not None:
            return await self._client.aio.interactions.create(**request)

        credentials = self._oauth_credentials
        if not credentials.valid:
            from google.auth.transport.requests import Request

            await asyncio.to_thread(credentials.refresh, Request())
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
            "Api-Revision": "2026-05-20",
        }
        if self._quota_project_id:
            headers["x-goog-user-project"] = self._quota_project_id
        import httpx

        async with httpx.AsyncClient(timeout=120) as http:
            response = await http.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers=headers,
                json=request,
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _ensure_png(data: bytes) -> bytes:
        """Return PNG bytes. Official Computer Use results use image/png."""
        if data.startswith(_PNG_MAGIC):
            return data
        from PIL import Image

        try:
            image = Image.open(BytesIO(data))
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception:
            logger.warning("Screenshot is not PNG and could not be converted")
            return data

    @classmethod
    def _image_input(cls, data: bytes) -> dict[str, Any]:
        png = cls._ensure_png(data)
        return {
            "type": "image",
            "data": base64.standard_b64encode(png).decode(),
            "mime_type": _IMAGE_PNG,
        }

    @staticmethod
    def _interaction_outputs(interaction: Any) -> list[dict[str, Any]]:
        if isinstance(interaction, dict):
            outputs = interaction.get("outputs") or interaction.get("steps")
            return [dict(item) for item in (outputs or [])]
        outputs = getattr(interaction, "outputs", None)
        if outputs is None:
            outputs = getattr(interaction, "steps", None)
        return [_to_plain_dict(item) for item in (outputs or [])]

    def _interaction_tools(self, computer_tool: dict[str, Any]) -> list[dict[str, Any]]:
        tools = [computer_tool]
        if getattr(self, "_use_builtin_search", False):
            tools.append(gemini_mcp_fetch_tool())
        return tools

    def _compose_initial_goal_text(self, goal: str) -> str:
        if getattr(self, "_use_builtin_search", False):
            return f"{goal}\n\n{mcp_fetch_instruction()}"
        return goal

    async def iter_turns(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_log: Callable[[str, str], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Yield Gemini turn events for per-turn consumers.

        Wraps :meth:`_iter_turns_core` while preserving the
        ``agen.asend(bool)`` resume protocol the safety flow relies on.
        Values sent into this generator are forwarded verbatim to the
        inner generator and its yielded events are forwarded back.
        """
        inner = self._iter_turns_core(
            goal,
            executor,
            turn_limit=turn_limit,
            on_log=on_log,
        )
        try:
            sent: Any = None
            while True:
                try:
                    ev = await inner.asend(sent)
                except StopAsyncIteration:
                    return
                sent = yield ev
        finally:
            await inner.aclose()

    async def _iter_turns_core(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_log: Callable[[str, str], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Core iter_turns body — see :meth:`iter_turns` for the public contract.

        Safety confirmations are emitted as :class:`SafetyRequired`
        events and resumed via ``agen.asend(bool)``.
        """
        # Initial screenshot
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            yield RunCompleted(final_text="Error: Could not capture initial screenshot")
            return

        next_input = [
            {"type": "text", "text": self._compose_initial_goal_text(goal)},
            self._image_input(screenshot_bytes),
        ]
        previous_interaction_id: str | None = None

        _turn_start: float | None = None
        saw_computer_action = False
        nudged_for_computer_use = False

        for turn in range(turn_limit):
            if _turn_start is not None and on_log:
                on_log(
                    "info",
                    f"turn_duration_ms={int((time.monotonic() - _turn_start) * 1000)} provider=google model={self._model}",
                )
            _turn_start = time.monotonic()
            if on_log:
                on_log("info", f"Gemini CU turn {turn + 1}/{turn_limit}")

            try:
                interaction = await _call_with_retry(
                    lambda items=next_input,
                    previous_id=previous_interaction_id: self._create_interaction(
                        items,
                        previous_interaction_id=previous_id,
                    ),
                    provider="google",
                    on_log=on_log,
                )
            except Exception as api_err:
                error_msg = str(api_err)
                if on_log:
                    on_log("error", f"Gemini API error at turn {turn + 1}: {error_msg}")
                # Try to provide actionable info for common error patterns
                if "INVALID_ARGUMENT" in error_msg:
                    if on_log:
                        on_log(
                            "error",
                            "INVALID_ARGUMENT usually means: (1) screenshot too large/corrupt, "
                            "(2) model doesn't support computer_use tool, or "
                            "(3) conversation context exceeded limits. "
                            f"last screenshot: {len(screenshot_bytes)} bytes",
                        )
                yield RunCompleted(final_text=f"Gemini API error: {error_msg}")
                return
            interaction_id = (
                interaction.get("id")
                if isinstance(interaction, dict)
                else getattr(interaction, "id", "")
            )
            interaction_usage = (
                interaction.get("usage", {})
                if isinstance(interaction, dict)
                else _to_plain_dict(getattr(interaction, "usage", None))
            )
            self._usage["input_tokens"] += int(interaction_usage.get("total_input_tokens") or 0)
            self._usage["output_tokens"] += int(interaction_usage.get("total_output_tokens") or 0)
            previous_interaction_id = str(interaction_id or "")
            outputs = self._interaction_outputs(interaction)
            function_calls = [item for item in outputs if item.get("type") == "function_call"]
            interaction_text = (
                interaction.get("output_text")
                if isinstance(interaction, dict)
                else getattr(interaction, "output_text", "")
            )
            turn_text = str(interaction_text or "").strip()
            if not turn_text:
                turn_text = " ".join(
                    str(item.get("text") or item.get("content") or "").strip()
                    for item in outputs
                    if item.get("type") in {"text", "model_output"}
                ).strip()

            # No function calls → model is done
            if not function_calls:
                if (
                    _gemini_final_needs_computer_use(goal, turn_text)
                    and not saw_computer_action
                    and not nudged_for_computer_use
                ):
                    if on_log:
                        on_log(
                            "info",
                            "Gemini CU: model stopped before any computer action; nudging it to continue with the computer_use tool.",
                        )
                    try:
                        retry_ss = await executor.capture_screenshot()
                    except Exception:
                        retry_ss = screenshot_bytes
                    next_input = [
                        {
                            "type": "text",
                            "text": (
                                f"Active user task: {goal}\n\n"
                                "The task is not complete until you perform the requested "
                                "action with the computer_use tool. Continue now."
                            ),
                        },
                        self._image_input(retry_ss),
                    ]
                    nudged_for_computer_use = True
                    continue
                final_text = turn_text
                if on_log:
                    on_log("info", f"Gemini CU completed: {final_text[:200]}")
                yield RunCompleted(final_text=final_text)
                return

            yield ModelTurnStarted(
                turn=turn + 1,
                model_text=turn_text,
                pending_tool_uses=len(function_calls),
            )

            # Execute each function call
            results: list[CUActionResult] = []
            terminated = False
            terminate_reason = "Agent terminated: safety confirmation denied."

            result_inputs: list[dict[str, Any]] = []
            for idx, fc in enumerate(function_calls):
                args = dict(fc.get("arguments") or {})
                function_name = str(fc.get("name") or "")

                # Extract safety_decision BEFORE passing args to executor.
                # This ensures the acknowledgement is tracked regardless of
                # which executor implementation is used.
                safety_confirmed = False
                if "safety_decision" in args:
                    sd = args.pop("safety_decision")
                    decision = sd.get("decision") if isinstance(sd, dict) else None
                    if decision == "blocked":
                        if on_log:
                            on_log("warning", f"Safety blocked {function_name}")
                        terminated = True
                        terminate_reason = "Agent terminated: safety decision blocked the action."
                        break
                    if decision == "require_confirmation":
                        confirmed = yield SafetyRequired(
                            explanation=str(sd.get("explanation", "")),
                        )
                        if not confirmed:
                            if on_log:
                                on_log("warning", f"Safety denied for {function_name}")
                            terminated = True
                            break
                        safety_confirmed = True

                args["action_id"] = f"{turn + 1}:{idx}"
                if function_name == MCP_FETCH_TOOL_NAME:
                    url = str(args.get("url") or "")
                    text = await fetch_url_for_model(url)
                    result = CUActionResult(
                        name=MCP_FETCH_TOOL_NAME,
                        success=not text.startswith("Error:"),
                        error=text if text.startswith("Error:") else None,
                        extra={"url": url, "text": text},
                    )
                    if on_log:
                        on_log("info", f"MCP fetch {url or '(missing url)'}")
                else:
                    saw_computer_action = True
                    result = await executor.execute(function_name, args)
                # Stamp safety metadata so FunctionResponse includes
                # safety_acknowledgement when the user confirmed.
                if safety_confirmed:
                    result.safety_decision = SafetyDecision.REQUIRE_CONFIRMATION
                results.append(result)

            # Emit turn record
            try:
                screenshot_bytes = await executor.capture_screenshot()
            except Exception as ss_err:
                if on_log:
                    on_log("warning", f"Screenshot capture failed at turn {turn + 1}: {ss_err}")
                screenshot_bytes = b""

            screenshot_b64 = (
                base64.standard_b64encode(screenshot_bytes).decode() if screenshot_bytes else ""
            )

            if terminated and not results:
                yield RunCompleted(final_text=terminate_reason)
                return

            yield ToolBatchCompleted(
                turn=turn + 1,
                model_text=turn_text,
                results=results,
                screenshot_b64=screenshot_b64 or None,
            )

            if terminated:
                yield RunCompleted(final_text=terminate_reason)
                return

            current_url = executor.get_current_url()
            screenshot_ok = bool(screenshot_bytes) and len(screenshot_bytes) >= 100
            for call, r in zip(function_calls, results, strict=True):
                resp_data: dict[str, Any] = {"url": current_url}
                if r.error:
                    resp_data["error"] = r.error
                if r.safety_decision == SafetyDecision.REQUIRE_CONFIRMATION:
                    resp_data["safety_acknowledgement"] = True
                # Merge extra data, converting non-serializable types (tuples → lists)
                for k, v in r.extra.items():
                    if isinstance(v, tuple):
                        resp_data[k] = list(v)
                    elif isinstance(v, (str, int, float, bool, type(None), list, dict)):
                        resp_data[k] = v
                    else:
                        resp_data[k] = str(v)

                parts: list[dict[str, Any]] = [
                    {"type": "text", "text": json.dumps(resp_data, separators=(",", ":"))}
                ]
                if screenshot_ok:
                    parts.append(self._image_input(screenshot_bytes))
                result_inputs.append(
                    {
                        "type": "function_result",
                        "name": str(call.get("name") or r.name),
                        "call_id": str(call.get("id") or ""),
                        "result": parts,
                    }
                )

            if not result_inputs:
                if on_log:
                    on_log("warning", "No function responses to send; ending loop")
                yield RunCompleted(final_text=turn_text or "Gemini returned no function responses.")
                return
            next_input = result_inputs

        if _turn_start is not None and on_log:
            on_log(
                "info",
                f"turn_duration_ms={int((time.monotonic() - _turn_start) * 1000)} provider=google model={self._model}",
            )
        yield RunCompleted(
            final_text=f"Gemini CU reached the turn limit ({turn_limit}) without a final response.",
        )

    async def run_loop(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], bool] | None = None,
        on_turn: Callable[[CUTurnRecord], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Drive the native iterator while preserving the legacy callback API."""
        self._last_completion_payload = None
        final_text = ""
        pending_turn_text = ""
        pending_event: TurnEvent | None = None

        agen = self.iter_turns(
            goal,
            executor,
            turn_limit=turn_limit,
            on_log=on_log,
        )

        while True:
            try:
                if pending_event is not None:
                    event = pending_event
                    pending_event = None
                else:
                    event = await agen.__anext__()
            except StopAsyncIteration:
                break

            if isinstance(event, ModelTurnStarted):
                pending_turn_text = event.model_text
                continue

            if isinstance(event, SafetyRequired):
                confirmed = await _invoke_safety(on_safety, event.explanation)
                try:
                    pending_event = await agen.asend(confirmed)
                except StopAsyncIteration:
                    if not final_text and not confirmed:
                        final_text = "Agent terminated: safety confirmation denied."
                    break
                continue

            if isinstance(event, ToolBatchCompleted):
                if on_turn:
                    on_turn(
                        CUTurnRecord(
                            turn=event.turn,
                            model_text=event.model_text or pending_turn_text,
                            actions=event.results,
                            screenshot_b64=event.screenshot_b64,
                        )
                    )
                pending_turn_text = ""
                continue

            if isinstance(event, RunCompleted):
                final_text = event.final_text

        return final_text


# ---------------------------------------------------------------------------
# Claude Computer Use Client
# ---------------------------------------------------------------------------
