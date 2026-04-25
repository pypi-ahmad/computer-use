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
import datetime as _dt
import json
import logging
import os
from contextlib import contextmanager

# Default empty string so logs outside a session just render as "-".
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default=""
)


# Env-var gate for the log-record format. Default "console" keeps the
# familiar human-readable stderr line shape; "json" switches every
# handler on the root logger to a single-line JSON record per line
# for operators who pipe logs into a collector. Case-insensitive.
_LOG_FORMAT_ENV = "LOG_FORMAT"
_LOG_LEVEL_ENV = "LOG_LEVEL"


class JsonFormatter(logging.Formatter):
    """Single-line JSON formatter with session-id correlation.

    Emits: timestamp (ISO-8601 UTC, millisecond precision), level,
    logger name, message, and session_id (attached by
    :class:`SessionIdFilter`; "-" if no session is bound). If a log
    record carries ``exc_info`` the formatter appends ``exc_type``
    and the stringified traceback so downstream JSON collectors can
    index on exception class.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        ts = _dt.datetime.fromtimestamp(
            record.created, tz=_dt.timezone.utc,
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        payload: dict[str, object] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "session_id": getattr(record, "session_id", "-"),
        }
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    level: str | None = None,
    fmt: str | None = None,
    root_logger: logging.Logger | None = None,
) -> None:
    """Install the session-id filter and (optionally) a JSON formatter.

    Call once at process startup before any log records are emitted.
    Respects two env vars:

    * ``LOG_LEVEL`` — ``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR``.
      Default ``INFO``. Explicit ``level`` kwarg wins.
    * ``LOG_FORMAT`` — ``console`` (default) or ``json``. Explicit
      ``fmt`` kwarg wins. Unknown values fall back to ``console`` and
      emit a warning.

    Idempotent: calling twice reconfigures handlers rather than
    doubling filters.
    """
    level_name = (level or os.environ.get(_LOG_LEVEL_ENV, "INFO")).strip().upper()
    fmt_name = (fmt or os.environ.get(_LOG_FORMAT_ENV, "console")).strip().lower()
    root = root_logger or logging.getLogger()
    root.setLevel(getattr(logging, level_name, logging.INFO))

    if fmt_name == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        if fmt_name not in ("console", ""):
            logging.getLogger(__name__).warning(
                "Unknown LOG_FORMAT=%r; falling back to 'console'", fmt_name,
            )
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(session_id)s] %(name)s: %(message)s",
        )

    if not root.handlers:
        handler: logging.Handler = logging.StreamHandler()
        root.addHandler(handler)
    for handler in root.handlers:
        handler.setFormatter(formatter)

    install(root)


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
