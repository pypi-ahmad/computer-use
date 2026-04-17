"""Session-scoped logging context.

Propagates a per-request ``session_id`` through ``logging.LogRecord``
attributes via a :class:`contextvars.ContextVar`. Any code that runs
inside the context set by :func:`bind_session_id` (or
:func:`session_context`) — including async tasks spawned from it — will
have its log lines automatically tagged with the session_id.

This avoids threading a custom LoggerAdapter through every module
(agent loop, engine, screenshot, docker_manager, etc.) while still
giving operators a reliable correlation id when two sessions run
concurrently.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager

# Default empty string so logs outside a session just render as "-".
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default=""
)


class SessionIdFilter(logging.Filter):
    """Inject the current ``session_id`` ContextVar into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        sid = session_id_var.get("")
        # Short form (first 8 chars) keeps log lines readable.
        record.session_id = sid[:8] if sid else "-"
        return True


def install(root_logger: logging.Logger | None = None) -> None:
    """Attach the :class:`SessionIdFilter` to every handler on *root_logger*.

    Idempotent — safe to call multiple times.
    """
    root_logger = root_logger or logging.getLogger()
    for handler in root_logger.handlers:
        if not any(isinstance(f, SessionIdFilter) for f in handler.filters):
            handler.addFilter(SessionIdFilter())


def bind_session_id(session_id: str) -> contextvars.Token:
    """Bind *session_id* into the current async context.

    Returns the :class:`~contextvars.Token` so the caller can later call
    :func:`session_id_var.reset(token)` to restore the previous value.
    """
    return session_id_var.set(session_id or "")


@contextmanager
def session_context(session_id: str):
    """Context manager that binds *session_id* for the duration of the block."""
    token = bind_session_id(session_id)
    try:
        yield
    finally:
        session_id_var.reset(token)
