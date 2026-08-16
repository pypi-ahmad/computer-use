"""Typed HTTP contracts for the clean `/api/v2` surface."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import json
import os
import re
import secrets
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from google.oauth2.credentials import Credentials
from pydantic import BaseModel, ConfigDict, Field

from backend.v2.credentials import credential_vault
from backend.v2.models import CATALOG, ComputerUseModel
from backend.v2.orchestrator import ExecutionOutcome, ExecutionRequest, orchestrator
from backend.v2.persistence import SqliteStore, StoredSession, WorkflowVersion
from backend.v2.retention import frame_retention
from backend.v2.routing import CircuitBreaker, RouteFailure, RouteSpec, run_with_fallback

router = APIRouter(prefix="/api/v2", tags=["v2"])


def _camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ContractModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True, extra="forbid")


class CredentialSessionInput(ContractModel):
    credentials: dict[str, str]
    ttl_seconds: int = Field(default=28_800, ge=1, le=28_800)


class GoogleOAuthStartInput(ContractModel):
    quota_project_id: str | None = Field(default=None, max_length=128)
    ttl_seconds: int = Field(default=28_800, ge=1, le=28_800)


class WorkflowInput(ContractModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    name: str = Field(min_length=1, max_length=120)
    variables_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    steps: list[str] = Field(min_length=1, max_length=100)


class WorkflowVersionInput(ContractModel):
    variables_schema: dict[str, Any] | None = None
    steps: list[str] = Field(min_length=1, max_length=100)


class WorkflowCompileInput(ContractModel):
    variables: dict[str, Any] = Field(default_factory=dict)


class FallbackRouteInput(ContractModel):
    model: str
    route: str


class SessionInput(ContractModel):
    task: str = Field(min_length=1, max_length=10_000)
    model: str
    primary_route: str | None = None
    fallback_routes: list[str | FallbackRouteInput] = Field(default_factory=list, max_length=10)
    credential_session_id: str | None = None
    max_steps: int = Field(default=50, ge=1, le=200)
    reasoning_effort: str | None = Field(default=None, pattern="^(none|low|medium|high|xhigh|max)$")
    safety_policy: str = Field(
        default="provider_default", pattern="^(provider_default|confirm_mutating|read_only)$"
    )
    use_builtin_search: bool = False
    attached_files: list[str] = Field(default_factory=list, max_length=20)
    retain_audit_frames: bool = True


class SessionPatch(ContractModel):
    status: str = Field(pattern="^STOPPING$")


class SafetyDecisionInput(ContractModel):
    nonce: str = Field(min_length=1, max_length=256)
    confirm: bool


class ErrorEnvelope(ContractModel):
    code: str
    message: str
    details: Any = None
    is_retryable: bool = False
    request_id: str


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    *,
    details: Any = None,
    retryable: bool = False,
) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    error = ErrorEnvelope(
        code=code, message=message, details=details, is_retryable=retryable, request_id=request_id
    )
    return JSONResponse(status_code=status, content={"error": error.model_dump(by_alias=True)})


_stores: dict[str, SqliteStore] = {}
_store_lock = threading.Lock()
_retention = frame_retention
_circuit_breaker = CircuitBreaker()
_oauth_flows: dict[str, dict[str, Any]] = {}
_oauth_lock = threading.Lock()
_GOOGLE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language.retriever",
)
_GOOGLE_OAUTH_EXCHANGE_URL = "https://oauth2.googleapis.com/token"


def _store() -> SqliteStore:
    configured = os.getenv("CUA_V2_DB_PATH", "data/computer-use-v2.sqlite3")
    path = configured if configured == ":memory:" else str(Path(configured).resolve())
    with _store_lock:
        if path not in _stores:
            _stores[path] = SqliteStore(path)
        return _stores[path]


def _model_payload(model: ComputerUseModel) -> dict[str, Any]:
    return {
        "logicalId": model.logical_id,
        "displayName": model.display_name,
        "family": model.family,
        "supportsComputerUse": model.supports_computer_use,
        "contextWindow": model.context_window,
        "maxOutputTokens": model.max_output_tokens,
        "coordinateSpace": model.coordinate_space,
        "lifecycle": model.lifecycle,
        "supportsPromptCaching": model.supports_prompt_caching,
        "maxImageLongEdge": model.max_image_long_edge,
        "reasoningEfforts": model.reasoning_efforts,
        "routes": [
            {
                "id": route.id,
                "provider": route.provider,
                "transport": route.transport,
                "modelId": route.model_id,
                "toolVersion": route.tool_version,
                "beta": route.beta,
            }
            for route in model.routes
        ],
    }


def _session_payload(session: StoredSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "task": session.task,
        "model": session.model,
        "primaryRoute": session.primary_route,
        "status": session.status,
        "createdAt": session.created_at,
    }


def _workflow_payload(workflow: WorkflowVersion) -> dict[str, Any]:
    return {
        "id": workflow.id,
        "slug": workflow.slug,
        "name": workflow.name,
        "version": workflow.version,
        "variablesSchema": workflow.variables_schema,
        "steps": workflow.steps,
        "createdAt": workflow.created_at,
    }


def _route_readiness(provider: str) -> tuple[bool, str]:
    envs: dict[str, tuple[str, ...]] = {
        "OPENAI": ("OPENAI_API_KEY",),
        "ANTHROPIC": ("ANTHROPIC_API_KEY",),
        "GOOGLE": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    }
    configured = any(os.getenv(name, "").strip() for name in envs.get(provider, ()))
    if provider == "GOOGLE" and not configured:
        configured = bool(
            os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
            and (
                os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
                or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE", "").strip()
            )
        )
    auth_mode = "API_KEY_OR_OAUTH" if provider == "GOOGLE" else "API_KEY"
    return configured, auth_mode


@router.get("/models")
def list_models() -> dict[str, Any]:
    return {
        "data": [_model_payload(model) for model in CATALOG.models()],
        "catalogVersion": CATALOG.version,
        "verifiedAt": CATALOG.verified_at,
    }


@router.get("/provider-routes")
def list_provider_routes() -> dict[str, Any]:
    seen: set[tuple[str, str, str]] = set()
    data: list[dict[str, Any]] = []
    for model in CATALOG.models():
        for route in model.routes:
            key = (route.id, route.provider, route.transport)
            if key in seen:
                continue
            seen.add(key)
            configured, auth_mode = _route_readiness(route.provider)
            executable = route.provider in {"OPENAI", "ANTHROPIC", "GOOGLE"}
            data.append(
                {
                    "id": route.id,
                    "provider": route.provider,
                    "transport": route.transport,
                    "isConfigured": configured,
                    "isExecutable": executable,
                    "authMode": auth_mode,
                    "circuitState": _circuit_breaker.state(f"{model.logical_id}@{route.id}"),
                }
            )
    return {"data": data}


@router.post("/credential-sessions", status_code=201)
def create_credential_session(payload: CredentialSessionInput, request: Request) -> Any:
    try:
        created = credential_vault.create(payload.credentials, ttl_seconds=payload.ttl_seconds)
    except ValueError as exc:
        return error_response(request, 422, "VALIDATION_ERROR", str(exc))
    return {"id": created.id, "providers": created.providers, "expiresAt": created.expires_at}


@router.get("/credential-sessions/{credential_session_id}", response_model=None)
def get_credential_session(
    credential_session_id: str, request: Request
) -> dict[str, Any] | JSONResponse:
    session = credential_vault.status(credential_session_id)
    if session is None:
        return error_response(request, 404, "NOT_FOUND", "Credential session not found or expired")
    return {"id": session.id, "providers": session.providers, "expiresAt": session.expires_at}


def _google_oauth_client() -> tuple[str, str]:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    configured_file = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE", "").strip()
    if configured_file and (not client_id or not client_secret):
        document = json.loads(Path(configured_file).read_text(encoding="utf-8"))
        app = document.get("web") or document.get("installed") or {}
        client_id = client_id or str(app.get("client_id") or "")
        client_secret = client_secret or str(app.get("client_secret") or "")
    if not client_id or not client_secret:
        raise ValueError(
            "Google OAuth requires GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET, "
            "or GOOGLE_OAUTH_CLIENT_SECRET_FILE."
        )
    return client_id, client_secret


@router.post("/credential-sessions/google/oauth/start", status_code=201, response_model=None)
def start_google_oauth(
    payload: GoogleOAuthStartInput, request: Request
) -> dict[str, Any] | JSONResponse:
    try:
        client_id, client_secret = _google_oauth_client()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return error_response(request, 422, "OAUTH_NOT_CONFIGURED", str(exc))
    credential_session = credential_vault.create_empty(ttl_seconds=payload.ttl_seconds)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    redirect_uri = os.getenv("CUA_GOOGLE_OAUTH_REDIRECT_URI", "").strip() or str(
        request.url_for("google_oauth_callback")
    )
    with _oauth_lock:
        _oauth_flows[state] = {
            "expires_at": time.time() + 600,
            "credential_session_id": credential_session.id,
            "client_id": client_id,
            "client_secret": client_secret,
            "verifier": verifier,
            "redirect_uri": redirect_uri,
            "quota_project_id": payload.quota_project_id
            or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
            or None,
        }
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(_GOOGLE_OAUTH_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return {
        "credentialSessionId": credential_session.id,
        "expiresAt": credential_session.expires_at,
        "authorizationUrl": f"https://accounts.google.com/o/oauth2/v2/auth?{query}",
    }


@router.get(
    "/credential-sessions/google/oauth/callback", name="google_oauth_callback", response_model=None
)
async def google_oauth_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> HTMLResponse:
    with _oauth_lock:
        flow = _oauth_flows.pop(state, None)
    if error or not code or flow is None or float(flow["expires_at"]) <= time.time():
        message = html.escape(error or "The OAuth request is missing, invalid, or expired.")
        return HTMLResponse(f"<h1>Google sign-in failed</h1><p>{message}</p>", status_code=400)
    async with httpx.AsyncClient(timeout=30) as http:
        token_response = await http.post(
            _GOOGLE_OAUTH_EXCHANGE_URL,
            data={
                "code": code,
                "client_id": flow["client_id"],
                "client_secret": flow["client_secret"],
                "redirect_uri": flow["redirect_uri"],
                "grant_type": "authorization_code",
                "code_verifier": flow["verifier"],
            },
        )
    if token_response.is_error:
        return HTMLResponse(
            "<h1>Google sign-in failed</h1><p>Token exchange was rejected.</p>", status_code=400
        )
    token = token_response.json()
    credentials = Credentials(
        token=token.get("access_token"),
        refresh_token=token.get("refresh_token"),
        token_uri=_GOOGLE_OAUTH_EXCHANGE_URL,
        client_id=flow["client_id"],
        client_secret=flow["client_secret"],
        scopes=list(_GOOGLE_OAUTH_SCOPES),
    )
    try:
        credential_vault.put_google_oauth(
            flow["credential_session_id"],
            credentials,
            quota_project_id=flow["quota_project_id"],
        )
    except KeyError:
        return HTMLResponse(
            "<h1>Google sign-in failed</h1><p>The credential session expired.</p>", status_code=400
        )
    return HTMLResponse(
        "<h1>Google sign-in complete</h1><p>You can close this window and return to the workbench.</p>"
        "<script>window.opener?.postMessage({type:'cua-google-oauth-complete'}, window.location.origin);window.close()</script>"
    )


@router.delete("/credential-sessions/{credential_session_id}", status_code=204)
def delete_credential_session(credential_session_id: str) -> None:
    credential_vault.delete(credential_session_id)


@router.post("/sessions", status_code=201, response_model=None)
async def create_session(payload: SessionInput, request: Request) -> dict[str, Any] | JSONResponse:
    try:
        model = CATALOG.get(payload.model)
    except ValueError as exc:
        return error_response(
            request, 422, "VALIDATION_ERROR", str(exc), details={"field": "model"}
        )
    primary = payload.primary_route or model.routes[0].id
    primary_route = next((route for route in model.routes if route.id == primary), None)
    if primary_route is None:
        return error_response(
            request,
            422,
            "VALIDATION_ERROR",
            "Primary route is not compatible with the selected model",
            details={"allowedRoutes": [route.id for route in model.routes]},
        )

    selections: list[tuple[str, ComputerUseModel, Any]] = [
        (f"{model.logical_id}@{primary}", model, primary_route)
    ]
    for fallback in payload.fallback_routes:
        fallback_model_id = payload.model if isinstance(fallback, str) else fallback.model
        fallback_route_id = fallback if isinstance(fallback, str) else fallback.route
        try:
            fallback_model = CATALOG.get(fallback_model_id)
        except ValueError:
            return error_response(
                request, 422, "VALIDATION_ERROR", f"Unknown fallback model: {fallback_model_id}"
            )
        fallback_route = next(
            (route for route in fallback_model.routes if route.id == fallback_route_id), None
        )
        if fallback_route is None:
            return error_response(
                request,
                422,
                "VALIDATION_ERROR",
                f"Route {fallback_route_id} is not compatible with {fallback_model_id}",
            )
        key = f"{fallback_model.logical_id}@{fallback_route.id}"
        if key not in {item[0] for item in selections}:
            selections.append((key, fallback_model, fallback_route))

    if payload.attached_files:
        from backend.files import validate_attached_files

        try:
            for provider in {route.provider for _, _, route in selections}:
                await validate_attached_files(provider, payload.attached_files)
        except ValueError as exc:
            return error_response(request, 422, "ATTACHED_FILES_INVALID", str(exc))

    serialized_fallbacks = [key for key, _, _ in selections[1:]]
    stored = _store().create_session(
        payload.task, payload.model, primary, fallbacks=serialized_fallbacks
    )
    _store().set_session_status(stored.id, "RUNNING")
    _store().append_event(stored.id, "SESSION_STARTED", {"primaryRoute": selections[0][0]})
    _retention.set_enabled(stored.id, payload.retain_audit_frames)
    selection_by_id = {key: (selected_model, route) for key, selected_model, route in selections}
    live_action_count = 0

    def _on_execution_event(event: dict[str, Any]) -> None:
        nonlocal live_action_count
        orchestrator.publish(stored.id, event)
        if event.get("event") == "ACTION":
            live_action_count += 1
            action = event.get("action") or {}
            _store().append_action(
                stored.id,
                live_action_count,
                str(action.get("action") or "UNKNOWN").upper(),
                action,
                confirmed=True,
            )
        elif event.get("event") == "LOG":
            _store().append_event(
                stored.id, "LOG", {"level": event.get("level"), "message": event.get("message")}
            )

    async def _invoke(spec: RouteSpec) -> ExecutionOutcome:
        selected_model, route = selection_by_id[spec.id]
        if route.provider not in {"OPENAI", "ANTHROPIC", "GOOGLE"}:
            raise RouteFailure(
                "Route is unavailable: no verified execution bridge", retryable=False
            )
        credential = (
            credential_vault.resolve(payload.credential_session_id, route.provider)
            if payload.credential_session_id
            else None
        )
        oauth_credentials = (
            credential.oauth_credentials if credential and credential.method == "oauth" else None
        )
        quota_project_id = credential.quota_project_id if credential else None
        if credential is None:
            from backend.infra.config import resolve_api_key

            raw_key, _source = resolve_api_key(route.provider.lower())
            raw_key = raw_key or ""
        else:
            raw_key = credential.get_secret_value()
        if not raw_key and oauth_credentials is None:
            raise RouteFailure(f"No credential is available for {route.provider}", retryable=False)
        _store().append_event(
            stored.id, "ROUTE_ATTEMPTED", {"route": spec.id, "provider": route.provider}
        )
        try:
            return await orchestrator.start(
                ExecutionRequest(
                    session_id=stored.id,
                    task=payload.task,
                    provider=route.provider.lower(),
                    model_id=route.model_id,
                    api_key=raw_key or None,
                    max_steps=payload.max_steps,
                    reasoning_effort=payload.reasoning_effort
                    if selected_model.family in {"OPENAI", "GEMINI"}
                    else None,
                    oauth_credentials=oauth_credentials,
                    quota_project_id=quota_project_id,
                    safety_policy=payload.safety_policy,
                    use_builtin_search=payload.use_builtin_search,
                    attached_files=tuple(payload.attached_files),
                    on_event=_on_execution_event,
                )
            )
        except Exception as exc:
            # The legacy loop may already have executed an OS action. Do not
            # retry or fail over when completion is uncertain: replay could
            # duplicate clicks, typing, or destructive actions.
            raise RuntimeError("Execution failed with uncertain action state") from exc

    async def _coordinate() -> None:
        try:
            result = await run_with_fallback(
                [
                    RouteSpec(key, route.provider, route.model_id, max_attempts=1)
                    for key, _, route in selections
                ],
                _invoke,
                _circuit_breaker,
            )
            outcome = result.value
            if live_action_count == 0:
                for sequence, action in enumerate(outcome.actions, start=1):
                    _store().append_action(
                        stored.id,
                        sequence,
                        str(action.get("action", "UNKNOWN")).upper(),
                        action,
                        confirmed=True,
                    )
            _store().append_metric(
                stored.id,
                "EXECUTION",
                outcome.duration_ms,
                outcome.input_tokens,
                outcome.output_tokens,
            )
            _store().append_event(
                stored.id,
                "ROUTE_SUCCEEDED",
                {
                    "route": result.route_id,
                    "attempts": result.attempts,
                    "executionSessionId": outcome.session_id,
                },
            )
            _store().save_checkpoint(
                stored.id, payload.task, len(outcome.actions), None, {"status": outcome.status}
            )
            _store().set_session_status(stored.id, outcome.status)
            orchestrator.publish(stored.id, {"event": "SESSION_TERMINAL", "status": outcome.status})
        except asyncio.CancelledError:
            _store().append_event(stored.id, "SESSION_STOPPED", {})
            _store().set_session_status(stored.id, "STOPPED")
            orchestrator.publish(stored.id, {"event": "SESSION_TERMINAL", "status": "STOPPED"})
            raise
        except RouteFailure as exc:
            _store().append_event(stored.id, "SESSION_FAILED", {"message": str(exc)})
            _store().set_session_status(stored.id, "ERROR")
            orchestrator.publish(
                stored.id, {"event": "SESSION_TERMINAL", "status": "ERROR", "message": str(exc)}
            )
        except Exception as exc:
            _store().append_event(stored.id, "SESSION_REQUIRES_REVIEW", {"message": str(exc)})
            _store().set_session_status(stored.id, "ERROR")
            orchestrator.publish(
                stored.id, {"event": "SESSION_TERMINAL", "status": "ERROR", "message": str(exc)}
            )

    task = asyncio.create_task(_coordinate())
    orchestrator.track(stored.id, task)
    return {
        **_session_payload(stored),
        "status": "RUNNING",
        "executionSessionId": stored.id,
        "activeRoute": selections[0][0],
        "fallbackRoutes": serialized_fallbacks,
    }


@router.get("/sessions")
def list_sessions(cursor: int = 0, limit: int = 50) -> dict[str, Any]:
    items, next_cursor = _store().list_sessions(
        cursor=max(0, cursor), limit=max(1, min(limit, 100))
    )
    return {"data": [_session_payload(item) for item in items], "nextCursor": next_cursor}


@router.get("/sessions/{session_id}", response_model=None)
def get_session(session_id: str, request: Request) -> dict[str, Any] | JSONResponse:
    session = _store().get_session(session_id)
    return (
        _session_payload(session)
        if session is not None
        else error_response(request, 404, "NOT_FOUND", "Session not found")
    )


@router.patch("/sessions/{session_id}", response_model=None)
def stop_session(
    session_id: str, payload: SessionPatch, request: Request
) -> dict[str, Any] | JSONResponse:
    session = _store().get_session(session_id)
    if session is not None:
        orchestrator.stop(session_id)
        session = _store().set_session_status(session_id, payload.status)
    return (
        _session_payload(session)
        if session is not None
        else error_response(request, 404, "NOT_FOUND", "Session not found")
    )


@router.post("/sessions/{session_id}/safety-decisions", response_model=None)
def decide_safety(
    session_id: str, payload: SafetyDecisionInput, request: Request
) -> dict[str, Any] | JSONResponse:
    from backend import safety as safety_registry

    execution_id = orchestrator.execution_id(session_id)
    if execution_id is None:
        return error_response(
            request, 404, "NOT_FOUND", "No active safety decision exists for this session"
        )
    if not safety_registry.verify_nonce(execution_id, payload.nonce):
        return error_response(
            request, 403, "INVALID_NONCE", "Invalid or expired safety decision nonce"
        )
    safety_registry.decisions[execution_id] = payload.confirm
    safety_registry.set_decision(execution_id)
    _store().append_event(session_id, "SAFETY_DECIDED", {"confirmed": payload.confirm})
    return {"sessionId": session_id, "confirmed": payload.confirm}


@router.delete("/sessions/{session_id}", status_code=204, response_model=None)
def delete_session(session_id: str, request: Request) -> None | JSONResponse:
    orchestrator.stop(session_id)
    if not _store().delete_session(session_id):
        return error_response(request, 404, "NOT_FOUND", "Session not found")
    _retention.purge_session(session_id)
    _retention.clear(session_id)
    return None


def _page(items: list[dict[str, Any]], cursor: int, limit: int) -> dict[str, Any]:
    start = max(0, cursor)
    size = max(1, min(limit, 100))
    data = items[start : start + size]
    next_cursor = start + size if start + size < len(items) else None
    return {"data": data, "nextCursor": next_cursor}


@router.get("/sessions/{session_id}/actions")
def list_actions(session_id: str, cursor: int = 0, limit: int = 50) -> dict[str, Any]:
    return _page(_store().list_actions(session_id), cursor, limit)


@router.get("/sessions/{session_id}/events")
def list_events(session_id: str, cursor: int = 0, limit: int = 50) -> dict[str, Any]:
    return _page(_store().list_events(session_id), cursor, limit)


@router.get("/sessions/{session_id}/metrics")
def list_metrics(session_id: str, cursor: int = 0, limit: int = 50) -> dict[str, Any]:
    return _page(_store().list_metrics(session_id), cursor, limit)


@router.get("/analytics")
def analytics(
    session_id: str | None = None, model: str | None = None, route: str | None = None
) -> dict[str, Any]:
    return _store().analytics(session_id=session_id, model=model, route=route)


@router.get("/desktop")
def desktop() -> dict[str, str]:
    params = {
        "autoconnect": "1",
        "reconnect": "1",
        "resize": "scale",
        "path": "vnc/websockify",
    }
    return {"viewerUrl": f"/vnc/vnc.html?{urlencode(params)}"}


@router.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    routes = []
    for model in CATALOG.models():
        for route in model.routes:
            configured, auth_mode = _route_readiness(route.provider)
            routes.append(
                {
                    "model": model.logical_id,
                    "route": route.id,
                    "provider": route.provider,
                    "configured": configured,
                    "authMode": auth_mode,
                }
            )
    return {
        "catalogVersion": CATALOG.version,
        "verifiedAt": CATALOG.verified_at,
        "models": len(CATALOG.models()),
        "routes": routes,
        "databaseJournalMode": _store().journal_mode,
        "frameRetention": _retention.preview(),
    }


@router.get("/sessions/{session_id}/export", response_model=None)
def export_session(
    session_id: str, request: Request, include_frames: bool = False
) -> Response | JSONResponse:
    session = _store().get_session(session_id)
    if session is None:
        return error_response(request, 404, "NOT_FOUND", "Session not found")
    payloads = {
        "session.json": _session_payload(session),
        "actions.json": _store().list_actions(session_id),
        "events.json": _store().list_events(session_id),
        "metrics.json": _store().list_metrics(session_id),
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, payload in payloads.items():
            bundle.writestr(name, json.dumps(payload, indent=2))
        if include_frames:
            for frame in _retention.session_files(session_id):
                bundle.write(frame, f"frames/{frame.name}")
    return Response(
        archive.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.zip"'},
    )


@router.get("/retention/preview")
def retention_preview() -> dict[str, int]:
    return _retention.preview()


@router.post("/retention/prune")
def retention_prune() -> dict[str, int]:
    before = _retention.preview()
    removed = _retention.evict()
    return {
        "removedFileCount": len(removed),
        "reclaimedBytes": before["totalBytes"] - _retention.preview()["totalBytes"],
    }


@router.post("/workflows", status_code=201)
def create_workflow(payload: WorkflowInput) -> dict[str, Any]:
    return _workflow_payload(
        _store().create_workflow(
            payload.slug, payload.name, payload.variables_schema, payload.steps
        )
    )


@router.get("/workflows")
def list_workflows() -> dict[str, Any]:
    return {"data": [_workflow_payload(item) for item in _store().list_workflows()]}


@router.post("/workflows/{workflow_id}/versions", status_code=201, response_model=None)
def create_workflow_version(
    workflow_id: str, payload: WorkflowVersionInput, request: Request
) -> dict[str, Any] | JSONResponse:
    try:
        return _workflow_payload(
            _store().create_workflow_version(workflow_id, payload.steps, payload.variables_schema)
        )
    except KeyError:
        return error_response(request, 404, "NOT_FOUND", "Workflow not found")


@router.post("/workflows/{workflow_id}/compile", response_model=None)
def compile_workflow(
    workflow_id: str, payload: WorkflowCompileInput, request: Request
) -> dict[str, Any] | JSONResponse:
    workflow = _store().get_workflow(workflow_id)
    if workflow is None:
        return error_response(request, 404, "NOT_FOUND", "Workflow not found")
    required = workflow.variables_schema.get("required", [])
    missing = [name for name in required if name not in payload.variables]
    if missing:
        return error_response(
            request,
            422,
            "WORKFLOW_VARIABLES_INVALID",
            "Required workflow variables are missing",
            details={"missing": missing},
        )

    def substitute(step: str) -> str:
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda match: str(payload.variables.get(match.group(1), match.group(0))),
            step,
        )

    return {
        "workflowId": workflow.id,
        "version": workflow.version,
        "instructions": [substitute(step) for step in workflow.steps],
    }
