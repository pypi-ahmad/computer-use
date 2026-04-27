# === merged from tests/test_server_validation.py ===
"""Tests for server-side validation: models, providers, rate limiting, safety."""

from __future__ import annotations

from types import SimpleNamespace
import pytest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from backend.models.schemas import AgentAction, AgentSession, ActionType, SessionStatus, StepRecord


@pytest.fixture(scope="module")
def client():
    """Create a FastAPI TestClient for backend.server.app."""
    from backend.server import app
    return TestClient(app)


class TestModelEndpoint:
    """Tests GET /api/models returns a populated list with required fields."""

    def test_models_returns_list(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert len(data["models"]) > 0

    def test_models_have_required_fields(self, client):
        resp = client.get("/api/models")
        for m in resp.json()["models"]:
            assert "provider" in m
            assert "model_id" in m
            assert "supports_computer_use" in m

    def test_models_endpoint_only_returns_cu_capable_models(self, client):
        resp = client.get("/api/models")
        for m in resp.json()["models"]:
            assert m["supports_computer_use"] is True


class TestEnginesEndpoint:
    """Tests GET /api/engines returns at least computer_use."""

    def test_engines_returns_list(self, client):
        resp = client.get("/api/engines")
        assert resp.status_code == 200
        data = resp.json()
        assert "engines" in data
        assert len(data["engines"]) >= 1
        assert data["engines"][0]["value"] == "computer_use"


class TestHealthEndpoint:
    """Tests GET /api/health returns status ok."""

    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestAgentServiceModeEndpoint:
    """Tests POST /api/agent-service/mode stays desktop-only."""

    def test_browser_mode_switch_rejected(self, client):
        resp = client.post("/api/agent-service/mode", json={"mode": "browser"})
        assert resp.status_code == 400
        assert "no longer supported" in resp.json().get("error", "").lower()

    def test_desktop_mode_switch_allowed(self, client):
        mock_response = Mock()
        mock_response.json.return_value = {"mode": "desktop"}
        mock_response.raise_for_status.return_value = None

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_response

        with patch("backend.server.httpx.AsyncClient", return_value=mock_client):
            resp = client.post("/api/agent-service/mode", json={"mode": "desktop"})

        assert resp.status_code == 200
        assert resp.json()["mode"] == "desktop"


class TestAgentStartValidation:
    """Test input validation on POST /api/agent/start."""

    def test_invalid_engine_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test", "engine": "invalid", "provider": "google",
            "model": "gemini-3-flash-preview", "mode": "desktop",
        })
        assert resp.status_code == 400
        assert "engine" in resp.json().get("error", "").lower()

    def test_browser_mode_accepted(self, client):
        """Legacy ``mode="browser"`` is accepted for wire compatibility but
        is now ignored — Desktop and Browser are a single unified Computer
        Use surface; the model decides whether to drive desktop apps or
        Chromium itself."""
        from backend.server import _agent_start_limiter
        _agent_start_limiter._calls.clear()  # avoid collision with prior tests in the rolling window
        fake_loop = SimpleNamespace(session_id="session-browser-1", run=AsyncMock())
        fake_task = Mock()
        fake_task.done.return_value = False

        def fake_create_task(coro):
            coro.close()
            return fake_task

        with patch.dict("backend.server._active_tasks", {}, clear=True), \
             patch.dict("backend.server._active_loops", {}, clear=True), \
             patch("backend.server.resolve_api_key", return_value=("AIza-test", "ui")), \
             patch("backend.server.start_container", new_callable=AsyncMock, return_value=True), \
             patch("backend.server.get_container_state",
                   return_value={"container": "running", "agent": "ready",
                                 "last_health_error": None}), \
             patch("backend.server.AgentLoop", return_value=fake_loop) as mock_agent_loop, \
             patch("backend.server.asyncio.create_task", side_effect=fake_create_task):
            resp = client.post("/api/agent/start", json={
                "task": "test", "engine": "computer_use", "provider": "google",
                "model": "gemini-3-flash-preview", "mode": "browser",
                "execution_target": "docker",
            })
        assert resp.status_code == 200
        # Response no longer surfaces ``mode``; AgentLoop is no longer
        # called with ``mode=…`` because the unified surface dropped it.
        assert "mode" not in resp.json()
        assert "mode" not in mock_agent_loop.call_args.kwargs

    def test_unknown_mode_accepted_and_ignored(self, client):
        """Any value supplied for the legacy ``mode`` field is accepted and
        ignored under the unified surface. The request only fails for
        truly invalid fields (engine/provider/model)."""
        from backend.server import _agent_start_limiter
        _agent_start_limiter._calls.clear()
        fake_loop = SimpleNamespace(session_id="session-mode-ignored", run=AsyncMock())
        fake_task = Mock()
        fake_task.done.return_value = False

        def fake_create_task(coro):
            coro.close()
            return fake_task

        with patch.dict("backend.server._active_tasks", {}, clear=True), \
             patch.dict("backend.server._active_loops", {}, clear=True), \
             patch("backend.server.resolve_api_key", return_value=("AIza-test", "ui")), \
             patch("backend.server.start_container", new_callable=AsyncMock, return_value=True), \
             patch("backend.server.get_container_state",
                   return_value={"container": "running", "agent": "ready",
                                 "last_health_error": None}), \
             patch("backend.server.AgentLoop", return_value=fake_loop), \
             patch("backend.server.asyncio.create_task", side_effect=fake_create_task):
            resp = client.post("/api/agent/start", json={
                "task": "test", "engine": "computer_use", "provider": "google",
                "model": "gemini-3-flash-preview", "mode": "carplay",
                "execution_target": "docker",
            })
        assert resp.status_code == 200

    def test_invalid_provider_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test", "engine": "computer_use", "provider": "invalid",
            "model": "gemini-3-flash-preview", "mode": "desktop",
        })
        assert resp.status_code == 400
        assert "provider" in resp.json().get("error", "").lower()

    def test_invalid_model_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test", "engine": "computer_use", "provider": "google",
            "model": "nonexistent-model", "mode": "desktop",
        })
        assert resp.status_code == 400
        assert "not allowed" in resp.json().get("error", "").lower()

    def test_openai_model_can_be_selected(self, client):
        models = client.get("/api/models").json()["models"]
        openai_models = [m for m in models if m["provider"] == "openai"]
        assert any(m["model_id"] == "gpt-5.5" for m in openai_models)

    def test_openai_happy_path_is_accepted(self, client):
        fake_loop = SimpleNamespace(session_id="session-openai-1", run=AsyncMock())
        fake_task = Mock()
        fake_task.done.return_value = False

        def fake_create_task(coro):
            coro.close()
            return fake_task

        with patch.dict("backend.server._active_tasks", {}, clear=True), \
             patch.dict("backend.server._active_loops", {}, clear=True), \
             patch("backend.server.resolve_api_key", return_value=("sk-test-openai", "ui")), \
             patch("backend.server.start_container", new_callable=AsyncMock, return_value=True), \
             patch("backend.server.get_container_state",
                   return_value={"container": "running", "agent": "ready",
                                 "last_health_error": None}), \
             patch("backend.server.AgentLoop", return_value=fake_loop) as mock_agent_loop, \
             patch("backend.server.asyncio.create_task", side_effect=fake_create_task):
            resp = client.post("/api/agent/start", json={
                "task": "open a page",
                "engine": "computer_use",
                "provider": "openai",
                "model": "gpt-5.5",
                "mode": "desktop",
                "execution_target": "docker",
                "max_steps": 5,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "session-openai-1"
        assert data["status"] == "running"
        assert data["engine"] == "computer_use"
        assert "mode" not in data
        mock_agent_loop.assert_called_once()
        assert mock_agent_loop.call_args.kwargs["provider"] == "openai"
        assert mock_agent_loop.call_args.kwargs["model"] == "gpt-5.5"
        assert mock_agent_loop.call_args.kwargs["api_key"] == "sk-test-openai"

    def test_openai_model_list_and_start_path_work_together(self, client):
        openai_model = next(
            model["model_id"]
            for model in client.get("/api/models").json()["models"]
            if model["provider"] == "openai"
        )
        fake_loop = SimpleNamespace(session_id="session-openai-2", run=AsyncMock())
        fake_task = Mock()
        fake_task.done.return_value = False

        def fake_create_task(coro):
            coro.close()
            return fake_task

        with patch.dict("backend.server._active_tasks", {}, clear=True), \
             patch.dict("backend.server._active_loops", {}, clear=True), \
             patch("backend.server.resolve_api_key", return_value=("sk-test-openai", "ui")), \
             patch("backend.server.start_container", new_callable=AsyncMock, return_value=True), \
             patch("backend.server.get_container_state",
                   return_value={"container": "running", "agent": "ready",
                                 "last_health_error": None}), \
             patch("backend.server.AgentLoop", return_value=fake_loop), \
             patch("backend.server.asyncio.create_task", side_effect=fake_create_task):
            resp = client.post("/api/agent/start", json={
                "task": "open a page",
                "engine": "computer_use",
                "provider": "openai",
                "model": openai_model,
                "mode": "desktop",
                "execution_target": "docker",
                "max_steps": 5,
            })

        assert resp.status_code == 200
        assert resp.json()["session_id"] == "session-openai-2"

    def test_invalid_execution_target_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test", "engine": "computer_use", "provider": "google",
            "model": "gemini-3-flash-preview", "mode": "desktop",
            "execution_target": "local",
        })
        assert resp.status_code == 400
        assert "execution_target" in resp.json().get("error", "").lower()

    def test_empty_task_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "   ", "engine": "computer_use", "provider": "google",
            "model": "gemini-3-flash-preview", "mode": "desktop",
        })
        assert resp.status_code == 400

    def test_missing_api_key_rejected(self, client):
        """Without any API key source, should get a clear error."""
        with patch("backend.server.resolve_api_key", return_value=("", "none")):
            resp = client.post("/api/agent/start", json={
                "task": "test task", "engine": "computer_use", "provider": "google",
                "model": "gemini-3-flash-preview", "mode": "desktop",
            })
        # Should be 400 (no API key)
        assert resp.status_code == 400
        assert "api key" in resp.json().get("error", "").lower()


class TestSafetyConfirmEndpoint:
    """Tests POST /api/agent/safety-confirm rejects invalid/missing session IDs."""

    def test_invalid_session_rejected(self, client):
        resp = client.post("/api/agent/safety-confirm", json={
            "session_id": "not-a-uuid", "confirm": True,
        })
        data = resp.json()
        assert "error" in data

    def test_nonexistent_session(self, client):
        resp = client.post("/api/agent/safety-confirm", json={
            "session_id": "00000000-0000-0000-0000-000000000000",
            "confirm": True,
        })
        data = resp.json()
        assert "error" in data


class TestContainerEndpoints:
    """Tests GET /api/container/status with a mocked Docker backend."""

    def test_container_status(self, client):
        with patch("backend.server.get_container_status", new_callable=AsyncMock,
                    return_value={"name": "cua-environment", "running": False,
                                  "image": "cua-ubuntu:latest", "agent_service": False}):
            resp = client.get("/api/container/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data

# === merged from tests/test_health_and_ready.py ===
"""SC5 — `/api/health` and `/api/ready` probe regression tests.

`/api/health` is the Docker HEALTHCHECK target: cheap, dependency-free,
always 200 when FastAPI is up. `/api/ready` adds three dependency
checks (Docker daemon reachable, at least one provider API key set,
container not in an unexpected state) and must return HTTP 503 with
a ``reasons`` list when any of them fails.
"""


from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from backend.server import app
    return TestClient(app)


class TestHealthLiveness:
    """`/api/health` must stay cheap and dependency-free."""

    def test_health_always_200(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_ignores_container_state(self, client: TestClient) -> None:
        """Even when the container is stopped / unknown, liveness stays
        up. This is the invariant that lets the Docker HEALTHCHECK
        target `/health` without flapping on upstream hiccups."""
        stopped = {"container": "stopped", "agent": "unknown", "last_health_error": None}
        with patch("backend.server.get_container_state", return_value=stopped):
            resp = client.get("/api/health")
        assert resp.status_code == 200


class TestReadiness:
    """`/api/ready` returns 200 only when the backend can start a session."""

    def test_ready_happy_path(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        # Docker reachable + a provider key set + sensible container state.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-fake-key-for-readiness-probe")
        ok_state = {"container": "running", "agent": "ready", "last_health_error": None}
        with patch(
            "backend.server._dm_run",
            new=AsyncMock(return_value=(0, "25.0.3\n", "")),
        ), patch("backend.server.get_container_state", return_value=ok_state):
            resp = client.get("/api/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ready"] is True
        assert body["container"] == "running"

    def test_ready_503_when_docker_unreachable(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        with patch(
            "backend.server._dm_run",
            new=AsyncMock(return_value=(1, "", "Cannot connect to the Docker daemon")),
        ):
            resp = client.get("/api/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["ready"] is False
        assert any("docker daemon" in r.lower() for r in body["reasons"])

    def test_ready_503_when_no_provider_key(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Explicitly unset all three provider keys so the env scan fails.
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        with patch(
            "backend.server._dm_run",
            new=AsyncMock(return_value=(0, "25.0.3\n", "")),
        ):
            resp = client.get("/api/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["ready"] is False
        assert any("provider API key" in r for r in body["reasons"])

    def test_ready_aggregates_multiple_failures(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both the docker probe AND the key check fail → both reasons
        surface. Operators see every missing thing, not just the first."""
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        with patch(
            "backend.server._dm_run",
            new=AsyncMock(return_value=(1, "", "no daemon")),
        ):
            resp = client.get("/api/ready")
        assert resp.status_code == 503
        reasons = resp.json()["reasons"]
        assert len(reasons) >= 2


class TestGracefulShutdownInvariant:
    """SC5 constraint: the lifespan shutdown cancels in-flight session
    tasks and broadcasts with a bounded wait. This test does not
    exercise SIGTERM (that's an integration-harness concern); it
    verifies the source invariant: the lifespan calls ``task.cancel()``
    on every non-done entry in ``_active_tasks`` before shared clients
    are closed."""

    def test_lifespan_cancels_active_tasks_on_shutdown(self) -> None:
        import inspect
        from backend import server

        src = inspect.getsource(server._lifespan)
        assert "task.cancel()" in src or ".cancel()" in src, (
            "Lifespan shutdown must cancel in-flight tasks; otherwise a"
            " SIGTERM strands active agent runs."
        )
        assert "asyncio.gather" in src, (
            "Lifespan shutdown must await cancelled tasks with"
            " asyncio.gather so finalize-node persistence completes."
        )
        assert "_broadcast_tasks" in src, (
            "Lifespan shutdown must drain pending WS broadcast tasks"
            " so agent_finished events reach subscribers."
        )

# === merged from tests/test_screenshot_publisher.py ===
"""P-PUB — regression tests for the shared screenshot publisher.

Before this refactor every ``/ws`` client spawned its own
``_stream_screenshots`` task; N viewers produced N independent
``capture_screenshot`` calls contending with keyboard/mouse actions
behind the in-container ``_ACTION_LOCK``. These tests lock in the new
contract:

  1. Two subscribers on the same session => one publisher task
     (refcounted fan-out).
  2. Zero subscribers (every viewer on noVNC) => zero
     ``capture_screenshot`` calls in steady state.
  3. Session cleanup leaves no leaked publisher tasks.
  4. Single-subscriber cadence is preserved — at least one capture
     happens within the expected window.

Tests drive the publisher via public helpers (``_subscribe_screenshots``,
``_unsubscribe_screenshots``) and the real ``_cleanup_session`` path
rather than a real websocket, which keeps them fast and deterministic.
"""


import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# Short cadence so waiting for "at least one tick" is cheap.
_FAST_INTERVAL = 0.03


def _make_ws() -> MagicMock:
    """Stand-in ws with the only methods the publisher uses."""
    ws = MagicMock()
    ws.send_text = AsyncMock()
    return ws


def _seed_session(server, session_id: str) -> None:
    """Register a minimal active session so cleanup exercises the real path."""
    server._active_tasks[session_id] = MagicMock()
    server._active_loops[session_id] = MagicMock()


async def _drain_task(task, *, max_wait: float = 2.0) -> None:
    """Await a (likely-cancelled) task until it transitions to done.

    Tests hold their own task references because
    ``_unsubscribe_screenshots`` clears ``server._screenshot_publisher_task``
    before we get a chance to inspect it. Catch CancelledError +
    TimeoutError so the drain never raises.
    """
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=max_wait)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


@pytest.fixture
def server_mod(monkeypatch):
    """Import backend.server with the publisher's IO stubbed out.

    We patch:
      * ``backend.agent.loop.capture_screenshot`` → returns a
        tiny fake PNG-base64 string. We also clear the server's bound
        import so the freshly-patched callable is picked up.
      * ``backend.infra.docker.is_container_running`` → True, so the
        publisher doesn't short-circuit on the container-not-running
        branch.
      * ``config.ws_screenshot_interval`` → _FAST_INTERVAL for speed.

    Also resets publisher state between tests so they don't bleed
    into one another.
    """
    from backend import server

    # Reset publisher state.
    server._active_tasks.clear()
    server._active_loops.clear()
    server._screenshot_subscribers.clear()
    server._screenshot_subscribers_by_session.clear()
    server._ws_screenshot_sessions.clear()
    server._ws_clients.clear()
    server._last_screenshot_frame = None
    server._screenshot_capture_count = 0
    if server._screenshot_publisher_task is not None:
        server._screenshot_publisher_task.cancel()
        server._screenshot_publisher_task = None

    monkeypatch.setattr(server.config, "ws_screenshot_interval", _FAST_INTERVAL)
    monkeypatch.setattr(server.config, "ws_screenshot_suspend_when_idle", True)

    # capture_screenshot is imported by name into server; replace
    # that binding directly so the publisher uses our fake.
    fake_capture = AsyncMock(return_value="QUJD")  # base64("ABC")
    monkeypatch.setattr(server, "capture_screenshot", fake_capture)

    # Patch is_container_running where the publisher imports it.
    import backend.infra.docker as dm
    monkeypatch.setattr(dm, "is_container_running", AsyncMock(return_value=True))

    yield server, fake_capture

    # Teardown: cancel any publisher the test may have left running.
    if server._screenshot_publisher_task is not None:
        server._screenshot_publisher_task.cancel()


class TestPublisherRefcount:
    """Success criterion (1) and (3)."""

    def test_two_subscribers_share_one_publisher(self, server_mod):
        """Two ws clients subscribed on the same session must produce
        exactly ONE publisher task, not two. This is the whole point
        of the refactor — N clients, one capture loop."""
        server, _ = server_mod

        async def go():
            session_id = "sess-shared"
            _seed_session(server, session_id)
            ws1, ws2 = _make_ws(), _make_ws()
            server._subscribe_screenshots(ws1, session_id)
            first_task = server._screenshot_publisher_task
            server._subscribe_screenshots(ws2, session_id)
            second_task = server._screenshot_publisher_task

            # Let the loop tick a few times.
            await asyncio.sleep(_FAST_INTERVAL * 3)

            assert first_task is not None and not first_task.done()
            assert second_task is first_task, (
                "Second subscriber started a NEW publisher task — "
                "refcounting is broken."
            )
            assert len(server._screenshot_subscribers) == 2
            assert len(server._screenshot_subscribers_by_session[session_id]) == 2

            # Cleanup: last unsubscribe must cancel the task.
            server._unsubscribe_screenshots(ws1)
            assert server._screenshot_publisher_task is first_task, (
                "Publisher was cancelled while a subscriber still remained."
            )
            server._unsubscribe_screenshots(ws2)
            assert server._screenshot_publisher_task is None
            await _drain_task(first_task)
            assert first_task.done()

        asyncio.run(go())

    def test_session_cleanup_cycles_leak_no_tasks(self, server_mod):
        """Success criterion (3): opening and closing N sessions via
        ``_cleanup_session`` must leave exactly zero publisher tasks alive."""
        server, _ = server_mod

        async def go():
            all_spawned = []
            for idx in range(5):
                session_id = f"sess-cycle-{idx}"
                _seed_session(server, session_id)
                ws = _make_ws()
                server._subscribe_screenshots(ws, session_id)
                task = server._screenshot_publisher_task
                all_spawned.append(task)
                await asyncio.sleep(_FAST_INTERVAL)
                server._cleanup_session(session_id)
                await _drain_task(task)

            # Every spawned task must be finished; the current
            # task-slot must be None.
            for t in all_spawned:
                assert t is not None and t.done()
            assert server._screenshot_publisher_task is None
            assert len(server._screenshot_subscribers) == 0
            assert len(server._screenshot_subscribers_by_session) == 0
            assert len(server._ws_screenshot_sessions) == 0

        asyncio.run(go())


class TestNoVncZeroCaptures:
    """Success criterion (2)."""

    def test_zero_captures_when_no_subscribers(self, server_mod):
        """With a client connected but opted OUT of the screenshot
        stream (``screenshot_mode: off`` / noVNC mode), steady-state
        capture count must stay flat. Previously the per-client task
        fired anyway."""
        server, fake_capture = server_mod

        async def go():
            # Simulate a client that connected and immediately opted
            # out: subscribe, then unsubscribe. With
            # suspend_when_idle=True the publisher task is cancelled.
            _seed_session(server, "sess-novnc")
            ws = _make_ws()
            server._subscribe_screenshots(ws, "sess-novnc")
            task = server._screenshot_publisher_task
            server._unsubscribe_screenshots(ws)
            await _drain_task(task)

            # Drop any captures that might have slipped through on
            # the very first tick (race-free because the loop's
            # first action is ``await asyncio.sleep``).
            baseline = server._screenshot_capture_count

            # Wait several cadence periods — with zero subscribers
            # and suspend_when_idle, count MUST NOT increase.
            await asyncio.sleep(_FAST_INTERVAL * 10)
            assert server._screenshot_capture_count == baseline, (
                f"capture_screenshot was called {server._screenshot_capture_count - baseline} "
                f"times during a no-subscriber window; publisher did not suspend."
            )
            # And the real proof — the underlying stub agrees.
            assert fake_capture.await_count == baseline

        asyncio.run(go())


class TestSingleSubscriberCadence:
    """Success criterion (4): single-client regression guard."""

    def test_single_subscriber_still_receives_frames(self, server_mod):
        """A lone subscriber must still get at least one
        ``screenshot_stream`` message within a few cadence intervals.
        Previous behaviour was "one task per client"; the new loop
        must serve the same N=1 case just as well."""
        server, fake_capture = server_mod

        async def go():
            _seed_session(server, "sess-single")
            ws = _make_ws()
            server._subscribe_screenshots(ws, "sess-single")
            # Wait up to ~6 cadence intervals — generous enough to
            # absorb scheduling jitter on CI.
            await asyncio.sleep(_FAST_INTERVAL * 6)

            assert fake_capture.await_count >= 1, (
                "Publisher did not capture any frame for a subscribed client."
            )
            # ws.send_text should have been called with an event
            # payload carrying the fake base64.
            assert ws.send_text.await_count >= 1
            payload = ws.send_text.await_args_list[0].args[0]
            assert '"event": "screenshot_stream"' in payload
            assert '"screenshot": "QUJD"' in payload

            task = server._screenshot_publisher_task
            server._unsubscribe_screenshots(ws)
            await _drain_task(task)

        asyncio.run(go())

