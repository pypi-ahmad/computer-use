"""OpenAI Computer Use client — split out of ``backend.engine`` (Q2).

The class body lives here; ``backend.engine`` re-exports it so imports
like ``from backend.engine import OpenAICUClient`` keep working.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Any, Callable

from backend.config import config as _app_config
from backend.engine import (
    CUActionResult,
    CUTurnRecord,
    ActionExecutor,
    _call_with_retry,
    _invoke_safety,
    _to_plain_dict,
    _extract_openai_output_text,
    _build_openai_computer_call_output,
    _sanitize_openai_response_item_for_replay,
    DEFAULT_TURN_LIMIT,
)

logger = logging.getLogger(__name__)

class OpenAICUClient:
    """OpenAI Responses API computer-use client.

    Uses the built-in ``computer`` tool with ``gpt-5.4`` or another
    allowlisted OpenAI model. The harness executes all returned actions and
    returns screenshots through ``computer_call_output`` items.
    """

    # Canonical values per the OpenAI Responses API (April 2026).
    VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high")
    # Legacy aliases from earlier SDKs — accepted on input and mapped
    # to the canonical enum before the request leaves the process.
    # Passing ``none`` / ``xhigh`` to the live API returns HTTP 400.
    _LEGACY_EFFORT_ALIASES = {"none": "minimal", "xhigh": "high"}

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4",
        system_prompt: str | None = None,
        # CU-mode floor is ``high`` per the April 2026 OpenAI CU guide
        # ("high is the floor for agentic/computer-use work"). The
        # server-facing API still lets the user override (falling back
        # to env + "high" for non-CU paths).
        reasoning_effort: str = "high",
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
        # Map legacy aliases (``none`` / ``xhigh``) → canonical, then
        # fall back to ``high`` (the CU floor) on anything unknown so
        # the wire never carries a value the API will 400 on.
        reasoning_effort = self._LEGACY_EFFORT_ALIASES.get(
            reasoning_effort, reasoning_effort,
        )
        if reasoning_effort not in self.VALID_REASONING_EFFORTS:
            reasoning_effort = "high"
        self._reasoning_effort = reasoning_effort

    async def _create_response(self, *, on_log: "Callable[[str, str], None] | None" = None, **kwargs: Any) -> Any:
        """Call the async OpenAI Responses API with transient-error retry."""
        return await _call_with_retry(
            lambda: self._client.responses.create(**kwargs),
            provider="openai",
            on_log=on_log,
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
        """Run the OpenAI native computer-use loop via the Responses API."""
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            return "Error: Could not capture initial screenshot"

        next_input: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": goal},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{base64.standard_b64encode(screenshot_bytes).decode()}",
                        "detail": "original",
                    },
                ],
            }
        ]
        # ZDR-safe: never use previous_response_id; always send full context
        final_text = ""
        _turn_start: float | None = None

        for turn in range(turn_limit):
            if _turn_start is not None and on_log:
                on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=openai model={self._model}")
            _turn_start = time.monotonic()
            if on_log:
                on_log("info", f"OpenAI CU turn {turn + 1}/{turn_limit}")

            request: dict[str, Any] = {
                "model": self._model,
                "input": next_input,
                "tools": [{"type": "computer"}],
                "parallel_tool_calls": False,
                "include": ["reasoning.encrypted_content"],
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
                final_text = turn_text or "OpenAI completed without a final message."
                if on_log:
                    on_log("info", f"OpenAI CU completed: {final_text[:200]}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=turn_text, actions=[]))
                break

            tool_outputs: list[dict[str, Any]] = []
            results: list[CUActionResult] = []
            screenshot_b64: str | None = None
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
                screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()
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

            # Build next input: include response output items + tool call results
            # ZDR orgs cannot use previous_response_id, so we replay full context.
            # Response output items contain output-only fields (e.g. "status"
            # and "pending_safety_checks") that are NOT accepted as input.
            # Note: preserve "phase" on message items – GPT-5.4 needs it to
            # distinguish intermediate commentary from the final answer.
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

        def _coords(*keys: str) -> tuple[int | None, ...]:
            values: list[int | None] = []
            for key in keys:
                raw = payload.get(key)
                values.append(int(raw) if isinstance(raw, (int, float)) else None)
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
                start_x = int(first.get("x")) if isinstance(first.get("x"), (int, float)) else None
                start_y = int(first.get("y")) if isinstance(first.get("y"), (int, float)) else None
                end_x = int(last.get("x")) if isinstance(last.get("x"), (int, float)) else None
                end_y = int(last.get("y")) if isinstance(last.get("y"), (int, float)) else None
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
        x = payload.get("x")
        y = payload.get("y")
        px = int(x) if isinstance(x, (int, float)) else None
        py = int(y) if isinstance(y, (int, float)) else None
        delta_x = payload.get("delta_x", payload.get("deltaX", payload.get("scroll_x", 0)))
        delta_y = payload.get("delta_y", payload.get("deltaY", payload.get("scroll_y", 0)))
        dx = int(delta_x) if isinstance(delta_x, (int, float)) else 0
        dy = int(delta_y) if isinstance(delta_y, (int, float)) else 0

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


