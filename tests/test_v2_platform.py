from __future__ import annotations

import asyncio
import struct
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.v2.adapters import (
    ProtocolActionError,
    parse_anthropic_action,
    parse_gemini_action,
    parse_openai_action,
)
from backend.v2.credentials import CredentialVault
from backend.v2.frames import (
    CUAF_MAGIC,
    BinaryFrame,
    FrameBroker,
    FrameCodec,
    pack_cuaf_frame,
    unpack_cuaf_frame,
)
from backend.v2.models import ModelCatalog
from backend.v2.orchestrator import ExecutionOutcome
from backend.v2.persistence import SqliteStore
from backend.v2.routing import CircuitBreaker, RouteFailure, RouteSpec, run_with_fallback


def test_model_catalog_is_transport_aware_and_computer_use_only() -> None:
    catalog = ModelCatalog.load()
    models = catalog.models()
    assert models
    assert all(model.supports_computer_use for model in models)
    assert catalog.get("gpt-5.6-luna").routes[0].transport == "OPENAI_RESPONSES"
    assert catalog.get("gpt-5.6-terra").routes[0].id == "openai-direct"
    assert catalog.get("gpt-5.6-terra").routes[0].transport == "OPENAI_RESPONSES"
    assert "computer-use-preview" not in {model.logical_id for model in models}
    assert "gemini-2.5-computer-use-preview" not in {model.logical_id for model in models}
    gemini = {model.logical_id: model for model in models if model.family == "GEMINI"}
    assert set(gemini) == {"gemini-3.7-flash", "gemini-3.5-flash-lite"}
    assert all(model.routes[0].id == "gemini-direct" for model in gemini.values())
    assert all(model.routes[0].transport == "GEMINI_INTERACTIONS" for model in gemini.values())
    assert catalog.get("gemini-3.7-flash").reasoning_efforts == ["low", "medium", "high"]


def test_google_route_is_configured_from_process_google_api_key(monkeypatch) -> None:
    from backend.v2.api import _route_readiness

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-from-user-env")
    configured, auth_mode = _route_readiness("GOOGLE")
    assert configured is True
    assert auth_mode == "API_KEY_OR_OAUTH"


def test_sqlite_store_persists_session_actions_events_metrics_and_workflow_versions() -> None:
    store = SqliteStore(":memory:")
    assert store.journal_mode in {"memory", "wal"}
    workflow = store.create_workflow("checkout", "Checkout", {"type": "object"}, ["Open cart"])
    second = store.create_workflow_version(workflow.id, ["Open cart", "Pay"])
    session = store.create_session("buy coffee", "gpt-5.6-luna", "openai-direct")
    store.append_action(session.id, 1, "CLICK", {"x": 10, "y": 20}, confirmed=True)
    store.append_event(session.id, "ROUTE_SELECTED", {"routeId": "openai-direct"})
    store.append_metric(session.id, "INFERENCE", 125.5, 32, 8)
    checkpoint = store.save_checkpoint(session.id, "buy coffee", 1, "frame-sha", {"approved": True})

    assert second.version == 2
    assert store.list_actions(session.id)[0]["isConfirmed"] is True
    assert store.list_events(session.id)[0]["type"] == "ROUTE_SELECTED"
    assert store.list_metrics(session.id)[0]["durationMs"] == 125.5
    assert checkpoint.last_confirmed_action == 1


def test_route_fallback_skips_open_circuit_and_retries_transient_failures() -> None:
    calls: list[str] = []
    primary = RouteSpec("primary", "OPENAI", "gpt", max_attempts=2)
    fallback = RouteSpec("fallback", "AZURE_OPENAI", "gpt", max_attempts=1)
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60)

    async def invoke(route: RouteSpec) -> str:
        calls.append(route.id)
        if route.id == "primary":
            raise RouteFailure("rate limited", retryable=True, status_code=429)
        return "ok"

    result = asyncio.run(
        run_with_fallback([primary, fallback], invoke, breaker, sleep=lambda _: asyncio.sleep(0))
    )
    assert result.value == "ok"
    assert result.route_id == "fallback"
    assert calls == ["primary", "fallback"]


def test_credential_vault_never_serializes_secrets_and_expires() -> None:
    now = [100.0]
    vault = CredentialVault(max_ttl_seconds=10, clock=lambda: now[0])
    session = vault.create({"OPENAI": "sk-secret"}, ttl_seconds=5)
    assert session.model_dump()["providers"] == ["OPENAI"]
    assert "secret" not in str(session.model_dump()).lower()
    assert vault.resolve(session.id, "OPENAI").get_secret_value() == "sk-secret"
    now[0] = 106.0
    assert vault.resolve(session.id, "OPENAI") is None


def test_credential_vault_accepts_process_local_google_oauth() -> None:
    from google.oauth2.credentials import Credentials

    vault = CredentialVault()
    session = vault.create_empty()
    oauth = Credentials(token="access-token")
    vault.put_google_oauth(session.id, oauth, quota_project_id="quota-project")

    resolved = vault.resolve(session.id, "GOOGLE")
    assert resolved is not None
    assert resolved.method == "oauth"
    assert resolved.oauth_credentials is oauth
    assert resolved.quota_project_id == "quota-project"
    assert "access-token" not in str(vault.status(session.id).model_dump())


def test_cuaf_binary_frame_round_trip() -> None:
    data = b"webp-image"
    packed = pack_cuaf_frame(7, 1440, 900, 123456, FrameCodec.WEBP, data)
    assert packed.startswith(CUAF_MAGIC)
    frame = unpack_cuaf_frame(packed)
    assert (frame.sequence, frame.width, frame.height, frame.payload) == (7, 1440, 900, data)
    assert struct.calcsize(">4sBBQIIQ") < len(packed)


def test_desktop_viewer_url_includes_novnc_and_password(monkeypatch) -> None:
    monkeypatch.setenv("VNC_PASSWORD", "desk-secret")
    from backend.server import app

    with TestClient(app) as client:
        payload = client.get("/api/v2/desktop").json()
    assert payload["viewerUrl"].startswith("/vnc/vnc.html?")
    assert "autoconnect=1" in payload["viewerUrl"]
    assert "path=vnc%2Fwebsockify" in payload["viewerUrl"]
    assert "password=desk-secret" in payload["viewerUrl"]


def test_desktop_stream_survives_capture_failure_without_retaining_frames(monkeypatch) -> None:
    import backend.server as server

    puts: list[tuple[object, ...]] = []
    captures = {"n": 0}

    async def capture() -> BinaryFrame:
        captures["n"] += 1
        if captures["n"] == 1:
            raise RuntimeError("sandbox warming")
        return BinaryFrame(1, 2, 1, 1000, FrameCodec.WEBP, b"frame")

    monkeypatch.setattr(server.config, "ws_screenshot_interval", 0)
    monkeypatch.setattr(server, "_v2_frame_broker", SimpleNamespace(capture=capture))
    monkeypatch.setattr(server, "_v2_latest_canonical_frame", (b"png", 2, 1, 1000))
    monkeypatch.setattr(
        server,
        "_v2_frame_retention",
        SimpleNamespace(put=lambda *args: puts.append(args), is_enabled=lambda *_args: True),
    )

    with TestClient(server.app).websocket_connect("/api/v2/ws/desktop") as websocket:
        assert websocket.receive_json()["event"] == "SESSION_STREAM_READY"
        assert websocket.receive_json()["event"] == "FRAME_CAPTURE_FAILED"
        assert websocket.receive_json()["event"] == "FRAME"
        binary = websocket.receive_bytes()

    assert unpack_cuaf_frame(binary).payload == b"frame"
    assert puts == []


def test_frame_broker_coalesces_concurrent_capture() -> None:
    calls = 0

    async def capture() -> tuple[bytes, int, int, FrameCodec]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return b"png", 1440, 900, FrameCodec.PNG

    async def exercise() -> None:
        broker = FrameBroker(capture)
        first, second = await asyncio.gather(broker.capture(), broker.capture())
        assert first is second
        assert first.payload == b"png"
        assert (first.width, first.height) == (1440, 900)
        assert calls == 1

    asyncio.run(exercise())


def test_v2_api_contract(monkeypatch) -> None:
    monkeypatch.setenv("CUA_V2_DB_PATH", ":memory:")
    from backend.server import app
    from backend.v2.orchestrator import orchestrator

    orchestrator.configure(
        AsyncMock(return_value=ExecutionOutcome("execution-1", "COMPLETED", (), 1.0))
    )

    with TestClient(app) as client:
        models = client.get("/api/v2/models")
        assert models.status_code == 200
        assert models.json()["data"][0]["logicalId"]

        credential = client.post(
            "/api/v2/credential-sessions",
            json={"credentials": {"OPENAI": "sk-secret"}, "ttlSeconds": 60},
        )
        assert credential.status_code == 201
        assert "sk-secret" not in credential.text

        credential_id = credential.json()["id"]
        workflow = client.post(
            "/api/v2/workflows",
            json={
                "slug": "checkout",
                "name": "Checkout",
                "variablesSchema": {"type": "object"},
                "steps": ["Open cart"],
            },
        )
        assert workflow.status_code == 201

        invalid = client.post("/api/v2/sessions", json={"task": "x", "model": "obsolete"})
        assert invalid.status_code == 422
        envelope = invalid.json()["error"]
        assert envelope["code"] == "VALIDATION_ERROR"
        assert envelope["requestId"]

        created = client.post(
            "/api/v2/sessions",
            json={
                "task": "open browser",
                "model": "gpt-5.6-luna",
                "credentialSessionId": credential_id,
            },
        )
        assert created.status_code == 201
        session_id = created.json()["id"]
        assert client.get(f"/api/v2/sessions/{session_id}").status_code == 200
        stopped = client.patch(f"/api/v2/sessions/{session_id}", json={"status": "STOPPING"})
        assert stopped.json()["status"] == "STOPPING"
        assert client.get("/api/v2/analytics").json()["sampleCount"] >= 1

        fallback = client.post(
            "/api/v2/sessions",
            json={
                "task": "fallback",
                "model": "gpt-5.6-luna",
                "primaryRoute": "openai-direct",
                "fallbackRoutes": [{"model": "claude-sonnet-5", "route": "anthropic-direct"}],
                "credentialSessionId": credential_id,
            },
        )
        assert fallback.status_code == 201
        assert fallback.json()["activeRoute"] == "gpt-5.6-luna@openai-direct"
        fallback_id = fallback.json()["id"]
        for _ in range(20):
            events = client.get(f"/api/v2/sessions/{fallback_id}/events").json()["data"]
            if any(event["type"] == "ROUTE_SUCCEEDED" for event in events):
                break
            time.sleep(0.01)
        assert any(
            event["payload"].get("route") == "gpt-5.6-luna@openai-direct"
            for event in events
            if event["type"] == "ROUTE_SUCCEEDED"
        )

        malformed = client.post("/api/v2/sessions", json={})
        assert malformed.status_code == 422
        assert malformed.json()["error"]["code"] == "VALIDATION_ERROR"

        forbidden = client.post(
            "/api/v2/workflows",
            headers={"Origin": "https://attacker.invalid"},
            json={"slug": "blocked", "name": "Blocked", "steps": ["No"]},
        )
        assert forbidden.status_code == 403

        import backend.server as server

        monkeypatch.setattr(
            server,
            "_v2_frame_broker",
            SimpleNamespace(
                capture=AsyncMock(
                    return_value=BinaryFrame(9, 2, 1, 1000, FrameCodec.WEBP, b"frame")
                )
            ),
        )
        monkeypatch.setattr(
            server,
            "_v2_frame_retention",
            SimpleNamespace(put=lambda *_: None, is_enabled=lambda *_: False),
        )
        with client.websocket_connect("/api/v2/ws/execution-1") as websocket:
            assert websocket.receive_json()["event"] == "SESSION_STREAM_READY"
            assert websocket.receive_json()["event"] == "FRAME"
            binary = websocket.receive_bytes()
        assert unpack_cuaf_frame(binary).sequence == 9


def test_vendor_protocol_adapters_validate_and_canonicalize() -> None:
    assert (
        parse_openai_action({"action": {"type": "click", "x": 12, "y": 34}}).type.value == "CLICK"
    )
    assert parse_anthropic_action({"input": {"action": "type", "text": "hello"}}).text == "hello"
    assert (
        parse_gemini_action(
            {"functionCall": {"name": "scroll_at", "args": {"x": 1, "y": 2, "delta_y": 50}}}
        ).delta_y
        == 50
    )
    parsed_click = parse_gemini_action(
        {"functionCall": {"name": "click", "args": {"x": 4, "y": 5, "intent": "open"}}}
    )
    assert parsed_click.type.value == "CLICK"
    assert parsed_click.x == 4
    parsed_drag = parse_gemini_action(
        {
            "functionCall": {
                "name": "drag_and_drop",
                "args": {"start_x": 1, "start_y": 2, "end_x": 8, "end_y": 9},
            }
        }
    )
    assert parsed_drag.type.value == "DRAG"
    assert (parsed_drag.x, parsed_drag.y, parsed_drag.end_x, parsed_drag.end_y) == (1, 2, 8, 9)
    try:
        parse_openai_action({"action": {"type": "click", "x": -1, "y": 0}})
    except ProtocolActionError:
        pass
    else:
        raise AssertionError("negative coordinates must be rejected")
