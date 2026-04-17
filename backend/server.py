"""FastAPI application — REST endpoints + WebSocket streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import config, get_all_key_statuses, resolve_api_key
from backend.engine import _load_allowed_models_json
from pydantic import BaseModel, Field
from backend.models import (
    AgentAction,
    AgentSession,
    SessionStatus,
    StartTaskRequest,
    TaskStatusResponse,
)
from backend.agent.loop import AgentLoop
from backend.agent.graph import (
    init_runtime,
    load_session_snapshot,
    shutdown_runtime,
)
from backend.agent import safety as safety_registry
from backend.agent.screenshot import capture_screenshot, check_service_health
from backend.docker_manager import (
    build_image,
    get_container_status,
    start_container,
    stop_container,
)
from backend.parity_check import validate_tool_parity

import httpx

logging.basicConfig(
    level=logging.DEBUG if config.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
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
        Path(_SESSIONS_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        await init_runtime(_SESSIONS_DB_PATH)
        logger.info("Session checkpointer ready at %s", _SESSIONS_DB_PATH)
    except Exception:
        logger.exception("Failed to initialise session checkpointer")

    try:
        validate_tool_parity()
    except Exception as exc:
        logger.warning("Tool parity check failed: %s", exc)

    try:
        yield
    finally:
        # ── shutdown ──────────────────────────────────────────────────
        for sid in list(_active_tasks.keys()):
            task = _active_tasks.get(sid)
            if task and not task.done():
                task.cancel()
        _active_tasks.clear()
        _active_loops.clear()

        global _novnc_client
        if _novnc_client is not None and not _novnc_client.is_closed:
            try:
                await _novnc_client.aclose()
            except Exception:
                logger.exception("Error closing noVNC proxy client")
        _novnc_client = None

        try:
            await shutdown_runtime()
        except Exception:
            logger.exception("Error shutting down session checkpointer")

        logger.info("CUA backend shut down")


app = FastAPI(title="CUA — Computer Using Agent", version="1.0.0", lifespan=_lifespan)

# CORS: restrict to local dev origins by default; override with CORS_ORIGINS env var
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "").split(",")
    if o.strip()
] or [
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


# ── Security headers ──────────────────────────────────────────────────────────

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Conservative CSP; frontend bundles are served by the Vite dev server,
    # not this backend. Tighten further if you ever serve HTML from here.
    "Content-Security-Policy": (
        "default-src 'none'; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'"
    ),
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach basic security headers to every HTTP response."""
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response

# Security constants
_MAX_CONCURRENT_SESSIONS = 3
_MAX_STEPS_HARD_CAP = 200

# Sessions database (LangGraph sqlite checkpointer). Override with CUA_SESSIONS_DB.
_SESSIONS_DB_PATH = os.getenv(
    "CUA_SESSIONS_DB",
    str(Path.home() / ".computer-use" / "sessions.sqlite"),
)

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
        # Opportunistic eviction of idle keys to bound memory
        if len(self._calls) > 1024:
            self._calls = {
                k: v for k, v in self._calls.items()
                if v and now - v[-1] < self._window
            }
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
_ws_clients: list[WebSocket] = []


def _cleanup_session(sid: str) -> None:
    """Remove bookkeeping for a session (tasks, loops, safety state)."""
    _active_tasks.pop(sid, None)
    _active_loops.pop(sid, None)
    safety_registry.clear(sid)


async def _get_session_snapshot(session_id: str) -> AgentSession | None:
    """Return active or persisted session state for status/history calls.

    Active runs are served from ``_active_loops``. Once a run finishes
    and its entry is cleaned up, the LangGraph sqlite checkpointer
    becomes the source of truth (populated by the ``finalize`` node
    inside :class:`AgentLoop`).
    """
    loop = _active_loops.get(session_id)
    if loop:
        return loop.session

    try:
        snapshot = await load_session_snapshot(session_id)
    except Exception:
        logger.exception("Failed to load session snapshot — session_id=%s", session_id)
        return None
    if snapshot is None:
        return None
    try:
        return AgentSession.model_validate(snapshot)
    except Exception:
        logger.exception("Invalid session snapshot — session_id=%s", session_id)
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

_broadcast_tasks: set[asyncio.Task] = set()


async def _broadcast(event: str, data: dict) -> None:
    """Send a JSON message to all connected WebSocket clients."""
    msg = json.dumps({"event": event, **data})
    stale: list[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            stale.append(ws)
    for ws in stale:
        try:
            _ws_clients.remove(ws)
        except ValueError:
            pass


def _schedule_broadcast(event: str, data: dict) -> None:
    """Fire-and-forget broadcast whose task is tracked to prevent GC."""
    task = asyncio.create_task(_broadcast(event, data))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/api/models")
async def api_models():
    """Return the canonical allowed-models list for frontend dropdowns.

    Source of truth: backend/allowed_models.json
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
async def set_agent_mode(body: dict):
    """Keep the agent service in the supported desktop mode."""
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
async def api_start_container():
    """Build-if-needed and start the CUA Docker container."""
    success = await start_container()
    return {"success": success}


@app.post("/api/container/stop")
async def api_stop_container():
    """Stop all running agents then remove the Docker container."""
    for sid in list(_active_tasks.keys()):
        await _stop_agent(sid)
    success = await stop_container()
    return {"success": success}


@app.post("/api/container/build")
async def api_build_image():
    """Trigger a Docker image build."""
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
async def api_screenshot():
    """Get current screenshot as base64."""
    try:
        b64 = await capture_screenshot()
        return {"screenshot": b64}
    except Exception:
        logger.exception("Screenshot capture failed")
        return _error_response(500, "Could not capture screenshot")


@app.post("/api/agent/start")
async def api_start_agent(req: StartTaskRequest, request: Request):
    """Start a new agent session with input validation."""

    # ── Rate limit (per-IP) ─────────────────────────────────
    if not _agent_start_limiter.allow(_client_ip(request)):
        return _error_response(429, "Rate limit exceeded — max 10 starts per minute")

    # ── Validate inputs ───────────────────────────────────────────────
    if req.engine != "computer_use":
        return _error_response(400, f"Invalid engine: {req.engine}. Only 'computer_use' is supported.")
    if req.mode != "desktop":
        return _error_response(400, "Browser mode is no longer supported. Use mode='desktop'.")
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

    # Resolve API key: UI input → .env → system env
    resolved_key, key_source = resolve_api_key(req.provider, req.api_key)
    if not resolved_key or len(resolved_key) < 8:
        return _error_response(400, "API key is required. Provide it in the UI, .env file, or system environment variable.")

    # Cap max_steps to prevent runaway agents
    req.max_steps = min(req.max_steps, _MAX_STEPS_HARD_CAP)

    # Resolve reasoning_effort: request > env var > default "low"
    _VALID_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
    reasoning_effort = (req.reasoning_effort or os.getenv("OPENAI_REASONING_EFFORT") or "low").lower()
    if reasoning_effort not in _VALID_REASONING_EFFORTS:
        reasoning_effort = "low"

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
    if not container_ok:
        return _error_response(503, "Could not start the virtual environment. Please check that the system is set up correctly.")

    loop = AgentLoop(
        task=req.task,
        api_key=resolved_key,
        model=req.model,
        max_steps=req.max_steps,
        mode=req.mode,
        engine=req.engine,
        provider=req.provider,
        execution_target=req.execution_target,
        reasoning_effort=reasoning_effort if req.provider == "openai" else None,
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

        # Finished-session retention is handled by the LangGraph sqlite
        # checkpointer (written by the ``finalize`` node inside the loop).
        await _broadcast("agent_finished", {
            "session_id": loop.session_id,
            "status": session.status.value,
            "steps": len(session.steps),
        })
        _cleanup_session(loop.session_id)

    task = asyncio.create_task(_run_and_notify())
    _active_tasks[loop.session_id] = task

    logger.info("AUDIT session_started — session_id=%s engine=%s", loop.session_id, req.engine)

    return {
        "session_id": loop.session_id,
        "status": "running",
        "mode": req.mode,
        "engine": req.engine,
        "provider": req.provider,
    }


@app.post("/api/agent/stop/{session_id}")
async def api_stop_agent(session_id: str):
    """Stop a running agent session by ID."""
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
    ).model_dump()


# ── Safety Confirmation for CU Engine ─────────────────────────────────────────


class SafetyConfirmRequest(BaseModel):
    """Body for the safety-confirm endpoint."""
    session_id: str
    confirm: bool = False


class ValidateKeyRequest(BaseModel):
    """Body for the key validation endpoint."""
    provider: str = Field(max_length=20)
    api_key: str = Field(max_length=256)


@app.post("/api/keys/validate")
async def api_validate_key(req: ValidateKeyRequest, request: Request):
    """Lightweight API key validation — makes a minimal call to the provider.

    Returns ``{valid: true/false, message: ...}``.  Never logs the raw key.
    """
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
async def api_agent_safety_confirm(req: SafetyConfirmRequest):
    """Respond to a CU safety_decision / require_confirmation prompt.

    When the native Computer Use engine encounters a
    ``require_confirmation`` safety decision, it broadcasts a
    ``safety_confirmation`` event via WebSocket.  The frontend displays
    a dialog and calls this endpoint with the user's decision.

    The AgentLoop awaits an event in :mod:`backend.agent.safety` which
    this endpoint signals.
    """
    sid = req.session_id
    if not _is_valid_uuid(sid):
        return _error_response(400, "Invalid session_id")
    if sid not in _active_loops:
        return _error_response(404, "Session not found")

    # Store the decision and signal the waiting loop
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
                        data = await ws.receive_bytes()
                        await upstream.send(data)
                except Exception:
                    pass

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                        else:
                            await ws.send_text(msg)
                except Exception:
                    pass

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception as exc:
        logger.debug("VNC WebSocket proxy closed: %s", exc)


@app.get("/vnc/{path:path}")
async def vnc_http_proxy(path: str):
    """Proxy noVNC static files from the container's websockify web server."""
    from starlette.responses import Response
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
    await ws.accept()
    _ws_clients.append(ws)
    logger.info("WebSocket client connected (%d total)", len(_ws_clients))

    streaming_task: asyncio.Task | None = None
    try:
        streaming_task = asyncio.create_task(_stream_screenshots(ws))

        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"event": "pong"}))
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.warning("WebSocket error: %s", e)
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
        if streaming_task:
            streaming_task.cancel()


async def _stream_screenshots(ws: WebSocket):
    """Periodically send screenshots to a specific WS client.

    Uses 'desktop' mode (scrot) so the full X11 display is visible,
    including the browser window, desktop background, and taskbar.
    Skips capture attempts when the container is not running to avoid
    spamming warnings.
    """
    from backend.docker_manager import is_container_running

    while True:
        try:
            await asyncio.sleep(config.ws_screenshot_interval)
            # Only attempt capture when the container is actually running
            if not await is_container_running():
                continue
            b64 = await capture_screenshot(mode="desktop")
            await ws.send_text(json.dumps({
                "event": "screenshot_stream",
                "screenshot": b64,
            }))
        except asyncio.CancelledError:
            break
        except WebSocketDisconnect:
            break
        except Exception:
            await asyncio.sleep(2)
