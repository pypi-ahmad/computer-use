"""Gemini Computer Use client — split out of ``backend.engine`` (Q2).

The class body lives here; ``backend.engine`` re-exports it so imports
like ``from backend.engine import GeminiCUClient`` keep working.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, AsyncIterator, Callable

from backend.engine import (
    CUActionResult,
    CUTurnRecord,
    SafetyDecision,
    ActionExecutor,
    Environment,
    ModelTurnStarted,
    RunCompleted,
    SafetyRequired,
    ToolBatchCompleted,
    TurnEvent,
    _call_with_retry,
    _invoke_safety,
    DEFAULT_TURN_LIMIT,
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


# ---------------------------------------------------------------------------
# S4 — Gemini browser routing + Playwright opt-in
# ---------------------------------------------------------------------------

# Google's Gemini 3 Flash Preview reference implementation
# (github.com/google-gemini/computer-use-preview) drives a Chromium instance
# under Playwright.  The repo's default xdotool/full-desktop harness is
# compatible (the model returns normalized 0-999 coordinates which
# ``DesktopExecutor._denormalize_coords`` maps to any viewport), but when
# the agent has a choice of browser for a Gemini session, Chromium is the
# reference match.  This helper mirrors that preference so the Gemini adapter
# (and its tests) can assert "Chromium first, Firefox-ESR fallback with a
# warning" without disturbing the OpenAI / Anthropic paths.

_GEMINI_CHROMIUM_CANDIDATES: tuple[str, ...] = (
    "chromium-browser",
    "chromium",
)
_GEMINI_FIREFOX_FALLBACKS: tuple[str, ...] = (
    "firefox-esr",
    "firefox",
)


def _gemini_resolve_browser_binary(
    which: Callable[[str], str | None] | None = None,
    log: Callable[[str, str], None] | None = None,
) -> str | None:
    """Return the first available browser binary path for a Gemini session.

    Preference: ``chromium-browser`` → ``chromium`` → ``firefox-esr`` →
    ``firefox``.  Emits a single WARNING via *log* (or the module logger)
    when no Chromium flavour is installed and a Firefox fallback is used,
    because Google's reference implementation is Chromium-only.

    *which* defaults to ``shutil.which`` and is overridable for tests.
    Returns ``None`` when no browser is found at all.
    """
    import shutil

    if which is None:
        which = shutil.which

    for name in _GEMINI_CHROMIUM_CANDIDATES:
        path = which(name)
        if path:
            return path

    for name in _GEMINI_FIREFOX_FALLBACKS:
        path = which(name)
        if path:
            msg = (
                "Gemini: Chromium not installed; falling back to %s. "
                "Google's reference implementation uses Chromium." % name
            )
            if log is not None:
                log("warning", msg)
            else:
                logger.warning(msg)
            return path

    return None


def _gemini_playwright_enabled() -> bool:
    """Return True only when the opt-in Playwright path is requested AND
    the ``playwright`` package is actually importable.

    Controlled by ``CUA_GEMINI_USE_PLAYWRIGHT=1``.  Off by default so the
    image stays lean (Playwright ships ~500 MB of browser bundles).  When
    the flag is set but Playwright is not installed, logs an error and
    returns False so the caller falls back to the xdotool path cleanly.
    """
    if os.environ.get("CUA_GEMINI_USE_PLAYWRIGHT") != "1":
        return False
    try:
        import playwright  # noqa: F401
    except ImportError:
        logger.error(
            "CUA_GEMINI_USE_PLAYWRIGHT=1 but playwright is not installed. "
            "Rebuild the image with --build-arg INSTALL_PLAYWRIGHT=1 or "
            "pip install playwright && playwright install chromium. "
            "Falling back to the xdotool path for this session."
        )
        return False
    return True


class GeminiCUClient:
    """Native Gemini Computer Use tool protocol.

    API contract:
    - Declares ``types.Tool(computer_use=ComputerUse(...))``
    - Enables ``ThinkingConfig(thinking_level=\"high\")``
    - Sends screenshots inline in ``FunctionResponse`` parts
    - Handles ``safety_decision`` → ``require_confirmation``
    - Supports both ``ENVIRONMENT_BROWSER`` and ``ENVIRONMENT_DESKTOP``
    """

    # One-shot log guards so operators see the safety-threshold choice
    # exactly once per process, not once per turn.
    _relax_logged: bool = False
    _default_logged: bool = False

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
        # AI-3: read CUA_GEMINI_THINKING_LEVEL once at init so subsequent
        # env mutations don't change behaviour mid-session and we don't pay
        # the os.getenv cost on every _build_config call.
        _allowed_levels = {"minimal", "low", "medium", "high"}
        _level = os.getenv("CUA_GEMINI_THINKING_LEVEL", "high").lower()
        self._thinking_level = _level if _level in _allowed_levels else "high"

    async def _generate(self, *, contents: list, config: Any) -> Any:
        """Invoke Gemini generate_content via the native async SDK path.

        ``google-genai >= 1.0`` always exposes ``Client.aio.models``;
        the package is pinned in ``requirements.txt`` so this is the
        only supported call shape. The previous ``asyncio.to_thread``
        fallback was kept for older SDKs that no longer match the pin
        and has been removed.
        """
        return await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

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

        # Safety-threshold relaxation is opt-in.  Per Google's
        # safety-settings docs (2026-04), the default block threshold
        # on Gemini 2.5 / 3 models is already "Off" when the client
        # omits ``safety_settings``, so any additional relaxation
        # should be an explicit operator choice rather than an
        # implicit default.  Set ``CUA_GEMINI_RELAX_SAFETY=1`` to
        # attach BLOCK_ONLY_HIGH across the four HarmCategory buckets.
        # The ToS-mandated ``require_confirmation`` +
        # ``safety_acknowledgement`` handshake is unaffected either
        # way and remains the authoritative safety gate.
        safety_settings: list[Any] = []
        if os.environ.get("CUA_GEMINI_RELAX_SAFETY") == "1":
            if not GeminiCUClient._relax_logged:
                logger.info(
                    "Gemini CU safety relaxation enabled "
                    "(CUA_GEMINI_RELAX_SAFETY=1); attaching "
                    "BLOCK_ONLY_HIGH thresholds across HarmCategory "
                    "buckets.  ToS handshake unaffected.",
                )
                GeminiCUClient._relax_logged = True
            _HarmCategory = getattr(types, "HarmCategory", None)
            _SafetySetting = getattr(types, "SafetySetting", None)
            _HarmBlockThreshold = getattr(types, "HarmBlockThreshold", None)
            if _HarmCategory and _SafetySetting and _HarmBlockThreshold:
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
        elif not GeminiCUClient._default_logged:
            logger.warning(
                "Gemini CU using Google's default safety thresholds "
                "(Off for Gemini 2.5/3 per docs).  Set "
                "CUA_GEMINI_RELAX_SAFETY=1 to restore the previous "
                "BLOCK_ONLY_HIGH behaviour.",
            )
            GeminiCUClient._default_logged = True

        # Use thinking_level (recommended for Gemini 3) instead of
        # legacy include_thoughts / budget_tokens.
        _ThinkingConfig = types.ThinkingConfig
        _thinking_kwargs: dict[str, Any] = {}
        # AI-3: thinking level was resolved once in __init__.
        # Prefer thinking_level=<level> if the SDK supports it
        import inspect as _inspect
        _tc_params = _inspect.signature(_ThinkingConfig).parameters
        if "thinking_level" in _tc_params:
            _thinking_kwargs["thinking_level"] = self._thinking_level
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

    async def iter_turns(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_log: Callable[[str, str], None] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Yield Gemini turn events for the LangGraph driver.

        Safety confirmations are emitted as :class:`SafetyRequired`
        events and resumed via ``agen.asend(bool)`` so the shared graph
        interrupt path owns the approval lifecycle.
        """
        types = self._types
        config = self._build_config()

        # Initial screenshot
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            yield RunCompleted(final_text="Error: Could not capture initial screenshot")
            return

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(text=goal),
                    types.Part.from_bytes(data=screenshot_bytes, mime_type=_IMAGE_PNG),
                ],
            )
        ]

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
                response = await _call_with_retry(
                    lambda: self._generate(contents=contents, config=config),
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
                        on_log("error",
                            "INVALID_ARGUMENT usually means: (1) screenshot too large/corrupt, "
                            "(2) model doesn't support computer_use tool, or "
                            "(3) conversation context exceeded limits. "
                            f"Contents length: {len(contents)} turns, "
                            f"last screenshot: {len(screenshot_bytes)} bytes")
                yield RunCompleted(final_text=f"Gemini API error: {error_msg}")
                return

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
                    response = await _call_with_retry(
                        lambda: self._generate(contents=contents, config=config),
                        provider="google",
                        on_log=on_log,
                        attempts=2,
                    )
                except Exception as retry_err:
                    if on_log:
                        on_log("error", f"Retry also failed: {retry_err}")
                    yield RunCompleted(
                        final_text=f"Error: Gemini returned no candidates and retry failed: {retry_err}",
                    )
                    return

                if not response.candidates:
                    if on_log:
                        on_log("error", f"Gemini returned no candidates even after retry at turn {turn + 1}")
                    yield RunCompleted(final_text="Error: Gemini returned no candidates (after retry)")
                    return

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

            for fc in function_calls:
                args = dict(fc.args) if fc.args else {}

                # Extract safety_decision BEFORE passing args to executor.
                # This ensures the acknowledgement is tracked regardless of
                # which executor implementation is used.
                safety_confirmed = False
                if "safety_decision" in args:
                    sd = args.pop("safety_decision")
                    if isinstance(sd, dict) and sd.get("decision") == "require_confirmation":
                        confirmed = yield SafetyRequired(
                            explanation=str(sd.get("explanation", "")),
                        )
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

            if terminated and not results:
                yield RunCompleted(final_text="Agent terminated: safety confirmation denied.")
                return

            yield ToolBatchCompleted(
                turn=turn + 1,
                model_text=turn_text,
                results=results,
                screenshot_b64=screenshot_b64 or None,
            )

            if terminated:
                yield RunCompleted(final_text="Agent terminated: safety confirmation denied.")
                return

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
                yield RunCompleted(final_text=turn_text or "Gemini returned no function responses.")
                return

            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(function_response=fr) for fr in function_responses],
                )
            )

        if _turn_start is not None and on_log:
            on_log("info", f"turn_duration_ms={int((time.monotonic()-_turn_start)*1000)} provider=google model={self._model}")
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
                    on_turn(CUTurnRecord(
                        turn=event.turn,
                        model_text=event.model_text or pending_turn_text,
                        actions=event.results,
                        screenshot_b64=event.screenshot_b64,
                    ))
                pending_turn_text = ""
                continue

            if isinstance(event, RunCompleted):
                final_text = event.final_text

        return final_text


# ---------------------------------------------------------------------------
# Claude Computer Use Client
# ---------------------------------------------------------------------------

