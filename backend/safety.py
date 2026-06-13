from __future__ import annotations
"""Cross-module registry for the CU engine's safety-confirmation handshake.

The native Computer Use engine surfaces ``require_confirmation`` prompts
synchronously through :class:`backend.loop.AgentLoop`. The REST
endpoint that resolves those prompts lives in :mod:`backend.server`.
Putting the shared state here avoids a backward import from
``backend.loop`` into ``backend.server``.
"""


import asyncio
import hmac
import secrets


# Maps session_id → asyncio.Event the agent loop awaits.
events: dict[str, asyncio.Event] = {}

# Maps session_id → user's decision (True = confirm, False = deny).
decisions: dict[str, bool] = {}

# Maps session_id → per-prompt nonce. The confirm endpoint must echo this
# nonce, so an unrelated caller who can reach the port cannot resolve another
# session's safety prompt (S7).
nonces: dict[str, str] = {}


def get_or_create_event(session_id: str) -> asyncio.Event:
    """Return an existing event for the session or create a fresh one."""
    evt = events.get(session_id)
    if evt is None:
        evt = asyncio.Event()
        events[session_id] = evt
    return evt


def arm(session_id: str) -> tuple[str, asyncio.Event]:
    """Arm a fresh safety prompt for *session_id*; return ``(nonce, event)``.

    Creates a brand-new cleared :class:`asyncio.Event` (overwriting any stale
    one), drops any prior decision, and mints a per-prompt nonce. Called by the
    loop BEFORE broadcasting the prompt, so the confirm endpoint only ever
    ``.set()``s an already-armed event — closing the create/clear race (B2)
    where a confirmation could be lost and silently denied.
    """
    evt = asyncio.Event()  # fresh + cleared
    events[session_id] = evt
    decisions.pop(session_id, None)
    nonce = secrets.token_urlsafe(32)
    nonces[session_id] = nonce
    return nonce, evt


def verify_nonce(session_id: str, supplied: str) -> bool:
    """Constant-time check that *supplied* matches the session's armed nonce."""
    expected = nonces.get(session_id)
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def set_decision(session_id: str) -> bool:
    """Signal an already-armed event. Returns False if the session isn't armed.

    Unlike ``get_or_create_event(...).set()``, this never resurrects an event
    the loop is no longer awaiting (which would strand the confirmation).
    """
    evt = events.get(session_id)
    if evt is None:
        return False
    evt.set()
    return True


def clear(session_id: str) -> None:
    """Drop any pending event, decision, and nonce for the session."""
    events.pop(session_id, None)
    decisions.pop(session_id, None)
    nonces.pop(session_id, None)
