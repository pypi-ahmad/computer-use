"""System prompt for the Computer Use engine.

Provides a single ``get_system_prompt("computer_use", mode)`` entry-point
used by :class:`backend.agent.loop.AgentLoop`.  The prompt covers both
*browser* and *desktop* modes and is compatible with the Gemini and
Anthropic native CU tool protocols.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Computer Use (native CU tool protocol) ───────────────────────────────────
# Gemini prompt covers both action naming and coordination semantics.
# Claude gets a minimal prompt since Anthropic auto-injects CU action schema.

SYSTEM_PROMPT_GEMINI_CU = """\
You are a computer-using agent that completes tasks by interacting with the screen.

You have native computer_use capabilities. The system will convert your tool calls
into real UI interactions (mouse clicks, keyboard input, scrolling, navigation).

ENVIRONMENT:
- Screen resolution: {viewport_width}x{viewport_height} (browser) or 1440x900 (desktop).
- Browser: Chromium via Playwright (browser mode) or any X11 application (desktop mode).
- Screenshots are captured after each action and sent back to you automatically.

INTERACTION RULES:
1. Use your built-in computer_use tool for all UI interactions — do NOT describe
   actions in text; emit tool calls.
2. Analyse each screenshot carefully before acting. Identify exact positions of
   buttons, links, text fields, and other interactive elements.
3. Click precisely at the CENTER of UI elements — avoid edges.
4. For text entry: click the input field first (click_at), then type (type_text_at).
   By default type_text_at clears the field and presses Enter; set press_enter=false
   or clear_before_typing=false to override.
5. Scroll to find content not yet visible (scroll_document or scroll_at).
6. Use key_combination for keyboard shortcuts (e.g., "Enter", "Control+C", "Tab").
7. Use navigate to go to a specific URL directly.
8. Use go_back / go_forward for browser history navigation.
9. Use wait_5_seconds when a page or application needs time to load.

COMPLETION:
- When the task is complete, state the result clearly in your final text response.
  Do NOT emit a tool call in your final turn.
- If you are stuck after 3 attempts at the same action, explain the blocker in text.

SAFETY:
- Some actions may include a safety_decision requiring confirmation. Follow the
  system's guidance.
- Do NOT interact with CAPTCHAs or security challenges unless you receive explicit
  user confirmation.
- Do NOT enter passwords, credit card numbers, or other sensitive data unless the
  task explicitly requires it and you have user confirmation.

IMPORTANT:
- You see the FULL screen (browser viewport or desktop).
- Coordinates are normalized (0-999 grid) — the system handles pixel scaling.
"""

SYSTEM_PROMPT_CLAUDE_CU = """\
You are a computer-using agent that completes tasks by interacting with the screen.

You have native computer_use capabilities. Use your built-in computer tool for all
UI interactions — do NOT describe actions in text; emit tool calls.

ENVIRONMENT:
- Screen resolution: {viewport_width}x{viewport_height} (browser) or 1440x900 (desktop).
- Browser: Chromium via Playwright (browser mode) or any X11 application (desktop mode).
- Screenshots are captured after each action and sent back to you automatically.

INTERACTION RULES:
1. Analyse each screenshot carefully before acting.
2. Click precisely at the CENTER of UI elements — avoid edges.
3. Coordinates are real pixel values matching the reported display dimensions.

COMPLETION:
- When the task is complete, state the result clearly in your final text response.
  Do NOT emit a tool call in your final turn.
- If you are stuck after 3 attempts at the same action, explain the blocker in text.

SAFETY:
- Do NOT interact with CAPTCHAs or security challenges unless you receive explicit
  user confirmation.
- Do NOT enter passwords, credit card numbers, or other sensitive data unless the
  task explicitly requires it and you have user confirmation.
"""

# Default prompt used for action-drift validation (points to Gemini prompt).
_DEFAULT_PROMPT_FOR_VALIDATION = SYSTEM_PROMPT_GEMINI_CU


def get_system_prompt(
    engine: str = "computer_use",
    mode: str = "browser",
    *,
    provider: str = "google",
    **_kwargs: Any,
) -> str:
    """Return the system prompt for the computer_use engine.

    Parameters
    ----------
    engine:
        Must be ``"computer_use"``.  Any other value logs a warning and
        still returns the CU prompt (single-engine app).
    mode:
        ``"browser"`` or ``"desktop"`` — used only for viewport injection.
    provider:
        ``"google"`` or ``"anthropic"`` — selects the provider-appropriate
        prompt template.
    **_kwargs:
        Accepted for backward compatibility (e.g. ``discovered_tools``
        from old callers) but ignored.
    """
    from backend.config import config

    if engine != "computer_use":
        logger.warning(
            "get_system_prompt called with engine=%r — only 'computer_use' is supported; "
            "returning CU prompt anyway",
            engine,
        )

    # Actual viewport dimensions (must match agent_service.py browser init)
    vw = str(config.screen_width - 100)
    vh = str(config.screen_height - 80)

    template = SYSTEM_PROMPT_CLAUDE_CU if provider == "anthropic" else SYSTEM_PROMPT_GEMINI_CU
    return (
        template
        .replace("{viewport_width}", vw)
        .replace("{viewport_height}", vh)
    )


# ── Prompt / Schema drift detection ──────────────────────────────────────────

# Regex that captures bare action names from prompt text
# Matches lines like: "  click_at          — Left-click at ..."
_ACTION_LINE_RE = re.compile(r"^\s{1,4}(\w+)\s+—", re.MULTILINE)


def _extract_prompt_actions(prompt_text: str) -> set[str]:
    """Extract action keywords from a system prompt string."""
    return {m.group(1) for m in _ACTION_LINE_RE.finditer(prompt_text)}


def validate_prompt_actions() -> list[str]:
    """Cross-check actions mentioned in the CU prompt against the capability schema.

    Returns a list of human-readable warning strings.  An empty list means
    full alignment.  Called at server startup to surface drift early.
    """
    from backend.engine_capabilities import EngineCapabilities

    caps = EngineCapabilities()
    warnings: list[str] = []

    prompt_actions = _extract_prompt_actions(_DEFAULT_PROMPT_FOR_VALIDATION)
    if not prompt_actions:
        return warnings

    schema_actions = caps.get_engine_actions("computer_use")

    extra = prompt_actions - schema_actions
    if extra:
        msg = (
            "[Computer Use] Prompt mentions actions not in engine_capabilities.json: "
            f"{sorted(extra)}"
        )
        warnings.append(msg)
        logger.warning(msg)

    missing = schema_actions - prompt_actions - {"done", "error"}
    if missing:
        logger.debug(
            "[Computer Use] Schema actions not in prompt (OK, prompts are curated): %s",
            sorted(missing),
        )

    return warnings
