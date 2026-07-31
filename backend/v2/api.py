"""Typed HTTP contracts for the clean `/api/v2` surface."""
from __future__ import annotations

import asyncio
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
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
    reasoning_effort: str | None = Field(default=None, pattern="^(minimal|low|medium|high|xhigh|none)$")
    retain_audit_frames: bool = True


class SessionPatch(ContractModel):
    status: str = Field(pattern="^STOPPING$")


class ErrorEnvelope(ContractModel):
    code: str
    message: str
    details: Any = None
    is_retryable: bool = False
    request_id: str


def error_response(request: Request, status: int, code: str, message: str, *, details: Any = None, retryable: bool = False) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    error = ErrorEnvelope(code=code, message=message, details=details, is_retryable=retryable, request_id=request_id)
    return JSONResponse(status_code=status, content={"error": error.model_dump(by_alias=True)})


_stores: dict[str, SqliteStore] = {}
_store_lock = threading.Lock()
_retention = frame_retention
_circuit_breaker = CircuitBreaker()


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
    return {"id": session.id, "task": session.task, "model": session.model, "primaryRoute": session.primary_route, "status": session.status, "createdAt": session.created_at}


def _workflow_payload(workflow: WorkflowVersion) -> dict[str, Any]:
    return {"id": workflow.id, "slug": workflow.slug, "name": workflow.name, "version": workflow.version, "variablesSchema": workflow.variables_schema, "steps": workflow.steps, "createdAt": workflow.created_at}


def _route_readiness(provider: str) -> tuple[bool, str]:
    envs: dict[str, tuple[str, ...]] = {
        "OPENAI": ("OPENAI_API_KEY",),
        "AZURE_OPENAI": ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"),
        "ANTHROPIC": ("ANTHROPIC_API_KEY",),
        "GOOGLE": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "AWS_BEDROCK": ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_WEB_IDENTITY_TOKEN_FILE"),
        "VERTEX_GEMINI": ("GOOGLE_CLOUD_PROJECT",),
        "VERTEX_CLAUDE": ("GOOGLE_CLOUD_PROJECT",),
    }
    configured = any(os.getenv(name, "").strip() for name in envs.get(provider, ()))
    auth_mode = {
        "AWS_BEDROCK": "AWS_DEFAULT_CHAIN",
        "VERTEX_GEMINI": "GOOGLE_ADC",
        "VERTEX_CLAUDE": "GOOGLE_ADC",
        "AZURE_OPENAI": "ENTRA_OR_API_KEY",
    }.get(provider, "API_KEY")
    return configured, auth_mode


@router.get("/models")
def list_models() -> dict[str, Any]:
    return {"data": [_model_payload(model) for model in CATALOG.models()], "catalogVersion": CATALOG.version, "verifiedAt": CATALOG.verified_at}


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
            data.append({"id": route.id, "provider": route.provider, "transport": route.transport, "isConfigured": configured, "isExecutable": executable, "authMode": auth_mode, "circuitState": _circuit_breaker.state(f"{model.logical_id}@{route.id}")})
    return {"data": data}


@router.post("/credential-sessions", status_code=201)
def create_credential_session(payload: CredentialSessionInput, request: Request) -> Any:
    try:
        created = credential_vault.create(payload.credentials, ttl_seconds=payload.ttl_seconds)
    except ValueError as exc:
        return error_response(request, 422, "VALIDATION_ERROR", str(exc))
    return {"id": created.id, "providers": created.providers, "expiresAt": created.expires_at}


@router.delete("/credential-sessions/{credential_session_id}", status_code=204)
def delete_credential_session(credential_session_id: str) -> None:
    credential_vault.delete(credential_session_id)


@router.post("/sessions", status_code=201, response_model=None)
async def create_session(payload: SessionInput, request: Request) -> dict[str, Any] | JSONResponse:
    try:
        model = CATALOG.get(payload.model)
    except ValueError as exc:
        return error_response(request, 422, "VALIDATION_ERROR", str(exc), details={"field": "model"})
    primary = payload.primary_route or model.routes[0].id
    primary_route = next((route for route in model.routes if route.id == primary), None)
    if primary_route is None:
        return error_response(request, 422, "VALIDATION_ERROR", "Primary route is not compatible with the selected model", details={"allowedRoutes": [route.id for route in model.routes]})

    selections: list[tuple[str, ComputerUseModel, Any]] = [(f"{model.logical_id}@{primary}", model, primary_route)]
    for fallback in payload.fallback_routes:
        fallback_model_id = payload.model if isinstance(fallback, str) else fallback.model
        fallback_route_id = fallback if isinstance(fallback, str) else fallback.route
        try:
            fallback_model = CATALOG.get(fallback_model_id)
        except ValueError:
            return error_response(request, 422, "VALIDATION_ERROR", f"Unknown fallback model: {fallback_model_id}")
        fallback_route = next((route for route in fallback_model.routes if route.id == fallback_route_id), None)
        if fallback_route is None:
            return error_response(request, 422, "VALIDATION_ERROR", f"Route {fallback_route_id} is not compatible with {fallback_model_id}")
        key = f"{fallback_model.logical_id}@{fallback_route.id}"
        if key not in {item[0] for item in selections}:
            selections.append((key, fallback_model, fallback_route))

    serialized_fallbacks = [key for key, _, _ in selections[1:]]
    stored = _store().create_session(payload.task, payload.model, primary, fallbacks=serialized_fallbacks)
    _store().set_session_status(stored.id, "RUNNING")
    _store().append_event(stored.id, "SESSION_STARTED", {"primaryRoute": selections[0][0]})
    _retention.set_enabled(stored.id, payload.retain_audit_frames)
    selection_by_id = {key: (selected_model, route) for key, selected_model, route in selections}

    async def _invoke(spec: RouteSpec) -> ExecutionOutcome:
        selected_model, route = selection_by_id[spec.id]
        if route.provider not in {"OPENAI", "ANTHROPIC", "GOOGLE"}:
            raise RouteFailure("Route is unavailable: no verified execution bridge", retryable=False)
        secret = credential_vault.resolve(payload.credential_session_id, route.provider) if payload.credential_session_id else None
        if secret is None:
            env_names = {"OPENAI": ("OPENAI_API_KEY",), "ANTHROPIC": ("ANTHROPIC_API_KEY",), "GOOGLE": ("GOOGLE_API_KEY", "GEMINI_API_KEY")}[route.provider]
            raw_key = next((os.getenv(name, "").strip() for name in env_names if os.getenv(name, "").strip()), "")
        else:
            raw_key = secret.get_secret_value()
        if not raw_key:
            raise RouteFailure(f"No credential is available for {route.provider}", retryable=False)
        _store().append_event(stored.id, "ROUTE_ATTEMPTED", {"route": spec.id, "provider": route.provider})
        try:
            return await orchestrator.start(
                ExecutionRequest(
                    payload.task,
                    route.provider.lower(),
                    route.model_id,
                    raw_key,
                    payload.max_steps,
                    payload.reasoning_effort if selected_model.family == "OPENAI" else None,
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
                [RouteSpec(key, route.provider, route.model_id, max_attempts=1) for key, _, route in selections],
                _invoke,
                _circuit_breaker,
            )
            outcome = result.value
            for sequence, action in enumerate(outcome.actions, start=1):
                _store().append_action(stored.id, sequence, str(action.get("action", "UNKNOWN")).upper(), action, confirmed=True)
            _store().append_metric(stored.id, "EXECUTION", outcome.duration_ms)
            _store().append_event(stored.id, "ROUTE_SUCCEEDED", {"route": result.route_id, "attempts": result.attempts, "executionSessionId": outcome.session_id})
            _store().save_checkpoint(stored.id, payload.task, len(outcome.actions), None, {"status": outcome.status})
            _store().set_session_status(stored.id, outcome.status)
        except asyncio.CancelledError:
            _store().append_event(stored.id, "SESSION_STOPPED", {})
            _store().set_session_status(stored.id, "STOPPED")
            raise
        except RouteFailure as exc:
            _store().append_event(stored.id, "SESSION_FAILED", {"message": str(exc)})
            _store().set_session_status(stored.id, "ERROR")
        except Exception as exc:
            _store().append_event(stored.id, "SESSION_REQUIRES_REVIEW", {"message": str(exc)})
            _store().set_session_status(stored.id, "ERROR")

    task = asyncio.create_task(_coordinate())
    orchestrator.track(stored.id, task)
    return {**_session_payload(stored), "status": "RUNNING", "executionSessionId": stored.id, "activeRoute": selections[0][0], "fallbackRoutes": serialized_fallbacks}


@router.get("/sessions")
def list_sessions(cursor: int = 0, limit: int = 50) -> dict[str, Any]:
    items, next_cursor = _store().list_sessions(cursor=max(0, cursor), limit=max(1, min(limit, 100)))
    return {"data": [_session_payload(item) for item in items], "nextCursor": next_cursor}


@router.get("/sessions/{session_id}", response_model=None)
def get_session(session_id: str, request: Request) -> dict[str, Any] | JSONResponse:
    session = _store().get_session(session_id)
    return _session_payload(session) if session is not None else error_response(request, 404, "NOT_FOUND", "Session not found")


@router.patch("/sessions/{session_id}", response_model=None)
def stop_session(session_id: str, payload: SessionPatch, request: Request) -> dict[str, Any] | JSONResponse:
    session = _store().get_session(session_id)
    if session is not None:
        orchestrator.stop(session_id)
        session = _store().set_session_status(session_id, payload.status)
    return _session_payload(session) if session is not None else error_response(request, 404, "NOT_FOUND", "Session not found")


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
def analytics(session_id: str | None = None, model: str | None = None, route: str | None = None) -> dict[str, Any]:
    return _store().analytics(session_id=session_id, model=model, route=route)


@router.post("/workflows", status_code=201)
def create_workflow(payload: WorkflowInput) -> dict[str, Any]:
    return _workflow_payload(_store().create_workflow(payload.slug, payload.name, payload.variables_schema, payload.steps))


@router.get("/workflows")
def list_workflows() -> dict[str, Any]:
    return {"data": [_workflow_payload(item) for item in _store().list_workflows()]}


@router.post("/workflows/{workflow_id}/versions", status_code=201, response_model=None)
def create_workflow_version(workflow_id: str, payload: WorkflowVersionInput, request: Request) -> dict[str, Any] | JSONResponse:
    try:
        return _workflow_payload(_store().create_workflow_version(workflow_id, payload.steps, payload.variables_schema))
    except KeyError:
        return error_response(request, 404, "NOT_FOUND", "Workflow not found")


@router.post("/workflows/{workflow_id}/compile", response_model=None)
def compile_workflow(workflow_id: str, payload: WorkflowCompileInput, request: Request) -> dict[str, Any] | JSONResponse:
    workflow = _store().get_workflow(workflow_id)
    if workflow is None:
        return error_response(request, 404, "NOT_FOUND", "Workflow not found")
    required = workflow.variables_schema.get("required", [])
    missing = [name for name in required if name not in payload.variables]
    if missing:
        return error_response(request, 422, "WORKFLOW_VARIABLES_INVALID", "Required workflow variables are missing", details={"missing": missing})

    def substitute(step: str) -> str:
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda match: str(payload.variables.get(match.group(1), match.group(0))), step)

    return {"workflowId": workflow.id, "version": workflow.version, "instructions": [substitute(step) for step in workflow.steps]}
