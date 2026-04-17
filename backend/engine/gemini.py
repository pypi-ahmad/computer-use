"""Gemini Computer Use client — split out of ``backend.engine`` (Q2).

The class body lives here; ``backend.engine`` re-exports it so imports
like ``from backend.engine import GeminiCUClient`` keep working.
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
    SafetyDecision,
    ActionExecutor,
    DesktopExecutor,
    Environment,
    _invoke_safety,
    _to_plain_dict,
    denormalize_x,
    denormalize_y,
    DEFAULT_SCREEN_WIDTH,
    DEFAULT_SCREEN_HEIGHT,
    DEFAULT_TURN_LIMIT,
    GEMINI_NORMALIZED_MAX,
    _CONTEXT_PRUNE_KEEP_RECENT,
    _IMAGE_PNG,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gemini Computer Use Client
# ---------------------------------------------------------------------------


def _prune_gemini_context(
    contents: list, types: Any, keep_recent: int,
) -> None:
    """Replace inline screenshot data in old turns with a text placeholder.

    Preserves the first message (goal + initial screenshot) and the last
    *keep_recent* Content entries.  Everything in between has its
    ``FunctionResponseBlob`` data stripped and image ``Part`` objects
    replaced with a text marker.
    """
    if len(contents) <= keep_recent + 1:
        return
    prune_end = len(contents) - keep_recent
    for idx in range(1, prune_end):
        content = contents[idx]
        if not hasattr(content, "parts") or not content.parts:
            continue
        new_parts = []
        pruned = False
        for part in content.parts:
            # Strip inline_data from FunctionResponsePart
            fr = getattr(part, "function_response", None)
            if fr is not None and hasattr(fr, "parts") and fr.parts:
                fr.parts.clear()
                pruned = True
            # Strip standalone image parts (from_bytes)
            if getattr(part, "inline_data", None) is not None:
                new_parts.append(types.Part(text="[screenshot omitted]"))
                pruned = True
            else:
                new_parts.append(part)
        if pruned:
            content.parts[:] = new_parts


class GeminiCUClient:
    """Native Gemini Computer Use tool protocol.

    API contract:
    - Declares ``types.Tool(computer_use=ComputerUse(...))``
    - Enables ``ThinkingConfig(thinking_level=\"high\")``
    - Sends screenshots inline in ``FunctionResponse`` parts
    - Handles ``safety_decision`` → ``require_confirmation``
    - Supports both ``ENVIRONMENT_BROWSER`` and ``ENVIRONMENT_DESKTOP``
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-flash-preview",
        environment: Environment = Environment.BROWSER,
        excluded_actions: list[str] | None = None,
        system_instruction: str | None = None,
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
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._environment = environment
        self._excluded = excluded_actions or []
        self._system_instruction = system_instruction

    def _get_env_enum(self) -> Any:
        """Map the Environment enum to the google-genai SDK environment constant."""
        types = self._types
        if self._environment == Environment.DESKTOP:
            desktop_env = getattr(types.Environment, "ENVIRONMENT_DESKTOP", None)
            if desktop_env is not None:
                return desktop_env
            logger.warning(
                "ENVIRONMENT_DESKTOP not available in google-genai SDK; "
                "falling back to ENVIRONMENT_BROWSER.  Desktop xdotool "
                "actions will still execute via DesktopExecutor."
            )
            return types.Environment.ENVIRONMENT_BROWSER
        return types.Environment.ENVIRONMENT_BROWSER

    def _build_config(self) -> Any:
        """Build the GenerateContentConfig with CU tools, safety, and thinking settings."""
        types = self._types
        tools = [
            types.Tool(
                computer_use=types.ComputerUse(
                    environment=self._get_env_enum(),
                    excluded_predefined_functions=self._excluded,
                )
            )
        ]

        # Relax safety thresholds so the model doesn't silently refuse when
        # seeing desktop screenshots that contain innocuous UI chrome the
        # safety classifier may over-flag (e.g. browser with sign-in pages,
        # system toolbars, ads).
        safety_settings = []
        _HarmCategory = getattr(types, "HarmCategory", None)
        _SafetySetting = getattr(types, "SafetySetting", None)
        _HarmBlockThreshold = getattr(types, "HarmBlockThreshold", None)
        if _HarmCategory and _SafetySetting and _HarmBlockThreshold:
            # Use BLOCK_ONLY_HIGH to avoid over-blocking desktop screenshots
            # while still filtering genuinely harmful content.
            block_level = getattr(_HarmBlockThreshold, "BLOCK_ONLY_HIGH", None)
            if block_level is not None:
                for cat_name in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                ):
                    cat = getattr(_HarmCategory, cat_name, None)
                    if cat is not None:
                        safety_settings.append(
                            _SafetySetting(category=cat, threshold=block_level)
                        )

        # Use thinking_level (recommended for Gemini 3) instead of
        # legacy include_thoughts / budget_tokens.
        _ThinkingConfig = types.ThinkingConfig
        _thinking_kwargs: dict[str, Any] = {}
        # Prefer thinking_level="high" if the SDK supports it
        import inspect as _inspect
        _tc_params = _inspect.signature(_ThinkingConfig).parameters
        if "thinking_level" in _tc_params:
            _thinking_kwargs["thinking_level"] = "high"
        else:
            # Fallback for older SDK versions
            _thinking_kwargs["include_thoughts"] = True

        kwargs: dict[str, Any] = {
            "tools": tools,
            "thinking_config": _ThinkingConfig(**_thinking_kwargs),
        }
        if safety_settings:
            kwargs["safety_settings"] = safety_settings
        if self._system_instruction:
            kwargs["system_instruction"] = self._system_instruction
        return self._genai.types.GenerateContentConfig(**kwargs)

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
        """Run the full Gemini CU agent loop.

        Args:
            goal: Natural language task.
            executor: Desktop executor used for the local runtime harness.
            turn_limit: Max loop iterations.
            on_safety: Callback(explanation) → bool. True=confirm, False=deny.
            on_turn: Progress callback per turn.
            on_log: Logging callback(level, message).

        Returns:
            Final text response from the model.
        """
        types = self._types
        config = self._build_config()

        # Initial screenshot
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            return "Error: Could not capture initial screenshot"

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(text=goal),
                    types.Part.from_bytes(data=screenshot_bytes, mime_type=_IMAGE_PNG),
                ],
            )
        ]

        final_text = ""
        _turn_start: float | None = None

        for turn in range(turn_limit):
            if _turn_start is not None and on_log:
                on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=google model={self._model}")
            _turn_start = time.monotonic()
            if on_log:
                on_log("info", f"Gemini CU turn {turn + 1}/{turn_limit}")

            # Prune old screenshots to prevent unbounded context growth.
            # Keep the first message (goal + initial screenshot) and
            # the most recent _CONTEXT_PRUNE_KEEP_RECENT turns intact.
            _prune_gemini_context(contents, types, _CONTEXT_PRUNE_KEEP_RECENT)

            try:
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self._model,
                    contents=contents,
                    config=config,
                )
            except Exception as api_err:
                error_msg = str(api_err)
                if on_log:
                    on_log("error", f"Gemini API error at turn {turn + 1}: {error_msg}")
                # Try to provide actionable info for common error patterns
                if "INVALID_ARGUMENT" in error_msg:
                    if on_log:
                        on_log("error",
                            "INVALID_ARGUMENT usually means: (1) screenshot too large/corrupt, "
                            "(2) model doesn't support computer_use tool, or "
                            "(3) conversation context exceeded limits. "
                            f"Contents length: {len(contents)} turns, "
                            f"last screenshot: {len(screenshot_bytes)} bytes")
                final_text = f"Gemini API error: {error_msg}"
                break

            if not response.candidates:
                if on_log:
                    on_log("warning", f"Gemini returned no candidates at turn {turn + 1} — retrying with nudge")

                # Retry once: append a user nudge reminding the model to
                # use computer_use tools and re-send with a fresh screenshot.
                try:
                    retry_ss = await executor.capture_screenshot()
                except Exception:
                    retry_ss = screenshot_bytes

                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=(
                                    "Please continue using the computer_use tools to "
                                    "complete the task. Here is the current screen."
                                )
                            ),
                            types.Part.from_bytes(
                                data=retry_ss, mime_type=_IMAGE_PNG
                            ),
                        ],
                    )
                )
                try:
                    response = await asyncio.to_thread(
                        self._client.models.generate_content,
                        model=self._model,
                        contents=contents,
                        config=config,
                    )
                except Exception as retry_err:
                    if on_log:
                        on_log("error", f"Retry also failed: {retry_err}")
                    final_text = f"Error: Gemini returned no candidates and retry failed: {retry_err}"
                    break

                if not response.candidates:
                    if on_log:
                        on_log("error", f"Gemini returned no candidates even after retry at turn {turn + 1}")
                    final_text = "Error: Gemini returned no candidates (after retry)"
                    break

            candidate = response.candidates[0]
            contents.append(candidate.content)

            # Extract function calls and text
            function_calls = [
                p.function_call for p in candidate.content.parts if p.function_call
            ]
            text_parts = [p.text for p in candidate.content.parts if p.text]
            turn_text = " ".join(text_parts)

            # No function calls → model is done
            if not function_calls:
                final_text = turn_text
                if on_log:
                    on_log("info", f"Gemini CU completed: {final_text[:200]}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=turn_text, actions=[]))
                break

            # Execute each function call
            results: list[CUActionResult] = []
            terminated = False

            for fc in function_calls:
                args = dict(fc.args) if fc.args else {}

                # Extract safety_decision BEFORE passing args to executor.
                # This ensures the acknowledgement is tracked regardless of
                # which executor implementation is used.
                safety_confirmed = False
                if "safety_decision" in args:
                    sd = args.pop("safety_decision")
                    if isinstance(sd, dict) and sd.get("decision") == "require_confirmation":
                        confirmed = await _invoke_safety(on_safety, sd.get("explanation", ""))
                        if not confirmed:
                            if on_log:
                                on_log("warning", f"Safety denied for {fc.name}")
                            terminated = True
                            break
                        safety_confirmed = True

                result = await executor.execute(fc.name, args)
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

            screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode() if screenshot_bytes else ""
            if on_turn:
                on_turn(CUTurnRecord(
                    turn=turn + 1, model_text=turn_text,
                    actions=results, screenshot_b64=screenshot_b64 or None,
                ))

            if terminated:
                final_text = "Agent terminated: safety confirmation denied."
                break

            # Build FunctionResponses with inline screenshot per Gemini CU docs:
            # https://ai.google.dev/gemini-api/docs/computer-use
            # Each FunctionResponse embeds the screenshot via
            #   parts=[FunctionResponsePart(inline_data=FunctionResponseBlob(...))]
            # The screenshot must NOT be sent as a separate Part.from_bytes().
            current_url = executor.get_current_url()
            screenshot_ok = bool(screenshot_bytes) and len(screenshot_bytes) >= 100

            function_responses = []
            for r in results:
                resp_data: dict[str, Any] = {"url": current_url}
                if r.error:
                    resp_data["error"] = r.error
                if r.safety_decision == SafetyDecision.REQUIRE_CONFIRMATION:
                    resp_data["safety_acknowledgement"] = "true"
                # Merge extra data, converting non-serializable types (tuples → lists)
                for k, v in r.extra.items():
                    if isinstance(v, tuple):
                        resp_data[k] = list(v)
                    elif isinstance(v, (str, int, float, bool, type(None), list, dict)):
                        resp_data[k] = v
                    else:
                        resp_data[k] = str(v)

                fr_kwargs: dict[str, Any] = {"name": r.name, "response": resp_data}

                if screenshot_ok:
                    fr_kwargs["parts"] = [
                        types.FunctionResponsePart(
                            inline_data=types.FunctionResponseBlob(
                                mime_type=_IMAGE_PNG,
                                data=screenshot_bytes,
                            )
                        )
                    ]

                function_responses.append(types.FunctionResponse(**fr_kwargs))

            # IMPORTANT: send ONLY FunctionResponse parts — no separate image Part
            if not function_responses:
                if on_log:
                    on_log("warning", "No function responses to send; ending loop")
                break

            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(function_response=fr) for fr in function_responses],
                )
            )

        if _turn_start is not None and on_log:
            on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=google model={self._model}")
        return final_text


# ---------------------------------------------------------------------------
# Claude Computer Use Client
# ---------------------------------------------------------------------------

