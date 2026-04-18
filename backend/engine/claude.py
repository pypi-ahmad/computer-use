"""Claude Computer Use client — split out of ``backend.engine`` (Q2).

The class body lives here; ``backend.engine`` re-exports it so imports
like ``from backend.engine import ClaudeCUClient`` keep working.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Callable

from backend.engine import (
    CUActionResult,
    CUTurnRecord,
    ActionExecutor,
    _call_with_retry,
    get_claude_scale_factor,
    resize_screenshot_for_claude,
    DEFAULT_TURN_LIMIT,
    _CONTEXT_PRUNE_KEEP_RECENT,
    _IMAGE_PNG,
)

logger = logging.getLogger(__name__)

# Per-turn Claude max_tokens budget. Opus 4.7 long-plan tasks frequently
# truncate at 16k; bumping to 32k removes the artificial ceiling while
# staying well inside the model's response limit. Override via
# ``CUA_CLAUDE_MAX_TOKENS`` if a deployment needs a tighter bound.
import os as _os
try:
    _CLAUDE_MAX_TOKENS = max(1024, min(int(_os.getenv("CUA_CLAUDE_MAX_TOKENS", "32768")), 65536))
except ValueError:
    _CLAUDE_MAX_TOKENS = 32768

# ---------------------------------------------------------------------------
# Claude Computer Use Client
# ---------------------------------------------------------------------------

class ClaudeCUClient:
    """Native Claude computer-use tool protocol.

    API contract:
    - Auto-detects tool version from model name:
      * Claude Sonnet 4.6 / Opus 4.6 / Opus 4.5 → ``computer_20251124``
        with beta header ``computer-use-2025-11-24``
      * All other CU models → ``computer_20250124``
        with beta header ``computer-use-2025-01-24``
    - Uses ``client.beta.messages.create()`` (beta endpoint required)
    - Enables thinking with a conservative token budget
    - Sends screenshots as base64 in ``tool_result`` content
    - Claude outputs real pixel coordinates (no normalization)
    - ``display_number`` is intentionally omitted (optional, often wrong)
    - Actions: screenshot, click, double_click, type, key, scroll,
      mouse_move, left_click_drag, triple_click, right_click,
      middle_click, left_mouse_down, left_mouse_up, hold_key, wait
    """

    # Models that require the newer computer_20251124 tool version.
    _NEW_TOOL_MODELS = (
        "claude-opus-4-7", "claude-opus-4.7",
        "claude-sonnet-4-6", "claude-sonnet-4.6",
        "claude-opus-4-6", "claude-opus-4.6",
        "claude-opus-4-5", "claude-opus-4.5",
    )

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        system_prompt: str | None = None,
        tool_version: str | None = None,
        beta_flag: str | None = None,
    ):
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic is required. Install: pip install anthropic"
            ) from exc

        self._anthropic = anthropic
        # AsyncAnthropic avoids the per-call thread-pool hop we used to
        # do via ``asyncio.to_thread(sync_client.beta.messages.create, ...)``.
        # The agent loop spawns up to 3 concurrent sessions and each one
        # blocks a worker for tens of seconds on Opus — the threadpool
        # pressure was real.
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt or ""

        # Use explicit values from allowed_models.json if provided,
        # otherwise auto-detect from model name (backwards compatibility).
        if tool_version and beta_flag:
            self._tool_version = tool_version
            self._beta_flag = beta_flag
        elif any(tag in model for tag in self._NEW_TOOL_MODELS):
            self._tool_version = "computer_20251124"
            self._beta_flag = "computer-use-2025-11-24"
        else:
            self._tool_version = "computer_20250124"
            self._beta_flag = "computer-use-2025-01-24"

    def _build_tools(self, sw: int, sh: int) -> list[dict]:
        """Build the Claude computer-use tool definition with display dimensions."""
        tool: dict[str, Any] = {
            "type": self._tool_version,
            "name": "computer",
            "display_width_px": sw,
            "display_height_px": sh,
        }
        # Enable zoom action for computer_20251124 tool version
        if self._tool_version == "computer_20251124":
            tool["enable_zoom"] = True
        return [tool]

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
        """Run the full Claude CU agent loop.

        Handles screenshot scaling, context pruning, safety refusals,
        and all Claude stop_reason variants. Returns final text.
        """
        # Compute screenshot scaling to prevent coordinate drift.
        scale = get_claude_scale_factor(executor.screen_width, executor.screen_height, self._model)
        scaled_w = int(executor.screen_width * scale)
        scaled_h = int(executor.screen_height * scale)
        if scale < 1.0 and on_log:
            on_log("info", f"Claude screenshot scale={scale:.3f} → {scaled_w}x{scaled_h}")

        tools = self._build_tools(scaled_w, scaled_h)

        screenshot_bytes = await executor.capture_screenshot()
        screenshot_bytes, _, _ = resize_screenshot_for_claude(screenshot_bytes, scale)
        screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": goal},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _IMAGE_PNG,
                            "data": screenshot_b64,
                        },
                    },
                ],
            }
        ]

        final_text = ""
        _turn_start: float | None = None

        for turn in range(turn_limit):
            if _turn_start is not None and on_log:
                on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=anthropic model={self._model}")
            _turn_start = time.monotonic()
            if on_log:
                on_log("info", f"Claude CU turn {turn + 1}/{turn_limit}")

            # Prune old screenshots to prevent unbounded context growth
            _prune_claude_context(messages, _CONTEXT_PRUNE_KEEP_RECENT)

            # AI4: retry on 429/network transients; non-transient errors propagate.
            response = await _call_with_retry(
                lambda: self._client.beta.messages.create(
                    model=self._model,
                    max_tokens=_CLAUDE_MAX_TOKENS,
                    system=self._system_prompt,
                    tools=tools,
                    messages=messages,
                    betas=[self._beta_flag],
                    thinking={"type": "enabled", "budget_tokens": 4096},
                ),
                provider="anthropic",
                on_log=on_log,
            )

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            tool_uses = [b for b in assistant_content if b.type == "tool_use"]
            text_blocks = [b.text for b in assistant_content
                          if hasattr(b, "text") and b.text]
            turn_text = " ".join(text_blocks)

            # Handle all stop_reason values explicitly
            stop = response.stop_reason
            if stop == "refusal":
                # C-7: Anthropic's API does not allow overriding a refusal,
                # so surfacing a confirm/deny modal would be misleading.
                # Emit a one-way log notice (the frontend renders a banner
                # for ``data.type == "refusal_notice"``) and end the turn.
                refusal_reason = turn_text or "Model refused to continue (safety refusal)."
                if on_log:
                    on_log("warning", f"Claude refused: {refusal_reason[:200]}")
                final_text = refusal_reason
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=final_text, actions=[]))
                break
            if stop == "model_context_window_exceeded":
                final_text = "Error: context window exceeded. Task too long."
                if on_log:
                    on_log("error", "Claude context window exceeded")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=final_text, actions=[]))
                break
            if stop in ("max_tokens", "stop_sequence"):
                final_text = turn_text or f"Response truncated (stop_reason={stop})."
                if on_log:
                    on_log("warning", f"Claude stop_reason={stop}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=final_text, actions=[]))
                break
            if stop == "end_turn" or not tool_uses:
                final_text = turn_text
                if on_log:
                    on_log("info", f"Claude CU completed: {final_text[:200]}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=turn_text, actions=[]))
                break

            # Execute tool uses
            tool_result_parts: list[dict[str, Any]] = []
            results: list[CUActionResult] = []

            for tu in tool_uses:
                result = await self._execute_claude_action(
                    tu.input, executor, scale_factor=scale,
                )
                results.append(result)

                screenshot_bytes = await executor.capture_screenshot()
                screenshot_bytes, _, _ = resize_screenshot_for_claude(
                    screenshot_bytes, scale,
                )
                screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()

                content: list[dict] = []
                if result.error:
                    content.append({"type": "text", "text": f"Error: {result.error}"})
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": _IMAGE_PNG,
                        "data": screenshot_b64,
                    },
                })

                tool_result_parts.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": content,
                })

            if on_turn:
                on_turn(CUTurnRecord(
                    turn=turn + 1, model_text=turn_text,
                    actions=results, screenshot_b64=screenshot_b64,
                ))

            messages.append({"role": "user", "content": tool_result_parts})

        if _turn_start is not None and on_log:
            on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=anthropic model={self._model}")
        return final_text

    async def _execute_claude_action(
        self, action_input: dict, executor: ActionExecutor,
        *, scale_factor: float = 1.0,
    ) -> CUActionResult:
        """Map Claude computer tool actions to executor calls.

        Claude actions (computer_20251124): screenshot, click, double_click,
        type, key, scroll, mouse_move, left_click_drag, triple_click,
        right_click, middle_click, left_mouse_down, left_mouse_up,
        hold_key, wait, zoom.

        Claude uses REAL pixel coordinates — no denormalization.
        When screenshot scaling is active, coordinates are upscaled by
        dividing by scale_factor before passing to the executor.
        """
        action = action_input.get("action", "")

        if action == "screenshot":
            return CUActionResult(name="screenshot")

        def _upscale_coord(coord: list[int] | None) -> list[int] | None:
            """Upscale Claude's coordinates back to real screen pixels."""
            if coord is None or scale_factor >= 1.0:
                return coord
            return [int(c / scale_factor) for c in coord]

        # Build args in the CU format the executor expects
        coord = _upscale_coord(action_input.get("coordinate"))
        args: dict[str, Any] = {}

        if action in ("click", "double_click", "right_click", "triple_click", "middle_click"):
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            if action in ("double_click", "right_click", "triple_click", "middle_click"):
                return await self._special_click(action, coord, executor)
            return await executor.execute("click_at", args)

        elif action == "type":
            text = action_input.get("text", "")
            try:
                result = await executor.execute("type_at_cursor", {
                    "text": text,
                    "press_enter": False,
                })
                return CUActionResult(
                    name="type", success=result.success,
                    error=result.error, extra={"text": text},
                )
            except Exception as exc:
                return CUActionResult(name="type", success=False, error=str(exc))

        elif action == "key":
            key = action_input.get("key", "")
            KEY_MAP = {"Return": "Enter", "space": "Space"}
            args["keys"] = KEY_MAP.get(key, key)
            return await executor.execute("key_combination", args)

        elif action == "scroll":
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            args["direction"] = action_input.get("direction", "down")
            amount = action_input.get("amount", 3)
            args["magnitude"] = min(999, amount * 200)
            return await executor.execute("scroll_at", args)

        elif action == "mouse_move":
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            return await executor.execute("hover_at", args)

        elif action == "left_click_drag":
            start = _upscale_coord(
                action_input.get("start_coordinate", coord or [0, 0])
            )
            end = _upscale_coord(action_input.get("coordinate", [0, 0]))
            args["x"], args["y"] = start[0], start[1]
            args["destination_x"], args["destination_y"] = end[0], end[1]
            return await executor.execute("drag_and_drop", args)

        elif action == "left_mouse_down":
            return await executor.execute("left_mouse_down", {})

        elif action == "left_mouse_up":
            return await executor.execute("left_mouse_up", {})

        elif action == "hold_key":
            key = action_input.get("key", "")
            duration = action_input.get("duration", 1)
            return await executor.execute("hold_key", {"key": key, "duration": duration})

        elif action == "wait":
            duration = action_input.get("duration", 5)
            await asyncio.sleep(min(duration, 30))
            return CUActionResult(name="wait", extra={"duration": duration})

        elif action == "zoom":
            # Zoom returns a cropped screenshot region — we acknowledge it
            # but the actual zoom behavior is handled by the API when
            # enable_zoom is set in the tool definition.
            return CUActionResult(name="zoom")

        else:
            return CUActionResult(name=action, success=False,
                                  error=f"Unknown Claude action: {action}")

    async def _special_click(
        self, action: str, coord: list[int] | None, executor: ActionExecutor,
    ) -> CUActionResult:
        """Handle double_click, right_click, triple_click, and middle_click."""
        x, y = (coord[0], coord[1]) if coord else (0, 0)
        try:
            return await executor.execute(action, {"x": x, "y": y})
        except Exception as exc:
            return CUActionResult(name=action, success=False, error=str(exc))



# ---------------------------------------------------------------------------
# Claude context pruning
# ---------------------------------------------------------------------------


def _prune_claude_context(messages: list[dict], keep_recent: int) -> None:
    """Replace base64 screenshot data in old turns with a placeholder.

    Keeps the first user message (goal + initial screenshot) and the last
    *keep_recent* message pairs intact.  Older tool_result images are
    replaced with ``[screenshot omitted]``.
    """
    if len(messages) <= keep_recent + 1:
        return
    prune_end = len(messages) - keep_recent
    for idx in range(1, prune_end):
        msg = messages[idx]
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            # tool_result → strip images inside "content" list
            if part.get("type") == "tool_result" and isinstance(part.get("content"), list):
                new_inner: list[dict] = []
                for inner in part["content"]:
                    if isinstance(inner, dict) and inner.get("type") == "image":
                        new_inner.append({"type": "text", "text": "[screenshot omitted]"})
                    else:
                        new_inner.append(inner)
                part["content"] = new_inner
            # Standalone images in user messages
            elif part.get("type") == "image":
                part.clear()
                part["type"] = "text"
                part["text"] = "[screenshot omitted]"

