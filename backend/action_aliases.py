"""Action alias resolution for the computer_use engine.

Normalizes action aliases to canonical ActionType values.

Usage::

    from backend.action_aliases import resolve_action

    resolved = resolve_action("press")  # → "key"
"""

from __future__ import annotations

# ── Alias map: variant name → canonical ActionType value ──────────────────────
# Only aliases whose targets are valid computer_use actions or ActionType members.

ACTION_ALIASES: dict[str, str] = {
    # Mouse
    "left_click": "click",
    "click_element": "click",
    "dblclick": "double_click",
    "rightclick": "right_click",
    "context_click": "right_click",
    "mouseover": "hover",
    "mouse_move": "hover",
    "mousemove": "hover",
    "drag_and_drop": "drag",
    "drag_drop": "drag",
    # Keyboard
    "press": "key",
    "press_key": "key",
    "keypress": "key",
    "send_keys": "key",
    "type_text": "type",
    "input_text": "type",
    "input": "type",
    "enter_text": "type",
    "write": "type",
    "fill_form": "fill",
    "set_value": "fill",
    # Navigation
    "navigate": "open_url",
    "goto": "open_url",
    "go_to": "open_url",
    "go": "open_url",
    "open": "open_url",
    "visit": "open_url",
    "browse": "open_url",
    "back": "go_back",
    "forward": "go_forward",
    # Scrolling
    "scroll_up": "scroll",
    "scroll_down": "scroll",
    # DOM / JS
    "extract_text": "get_text",
    "read_text": "get_text",
    "get_content": "get_text",
    "eval_js": "evaluate_js",
    "execute_js": "evaluate_js",
    "run_js": "evaluate_js",
    "javascript": "evaluate_js",
    # Control
    "sleep": "wait",
    "pause": "wait",
    "delay": "wait",
    # Terminal
    "complete": "done",
    "finish": "done",
    "finished": "done",
    "success": "done",
    "fail": "error",
    "abort": "error",
}



def resolve_action(action: str) -> str:
    """Resolve an action string to its canonical ActionType value.

    Returns the canonical action name, or the original string if no alias match.
    """
    normalized = action.strip().lower()
    return ACTION_ALIASES.get(normalized, normalized)
