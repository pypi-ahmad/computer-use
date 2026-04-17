"""Cross-module registry for the CU engine's safety-confirmation handshake.

The native Computer Use engine surfaces ``require_confirmation`` prompts
synchronously through :class:`backend.agent.loop.AgentLoop`. The REST
endpoint that resolves those prompts lives in :mod:`backend.server`.
Putting the shared state here avoids a backward import from
``backend.agent.loop`` into ``backend.server``.
"""

from __future__ import annotations

import asyncio


# Maps session_id → asyncio.Event the agent loop awaits.
events: dict[str, asyncio.Event] = {}

# Maps session_id → user's decision (True = confirm, False = deny).
decisions: dict[str, bool] = {}


def get_or_create_event(session_id: str) -> asyncio.Event:
    """Return an existing event for the session or create a fresh one."""
    evt = events.get(session_id)
    if evt is None:
        evt = asyncio.Event()
        events[session_id] = evt
    return evt


def clear(session_id: str) -> None:
    """Drop any pending event and decision for the session."""
    events.pop(session_id, None)
    decisions.pop(session_id, None)
