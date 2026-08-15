"""Strict vendor-response boundaries producing provider-neutral actions."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CanonicalActionType(enum.StrEnum):
    CLICK = "CLICK"
    DOUBLE_CLICK = "DOUBLE_CLICK"
    TYPE = "TYPE"
    KEY = "KEY"
    SCROLL = "SCROLL"
    MOVE = "MOVE"
    DRAG = "DRAG"
    SCREENSHOT = "SCREENSHOT"
    WAIT = "WAIT"


class CanonicalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: CanonicalActionType
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    end_x: int | None = Field(default=None, ge=0)
    end_y: int | None = Field(default=None, ge=0)
    text: str | None = None
    keys: list[str] | None = None
    delta_x: int | None = None
    delta_y: int | None = None


class ProtocolActionError(ValueError):
    """An untrusted vendor response could not be converted safely."""


_NAMES = {
    "click": CanonicalActionType.CLICK,
    "click_at": CanonicalActionType.CLICK,
    "double_click": CanonicalActionType.DOUBLE_CLICK,
    "type": CanonicalActionType.TYPE,
    "type_text": CanonicalActionType.TYPE,
    "type_text_at": CanonicalActionType.TYPE,
    "keypress": CanonicalActionType.KEY,
    "key": CanonicalActionType.KEY,
    "key_combination": CanonicalActionType.KEY,
    "press_key": CanonicalActionType.KEY,
    "hotkey": CanonicalActionType.KEY,
    "key_down": CanonicalActionType.KEY,
    "key_up": CanonicalActionType.KEY,
    "scroll": CanonicalActionType.SCROLL,
    "scroll_at": CanonicalActionType.SCROLL,
    "mouse_move": CanonicalActionType.MOVE,
    "hover_at": CanonicalActionType.MOVE,
    "move": CanonicalActionType.MOVE,
    "drag": CanonicalActionType.DRAG,
    "drag_and_drop": CanonicalActionType.DRAG,
    "screenshot": CanonicalActionType.SCREENSHOT,
    "take_screenshot": CanonicalActionType.SCREENSHOT,
    "wait": CanonicalActionType.WAIT,
    "mouse_down": CanonicalActionType.CLICK,
    "mouse_up": CanonicalActionType.CLICK,
}


def _canonical(name: str, arguments: dict[str, Any]) -> CanonicalAction:
    action_type = _NAMES.get(name.lower())
    if action_type is None:
        raise ProtocolActionError(f"Unsupported computer action: {name}")
    coordinates = arguments.get("coordinate") or arguments.get("coordinates")
    x = arguments.get("x", arguments.get("start_x"))
    y = arguments.get("y", arguments.get("start_y"))
    if isinstance(coordinates, list) and len(coordinates) >= 2:
        x, y = coordinates[:2]
    end = arguments.get("end_coordinate") or arguments.get("endCoordinates")
    end_x = arguments.get("end_x", arguments.get("destination_x"))
    end_y = arguments.get("end_y", arguments.get("destination_y"))
    if isinstance(end, list) and len(end) >= 2:
        end_x, end_y = end[:2]
    keys = arguments.get("keys")
    if isinstance(keys, str):
        keys = [keys]
    try:
        return CanonicalAction(
            type=action_type,
            x=x,
            y=y,
            end_x=end_x,
            end_y=end_y,
            text=arguments.get("text"),
            keys=keys,
            delta_x=arguments.get("delta_x", arguments.get("scroll_x")),
            delta_y=arguments.get("delta_y", arguments.get("scroll_y")),
        )
    except ValidationError as exc:
        raise ProtocolActionError("Invalid computer action coordinates or arguments") from exc


def parse_openai_action(payload: dict[str, Any]) -> CanonicalAction:
    """Parse OpenAI/Azure `computer_call.action`."""
    action = payload.get("action", payload)
    if not isinstance(action, dict) or not isinstance(action.get("type"), str):
        raise ProtocolActionError("OpenAI computer action is missing action.type")
    return _canonical(action["type"], action)


def parse_anthropic_action(payload: dict[str, Any]) -> CanonicalAction:
    """Parse Anthropic direct/Bedrock/Vertex computer tool input."""
    tool_input = payload.get("input", payload)
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("action"), str):
        raise ProtocolActionError("Anthropic computer action is missing input.action")
    return _canonical(tool_input["action"], tool_input)


def parse_gemini_action(payload: dict[str, Any]) -> CanonicalAction:
    """Parse Gemini direct/Vertex function-call name and args."""
    call = payload.get("functionCall", payload.get("function_call", payload))
    if not isinstance(call, dict) or not isinstance(call.get("name"), str):
        raise ProtocolActionError("Gemini computer action is missing functionCall.name")
    arguments = call.get("args", {})
    if not isinstance(arguments, dict):
        raise ProtocolActionError("Gemini computer action args must be an object")
    return _canonical(call["name"], arguments)
