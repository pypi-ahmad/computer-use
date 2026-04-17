"""Pydantic schema for WebSocket events broadcast to the frontend.

The backend historically broadcast loosely-typed ``{"event": ..., **data}``
dicts. This module is the single source of truth for the wire format:

* Every outbound event is a subclass of :class:`WSEvent` discriminated
  by the ``event`` field.
* :func:`validate_outbound` is called from :func:`backend.server._broadcast`
  so a typo or schema drift is logged instead of silently shipping bad
  JSON to every connected client.
* The matching TypeScript types live in ``frontend/src/types/ws.d.ts``
  — keep them in sync with any change here.

Kept intentionally permissive for forward compat: unknown events fall
through as :class:`GenericWSEvent` rather than being rejected.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class _WSEventBase(BaseModel):
    """Base for strongly-typed outbound events."""

    model_config = ConfigDict(extra="allow")  # forward-compat


class ScreenshotEvent(_WSEventBase):
    """Single screenshot (one-off broadcast for a step-bound frame)."""

    event: Literal["screenshot"] = "screenshot"
    screenshot: str = Field(description="base64 PNG")


class ScreenshotStreamEvent(_WSEventBase):
    """Continuous-stream screenshot frame sent from :func:`_stream_screenshots`."""

    event: Literal["screenshot_stream"] = "screenshot_stream"
    screenshot: str


class LogEvent(_WSEventBase):
    """Log line from the agent loop (includes safety_confirmation payloads)."""

    event: Literal["log"] = "log"
    log: dict[str, Any]


class StepEvent(_WSEventBase):
    """One step record appended to the session timeline."""

    event: Literal["step"] = "step"
    step: dict[str, Any]


class AgentFinishedEvent(_WSEventBase):
    """Terminal event for a session (status=completed|error|stopped)."""

    event: Literal["agent_finished"] = "agent_finished"
    session_id: str
    status: str
    steps: int


class AuthFailedEvent(_WSEventBase):
    """Agent-service auth failure surfaced to the UI after a container restart."""

    event: Literal["auth_failed"] = "auth_failed"
    status: int
    message: str


class PongEvent(_WSEventBase):
    """Heartbeat reply to a client-sent ``ping``."""

    event: Literal["pong"] = "pong"


class GenericWSEvent(_WSEventBase):
    """Forward-compat fallback for events not yet modelled here."""

    event: str


WSEvent = Union[
    ScreenshotEvent,
    ScreenshotStreamEvent,
    LogEvent,
    StepEvent,
    AgentFinishedEvent,
    AuthFailedEvent,
    PongEvent,
    GenericWSEvent,
]


_TYPED_EVENTS: dict[str, type[_WSEventBase]] = {
    "screenshot": ScreenshotEvent,
    "screenshot_stream": ScreenshotStreamEvent,
    "log": LogEvent,
    "step": StepEvent,
    "agent_finished": AgentFinishedEvent,
    "auth_failed": AuthFailedEvent,
    "pong": PongEvent,
}


def validate_outbound(event: str, data: dict[str, Any]) -> Optional[str]:
    """Validate a dict payload against the registered event schema.

    Returns ``None`` if the payload is valid, otherwise a short string
    describing the first validation error. The caller (broadcast layer)
    logs this and still ships the payload — the intent is an early-
    warning for schema drift without breaking the user-facing stream.
    """
    model = _TYPED_EVENTS.get(event)
    if model is None:
        return None  # unknown event — allowed for forward compat
    try:
        model.model_validate({"event": event, **data})
    except Exception as exc:  # pydantic.ValidationError or value errors
        return str(exc).splitlines()[0][:200]
    return None
