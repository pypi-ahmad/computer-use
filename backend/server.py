"""FastAPI application — REST endpoints + WebSocket streaming."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.infra.config import config, get_all_key_statuses, resolve_api_key
from backend.engine import default_openai_reasoning_effort_for_model as _default_openai_reasoning_effort_for_model
from backend.engine import validate_builtin_search_config as _validate_builtin_search_config
from backend import files as file_registry
from backend.models.schemas import load_allowed_models_json as _load_allowed_models_json
from backend.infra.observability import install as _install_sid_filter
from pydantic import BaseModel, ConfigDict, Field
from backend.models.schemas import (
    AgentAction,
    AgentSession,
    SessionStatus,
    StartTaskRequest,
    TaskStatusResponse,
)
from backend.loop import AgentLoop
from backend import safety as safety_registry
from backend.executor import capture_screenshot, check_service_health
from backend.infra.docker import (
    _run as _dm_run,
    build_image,
    get_container_status,
    get_state as get_container_state,
    start_container,
    stop_container,
)
from backend.models.validation import validate_tool_parity

import httpx

logging.basicConfig(
    level=logging.DEBUG if config.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s sid=%(session_id)s: %(message)s",
)
# Inject session_id into every LogRecord so concurrent sessions are
# distinguishable in the logs. Records outside a session render as '-'.
_install_sid_filter(logging.getLogger())
logger = logging.getLogger(__name__)


def _error_response(status_code: int, message: str) -> JSONResponse:
    """Return a uniformly-shaped JSON error response with the given HTTP status."""
    return JSONResponse(status_code=status_code, content={"error": message})


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Application lifespan — startup + shutdown managed in one block."""
    # ── startup ────────────────────────────────────────────────────────
    logger.info(
        "CUA backend starting — model=%s, agent_service=%s, mode=%s",
        config.gemini_model, config.agent_service_url, config.agent_mode,
    )
    try:
        validate_tool_parity()
    except Exception as exc:
        logger.warning("Tool parity check failed: %s", exc)

    try:
        yield
    finally:
        global _novnc_client, _screenshot_publisher_task, _last_screenshot_frame
        # ── shutdown ──────────────────────────────────────────────────
        # Cancel in-flight run tasks, then await them (and any pending
        # broadcasts) so WS flushes get a chance to complete before
        # we tear down shared clients.
        pending: list[asyncio.Task] = []
        for sid in list(_active_tasks.keys()):
            task = _active_tasks.get(sid)
            if task and not task.done():
                task.cancel()
                pending.append(task)
        pending.extend(t for t in list(_broadcast_tasks) if not t.done())
        if _screenshot_publisher_task is not None and not _screenshot_publisher_task.done():
            _screenshot_publisher_task.cancel()
            pending.append(_screenshot_publisher_task)
        if pending:
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                logger.exception("Error awaiting in-flight tasks on shutdown")
        _active_tasks.clear()
        _active_loops.clear()
        _broadcast_tasks.clear()
        _screenshot_subscribers.clear()
        _screenshot_subscribers_by_session.clear()
        _ws_screenshot_sessions.clear()
        _screenshot_publisher_task = None
        _last_screenshot_frame = None

        if _novnc_client is not None and not _novnc_client.is_closed:
            try:
                await _novnc_client.aclose()
            except Exception:
                logger.exception("Error closing noVNC proxy client")
        _novnc_client = None

        # P11: release the shared httpx client pool used by every
        # DesktopExecutor so FD counts don't grow across reloads.
        try:
            from backend.engine import close_shared_executor_clients
            await close_shared_executor_clients()
        except Exception:
            logger.exception("Error closing shared executor httpx clients")

        # Wipe any uploaded RAG files left over on disk.
        try:
            await file_registry.close_store()
        except Exception:
            logger.exception("Error closing file_store")

        logger.info("CUA backend shut down")


app = FastAPI(title="CUA — Computer Using Agent", version="1.0.0", lifespan=_lifespan)

# CORS: restrict to local dev origins by default; override with CORS_ORIGINS env var.
#
# Each configured origin is validated against a conservative pattern
# (scheme + host[:port], no path/query/fragment) before being accepted.
# This prevents a typo or malicious env var from widening credential
# scope to an arbitrary origin. Invalid entries are dropped with a
# warning instead of silently accepted.
_ORIGIN_RE = re.compile(r"^https?://[a-zA-Z0-9.\-_]+(?::\d{1,5})?$")


def _parse_cors_origins(raw: str) -> list[str]:
    candidates = [o.strip() for o in raw.split(",") if o.strip()]
    accepted: list[str] = []
    for o in candidates:
        if _ORIGIN_RE.match(o):
            accepted.append(o)
        else:
            logger.warning("Ignoring invalid CORS origin: %r", o)
    return accepted


_ALLOWED_ORIGINS = _parse_cors_origins(os.getenv("CORS_ORIGINS", "")) or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# ── API version aliasing ──────────────────────────────────────────────────────
#
# The original, unversioned routes (``/api/...``) remain the canonical
# surface, but we also accept ``/api/v1/...`` so new clients can pin a
# version without a coordinated backend rename. A future v2 simply adds
# real v2 handlers; v1 keeps pointing at the unversioned implementation.

_API_V1_PREFIX = "/api/v1/"


@app.middleware("http")
async def _api_version_alias(request: Request, call_next):
    """Rewrite ``/api/v1/<rest>`` to ``/api/<rest>`` so v1 clients hit
    the existing handlers with zero duplication."""
    path = request.url.path
    if path.startswith(_API_V1_PREFIX):
        new_path = "/api/" + path[len(_API_V1_PREFIX):]
        # Starlette's Request.url is immutable; the scope is what routing reads.
        request.scope["path"] = new_path
        request.scope["raw_path"] = new_path.encode("latin-1")
    return await call_next(request)


# ── Security headers ──────────────────────────────────────────────────────────

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Conservative CSP; frontend bundles are served by the Vite dev server,
    # not this backend. Tighten further if you ever serve HTML from here.
    # ``connect-src 'self'`` already covers same-origin ws/wss upgrades —
    # the explicit ``ws: wss:`` tokens used to allow WebSocket connects to
    # any host, which defeats the directive's whole purpose.
    "Content-Security-Policy": (
        "default-src 'none'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    ),
    # S10: cross-origin isolation. Prevents this origin from being
    # grouped with attacker-controlled windows/resources and from
    # exposing side-channel timers to cross-origin code.
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-site",
    # S10: lock down powerful features we don't use. An explicit deny
    # keeps a compromised page from silently turning on camera / mic /
    # geolocation / sensors / payment via this origin.
    "Permissions-Policy": (
        "accelerometer=(), ambient-light-sensor=(), autoplay=(), "
        "battery=(), camera=(), display-capture=(), document-domain=(), "
        "encrypted-media=(), fullscreen=(self), geolocation=(), "
        "gyroscope=(), magnetometer=(), microphone=(), midi=(), "
        "payment=(), picture-in-picture=(), publickey-credentials-get=(), "
        "screen-wake-lock=(), sync-xhr=(), usb=(), xr-spatial-tracking=()"
    ),
}


# S9: Host header allowlist — prevents DNS-rebinding attacks where a
# malicious page makes the browser resolve ``attacker.com`` to
# ``127.0.0.1`` and then hits the backend as if it were local.
# Populated from the host portion of the CORS origins plus explicit
# overrides via ``CUA_ALLOWED_HOSTS`` (comma-separated).
def _compute_allowed_hosts() -> set[str]:
    # ``testserver`` is the default Host header Starlette's TestClient
    # emits. Including it permanently in production code is unnecessary
    # (browsers won't resolve it), so it's now opt-in via the
    # ``CUA_TEST_MODE=1`` flag that pytest sets in conftest. This keeps
    # the production allowlist minimal without forcing every test to
    # patch the Host header.
    hosts: set[str] = {"127.0.0.1", "localhost", "::1"}
    if os.getenv("CUA_TEST_MODE", "").strip().lower() in ("1", "true", "yes"):
        hosts.add("testserver")
    for o in _ALLOWED_ORIGINS:
        try:
            # strip scheme and optional port
            rest = o.split("://", 1)[1]
            host = rest.split(":", 1)[0]
            if host:
                hosts.add(host)
        except Exception:
            continue
    extra = os.getenv("CUA_ALLOWED_HOSTS", "").strip()
    if extra:
        for h in extra.split(","):
            h = h.strip()
            if h:
                hosts.add(h)
    return hosts


_ALLOWED_HOSTS = _compute_allowed_hosts()


@app.middleware("http")
async def _host_allowlist(request: Request, call_next):
    """Reject requests whose ``Host`` header isn't on the allowlist."""
    raw_host = request.headers.get("host", "").strip().lower()
    # strip port
    host_only = raw_host.split(":", 1)[0]
    if host_only and host_only not in _ALLOWED_HOSTS:
        logger.warning("Rejected request for host %r (not in allowlist)", raw_host)
        return JSONResponse(status_code=400, content={"error": "Invalid host header"})
    return await call_next(request)


# S-G: Reject obviously oversized request bodies before they're buffered
# into memory. All current /api/* endpoints accept tiny JSON payloads
# (model id, key, task prompt). 256 KiB is generous for a prompt and
# still bounded enough to make a body-flood DoS uninteresting.
_MAX_REQUEST_BODY_BYTES = int(os.getenv("CUA_MAX_BODY_BYTES", str(256 * 1024)))


@app.middleware("http")
async def _body_size_limit(request: Request, call_next):
    """Enforce a maximum Content-Length on /api/* mutations."""
    if request.method in ("POST", "PUT", "PATCH") and request.url.path.startswith("/api/"):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > _MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"error": "Request body too large"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid Content-Length"},
                )
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach basic security headers to every HTTP response.

    Skipped for the ``/vnc/*`` reverse proxy because noVNC is a full HTML
    app that needs to load its own JS/CSS/fonts and be embedded in an
    iframe by the frontend. Applying a strict ``default-src 'none'`` +
    ``frame-ancestors 'none'`` CSP to those responses breaks the live
    desktop view (browsers surface it as "refused to connect").
    """
    response = await call_next(request)
    if request.url.path.startswith("/vnc/"):
        return response
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response

# Security constants
_MAX_CONCURRENT_SESSIONS = 3
_MAX_STEPS_HARD_CAP = 200

# Optional shared secret for /ws authentication. When set, clients must
# pass ``?token=<value>`` on connect. Unset (default) preserves the
# localhost-only behaviour existing deployments rely on.
_WS_AUTH_TOKEN = os.getenv("CUA_WS_TOKEN", "").strip()
_WS_AUTH_CLOSE_CODE = 4401
_WS_AUTH_CLOSE_REASON = "bad or missing token"


def _consteq(a: str, b: str) -> bool:
    """Constant-time string comparison for secrets."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _ws_token_ok(ws: WebSocket) -> bool:
    """Return True when the shared-secret gate passes (or is disabled).

    Empty ``CUA_WS_TOKEN`` (unset) means default-open: the gate always
    passes so local development keeps working without configuration.
    Callers must still run :func:`_ws_origin_ok` separately.

    This is the single source of truth for /ws and /vnc/websockify so
    the two interactive surfaces stay in lockstep.
    """
    if not _WS_AUTH_TOKEN:
        return True
    supplied = ws.query_params.get("token", "")
    return bool(supplied) and _consteq(supplied, _WS_AUTH_TOKEN)


# Hosts that may embed or connect to this backend in addition to the CORS
# origins. ``null`` origins (e.g. the noVNC iframe in a sandboxed context,
# or an origin-less ``file://`` page) are accepted only when the
# connection is already gated by ``CUA_WS_TOKEN``. Missing Origin
# headers (non-browser clients such as curl/Python) are accepted on
# loopback only.
#
# ``testclient`` is Starlette's default ``request.client.host`` for the
# ``TestClient`` fixture; including it keeps the hermetic test suite
# from needing to patch every request and has no real-world attack
# surface (it's not a routable hostname).
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testclient"}


def _rest_origin_ok(request: Request) -> bool:
    """Origin/Host gate for sensitive REST endpoints (S1, S9).

    - Accept same-site requests (no Origin header, loopback host).
    - Accept explicit CORS allowlist.
    - Reject everything else.
    """
    origin = request.headers.get("origin", "").strip()
    if origin and origin in _ALLOWED_ORIGINS:
        return True
    if origin in ("", "null"):
        client_host = request.client.host if request.client else ""
        return client_host in _LOOPBACK_HOSTS
    return False


def _ws_origin_ok(ws: WebSocket) -> bool:
    """Return True if the WebSocket origin is acceptable.

    - Browsers always send an Origin header on cross-origin upgrades.
      We enforce the CORS allowlist so a malicious page can't open
      ``ws://127.0.0.1:8100/ws`` and siphon live screenshots.
    - Non-browser clients (curl, Python websockets) typically omit
      the header; we allow those only from loopback.
    - When ``CUA_WS_TOKEN`` is set the token check is the primary
      defence, so a missing/``null`` origin is accepted and the token
      check will reject the connection if it's not presented.
    """
    origin = ws.headers.get("origin", "").strip()
    if origin in _ALLOWED_ORIGINS:
        return True
    if origin in ("", "null"):
        if _WS_AUTH_TOKEN:
            return True
        host = ws.client.host if ws.client else ""
        return host in _LOOPBACK_HOSTS
    return False


# C-1: shared dependency for sensitive REST POST endpoints. Without
# this every state-changing endpoint relied on FastAPI CORS alone, which
# does not block requests that omit Origin (curl, Python clients) or
# requests from same-origin XSS / a malicious page within an
# allowlisted origin. Returns 403 instead of raising so the response
# shape matches the rest of the API's ``{"error": "..."}``.
def _require_origin(request: Request) -> Response:
    """Reject the request when its Origin / loopback combo isn't allowed.

    Returning ``None`` proceeds; returning a ``Response`` short-circuits.
    Used as a guard at the top of each protected handler.
    """
    if not _rest_origin_ok(request):
        logger.warning(
            "Rejected REST %s %s from origin=%r ip=%r",
            request.method, request.url.path,
            request.headers.get("origin", ""),
            request.client.host if request.client else "",
        )
        return _error_response(403, "Forbidden")
    return None  # type: ignore[return-value]


# ── Allowed models (single source of truth: backend/allowed_models.json) ──────

_ALLOWED_MODELS: list[dict] = _load_allowed_models_json()
_CU_ALLOWED_MODELS = [m for m in _ALLOWED_MODELS if m.get("supports_computer_use")]
_VALID_PROVIDERS = {m["provider"] for m in _CU_ALLOWED_MODELS}

_VALID_MODELS_BY_PROVIDER: dict[str, set[str]] = {}
for _m in _CU_ALLOWED_MODELS:
    _VALID_MODELS_BY_PROVIDER.setdefault(_m["provider"], set()).add(_m["model_id"])


# ── Rate limiter (in-memory sliding window, per-key) ──────────────────────────

class _RateLimiter:
    """Simple sliding-window rate limiter keyed by caller identity (e.g. IP)."""
    # Hard ceilings on the in-memory bucket map (P3).
    # Eviction triggers at ``_EVICT_THRESHOLD`` (90 % of the ceiling) so a
    # spoofed-IP flood can't transiently bloat the dict up to 2× ``_EVICT_TO``
    # between sweeps. After eviction we keep only ``_EVICT_TO`` most recently
    # active keys, which is aggressive enough that sustained abuse cannot
    # inflate memory beyond ~1 KB per live key × ceiling.
    _HARD_KEY_CEILING = 1024
    _EVICT_THRESHOLD = 921  # ≈ 0.9 × _HARD_KEY_CEILING
    _EVICT_TO = 256

    def __init__(self, max_calls: int, window_seconds: float):
        """Configure the limiter with *max_calls* per *window_seconds* per key."""
        self._max = max_calls
        self._window = window_seconds
        self._calls: dict[str, list[float]] = {}

    def allow(self, key: str = "_global") -> bool:
        """Return True and record a call if *key* is under the rate limit."""
        now = time.monotonic()
        bucket = [t for t in self._calls.get(key, []) if now - t < self._window]
        if len(bucket) >= self._max:
            self._calls[key] = bucket
            return False
        bucket.append(now)
        self._calls[key] = bucket
        # Bounded-memory eviction. First try the cheap filter (idle-key
        # drop), then apply a hard ceiling so a spoofed-IP flood can't
        # sustain high-volume entries indefinitely — we keep the
        # ``_EVICT_TO`` most recently active keys and discard the rest.
        if len(self._calls) > self._EVICT_THRESHOLD:
            self._calls = {
                k: v for k, v in self._calls.items()
                if v and now - v[-1] < self._window
            }
            if len(self._calls) > self._EVICT_TO:
                # Keep the _EVICT_TO most-recently-active keys.
                kept = sorted(
                    self._calls.items(),
                    key=lambda kv: kv[1][-1] if kv[1] else 0,
                    reverse=True,
                )[: self._EVICT_TO]
                self._calls = dict(kept)
        return True


_agent_start_limiter = _RateLimiter(max_calls=10, window_seconds=60.0)
_validate_key_limiter = _RateLimiter(max_calls=20, window_seconds=60.0)


def _client_ip(request: Request) -> str:
    """Best-effort client IP extraction (falls back to 'unknown')."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _is_valid_uuid(value: str) -> bool:
    """Return True if *value* is a well-formed UUID."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


# ── In-memory state ──────────────────────────────────────────────────────────

_active_loops: dict[str, AgentLoop] = {}
_active_tasks: dict[str, asyncio.Task] = {}
# Set (not list) so add/remove are O(1) and iteration via snapshot
# avoids mutation-during-iteration when two broadcasts interleave.
_ws_clients: set[WebSocket] = set()

# ── P-PUB — shared screenshot publisher ──────────────────────────────
#
# Previously every ``/ws`` client spawned its own ``_stream_screenshots``
# task. With N viewers that meant N independent ``capture_screenshot``
# calls contending with keyboard/mouse actions behind the single
# container-side ``_ACTION_LOCK``, and every client received the same
# frames anyway. The publisher below replaces that with ONE loop per
# container/process that fans the latest frame out to every subscribed
# client. Subscribers are tracked per session so ``_cleanup_session``
# can drop demand for a finished run without closing the websocket.
#
# Reference counting:
#   * ``_screenshot_subscribers`` is a subset of ``_ws_clients``.
#   * ``_screenshot_subscribers_by_session`` tracks which ws clients are
#     actively using screenshot fallback for each live session.
#   * Adding the first subscriber starts :func:`_screenshot_publisher_loop`.
#   * Removing the last subscriber cancels it (or, when
#     ``config.ws_screenshot_suspend_when_idle`` is False, leaves it
#     running — the loop still dedupes so it's cheap).
#   * On disconnect / session cleanup, :func:`_unsubscribe_screenshots`
#     is idempotent — double-unsubscribe is a no-op.
_screenshot_subscribers: set[WebSocket] = set()
_ws_screenshot_sessions: dict[WebSocket, str] = {}
_screenshot_subscribers_by_session: dict[str, set[WebSocket]] = {}
_screenshot_publisher_task: asyncio.Task | None = None
# Cached most-recent (b64, hash) so a newly-attached subscriber can
# paint immediately instead of waiting up to one cadence interval.
_last_screenshot_frame: tuple[str, str] | None = None
# Metric for tests / ops: how many times the publisher loop has
# actually invoked ``capture_screenshot``. Observable via logs and
# the dedicated test harness; NOT exposed over HTTP.
_screenshot_capture_count: int = 0


def _cleanup_session(sid: str) -> None:
    """Remove bookkeeping for a session (tasks, loops, safety state).

    Uses a single try/finally chain so a raised exception in any one
    step can't leave the other maps desynchronised (R5). Every entry
    is touched exactly once regardless of failures earlier in the
    chain; errors are logged but never propagated — cleanup is a
    best-effort operation on the way out of a session.
    """
    try:
        _active_tasks.pop(sid, None)
    except Exception:
        logger.exception("cleanup: _active_tasks.pop failed for %s", sid)
    try:
        _active_loops.pop(sid, None)
    except Exception:
        logger.exception("cleanup: _active_loops.pop failed for %s", sid)
    try:
        safety_registry.clear(sid)
    except Exception:
        logger.exception("cleanup: safety_registry.clear failed for %s", sid)
    try:
        _drop_screenshot_session(sid)
    except Exception:
        logger.exception("cleanup: screenshot-subscriber cleanup failed for %s", sid)
    # C9: cancel any queued broadcast tasks for this session so they
    # don't keep the event loop busy after the agent has finished.
    try:
        bucket = _session_broadcast_tasks.pop(sid, None)
        if bucket:
            for t in list(bucket):
                if not t.done():
                    t.cancel()
    except Exception:
        logger.exception("cleanup: broadcast-task cancel failed for %s", sid)


async def _get_session_snapshot(session_id: str) -> AgentSession | None:
    """Return active session state for status/history calls."""
    loop = _active_loops.get(session_id)
    if loop:
        return loop.session
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

_broadcast_tasks: set[asyncio.Task] = set()
# C9: per-session broadcast-task registry so ``_cleanup_session`` can
# cancel fan-out that's still queued behind other WS sends once the
# agent has finished. Tasks already mid-write complete normally;
# cancellation only interrupts at the next ``await`` point.
_session_broadcast_tasks: dict[str, set[asyncio.Task]] = {}
# P-5: cap on per-session pending broadcasts. Configurable via env so
# operators with very chatty UIs can raise it.
_MAX_SESSION_BROADCAST_BACKLOG = max(
    8, int(os.getenv("CUA_MAX_SESSION_BROADCAST_BACKLOG", "64"))
)


async def _broadcast(event: str, data: dict) -> None:
    """Send a JSON message to all connected WebSocket clients."""
    # Validate against backend/ws_schema.py so a rename / missing field
    # in an event payload is surfaced as a WARNING instead of shipping
    # silent garbage to every connected frontend. Still broadcasts on
    # failure — the schema is advisory, not a blocker.
    err = validate_outbound(event, data)
    if err:
        logger.warning("WS event %s failed schema validation: %s", event, err)
    msg = json.dumps({"event": event, **data})
    stale: list[WebSocket] = []
    # Snapshot so concurrent broadcasts don't observe mutation.
    for ws in list(_ws_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _unsubscribe_screenshots(ws)
        _ws_clients.discard(ws)


def _schedule_broadcast(event: str, data: dict, *, session_id: str | None = None) -> None:
    """Fire-and-forget broadcast whose task is tracked to prevent GC."""
    # P-5: bound the per-session backlog. If a single session's
    # broadcast queue grows past _MAX_SESSION_BROADCAST_BACKLOG it
    # means the WebSocket consumer (browser) has stalled. Drop the
    # *oldest* still-queued task in that bucket rather than letting
    # the registry grow unbounded — newer events are more useful to
    # a recovering UI than ancient ones.
    if session_id:
        bucket = _session_broadcast_tasks.setdefault(session_id, set())
        if len(bucket) >= _MAX_SESSION_BROADCAST_BACKLOG:
            # Cancel one not-yet-done task; cancellation only takes
            # effect at the next await, so anything mid-send finishes.
            for t in list(bucket):
                if not t.done():
                    t.cancel()
                    bucket.discard(t)
                    _broadcast_tasks.discard(t)
                    break
    task = asyncio.create_task(_broadcast(event, data))
    _broadcast_tasks.add(task)
    if session_id:
        _session_broadcast_tasks.setdefault(session_id, set()).add(task)

    def _done(t: asyncio.Task) -> None:
        _broadcast_tasks.discard(t)
        if session_id:
            bucket = _session_broadcast_tasks.get(session_id)
            if bucket is not None:
                bucket.discard(t)
                if not bucket:
                    _session_broadcast_tasks.pop(session_id, None)

    task.add_done_callback(_done)


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Liveness probe.

    Returns 200 with ``{"status": "ok"}`` as long as the FastAPI
    process is servicing requests. This is intentionally cheap and
    has no dependency on the Docker daemon or provider API keys —
    the Docker HEALTHCHECK directive points here, not at ``/ready``,
    so a slow upstream provider or a transient docker-daemon hiccup
    does not mark the container itself as unhealthy.
    """
    return {"status": "ok"}


@app.get("/api/ready")
async def ready():
    """Readiness probe.

    Returns 200 only when the backend can actually start a session:

      * the Docker daemon is reachable (``docker ps`` succeeds);
      * at least one provider API key is configured (UI entry is not
        enough at readiness time — a deployed instance needs a
        .env or system-env key to serve traffic);
      * the sandbox container is either already ``running`` or
        ``stopped``-and-cleanly-startable (``unknown`` from the
        cached readiness dict is tolerated because the first session
        start will probe it live).

    On failure returns HTTP 503 with a ``reasons`` list so the
    operator sees which check tripped. Not wired into the Docker
    HEALTHCHECK directive — that's deliberately liveness-only — so
    readiness can be surfaced to a separate orchestrator (kubelet
    probe, load-balancer, etc.) without taking the container down.
    """
    reasons: list[str] = []

    # Docker daemon reachable?
    try:
        rc, _, err = await _dm_run(["docker", "version", "--format", "{{.Server.Version}}"])
        if rc != 0:
            reasons.append(f"docker daemon unreachable: {err.strip() or 'non-zero exit'}")
    except Exception as exc:  # docker binary missing, permissions, etc.
        reasons.append(f"docker daemon probe failed: {type(exc).__name__}: {exc}")

    # At least one provider API key configured (env or .env, NOT UI).
    # Mirrors ``backend.infra.config``'s ``_PROVIDER_KEY_ENV_VARS`` map.
    key_env_names = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY")
    has_any_key = any(os.environ.get(name, "").strip() for name in key_env_names)
    if not has_any_key:
        reasons.append(
            "no provider API key configured; set one of "
            + ", ".join(key_env_names),
        )

    # Sandbox state must not be a hard ``error`` state.
    state = get_container_state()
    container = (state or {}).get("container", "unknown")
    if container not in ("running", "stopped", "starting", "unknown"):
        reasons.append(f"container in unexpected state: {container!r}")

    if reasons:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "reasons": reasons},
        )
    return {"ready": True, "container": container}


@app.get("/api/models")
async def api_models():
    """Return the canonical allowed-models list for frontend dropdowns.

    Source of truth: backend/models/allowed_models.json
    """
    return {"models": _CU_ALLOWED_MODELS}

@app.get("/api/engines")
async def api_engines():
    """Return the single supported engine for frontend dropdowns."""
    return {"engines": [{
        "value": "computer_use",
        "label": "\U0001f5a5\ufe0f Computer Use (Native CU Protocol) \u2605 Recommended",
        "category": "desktop",
        "priority": 6,
    }]}


@app.get("/api/container/status")
async def container_status():
    """Return Docker container and agent-service status."""
    return await get_container_status()


@app.get("/api/agent-service/health")
async def agent_service_health():
    """Check if the internal desktop agent service is responding."""
    healthy = await check_service_health()
    return {"healthy": healthy, "url": config.agent_service_url}


@app.post("/api/agent-service/mode")
async def set_agent_mode(body: dict, request: Request):
    """Keep the agent service in the supported desktop mode."""
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden
    mode = body.get("mode", "desktop")
    if mode != "desktop":
        return _error_response(400, "Browser mode is no longer supported. Use mode='desktop'.")
    url = f"{config.agent_service_url}/mode"
    token = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()
    headers = {"X-Agent-Token": token} if token else None
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.post(url, json={"mode": mode}, headers=headers)
            return resp.json()
        except Exception:
            logger.exception("Failed to set agent mode")
            return _error_response(502, "Could not reach the agent service")


@app.post("/api/container/start")
async def api_start_container(request: Request):
    """Build-if-needed and start the CUA Docker container."""
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden
    success = await start_container()
    return {"success": success}


@app.post("/api/container/stop")
async def api_stop_container(request: Request):
    """Stop all running agents then remove the Docker container."""
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden
    for sid in list(_active_tasks.keys()):
        await _stop_agent(sid)
    success = await stop_container()
    return {"success": success}


@app.post("/api/container/build")
async def api_build_image(request: Request):
    """Trigger a Docker image build."""
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden
    success = await build_image()
    return {"success": success}


@app.get("/api/keys/status")
async def api_keys_status():
    """Return availability and source of API keys for all providers.

    Response example::

        {
          "keys": [
            {"provider": "google",    "available": true, "source": "env",    "masked_key": "AIza...4xQk"},
            {"provider": "anthropic", "available": true, "source": "dotenv", "masked_key": "sk-a...9f2e"}
          ]
        }

    Sources: ``"env"`` = system environment variable, ``"dotenv"`` = .env file,
    ``"none"`` = not configured.
    """
    return {"keys": get_all_key_statuses()}


@app.get("/api/screenshot")
async def api_screenshot(request: Request):
    """Get current screenshot as base64.

    S1: gated by the same origin/token checks as ``/ws``. Without
    this, any webpage the user opens can ``fetch('/api/screenshot')``
    and steal the live desktop, because browsers send cookies /
    same-origin credentials and the response is JSON (no CORS
    preflight with a simple GET).
    """
    if not _rest_origin_ok(request):
        return _error_response(403, "Forbidden")
    if _WS_AUTH_TOKEN:
        supplied = request.query_params.get("token", "") or \
            request.headers.get("x-cua-token", "")
        if not supplied or not _consteq(supplied, _WS_AUTH_TOKEN):
            return _error_response(401, "Unauthorized")
    try:
        b64 = await capture_screenshot()
        return {"screenshot": b64}
    except Exception:
        logger.exception("Screenshot capture failed")
        return _error_response(500, "Could not capture screenshot")


# ── File upload (RAG / file_search) ──────────────────────────────────
# Files live on disk in a temp directory keyed by an opaque server-side
# id; the id flows back to the caller, then into the agent/start
# payload as ``attached_files``. At session start, OpenAI and Anthropic
# hand the bytes to their provider-native reference-file paths
# (OpenAI vector_stores / Anthropic Files API). Gemini CU rejects
# ``attached_files`` because Gemini File Search cannot be combined with
# Computer Use.
# Per the user-facing contract: .md/.txt/.pdf/.docx, max 10 files,
# max 1 GB each. Provider-side caps, such as Anthropic 500 MB/file,
# surface as upstream API errors at session start.

_FILE_UPLOAD_BYTES_CAP = file_registry.MAX_FILE_BYTES


@app.post("/api/files/upload")
async def api_upload_file(request: Request):
    """Persist a single uploaded file to the server-side store."""
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden

    # Rate-limit uploads per IP using the same bucket as agent/start
    # so a script can't burn through disk by hammering the endpoint.
    if not _agent_start_limiter.allow(_client_ip(request)):
        return _error_response(429, "Rate limit exceeded — slow down uploads")

    try:
        form = await request.form()
    except Exception:
        return _error_response(400, "Invalid multipart payload")

    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return _error_response(400, "Missing 'file' field")

    filename = getattr(upload, "filename", None) or ""
    # Defend the disk budget *before* spooling the entire payload into
    # memory — Starlette's UploadFile streams to a SpooledTemporaryFile
    # but ``.read()`` materialises it.  We refuse Content-Length over
    # the cap; multipart framing means the actual file body is slightly
    # smaller, never larger.
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > _FILE_UPLOAD_BYTES_CAP + 1024 * 1024:
        return _error_response(413, "File exceeds 1 GB limit")

    try:
        data = await upload.read()
    except Exception:
        return _error_response(400, "Could not read upload payload")

    try:
        rec = await file_registry.upload_file(filename=filename, data=data)
    except ValueError as exc:
        return _error_response(400, str(exc))
    except Exception:
        logger.exception("file_store.add failed")
        return _error_response(500, "Could not persist file")

    logger.info(
        "AUDIT files/upload — id=%s name=%r size=%d ext=%s",
        rec.file_id, rec.filename, rec.size_bytes, rec.extension,
    )
    return {
        "file_id": rec.file_id,
        "filename": rec.filename,
        "size_bytes": rec.size_bytes,
        "mime_type": rec.mime_type,
    }


@app.delete("/api/files/{file_id}")
async def api_delete_file(file_id: str, request: Request):
    """Drop a previously uploaded file from the store + disk."""
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden
    ok = await file_registry.delete_file(file_id)
    if not ok:
        return _error_response(404, f"file_id not found: {file_id}")
    return {"deleted": file_id}


@app.post("/api/agent/start")
async def api_start_agent(req: StartTaskRequest, request: Request):
    """Start a new agent session with input validation."""
    # C-1: origin gate before any work — keeps a CSRF/XSS-driven start
    # from consuming API-key quota or executing arbitrary tasks.
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden

    # ── Rate limit (per-IP) ─────────────────────────────────
    if not _agent_start_limiter.allow(_client_ip(request)):
        return _error_response(429, "Rate limit exceeded — max 10 starts per minute")

    # ── Validate inputs ───────────────────────────────────────────────
    if req.engine != "computer_use":
        return _error_response(400, f"Invalid engine: {req.engine}. Only 'computer_use' is supported.")
    if req.execution_target != "docker":
        return _error_response(400, f"Invalid execution_target: {req.execution_target}. Only 'docker' is supported.")
    if req.provider not in _VALID_PROVIDERS:
        return _error_response(400, f"Invalid provider: {req.provider}")
    if req.model not in _VALID_MODELS_BY_PROVIDER.get(req.provider, set()):
        allowed = ", ".join(
            m["model_id"] for m in _CU_ALLOWED_MODELS
            if m["provider"] == req.provider
        )
        return _error_response(400, f"Model '{req.model}' is not allowed. Supported models: {allowed}")
    if not req.task or not req.task.strip():
        return _error_response(400, "Task description is required")

    # Cap max_steps to prevent runaway agents
    req.max_steps = min(req.max_steps, _MAX_STEPS_HARD_CAP)

    # Validate attached_files (optional). Provider-specific handling:
    # OpenAI -> Responses file_search/vector store; Anthropic -> Files API
    # document blocks; Gemini -> explicit reject with Computer Use.
    if req.attached_files:
        try:
            req.attached_files = await file_registry.validate_attached_files(
                req.provider,
                req.attached_files,
            )
        except ValueError as exc:
            return _error_response(400, str(exc))

    # Resolve reasoning_effort: request > env var > model-specific default.
    # Per OpenAI's model pages and latest-model guide (checked 2026-04-27),
    # GPT-5.4 defaults to ``none`` and GPT-5.5 defaults to ``medium``.
    # The env var and per-request override still let operators opt into
    # cheaper or more exhaustive runs.
    # Canonical values per the OpenAI Responses API (2026-04):
    # {"minimal","low","medium","high","xhigh"}. ``none`` is kept as
    # a legacy alias and normalized by ``OpenAICUClient.__init__``.
    _VALID_REASONING_EFFORTS = {"minimal", "low", "medium", "high", "none", "xhigh"}
    default_reasoning_effort = _default_openai_reasoning_effort_for_model(req.model)
    reasoning_effort = (req.reasoning_effort or os.getenv("OPENAI_REASONING_EFFORT") or default_reasoning_effort).lower()
    if reasoning_effort not in _VALID_REASONING_EFFORTS:
        reasoning_effort = default_reasoning_effort

    try:
        _validate_builtin_search_config(
            provider=req.provider,
            model=req.model,
            use_builtin_search=req.use_builtin_search,
            reasoning_effort=reasoning_effort,
            search_max_uses=req.search_max_uses,
            search_allowed_domains=req.search_allowed_domains,
            search_blocked_domains=req.search_blocked_domains,
            allowed_callers=req.allowed_callers,
        )
    except ValueError as exc:
        return _error_response(400, str(exc))

    # Resolve API key: UI input → .env → system env
    resolved_key, key_source = resolve_api_key(req.provider, req.api_key)
    if not resolved_key or len(resolved_key) < 8:
        return _error_response(400, "API key is required. Provide it in the UI, .env file, or system environment variable.")

    # Limit concurrent sessions
    active_count = sum(1 for t in _active_tasks.values() if not t.done())
    if active_count >= _MAX_CONCURRENT_SESSIONS:
        return _error_response(429, f"Maximum {_MAX_CONCURRENT_SESSIONS} concurrent sessions allowed")

    # Audit log (mask API key)
    masked_key = resolved_key[:4] + "..." + resolved_key[-4:] if len(resolved_key) > 8 else "****"
    logger.info("AUDIT agent/start — task=%r engine=%s provider=%s model=%s key=%s source=%s target=%s",
                req.task[:80], req.engine, req.provider, req.model, masked_key, key_source,
                req.execution_target)

    container_ok = await start_container()
    state = get_container_state()
    # D-READY — session creation requires a positive ready signal, not
    # just "docker says the container exists". ``start_container()``
    # refreshes readiness even on the already-running fast path, then we
    # re-check the cached state here so a race between readiness and
    # teardown can still be surfaced as a clean 409 instead of a later
    # screenshot/action network error.
    if not container_ok:
        if state.get("agent") == "unready":
            detail = state.get("last_health_error") or "agent service not reachable"
            return _error_response(
                409,
                f"Sandbox is not ready ({detail}). Restart the environment and try again.",
            )
        return _error_response(
            503,
            "Could not start the virtual environment. Please check that the system is set up correctly.",
        )
    if state.get("agent") != "ready":
        detail = state.get("last_health_error") or "agent service not reachable"
        return _error_response(
            409,
            f"Sandbox is not ready ({detail}). Restart the environment and try again.",
        )

    loop = AgentLoop(
        task=req.task,
        api_key=resolved_key,
        model=req.model,
        max_steps=req.max_steps,
        engine=req.engine,
        provider=req.provider,
        execution_target=req.execution_target,
        reasoning_effort=reasoning_effort if req.provider == "openai" else None,
        use_builtin_search=req.use_builtin_search,
        search_max_uses=req.search_max_uses,
        search_allowed_domains=req.search_allowed_domains,
        search_blocked_domains=req.search_blocked_domains,
        allowed_callers=req.allowed_callers,
        attached_files=req.attached_files or [],
        on_log=lambda entry: _schedule_broadcast(
            "log", {"log": entry.model_dump()}
        ),
        on_step=lambda step: _schedule_broadcast(
            "step",
            {"step": step.model_dump(exclude={"screenshot_b64", "raw_model_response"})},
        ),
        on_screenshot=lambda b64: _schedule_broadcast(
            "screenshot", {"screenshot": b64}
        ),
    )

    _active_loops[loop.session_id] = loop

    async def _run_and_notify():
        """Run the agent loop then broadcast a finish event to all WS clients."""
        try:
            session = await loop.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Agent session crashed — session_id=%s", loop.session_id)
            loop.session.status = SessionStatus.ERROR
            session = loop.session

        await _broadcast("agent_finished", {
            "session_id": loop.session_id,
            "status": session.status.value,
            "steps": len(session.steps),
            "final_text": session.final_text,
            "gemini_grounding": session.gemini_grounding,
        })
        _cleanup_session(loop.session_id)

    task = asyncio.create_task(_run_and_notify())
    _active_tasks[loop.session_id] = task

    logger.info("AUDIT session_started — session_id=%s engine=%s", loop.session_id, req.engine)

    return {
        "session_id": loop.session_id,
        "status": "running",
        "engine": req.engine,
        "provider": req.provider,
    }


@app.post("/api/agent/stop/{session_id}")
async def api_stop_agent(session_id: str, request: Request):
    """Stop a running agent session by ID."""
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden
    if not _is_valid_uuid(session_id):
        return _error_response(400, "Invalid session_id")
    return await _stop_agent(session_id)


async def _stop_agent(session_id: str):
    """Internal helper to cancel an agent loop and its asyncio task."""
    loop = _active_loops.get(session_id)
    if not loop:
        return _error_response(404, "Session not found")

    loop.request_stop()

    task = _active_tasks.get(session_id)
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    _cleanup_session(session_id)
    logger.info("AUDIT session_stopped — session_id=%s", session_id)
    return {"session_id": session_id, "status": "stopped"}


@app.get("/api/agent/status/{session_id}")
async def api_agent_status(session_id: str):
    """Return the current status of an agent session."""
    if not _is_valid_uuid(session_id):
        return _error_response(400, "Invalid session_id")
    session = await _get_session_snapshot(session_id)
    if not session:
        return _error_response(404, "Session not found")

    last_action: AgentAction | None = None
    if session.steps:
        last_action = session.steps[-1].action

    return TaskStatusResponse(
        session_id=session.session_id,
        status=session.status,
        current_step=len(session.steps),
        total_steps=session.max_steps,
        last_action=last_action,
        final_text=session.final_text,
        gemini_grounding=session.gemini_grounding,
    ).model_dump()


# ── Safety Confirmation for CU Engine ─────────────────────────────────────────


class SafetyConfirmRequest(BaseModel):
    """Body for the safety-confirm endpoint."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    confirm: bool = False


class ValidateKeyRequest(BaseModel):
    """Body for the key validation endpoint."""
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(max_length=20)
    api_key: str = Field(max_length=256)


@app.post("/api/keys/validate")
async def api_validate_key(req: ValidateKeyRequest, request: Request):
    """Lightweight API key validation — makes a minimal call to the provider.

    Returns ``{valid: true/false, message: ...}``.  Never logs the raw key.
    """
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden
    if not _validate_key_limiter.allow(_client_ip(request)):
        return _error_response(429, "Rate limit exceeded — max 20 validations per minute")

    if req.provider not in _VALID_PROVIDERS:
        return _error_response(400, f"Invalid provider: {req.provider}")

    if not req.api_key or len(req.api_key) < 8:
        return {"valid": False, "message": "Key is too short"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if req.provider == "google":
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": req.api_key},
                )
                if resp.status_code == 200:
                    return {"valid": True, "message": "Key is valid"}
                if resp.status_code in (400, 403) or "API_KEY_INVALID" in resp.text:
                    return {"valid": False, "message": "Invalid API key"}
                return {"valid": False, "message": "Could not validate key"}

            elif req.provider == "anthropic":
                resp = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": req.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                if resp.status_code == 200:
                    return {"valid": True, "message": "Key is valid"}
                if resp.status_code == 401:
                    return {"valid": False, "message": "Invalid API key"}
                return {"valid": False, "message": "Could not validate key"}

            elif req.provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {req.api_key}"},
                )
                if resp.status_code == 200:
                    return {"valid": True, "message": "Key is valid"}
                if resp.status_code == 401:
                    return {"valid": False, "message": "Invalid API key"}
                return {"valid": False, "message": "Could not validate key"}

    except httpx.TimeoutException:
        return {"valid": False, "message": "Validation timed out — try again"}
    except Exception:
        return {"valid": False, "message": "Could not validate key"}

    return {"valid": False, "message": "Unknown provider"}


@app.post("/api/agent/safety-confirm")
async def api_agent_safety_confirm(req: SafetyConfirmRequest, request: Request):
    """Respond to a CU safety_decision / require_confirmation prompt."""
    forbidden = _require_origin(request)
    if forbidden is not None:
        return forbidden
    sid = req.session_id
    if not _is_valid_uuid(sid):
        return _error_response(400, "Invalid session_id")
    if sid not in _active_loops:
        return _error_response(404, "Session not found")

    safety_registry.decisions[sid] = req.confirm
    safety_registry.get_or_create_event(sid).set()

    logger.info("AUDIT safety_confirm — session_id=%s confirm=%s", sid, req.confirm)
    return {"session_id": sid, "confirmed": req.confirm}


@app.get("/api/agent/history/{session_id}")
async def api_agent_history(session_id: str):
    """Return the full step history for a session (without screenshots)."""
    if not _is_valid_uuid(session_id):
        return _error_response(400, "Invalid session_id")
    session = await _get_session_snapshot(session_id)
    if not session:
        return _error_response(404, "Session not found")

    steps = [s.model_dump(exclude={"screenshot_b64"}) for s in session.steps]
    return {"session_id": session_id, "steps": steps}


# ── noVNC Reverse Proxy ───────────────────────────────────────────────────────
# Proxy requests so the frontend never hits Docker-mapped ports directly.

_NOVNC_HTTP = "http://127.0.0.1:6080"
_NOVNC_WS   = "ws://127.0.0.1:6080"
_novnc_client: httpx.AsyncClient | None = None


def _get_novnc_client() -> httpx.AsyncClient:
    """Return a reusable httpx client for noVNC proxying."""
    global _novnc_client
    if _novnc_client is None or _novnc_client.is_closed:
        _novnc_client = httpx.AsyncClient(timeout=10.0)
    return _novnc_client


@app.websocket("/vnc/websockify")
async def vnc_ws_proxy(ws: WebSocket):
    """Proxy the noVNC WebSocket to the container's websockify."""
    if not _ws_origin_ok(ws):
        await ws.close(code=4403)
        logger.warning(
            "Rejected /vnc/websockify from bad origin: %r",
            ws.headers.get("origin", ""),
        )
        return
    # Shared-secret gate — must match /ws exactly. Reject BEFORE
    # ws.accept() and BEFORE opening the upstream socket to the
    # container's websockify, so a missing/bad token never gets any
    # data plane access and never consumes a backend connection slot.
    if not _ws_token_ok(ws):
        await ws.close(code=_WS_AUTH_CLOSE_CODE, reason=_WS_AUTH_CLOSE_REASON)
        logger.warning(
            "Rejected /vnc/websockify connection from %s: %s",
            ws.client.host if ws.client else "unknown",
            _WS_AUTH_CLOSE_REASON,
        )
        return
    await ws.accept()
    try:
        import websockets
        async with websockets.connect(
            f"{_NOVNC_WS}/websockify",
            subprotocols=["binary"],
            max_size=2**22,
        ) as upstream:

            async def client_to_upstream():
                try:
                    while True:
                        # Per-message timeout so a stalled peer can't wedge
                        # this pump indefinitely. 60s is generous for
                        # interactive VNC traffic; real sessions see
                        # input within milliseconds.
                        data = await asyncio.wait_for(ws.receive_bytes(), timeout=60)
                        await asyncio.wait_for(upstream.send(data), timeout=30)
                except (asyncio.TimeoutError, Exception):
                    pass

            async def upstream_to_client():
                try:
                    while True:
                        msg = await asyncio.wait_for(upstream.recv(), timeout=60)
                        if isinstance(msg, bytes):
                            await asyncio.wait_for(ws.send_bytes(msg), timeout=30)
                        else:
                            await asyncio.wait_for(ws.send_text(msg), timeout=30)
                except (asyncio.TimeoutError, Exception):
                    pass

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception as exc:
        logger.debug("VNC WebSocket proxy closed: %s", exc)


# Allowlisted noVNC static asset prefixes. Anything outside this list
# gets rejected at the edge regardless of what websockify would serve.
_NOVNC_ALLOWED_PREFIXES = (
    "vnc.html", "vnc_lite.html",
    "core/", "app/", "vendor/",
    "images/",
)


def _is_safe_vnc_path(path: str) -> bool:
    """Reject traversal, absolute paths, encoded slashes; enforce whitelist."""
    if not path:
        return False
    if path.startswith("/") or "\\" in path:
        return False
    lowered = path.lower()
    if "%2f" in lowered or "%5c" in lowered:
        return False
    # After url-decoding FastAPI already strips ``%2F``, so the
    # remaining check catches literal ``..`` segments.
    for seg in path.split("/"):
        if seg in ("..", "."):
            return False
    return any(path == p or path.startswith(p) for p in _NOVNC_ALLOWED_PREFIXES)


@app.get("/vnc/{path:path}")
async def vnc_http_proxy(path: str):
    """Proxy noVNC static files from the container's websockify web server."""
    from starlette.responses import Response
    if not _is_safe_vnc_path(path):
        return Response(content="forbidden", status_code=403)
    client = _get_novnc_client()
    try:
        resp = await client.get(f"{_NOVNC_HTTP}/{path}")
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=content_type,
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError):
        return Response(content="noVNC not available yet", status_code=502)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Accept a WebSocket connection for real-time event streaming."""
    # Origin check (C2): browsers send the Origin header on WS upgrades
    # but do NOT enforce CORS for WebSockets — the server must.
    # Without this, any webpage the user visits can open a
    # ``ws://127.0.0.1:8100/ws`` and read live desktop screenshots.
    if not _ws_origin_ok(ws):
        await ws.close(code=4403)
        logger.warning(
            "Rejected /ws from bad origin: %r",
            ws.headers.get("origin", ""),
        )
        return
    # Optional shared-secret gate. When CUA_WS_TOKEN is set, reject any
    # connection that doesn't present a matching ``?token=`` param.
    # Reuses the same helper as /vnc/websockify so the two surfaces
    # cannot drift apart.
    if not _ws_token_ok(ws):
        await ws.close(code=_WS_AUTH_CLOSE_CODE, reason=_WS_AUTH_CLOSE_REASON)
        logger.warning(
            "Rejected /ws connection from %s: %s",
            ws.client.host if ws.client else "unknown",
            _WS_AUTH_CLOSE_REASON,
        )
        return
    await ws.accept()
    _ws_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(_ws_clients))

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                mtype = msg.get("type")
                if mtype == "ping":
                    await ws.send_text(json.dumps({"event": "pong"}))
                elif mtype == "screenshot_mode":
                    # P-PUB — client tells us whether it currently
                    # needs the fallback screenshot stream. ``on`` /
                    # unknown-value defaults to subscribed only when the
                    # client also supplies an active session_id. Missing
                    # or stale session ids degrade to "off" so the
                    # publisher's lifetime is bounded by live sessions.
                    mode = (msg.get("mode") or "on").lower()
                    raw_session_id = (msg.get("session_id") or "").strip()
                    if mode == "off":
                        _unsubscribe_screenshots(ws)
                    elif raw_session_id and raw_session_id in _active_loops:
                        _subscribe_screenshots(ws, raw_session_id)
                    else:
                        _unsubscribe_screenshots(ws)
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.warning("WebSocket error: %s", e)
    finally:
        _unsubscribe_screenshots(ws)
        if ws in _ws_clients:
            _ws_clients.discard(ws)


async def _screenshot_publisher_loop():
    """Single capture loop that fans the latest frame to every
    subscribed /ws client.

    Design notes:

    * One loop per process. Started lazily by
      :func:`_subscribe_screenshots` on the 0→1 transition and
      cancelled by :func:`_unsubscribe_screenshots` on the 1→0
      transition when ``config.ws_screenshot_suspend_when_idle`` is
      True (default).
    * A subscriber is a ws client that has explicitly told us it
      wants screenshot frames (i.e. it is in the screenshot-fallback
      view). Clients on the noVNC interactive surface send
      ``{"type": "screenshot_mode", "mode": "off"}`` and do NOT
      count as subscribers — when every viewer is on noVNC we
      perform zero ``capture_screenshot`` calls, which is the whole
      point of this refactor.
    * Dedup is kept from the old per-client implementation: we only
      fan out a frame whose PNG hash differs from the previous one.
    """
    from backend.infra.docker import is_container_running

    global _screenshot_capture_count, _last_screenshot_frame
    auth_reported = False
    error_reported = False
    last_hash: str | None = _last_screenshot_frame[1] if _last_screenshot_frame else None
    logger.info("Screenshot publisher started (cadence=%.2fs)", config.ws_screenshot_interval)
    try:
        while True:
            try:
                await asyncio.sleep(config.ws_screenshot_interval)
                # No-subscriber suspend path. The loop task stays
                # alive but doesn't capture, so ``_screenshot_capture_count``
                # stays flat — this is what the "all clients on noVNC"
                # success criterion asserts.
                if config.ws_screenshot_suspend_when_idle and not _screenshot_subscribers:
                    auth_reported = False
                    error_reported = False
                    continue
                if not await is_container_running():
                    auth_reported = False
                    error_reported = False
                    last_hash = None
                    continue

                b64 = await capture_screenshot(mode="desktop")
                _screenshot_capture_count += 1
                auth_reported = False
                error_reported = False

                try:
                    frame_hash = hashlib.blake2b(
                        base64.b64decode(b64), digest_size=16,
                    ).hexdigest()
                except Exception:
                    frame_hash = hashlib.blake2b(
                        b64.encode("ascii"), digest_size=16,
                    ).hexdigest()

                # Always refresh the cache so a newly-attached
                # subscriber gets the freshest frame even when we
                # skip the fan-out below because the frame is a
                # duplicate of the previous one.
                _last_screenshot_frame = (b64, frame_hash)
                if frame_hash == last_hash:
                    continue
                last_hash = frame_hash

                msg = json.dumps({"event": "screenshot_stream", "screenshot": b64})
                stale: list[WebSocket] = []
                for ws in list(_screenshot_subscribers):
                    try:
                        await ws.send_text(msg)
                    except Exception:
                        stale.append(ws)
                for ws in stale:
                    _unsubscribe_screenshots(ws)
                    _ws_clients.discard(ws)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (401, 403) and not auth_reported:
                    auth_reported = True
                    logger.warning(
                        "Screenshot publisher auth failed (%s) — likely "
                        "AGENT_SERVICE_TOKEN mismatch after container restart",
                        status,
                    )
                    notice = json.dumps({
                        "event": "auth_failed",
                        "status": status,
                        "message": "Agent service rejected request (token mismatch). "
                                   "Restart the backend to pick up the current container token.",
                    })
                    for ws in list(_screenshot_subscribers):
                        try:
                            await ws.send_text(notice)
                        except Exception:
                            pass
                await asyncio.sleep(2)
            except Exception as exc:
                if not error_reported:
                    error_reported = True
                    logger.warning("Screenshot publisher error: %s", exc)
                await asyncio.sleep(2)
    except asyncio.CancelledError:
        logger.info("Screenshot publisher stopped")
        raise


def _detach_screenshot_subscriber(ws: WebSocket) -> str | None:
    """Remove *ws* from screenshot bookkeeping and return its session id."""
    session_id = _ws_screenshot_sessions.pop(ws, None)
    _screenshot_subscribers.discard(ws)
    if session_id is not None:
        bucket = _screenshot_subscribers_by_session.get(session_id)
        if bucket is not None:
            bucket.discard(ws)
            if not bucket:
                _screenshot_subscribers_by_session.pop(session_id, None)
    return session_id


def _maybe_stop_screenshot_publisher() -> None:
    """Cancel the idle publisher and clear stale cached frames."""
    global _screenshot_publisher_task, _last_screenshot_frame
    if _screenshot_publisher_task is not None and _screenshot_publisher_task.done():
        _screenshot_publisher_task = None
    if config.ws_screenshot_suspend_when_idle and not _screenshot_subscribers:
        _last_screenshot_frame = None
        if _screenshot_publisher_task is not None and not _screenshot_publisher_task.done():
            _screenshot_publisher_task.cancel()
            _screenshot_publisher_task = None


def _drop_screenshot_session(session_id: str) -> None:
    """Remove every screenshot subscriber currently attached to *session_id*."""
    for ws in list(_screenshot_subscribers_by_session.pop(session_id, ())):
        if _ws_screenshot_sessions.get(ws) == session_id:
            _ws_screenshot_sessions.pop(ws, None)
        _screenshot_subscribers.discard(ws)
    _maybe_stop_screenshot_publisher()


def _subscribe_screenshots(ws: WebSocket, session_id: str) -> None:
    """Add *ws* to the screenshot subscriber set for *session_id*.

    Idempotent. On the 0→1 transition starts the shared publisher
    task. The cached last frame (if any) is sent synchronously via a
    scheduled send so the new subscriber paints something before the
    next cadence tick.
    """
    global _screenshot_publisher_task
    if not session_id:
        _unsubscribe_screenshots(ws)
        return
    if ws in _screenshot_subscribers and _ws_screenshot_sessions.get(ws) == session_id:
        return
    _detach_screenshot_subscriber(ws)
    _ws_screenshot_sessions[ws] = session_id
    _screenshot_subscribers.add(ws)
    _screenshot_subscribers_by_session.setdefault(session_id, set()).add(ws)
    # Replay cached frame to the new subscriber so small UIs feel
    # instant. Fire-and-forget: if the send fails the publisher will
    # reap this ws on its next tick.
    if _last_screenshot_frame is not None:
        b64, _ = _last_screenshot_frame
        try:
            asyncio.get_running_loop().create_task(
                ws.send_text(json.dumps({"event": "screenshot_stream", "screenshot": b64}))
            )
        except RuntimeError:
            pass
    if _screenshot_publisher_task is None or _screenshot_publisher_task.done():
        _screenshot_publisher_task = asyncio.create_task(_screenshot_publisher_loop())


def _unsubscribe_screenshots(ws: WebSocket) -> None:
    """Remove *ws* from the subscriber set. Idempotent.

    On the 1→0 transition cancels the shared publisher when
    ``config.ws_screenshot_suspend_when_idle`` is True (default).
    """
    _detach_screenshot_subscriber(ws)
    _maybe_stop_screenshot_publisher()

# === merged from backend/ws_schema.py ===
"""Pydantic schema for WebSocket events broadcast to the frontend.

The backend historically broadcast loosely-typed ``{"event": ..., **data}``
dicts. This module is the single source of truth for the wire format:

* Every outbound event is a subclass of :class:`WSEvent` discriminated
  by the ``event`` field.
* :func:`validate_outbound` is called from :func:`backend.server._broadcast`
  so a typo or schema drift is logged instead of silently shipping bad
  JSON to every connected client.
* The matching TypeScript types live in ``frontend/src/types/ws.d.ts``
  â€” keep them in sync with any change here.

Kept intentionally permissive for forward compat: unknown events fall
through as :class:`GenericWSEvent` rather than being rejected.
"""


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
    final_text: Optional[str] = None
    gemini_grounding: Optional[dict[str, Any]] = None


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
    logs this and still ships the payload â€” the intent is an early-
    warning for schema drift without breaking the user-facing stream.
    """
    model = _TYPED_EVENTS.get(event)
    if model is None:
        return None  # unknown event â€” allowed for forward compat
    try:
        model.model_validate({"event": event, **data})
    except Exception as exc:  # pydantic.ValidationError or value errors
        return str(exc).splitlines()[0][:200]
    return None
