from __future__ import annotations
# === merged from backend/logging_ctx.py ===
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

# === merged from backend/tracing.py ===
"""In-process session trace recorder.

A trace is the ordered list of semantically-meaningful events that
happened during one agent session: model turns, tool batches, safety
approvals (and their resolution), retries, and the terminal event.
Traces are the input to the offline eval harness in ``evals/`` and
the artefact operators look at when triaging a run after the fact.

Design goals
------------

* **Additive.** Tracing records explicit session events without
  restructuring normal application logging.

* **Cheap.** :func:`record` is a dict append plus a monotonic clock
  read. The hot path never touches disk. Traces are flushed as a
  sidecar JSON file only on terminal events via :func:`flush`.

* **Redacted.** Screenshots are replaced with a SHA-256 digest +
  length; free-text fields (model output, log messages) are run
  through :func:`backend.engine.scrub_secrets`; API keys are never
  accepted into a payload. This is belt-and-braces on top of the
  engine-level scrubbing that already happens before the trace
  recorder sees the event.

* **Replayable.** :func:`load_trace` + :func:`iter_events` let an
  offline eval iterate a finished session's events without touching
  a real provider.

CLI
---

.. code-block:: console

    $ python -m backend.infra.observability dump <session_id>

Dumps the JSON trace for *session_id* from the configured trace
directory (``$CUA_TRACE_DIR``, default ``~/.computer-use/traces/``).
"""


import hashlib
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

# Stage names used in recorded events. Kept as plain strings
# (not an enum) so tests can assert on them without extra imports.
STAGE_SESSION = "session"
STAGE_PREFLIGHT = "preflight"
STAGE_MODEL_TURN = "model_turn"
STAGE_TOOL_BATCH = "tool_batch"
STAGE_APPROVAL = "approval"
STAGE_RETRY = "retry"
STAGE_FINALIZE = "finalize"
STAGE_ENGINE = "engine"


# Event types. Mirror the :class:`TurnEvent` union plus lifecycle
# bookkeeping events the recorder synthesises.
EVT_SESSION_START = "session_start"
EVT_SESSION_END = "session_end"
EVT_MODEL_TURN_STARTED = "model_turn_started"
EVT_TOOL_BATCH_COMPLETED = "tool_batch_completed"
EVT_SAFETY_REQUIRED = "safety_required"
EVT_APPROVAL_RESOLVED = "approval_resolved"
EVT_RUN_COMPLETED = "run_completed"
EVT_RUN_FAILED = "run_failed"
EVT_RETRY = "retry"
EVT_LOG = "log"
EVT_STEP = "step"


@dataclass
class TraceEvent:
    """One recorded event.

    Attributes
    ----------
    ts:
        Wall-clock unix timestamp (seconds since epoch, float).
    monotonic:
        Monotonic clock at record time. Used for interval math within
        the process; not comparable across processes.
    session_id:
        The session this event belongs to.
    stage:
        Pipeline stage that produced the event. One of
        the ``STAGE_*`` constants above.
    event_type:
        Semantic type of the event. One of the ``EVT_*`` constants.
    duration_ms:
        Optional elapsed-time measurement, for events that close a
        span (e.g. a model turn).
    payload:
        Event-specific redacted data. Always a dict.
    """

    ts: float
    monotonic: float
    session_id: str
    stage: str
    event_type: str
    duration_ms: Optional[float] = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------

_SCREENSHOT_KEYS = ("screenshot_b64", "screenshot", "image_b64")


def _digest(value: str) -> str:
    """Return ``sha256:<hex>`` for *value*. Empty input gets a stable marker."""
    if not value:
        return "sha256:empty"
    return "sha256:" + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _redact(payload: Any, *, _depth: int = 0) -> Any:
    """Return a deep-copied, redacted version of *payload*.

    Rules:
      * Any key whose name suggests a screenshot blob is replaced
        with ``{"sha256": <hex>, "len": <int>}``.
      * String values are passed through
        :func:`backend.engine.scrub_secrets` to strip API-key-shaped
        tokens before they land on disk.
      * Recursion is depth-capped to prevent pathological payloads
        from blowing the call stack.
    """
    if _depth > 8:
        return "<max-depth>"
    # Import lazily — keeps import-time cycles out of the way and lets
    # tests stub ``backend.engine`` without paying for its transitive
    # dependencies (httpx, anthropic SDK, etc.) just to exercise the
    # recorder.
    from backend.engine import scrub_secrets

    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if k in _SCREENSHOT_KEYS and isinstance(v, str):
                out[k] = {"sha256": _digest(v), "len": len(v)}
                continue
            out[k] = _redact(v, _depth=_depth + 1)
        return out
    if isinstance(payload, (list, tuple)):
        return [_redact(v, _depth=_depth + 1) for v in payload]
    if isinstance(payload, str):
        # Cap serialized free-text fields so a runaway model dump can't
        # inflate trace files beyond a reasonable size.
        scrubbed = scrub_secrets(payload) or payload
        if len(scrubbed) > 4096:
            return scrubbed[:4096] + f"…<truncated {len(scrubbed) - 4096}>"
        return scrubbed
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    # Fallback — best-effort repr. Dataclasses / pydantic objects are
    # handled via ``asdict`` / ``model_dump`` at the call sites.
    return repr(payload)[:512]


# ---------------------------------------------------------------------------
# Session trace container + registry
# ---------------------------------------------------------------------------


@dataclass
class SessionTrace:
    """All events recorded for one session, plus lifecycle metadata."""

    session_id: str
    task: str = ""
    started_ts: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.monotonic)
    events: list[TraceEvent] = field(default_factory=list)
    finished_ts: Optional[float] = None
    status: Optional[str] = None  # terminal status — "completed"|"error"|"stopped"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task": self.task,
            "started_ts": self.started_ts,
            "finished_ts": self.finished_ts,
            "status": self.status,
            "events": [e.to_dict() for e in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionTrace":
        events = [TraceEvent(**e) for e in data.get("events", [])]
        return cls(
            session_id=str(data.get("session_id", "")),
            task=str(data.get("task", "")),
            started_ts=float(data.get("started_ts", 0.0)),
            started_monotonic=0.0,
            events=events,
            finished_ts=data.get("finished_ts"),
            status=data.get("status"),
        )


# Module-level registry. Access is serialised by ``_lock``.
_lock = threading.Lock()
_traces: dict[str, SessionTrace] = {}


def _get_or_create(session_id: str, task: str = "") -> SessionTrace:
    with _lock:
        tr = _traces.get(session_id)
        if tr is None:
            tr = SessionTrace(session_id=session_id, task=task)
            _traces[session_id] = tr
        elif task and not tr.task:
            tr.task = task
        return tr


def get_trace(session_id: str) -> SessionTrace | None:
    """Return the in-memory trace for *session_id* or ``None``."""
    with _lock:
        return _traces.get(session_id)


def drop_trace(session_id: str) -> None:
    """Remove *session_id* from the in-memory registry."""
    with _lock:
        _traces.pop(session_id, None)


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------


def _default_trace_dir() -> Path:
    """Return the on-disk trace directory, creating it if missing."""
    raw = os.getenv("CUA_TRACE_DIR", "").strip()
    if raw:
        base = Path(raw).expanduser()
    else:
        base = Path.home() / ".computer-use" / "traces"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        # Falling back to a temp dir keeps a broken operator config
        # from taking the whole session down. The warning lands in the
        # normal logs with the session_id attached.
        import tempfile
        fallback = Path(tempfile.gettempdir()) / "cua-traces"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "trace dir %s unusable (%s); using %s", base, exc, fallback,
        )
        base = fallback
    return base


def trace_path(session_id: str) -> Path:
    """Return the canonical on-disk trace path for *session_id*."""
    # Reject obviously-bad ids so a caller can't traverse out of the
    # trace directory. Session ids are UUIDs in production; be strict.
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    if not safe:
        safe = "unknown"
    return _default_trace_dir() / f"{safe}.json"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def flush(session_id: str) -> Path | None:
    """Persist and drop the in-memory trace for *session_id*.

    Returns the written path, or ``None`` if no trace existed.
    Called by :func:`finalize_session` on terminal events; operators
    generally shouldn't call it directly.
    """
    with _lock:
        tr = _traces.pop(session_id, None)
    if tr is None:
        return None
    path = trace_path(session_id)
    try:
        _write_json(path, tr.to_dict())
    except Exception as exc:
        logger.warning("failed to persist trace for %s: %s", session_id, exc)
        return None
    return path


def load_trace(session_id: str) -> SessionTrace | None:
    """Load the persisted trace for *session_id* or return ``None``."""
    path = trace_path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        logger.warning("failed to read trace %s: %s", path, exc)
        return None
    return SessionTrace.from_dict(data)


def iter_events(
    trace: SessionTrace,
    *,
    stage: str | None = None,
    event_type: str | None = None,
) -> Iterable[TraceEvent]:
    """Iterate *trace* events, optionally filtered by stage / event type."""
    for evt in trace.events:
        if stage is not None and evt.stage != stage:
            continue
        if event_type is not None and evt.event_type != event_type:
            continue
        yield evt


# ---------------------------------------------------------------------------
# Recording API
# ---------------------------------------------------------------------------


def start_session(session_id: str, *, task: str = "") -> SessionTrace:
    """Begin a trace for *session_id*. Idempotent."""
    tr = _get_or_create(session_id, task=task)
    record(session_id, STAGE_SESSION, EVT_SESSION_START, {"task": task})
    return tr


def finalize_session(
    session_id: str,
    *,
    status: str,
    write: bool = True,
) -> Path | None:
    """Close the trace for *session_id* and optionally flush to disk."""
    tr = _get_or_create(session_id)
    with _lock:
        tr.finished_ts = time.time()
        tr.status = status
    record(
        session_id, STAGE_SESSION, EVT_SESSION_END,
        {"status": status, "event_count": len(tr.events)},
    )
    if not write:
        return None
    return flush(session_id)


def record(
    session_id: str,
    stage: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    duration_ms: float | None = None,
) -> TraceEvent:
    """Append one event to the in-memory trace for *session_id*.

    Safe to call from any thread. Constant-time (list append + dict
    redaction). Accepts a ``None`` payload for events with no data.
    """
    if not session_id:
        # No-op when called outside a bound session — keeps the
        # instrumentation points free of ``if session_id:`` guards.
        return TraceEvent(
            ts=time.time(), monotonic=time.monotonic(),
            session_id="", stage=stage, event_type=event_type,
            duration_ms=duration_ms, payload={},
        )
    tr = _get_or_create(session_id)
    evt = TraceEvent(
        ts=time.time(),
        monotonic=time.monotonic(),
        session_id=session_id,
        stage=stage,
        event_type=event_type,
        duration_ms=duration_ms,
        payload=_redact(payload) if payload else {},
    )
    with _lock:
        tr.events.append(evt)
    return evt


# ---------------------------------------------------------------------------
# Replay harness (eval-side)
# ---------------------------------------------------------------------------


def replay(
    trace: SessionTrace,
    handler: Callable[[TraceEvent], None],
) -> None:
    """Feed each event of *trace* to *handler* in order.

    The evals use this to drive invariant checks over a recorded
    session without needing a live provider. *handler* may raise to
    stop iteration — :func:`replay` does not swallow exceptions.
    """
    for evt in trace.events:
        handler(evt)


def assert_invariants(trace: SessionTrace) -> None:
    """Raise :class:`AssertionError` if *trace* violates core invariants.

    Invariants checked:
      * Every session has exactly one ``session_start`` and one
        ``session_end``.
      * ``safety_required`` is always followed by an
        ``approval_resolved`` before the next ``tool_batch_completed``.
      * Terminal status ∈ {"completed", "error", "stopped"}.
    """
    starts = [e for e in trace.events if e.event_type == EVT_SESSION_START]
    ends = [e for e in trace.events if e.event_type == EVT_SESSION_END]
    assert len(starts) == 1, f"expected 1 session_start, got {len(starts)}"
    assert len(ends) == 1, f"expected 1 session_end, got {len(ends)}"
    assert trace.status in {"completed", "error", "stopped"}, (
        f"bad terminal status: {trace.status!r}"
    )
    pending = False
    for evt in trace.events:
        if evt.event_type == EVT_SAFETY_REQUIRED:
            pending = True
        elif evt.event_type == EVT_APPROVAL_RESOLVED:
            pending = False
        elif evt.event_type == EVT_TOOL_BATCH_COMPLETED and pending:
            raise AssertionError(
                "tool_batch_completed emitted while a safety approval "
                "was still pending"
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m backend.infra.observability")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dump = sub.add_parser("dump", help="Dump a persisted trace as JSON")
    p_dump.add_argument("session_id")

    sub.add_parser("list", help="List session ids with persisted traces")

    args = parser.parse_args(argv)

    if args.cmd == "dump":
        tr = load_trace(args.session_id)
        if tr is None:
            print(f"no trace found for {args.session_id!r}", flush=True)
            return 2
        print(json.dumps(tr.to_dict(), indent=2))
        return 0
    if args.cmd == "list":
        d = _default_trace_dir()
        for p in sorted(d.glob("*.json")):
            print(p.stem)
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover — entry point only
    import sys
    raise SystemExit(_cli(sys.argv[1:]))

