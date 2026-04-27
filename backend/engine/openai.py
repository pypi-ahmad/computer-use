"""OpenAI Computer Use client — split out of ``backend.engine`` (Q2).

The class body lives here; ``backend.engine`` re-exports it so imports
like ``from backend.engine import OpenAICUClient`` keep working.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from typing import Any, Callable

from backend.infra.config import config as _app_config
from backend.models.schemas import load_allowed_models_json as _load_allowed_models_json
from backend.executor import ActionExecutor, CUActionResult
from backend.engine import (
    CUTurnRecord,
    _call_with_retry,
    _invoke_safety,
    _to_plain_dict,
    _extract_openai_output_text,
    _append_source_footer,
    _build_openai_computer_call_output,
    _sanitize_openai_response_item_for_replay,
    default_openai_reasoning_effort_for_model,
    validate_builtin_search_config,
    DEFAULT_TURN_LIMIT,
)

logger = logging.getLogger(__name__)


_OPENAI_ORIGINAL_MAX_PIXELS = 10_240_000
_OPENAI_ORIGINAL_MAX_DIMENSION = 6000
_OPENAI_GA_COMPUTER_MODEL_PREFIXES = ("gpt-5.4", "gpt-5.5")
_OPENAI_REGISTRY_GATED_MODEL_PREFIXES = ("gpt-5.5",)
_OPENAI_CU_REGISTRY_MODELS = frozenset(
    str(model.get("model_id"))
    for model in _load_allowed_models_json()
    if model.get("provider") == "openai" and model.get("supports_computer_use")
)


def _ensure_openai_ga_model_is_in_registry(model: str) -> None:
    """Reject GPT-5.5-family GA slugs that are absent from the registry."""
    if any(model.startswith(prefix) for prefix in _OPENAI_REGISTRY_GATED_MODEL_PREFIXES):
        if model in _OPENAI_CU_REGISTRY_MODELS:
            return
        allowed = ", ".join(sorted(_OPENAI_CU_REGISTRY_MODELS))
        raise ValueError(
            f"OpenAI model {model!r} is not in the computer-use registry "
            f"(backend/models/allowed_models.json). Supported OpenAI models: {allowed}."
        )


def _prepare_openai_computer_screenshot(
    png_bytes: bytes,
    *,
    on_log: "Callable[[str, str], None] | None" = None,
) -> tuple[bytes, float]:
    """Resize oversized screenshots and report the sent-image scale.

    OpenAI's GPT-5.5 image handling keeps ``detail: \"original\"`` inputs at
    full resolution up to 10.24 MP and a 6000 px long edge. If the runtime
    exceeds that budget, we downscale the bytes before upload and execute the
    returned coordinates back in the original screen space.
    """
    try:
        from PIL import Image
    except ImportError:
        return png_bytes, 1.0

    try:
        with Image.open(io.BytesIO(png_bytes)) as image:
            width, height = image.size
            if width <= 0 or height <= 0:
                return png_bytes, 1.0

            total_pixels = width * height
            scale = min(
                1.0,
                _OPENAI_ORIGINAL_MAX_DIMENSION / max(width, height),
                (_OPENAI_ORIGINAL_MAX_PIXELS / total_pixels) ** 0.5,
            )
            if scale >= 1.0:
                return png_bytes, 1.0

            new_width = max(1, int(round(width * scale)))
            new_height = max(1, int(round(height * scale)))
            resized = image.resize((new_width, new_height), Image.LANCZOS)
            out = io.BytesIO()
            resized.save(out, format="PNG")
    except Exception as exc:
        if on_log is not None:
            on_log("warning", f"OpenAI screenshot resize skipped: {exc}")
        return png_bytes, 1.0

    if on_log is not None:
        on_log(
            "info",
            f"OpenAI screenshot scale={scale:.3f} -> {new_width}x{new_height}",
        )
    return out.getvalue(), scale


def _extract_openai_sources(output_items: list[Any]) -> list[tuple[str, str]]:
    """Collect cited URLs from OpenAI Responses output items."""
    sources: list[tuple[str, str]] = []
    for item in output_items:
        item_dict = _to_plain_dict(item)
        if item_dict.get("type") == "message":
            for part in item_dict.get("content", []) or []:
                if not isinstance(part, dict):
                    continue
                for ann in part.get("annotations", []) or []:
                    if not isinstance(ann, dict) or ann.get("type") != "url_citation":
                        continue
                    url = ann.get("url")
                    if url:
                        sources.append((ann.get("title") or url, url))
        elif item_dict.get("type") == "web_search_call":
            action = item_dict.get("action") or {}
            if isinstance(action, dict):
                for src in action.get("sources", []) or []:
                    if not isinstance(src, dict):
                        continue
                    url = src.get("url")
                    if url:
                        sources.append((src.get("title") or url, url))
    return sources

class OpenAICUClient:
    """OpenAI Responses API computer-use client.

    Uses the built-in ``computer`` tool with ``gpt-5.5`` or another
    documented GA OpenAI computer-use model. The harness executes all
    returned actions and returns screenshots through
    ``computer_call_output`` items.
    """

    # Canonical values per the OpenAI GPT-5.5 docs (April 2026).
    VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
    # Legacy aliases from earlier SDKs — accepted on input and mapped
    # to the canonical enum before the request leaves the process.
    _LEGACY_EFFORT_ALIASES = {"none": "minimal"}

    def __init__(
        self,
        api_key: str,
        # Default bumped per OpenAI changelog (checked 2026-04-26):
        # https://developers.openai.com/api/docs/changelog
        model: str = "gpt-5.5",
        system_prompt: str | None = None,
        # Defaults are model-specific per OpenAI's model pages:
        # GPT-5.4 defaults to ``none`` and GPT-5.5 defaults to ``medium``.
        # Callers can still override with ``minimal``/``low``/``medium``/
        # ``high``/``xhigh``.
        reasoning_effort: str | None = None,
        # Official OpenAI Responses API web-search tool (April 2026).
        # When ``use_builtin_search`` is True the adapter appends
        # ``{"type": "web_search"}`` to the tools list and the model
        # decides per turn whether to invoke it. ``search_max_uses`` is
        # not part of OpenAI's contract (Anthropic-only) and is
        # ignored here for parity with the unified factory shape.
        use_builtin_search: bool = False,
        search_max_uses: int | None = None,
        search_allowed_domains: list[str] | None = None,
        search_blocked_domains: list[str] | None = None,
        # File-search activation (April 2026 Responses API):
        # https://developers.openai.com/api/docs/guides/tools-file-search
        # When ``attached_file_ids`` is non-empty, the adapter creates a
        # vector store, uploads every server-side file to it, and
        # appends ``{"type":"file_search","vector_store_ids":[id]}``
        # to the tools list per the docs.
        attached_file_ids: list[str] | None = None,
    ):
        # Prefer the native async client when available. Falling back to
        # the sync client behind ``asyncio.to_thread`` used to burn a
        # thread-pool slot per LLM call, which is especially painful
        # for high-reasoning tasks that take 30+ seconds per turn.
        try:
            from openai import AsyncOpenAI  # type: ignore
            self._async_cls = AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "openai is required. Install: pip install openai"
            ) from exc

        self._sync_client = None  # populated lazily if the sync fallback is needed
        openai_base_url = os.getenv("OPENAI_BASE_URL", None)
        kwargs: dict[str, Any] = {"api_key": api_key}
        if openai_base_url:
            kwargs["base_url"] = openai_base_url
        self._client = AsyncOpenAI(**kwargs)
        self._model = model
        self._system_prompt = system_prompt or ""
        _ensure_openai_ga_model_is_in_registry(model)
        default_effort = default_openai_reasoning_effort_for_model(model)
        reasoning_effort = reasoning_effort or default_effort
        # Map legacy aliases (``none``) → canonical, then fall back to
        # the doc-backed model default on anything unknown.
        reasoning_effort = self._LEGACY_EFFORT_ALIASES.get(
            reasoning_effort, reasoning_effort,
        )
        if reasoning_effort not in self.VALID_REASONING_EFFORTS:
            reasoning_effort = self._LEGACY_EFFORT_ALIASES.get(
                default_effort, default_effort,
            )
        self._reasoning_effort = reasoning_effort
        self._current_screenshot_scale = 1.0
        validate_builtin_search_config(
            provider="openai",
            model=model,
            use_builtin_search=use_builtin_search,
            reasoning_effort=self._reasoning_effort,
            search_max_uses=search_max_uses,
            search_allowed_domains=search_allowed_domains,
            search_blocked_domains=search_blocked_domains,
        )
        # Web-search wiring (April 2026 Responses API: tool type
        # ``web_search``). Unsupported combinations fail explicitly
        # instead of silently dropping the search tool.
        self._use_builtin_search = bool(use_builtin_search)
        self._search_allowed_domains = list(search_allowed_domains) if search_allowed_domains else None
        self._search_blocked_domains = list(search_blocked_domains) if search_blocked_domains else None
        del search_max_uses  # already validated above
        # Provider-side vector store id, lazily provisioned on the first
        # ``run_loop`` invocation when ``_attached_file_ids`` is non-empty.
        # Re-used across turns; cleaned up at run-loop exit.
        self._attached_file_ids: list[str] = list(attached_file_ids or [])
        self._vector_store_id: str | None = None

    def _build_tools(
        self,
        screen_width: int,
        screen_height: int,
        *,
        on_log: "Callable[[str, str], None] | None" = None,
    ) -> list[dict[str, Any]]:
        """Return the Responses API ``tools`` list for this model.

        GPT-5.5 / GPT-5.4 GA computer-use models use the built-in
        short-form tool:
            {"type": "computer"}
        The built-in tool infers display dimensions from the screenshots
        the harness sends, so no display_width / display_height /
        environment keys are needed.
        """
        model = self._model
        _ensure_openai_ga_model_is_in_registry(model)
        if any(model.startswith(prefix) for prefix in _OPENAI_GA_COMPUTER_MODEL_PREFIXES):
            # Built-in tool — dimensions inferred from screenshot bytes.
            tools: list[dict[str, Any]] = [{"type": "computer"}]
            if self._use_builtin_search:
                # April 2026 Responses API: ``{"type": "web_search"}``.
                # Optional ``filters.allowed_domains`` /
                # ``filters.blocked_domains`` per the Web Search tool
                # reference. The model decides whether to call it.
                ws_tool: dict[str, Any] = {"type": "web_search"}
                filters: dict[str, Any] = {}
                if self._search_allowed_domains:
                    filters["allowed_domains"] = self._search_allowed_domains
                if self._search_blocked_domains:
                    filters["blocked_domains"] = self._search_blocked_domains
                if filters:
                    ws_tool["filters"] = filters
                tools.append(ws_tool)
            # File-search tool, gated by user upload (activation rule
            # per https://developers.openai.com/api/docs/guides/tools-file-search).
            # Vector store is provisioned in ``_ensure_vector_store``
            # before the first turn — by the time we render the tools
            # list ``self._vector_store_id`` is already set.
            if self._vector_store_id is not None:
                tools.append({
                    "type": "file_search",
                    "vector_store_ids": [self._vector_store_id],
                })
            return tools
        allowed = ", ".join(sorted(_OPENAI_CU_REGISTRY_MODELS))
        raise ValueError(
            f"OpenAI model {model!r} is not a supported OpenAI computer-use model. "
            f"Use one of: {allowed}."
        )

    async def _create_response(self, *, on_log: "Callable[[str, str], None] | None" = None, **kwargs: Any) -> Any:
        """Call the async OpenAI Responses API with transient-error retry."""
        return await _call_with_retry(
            lambda: self._client.responses.create(**kwargs),
            provider="openai",
            on_log=on_log,
        )

    async def _ensure_vector_store(
        self,
        *,
        on_log: "Callable[[str, str], None] | None" = None,
    ) -> None:
        """Provision the vector store and upload all attached files.

        Implements the official two-step setup from
        https://developers.openai.com/api/docs/guides/tools-file-search:

          1. ``client.vector_stores.create(...)``
          2. ``client.vector_stores.files.upload_and_poll(vector_store_id=..., file=...)``

        ``upload_and_poll`` blocks until the file is fully indexed so
        the first ``responses.create`` call sees a queryable store.
        """
        if not self._attached_file_ids or self._vector_store_id is not None:
            return
        from backend.files import prepare_openai_file_search
        self._vector_store_id = await prepare_openai_file_search(
            self._client,
            self._attached_file_ids,
            on_log=on_log,
        )

    async def _cleanup_vector_store(
        self,
        *,
        on_log: "Callable[[str, str], None] | None" = None,
    ) -> None:
        """Delete the per-session vector store at run-loop exit.

        Best-effort: a transient delete failure does not propagate, the
        store will be GC'd by OpenAI eventually.  See the retrieval
        guide for vector store lifecycle.
        """
        from backend.files import cleanup_openai_vector_store
        await cleanup_openai_vector_store(
            self._client,
            self._vector_store_id,
            on_log=on_log,
        )
        self._vector_store_id = None

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
        """Run the OpenAI native computer-use loop via the Responses API."""
        # Provision the vector store before the first turn so the
        # ``file_search`` tool reference is non-null when ``_build_tools``
        # renders the request payload.
        await self._ensure_vector_store(on_log=on_log)
        try:
            return await self._run_loop_inner(
                goal,
                executor,
                turn_limit=turn_limit,
                on_safety=on_safety,
                on_turn=on_turn,
                on_log=on_log,
            )
        finally:
            await self._cleanup_vector_store(on_log=on_log)

    async def _run_loop_inner(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], bool] | None = None,
        on_turn: Callable[[CUTurnRecord], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Inner Responses-API loop (the original ``run_loop`` body)."""
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            return "Error: Could not capture initial screenshot"

        prepared_screenshot_bytes, current_screenshot_scale = _prepare_openai_computer_screenshot(
            screenshot_bytes,
            on_log=on_log,
        )
        self._current_screenshot_scale = current_screenshot_scale

        next_input: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": goal},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{base64.standard_b64encode(prepared_screenshot_bytes).decode()}",
                        "detail": "original",
                    },
                ],
            }
        ]
        # ZDR-safe: never use previous_response_id; always send full context
        final_text = ""
        _turn_start: float | None = None
        saw_computer_action = False
        nudged_for_computer_use = False

        for turn in range(turn_limit):
            self._current_screenshot_scale = current_screenshot_scale
            if _turn_start is not None and on_log:
                on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=openai model={self._model}")
            _turn_start = time.monotonic()
            if on_log:
                on_log("info", f"OpenAI CU turn {turn + 1}/{turn_limit}")

            include_fields = ["reasoning.encrypted_content"]
            if self._use_builtin_search:
                include_fields.append("web_search_call.action.sources")

            request: dict[str, Any] = {
                "model": self._model,
                "input": next_input,
                "tools": self._build_tools(
                    getattr(executor, "screen_width", 0) or 0,
                    getattr(executor, "screen_height", 0) or 0,
                    on_log=on_log if turn == 0 else None,
                ),
                "parallel_tool_calls": False,
                "include": include_fields,
                "reasoning": {"effort": self._reasoning_effort},
                "store": False,
                "truncation": "auto",
            }
            if self._system_prompt:
                request["instructions"] = self._system_prompt

            response = await self._create_response(on_log=on_log, **request)
            response_error = getattr(response, "error", None)
            if response_error:
                raise RuntimeError(getattr(response_error, "message", str(response_error)))
            output_items = list(getattr(response, "output", []) or [])
            turn_text = getattr(response, "output_text", "") or _extract_openai_output_text(output_items)
            computer_calls = [
                item for item in output_items
                if getattr(item, "type", None) == "computer_call"
            ]

            if not computer_calls:
                if (self._use_builtin_search or self._vector_store_id is not None) and not saw_computer_action and not nudged_for_computer_use:
                    if on_log:
                        on_log(
                            "info",
                            "OpenAI CU: retrieval-only turn before any computer action; nudging the model to continue with the computer tool.",
                        )
                    refreshed_screenshot = await executor.capture_screenshot()
                    prepared_refreshed_screenshot, current_screenshot_scale = _prepare_openai_computer_screenshot(
                        refreshed_screenshot,
                        on_log=on_log,
                    )
                    next_input = [
                        _sanitize_openai_response_item_for_replay(item)
                        for item in output_items
                    ]
                    next_input.append({
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
                                "image_url": f"data:image/png;base64,{base64.standard_b64encode(prepared_refreshed_screenshot).decode()}",
                                "detail": "original",
                            },
                        ],
                    })
                    nudged_for_computer_use = True
                    continue
                final_text = _append_source_footer(
                    turn_text or "OpenAI completed without a final message.",
                    _extract_openai_sources(output_items),
                )
                if on_log:
                    on_log("info", f"OpenAI CU completed: {final_text[:200]}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=turn_text, actions=[]))
                break

            saw_computer_action = True

            tool_outputs: list[dict[str, Any]] = []
            results: list[CUActionResult] = []
            screenshot_b64: str | None = None
            next_screenshot_scale = current_screenshot_scale
            terminated = False

            for computer_call in computer_calls:
                acknowledged_safety_checks: list[dict[str, Any]] | None = None
                pending_checks = [
                    _to_plain_dict(check)
                    for check in (getattr(computer_call, "pending_safety_checks", None) or [])
                ]
                if pending_checks:
                    explanation = " | ".join(
                        check.get("message") or check.get("code") or "Safety acknowledgement required"
                        for check in pending_checks
                    )
                    confirmed = await _invoke_safety(on_safety, explanation)
                    if not confirmed:
                        final_text = "Agent terminated: safety confirmation denied."
                        terminated = True
                        break
                    acknowledged_safety_checks = []
                    for check in pending_checks:
                        ack: dict[str, Any] = {"id": check["id"]}
                        if check.get("code") is not None:
                            ack["code"] = check["code"]
                        if check.get("message") is not None:
                            ack["message"] = check["message"]
                        acknowledged_safety_checks.append(ack)

                actions = list(getattr(computer_call, "actions", None) or [])
                if not actions:
                    single_action = getattr(computer_call, "action", None)
                    if single_action is not None:
                        actions = [single_action]

                for action in actions:
                    result = await self._execute_openai_action(action, executor)
                    results.append(result)
                    # Inter-action delay matching official CUA sample (120ms)
                    if action is not actions[-1]:
                        await asyncio.sleep(_app_config.screenshot_settle_delay)

                screenshot_bytes = await executor.capture_screenshot()
                prepared_screenshot_bytes, next_screenshot_scale = _prepare_openai_computer_screenshot(
                    screenshot_bytes,
                    on_log=on_log,
                )
                screenshot_b64 = base64.standard_b64encode(prepared_screenshot_bytes).decode()
                tool_outputs.append(
                    _build_openai_computer_call_output(
                        getattr(computer_call, "call_id"),
                        screenshot_b64,
                        acknowledged_safety_checks=acknowledged_safety_checks,
                    )
                )

            if on_turn:
                on_turn(CUTurnRecord(
                    turn=turn + 1,
                    model_text=turn_text,
                    actions=results,
                    screenshot_b64=screenshot_b64,
                ))

            if terminated:
                break
            if not tool_outputs:
                final_text = turn_text or "OpenAI returned no actionable computer calls."
                break
            current_screenshot_scale = next_screenshot_scale
            self._current_screenshot_scale = current_screenshot_scale

            # Build next input: include response output items + tool call results
            # ZDR orgs cannot use previous_response_id, so we replay full context.
            # Response output items contain output-only fields (e.g. "status"
            # and "pending_safety_checks") that are NOT accepted as input.
            # Note: preserve "phase" on message items – GPT-5.5 requires it
            # when manually replaying assistant commentary/final-answer items.
            response_output = list(getattr(response, "output", []) or [])
            next_input = []
            for item in response_output:
                next_input.append(_sanitize_openai_response_item_for_replay(item))
            next_input.extend(tool_outputs)
        else:
            final_text = f"OpenAI CU reached the turn limit ({turn_limit}) without a final response."

        if _turn_start is not None and on_log:
            on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=openai model={self._model}")
        return final_text

    async def _execute_openai_action(
        self,
        action: Any,
        executor: ActionExecutor,
    ) -> CUActionResult:
        """Translate OpenAI computer actions to the shared executor contract."""
        payload = _to_plain_dict(action)
        action_type = str(payload.get("type", ""))
        scale_factor = float(getattr(self, "_current_screenshot_scale", 1.0) or 1.0)
        if scale_factor <= 0.0:
            scale_factor = 1.0

        def _upscale_coord(raw: Any) -> int | None:
            if not isinstance(raw, (int, float)):
                return None
            value = float(raw)
            if scale_factor < 1.0:
                value /= scale_factor
            return int(round(value))

        def _coords(*keys: str) -> tuple[int | None, ...]:
            values: list[int | None] = []
            for key in keys:
                values.append(_upscale_coord(payload.get(key)))
            return tuple(values)

        if action_type == "screenshot":
            return CUActionResult(name="screenshot")

        if action_type == "click":
            x, y = _coords("x", "y")
            button = str(payload.get("button", "left")).lower()
            if x is None or y is None:
                return CUActionResult(name="click", success=False, error="Click action missing coordinates")
            if button == "right":
                return await executor.execute("right_click", {"x": x, "y": y})
            if button in {"middle", "wheel"}:
                return await executor.execute("middle_click", {"x": x, "y": y})
            return await executor.execute("click_at", {"x": x, "y": y})

        if action_type == "double_click":
            x, y = _coords("x", "y")
            if x is None or y is None:
                return CUActionResult(name="double_click", success=False, error="Double-click action missing coordinates")
            return await executor.execute("double_click", {"x": x, "y": y})

        if action_type == "move":
            x, y = _coords("x", "y")
            if x is None or y is None:
                return CUActionResult(name="move", success=False, error="Move action missing coordinates")
            return await executor.execute("move", {"x": x, "y": y})

        if action_type == "type":
            return await executor.execute("type_at_cursor", {
                "text": str(payload.get("text", "")),
                "press_enter": False,
            })

        if action_type == "keypress":
            keys = payload.get("keys")
            if not isinstance(keys, list):
                single_key = payload.get("key")
                keys = [single_key] if single_key else []
            normalized = "+".join(self._normalize_openai_keys(keys))
            if not normalized:
                return CUActionResult(name="keypress", success=False, error="Keypress action missing keys")
            return await executor.execute("key_combination", {"keys": normalized})

        if action_type == "wait":
            duration_ms = payload.get("ms")
            if not isinstance(duration_ms, (int, float)):
                duration_ms = payload.get("duration_ms")
            if not isinstance(duration_ms, (int, float)):
                duration_ms = 2000
            await asyncio.sleep(max(0.0, min(float(duration_ms), 30_000.0)) / 1000.0)
            return CUActionResult(name="wait", extra={"duration_ms": int(duration_ms)})

        if action_type == "scroll":
            return await self._execute_openai_scroll(payload, executor)

        if action_type == "drag":
            path = payload.get("path")
            start_x: int | None = None
            start_y: int | None = None
            end_x: int | None = None
            end_y: int | None = None
            if isinstance(path, list) and len(path) >= 2:
                first = _to_plain_dict(path[0])
                last = _to_plain_dict(path[-1])
                start_x = _upscale_coord(first.get("x"))
                start_y = _upscale_coord(first.get("y"))
                end_x = _upscale_coord(last.get("x"))
                end_y = _upscale_coord(last.get("y"))
            if start_x is None or start_y is None:
                start_x, start_y = _coords("x", "y")
            if end_x is None or end_y is None:
                end_x, end_y = _coords("destination_x", "destination_y")
            if None in {start_x, start_y, end_x, end_y}:
                return CUActionResult(name="drag", success=False, error="Drag action missing path coordinates")
            return await executor.execute("drag_and_drop", {
                "x": start_x,
                "y": start_y,
                "destination_x": end_x,
                "destination_y": end_y,
            })

        return CUActionResult(
            name=action_type or "unknown",
            success=False,
            error=f"Unsupported OpenAI action: {action_type}",
        )

    async def _execute_openai_scroll(
        self,
        payload: dict[str, Any],
        executor: ActionExecutor,
    ) -> CUActionResult:
        """Execute OpenAI pixel scroll actions through the shared executor."""
        scale_factor = float(getattr(self, "_current_screenshot_scale", 1.0) or 1.0)
        if scale_factor <= 0.0:
            scale_factor = 1.0

        def _upscale(raw: Any) -> int | None:
            if not isinstance(raw, (int, float)):
                return None
            value = float(raw)
            if scale_factor < 1.0:
                value /= scale_factor
            return int(round(value))

        x = payload.get("x")
        y = payload.get("y")
        px = _upscale(x)
        py = _upscale(y)
        delta_x = payload.get("delta_x", payload.get("deltaX", payload.get("scroll_x", 0)))
        delta_y = payload.get("delta_y", payload.get("deltaY", payload.get("scroll_y", 0)))
        dx = _upscale(delta_x) or 0
        dy = _upscale(delta_y) or 0

        dominant_y = abs(dy) >= abs(dx)
        if dominant_y:
            direction = "down" if dy >= 0 else "up"
            magnitude = abs(dy)
        else:
            direction = "right" if dx >= 0 else "left"
            magnitude = abs(dx)
        args: dict[str, Any] = {
            "direction": direction,
            # Preserve small-scroll fidelity: the previous ``max(magnitude, 200)``
            # silently turned a 20-pixel micro-scroll into a 200-pixel jump and
            # broke calendar/dropdown interactions. Clamp only the upper bound.
            "magnitude": min(max(int(magnitude), 1), 999),
        }
        if px is not None and py is not None:
            args["x"] = px
            args["y"] = py
        return await executor.execute("scroll_at", args)

    @staticmethod
    def _normalize_openai_keys(keys: list[Any]) -> list[str]:
        """Normalize OpenAI keypress values for desktop execution."""
        key_map = {
            "SPACE": "Space",
            "ENTER": "Enter",
            "RETURN": "Enter",
            "ESC": "Escape",
            "ESCAPE": "Escape",
            "CTRL": "Control",
            "CMD": "Meta",
            "COMMAND": "Meta",
            "OPTION": "Alt",
            "PGUP": "PageUp",
            "PGDN": "PageDown",
        }
        normalized: list[str] = []
        for key in keys:
            if key is None:
                continue
            token = str(key).strip()
            if not token:
                continue
            normalized.append(key_map.get(token.upper(), token))
        return normalized


