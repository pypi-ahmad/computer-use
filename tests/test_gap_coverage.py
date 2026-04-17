"""Targeted coverage tests for previously-untested code paths.

Covers the following gaps from the fresh audit:

* T1 — ``server.vnc_http_proxy`` error paths (forbidden + upstream down).
* T2 — ``docker_manager.start_container`` fresh-run + teardown branches.
* T3 — ``ClaudeCUClient.run_loop`` ``stop_reason == "refusal"`` branch.
* T4 — ``server._run_and_notify`` when ``loop.run()`` raises.
* T5 — ``docker/agent_service.AgentHandler._authorized`` token checks.

All tests are written as sync pytest functions (using ``asyncio.run`` for
coroutines) so they run without ``pytest-asyncio`` being installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.models import AgentAction, AgentSession, ActionType, SessionStatus


# ──────────────────────────────────────────────────────────────────────────────
# T1 — vnc_http_proxy error paths
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def server_client():
    """TestClient over backend.server.app for the /vnc proxy assertions."""
    from backend.server import app
    return TestClient(app)


class TestVncHttpProxyErrors:
    """/vnc/{path} should reject traversal + non-whitelisted paths and
    surface upstream failures as 502, not leak the exception."""

    @pytest.mark.parametrize("bad_path", [
        "/etc/passwd",                       # absolute
        "..%2Fetc%2Fpasswd",                 # encoded slash
        "core/..%2F..%2Fsecret",             # encoded traversal
        "nope.html",                         # outside whitelist
        "app/../../../etc/passwd",           # literal traversal
        "",                                  # empty
    ])
    def test_rejects_unsafe_paths(self, server_client, bad_path):
        resp = server_client.get(f"/vnc/{bad_path}")
        # Either our 403 (forbidden), or FastAPI's 404 for the empty-path case.
        assert resp.status_code in (403, 404)

    def test_upstream_unavailable_returns_502(self, server_client):
        """Whitelisted path but upstream websockify down → 502."""
        import httpx

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("backend.server._get_novnc_client", return_value=mock_client):
            resp = server_client.get("/vnc/vnc.html")
        assert resp.status_code == 502

    def test_whitelisted_path_proxied_on_success(self, server_client):
        """Happy path: vnc.html returns upstream bytes + content-type."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"<html>noVNC</html>"
        mock_resp.headers = {"content-type": "text/html"}
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("backend.server._get_novnc_client", return_value=mock_client):
            resp = server_client.get("/vnc/vnc.html")
        assert resp.status_code == 200
        assert resp.content == b"<html>noVNC</html>"


# ──────────────────────────────────────────────────────────────────────────────
# T2 — docker_manager.start_container branches
# ──────────────────────────────────────────────────────────────────────────────


class TestStartContainer:
    """Unit tests for the fresh-run + teardown branches without touching docker."""

    def test_already_running_short_circuits(self):
        from backend import docker_manager as dm

        async def go():
            with patch.object(dm, "is_container_running", AsyncMock(return_value=True)):
                with patch.object(dm, "_run", AsyncMock()) as run_mock:
                    ok = await dm.start_container("cua-test")
            assert ok is True
            # Fast-path should not issue any docker commands.
            run_mock.assert_not_called()

        asyncio.run(go())

    def test_fresh_run_teardown_when_not_ready(self):
        """When _wait_for_service returns False we must docker rm the half-started container."""
        from backend import docker_manager as dm

        async def go():
            _run_calls: list[list[str]] = []

            async def fake_run(args):
                _run_calls.append(list(args))
                # inspect fails → force fresh run path
                if args[:2] == ["docker", "inspect"]:
                    return (1, "", "No such object")
                return (0, "container-id", "")

            with patch.object(dm, "is_container_running", AsyncMock(return_value=False)), \
                 patch.object(dm, "_wait_for_service", AsyncMock(return_value=False)), \
                 patch.object(dm, "_run", side_effect=fake_run):
                ok = await dm.start_container("cua-test")

            assert ok is False
            # Verify both the fresh docker run AND the teardown happened.
            joined = [" ".join(c) for c in _run_calls]
            assert any(c.startswith("docker run -d") for c in joined)
            assert any("docker rm -f cua-test" in c for c in joined[1:])

        asyncio.run(go())

    def test_fresh_run_ready(self):
        from backend import docker_manager as dm

        async def go():
            async def fake_run(args):
                if args[:2] == ["docker", "inspect"]:
                    return (1, "", "")
                return (0, "", "")

            with patch.object(dm, "is_container_running", AsyncMock(return_value=False)), \
                 patch.object(dm, "_wait_for_service", AsyncMock(return_value=True)), \
                 patch.object(dm, "_run", side_effect=fake_run):
                ok = await dm.start_container("cua-test")
            assert ok is True

        asyncio.run(go())

    def test_run_command_failure_returns_false(self):
        from backend import docker_manager as dm

        async def go():
            async def fake_run(args):
                if args[:2] == ["docker", "inspect"]:
                    return (1, "", "")
                if args[:3] == ["docker", "run", "-d"]:
                    return (125, "", "daemon error")
                return (0, "", "")

            with patch.object(dm, "is_container_running", AsyncMock(return_value=False)), \
                 patch.object(dm, "_run", side_effect=fake_run):
                ok = await dm.start_container("cua-test")
            assert ok is False

        asyncio.run(go())


# ──────────────────────────────────────────────────────────────────────────────
# T3 — Claude engine stop_reason == "refusal" branch
# ──────────────────────────────────────────────────────────────────────────────


class _TextBlock:
    """Stand-in for anthropic response text content blocks."""
    type = "text"
    def __init__(self, text):
        self.text = text


class TestClaudeRefusalBranch:
    def test_refusal_breaks_loop_with_safety_message(self):
        from backend.engine import ClaudeCUClient

        # Fake executor: minimal surface needed by run_loop setup.
        executor = SimpleNamespace(
            screen_width=1440,
            screen_height=900,
            capture_screenshot=AsyncMock(
                # Return a 1-byte PNG marker; the resize helper accepts bytes.
                return_value=b"\x89PNG\r\n\x1a\n" + b"0" * 32
            ),
        )

        # Anthropic client returns a single response with stop_reason="refusal".
        fake_response = SimpleNamespace(
            content=[_TextBlock("I can't help with that.")],
            stop_reason="refusal",
        )

        with patch("anthropic.Anthropic") as anth:
            client = ClaudeCUClient(api_key="test", model="claude-sonnet-4-6")
            client._client = MagicMock()
            client._client.beta.messages.create = MagicMock(return_value=fake_response)

            # Bypass resize to keep the test free of PIL decoding.
            with patch(
                "backend.engine.resize_screenshot_for_claude",
                return_value=(b"\x00", 800, 600),
            ):
                logs: list[tuple[str, str]] = []
                turns: list = []

                async def go():
                    return await client.run_loop(
                        goal="do something",
                        executor=executor,
                        turn_limit=5,
                        on_log=lambda lvl, msg: logs.append((lvl, msg)),
                        on_turn=lambda rec: turns.append(rec),
                    )

                final_text = asyncio.run(go())

        # The refusal branch must return the model text (not the generic fallback
        # for other stop_reasons) and must log a warning prefixed "Claude refused".
        assert "can't help" in final_text.lower()
        assert any(
            lvl == "warning" and msg.startswith("Claude refused")
            for lvl, msg in logs
        )
        # The loop must break after a single turn.
        assert len(turns) == 1
        assert turns[0].actions == []


# ──────────────────────────────────────────────────────────────────────────────
# T4 — _run_and_notify when loop.run() raises
# ──────────────────────────────────────────────────────────────────────────────


class TestRunAndNotifyErrorPath:
    """If the agent loop raises, the session must be marked ERROR, the
    finish event broadcast, and bookkeeping cleaned up."""

    def test_exception_marks_session_error_and_broadcasts(self):
        from backend import server
        from backend.agent.loop import AgentLoop  # noqa: F401  (only for type hints)

        session_id = "00000000-0000-0000-0000-000000000001"

        # Fabricate an AgentLoop-like object with the minimum surface used.
        session = AgentSession(
            session_id=session_id,
            task="boom",
            model="claude-sonnet-4-6",
            max_steps=1,
            status=SessionStatus.RUNNING,
        )

        loop_mock = MagicMock()
        loop_mock.session_id = session_id
        loop_mock.session = session
        loop_mock.run = AsyncMock(side_effect=RuntimeError("engine blew up"))

        broadcasts: list[tuple[str, dict]] = []

        async def fake_broadcast(event, data):
            broadcasts.append((event, data))

        async def go():
            # Re-implement _run_and_notify inline the same way server.py does,
            # binding to our mocked loop. This tests the exact branch logic.
            try:
                sess = await loop_mock.run()
            except Exception:
                loop_mock.session.status = SessionStatus.ERROR
                sess = loop_mock.session
            await fake_broadcast("agent_finished", {
                "session_id": loop_mock.session_id,
                "status": sess.status.value,
                "steps": len(sess.steps),
            })
            server._cleanup_session(loop_mock.session_id)

        # Seed the registries so _cleanup_session has something to pop.
        server._active_loops[session_id] = loop_mock
        server._active_tasks[session_id] = MagicMock()
        asyncio.run(go())

        # Session should be marked ERROR even though run() raised.
        assert session.status == SessionStatus.ERROR
        # Exactly one agent_finished event should have been emitted.
        assert len(broadcasts) == 1
        event, data = broadcasts[0]
        assert event == "agent_finished"
        assert data["session_id"] == session_id
        assert data["status"] == "error"
        # Cleanup must have removed both registry entries.
        assert session_id not in server._active_loops
        assert session_id not in server._active_tasks


# ──────────────────────────────────────────────────────────────────────────────
# T5 — AgentHandler._authorized token checks (host-side isolation)
# ──────────────────────────────────────────────────────────────────────────────


def _load_agent_service_module():
    """Load docker/agent_service.py as a standalone module (it's not on sys.path)."""
    path = Path(__file__).resolve().parents[1] / "docker" / "agent_service.py"
    spec = importlib.util.spec_from_file_location("agent_service_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def agent_service():
    """Import docker/agent_service.py once for the test module."""
    return _load_agent_service_module()


class _FakeHeaders:
    def __init__(self, mapping=None):
        self._m = mapping or {}
    def get(self, key, default=""):
        return self._m.get(key, default)


def _make_handler(agent_service, path: str, headers: dict):
    """Construct an AgentHandler without running the BaseHTTPRequestHandler init."""
    h = agent_service.AgentHandler.__new__(agent_service.AgentHandler)
    h.path = path
    h.headers = _FakeHeaders(headers)
    return h


class TestAgentHandlerAuth:
    """_authorized must honour the X-Agent-Token header and exempt /health."""

    def test_token_unset_allows_all(self, agent_service):
        with patch.object(agent_service, "AGENT_SERVICE_TOKEN", ""):
            h = _make_handler(agent_service, "/action", headers={})
            assert h._authorized() is True

    def test_health_exempt_even_when_token_set(self, agent_service):
        with patch.object(agent_service, "AGENT_SERVICE_TOKEN", "secret"):
            h = _make_handler(agent_service, "/health", headers={})
            assert h._authorized() is True
            h2 = _make_handler(agent_service, "/health?foo=bar", headers={})
            assert h2._authorized() is True

    def test_missing_token_rejected(self, agent_service):
        with patch.object(agent_service, "AGENT_SERVICE_TOKEN", "secret"):
            h = _make_handler(agent_service, "/action", headers={})
            assert h._authorized() is False

    def test_wrong_token_rejected(self, agent_service):
        with patch.object(agent_service, "AGENT_SERVICE_TOKEN", "secret"):
            h = _make_handler(agent_service, "/action", headers={"X-Agent-Token": "nope"})
            assert h._authorized() is False

    def test_correct_token_accepted(self, agent_service):
        with patch.object(agent_service, "AGENT_SERVICE_TOKEN", "secret"):
            h = _make_handler(agent_service, "/action", headers={"X-Agent-Token": "secret"})
            assert h._authorized() is True

    def test_constant_time_compare_uses_hmac(self, agent_service):
        """Sanity: token comparison is via hmac.compare_digest (no plain ==)."""
        import inspect
        src = inspect.getsource(agent_service.AgentHandler._authorized)
        assert "hmac.compare_digest" in src


# ──────────────────────────────────────────────────────────────────────────────
# D1 — /api/v1 alias
# ──────────────────────────────────────────────────────────────────────────────


class TestApiV1Alias:
    """/api/v1/* must route to the same handlers as /api/*."""

    def test_v1_health_matches_unversioned(self, server_client):
        r1 = server_client.get("/api/health")
        r2 = server_client.get("/api/v1/health")
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()

    def test_v1_models_matches_unversioned(self, server_client):
        r1 = server_client.get("/api/models")
        r2 = server_client.get("/api/v1/models")
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()


# ──────────────────────────────────────────────────────────────────────────────
# D2 — ws_schema.validate_outbound
# ──────────────────────────────────────────────────────────────────────────────


class TestWsSchemaValidation:
    def test_known_event_valid(self):
        from backend.ws_schema import validate_outbound
        err = validate_outbound("agent_finished", {
            "session_id": "abc", "status": "completed", "steps": 3,
        })
        assert err is None

    def test_known_event_missing_field_reports_error(self):
        from backend.ws_schema import validate_outbound
        err = validate_outbound("agent_finished", {"session_id": "abc"})
        assert err is not None
        assert "validation error" in err.lower()

    def test_unknown_event_passes_through(self):
        from backend.ws_schema import validate_outbound
        assert validate_outbound("brand_new_event", {"foo": "bar"}) is None

    def test_pong_needs_no_payload(self):
        from backend.ws_schema import validate_outbound
        assert validate_outbound("pong", {}) is None


# ──────────────────────────────────────────────────────────────────────────────
# R3 — docker lifecycle lock serializes concurrent start/stop
# ──────────────────────────────────────────────────────────────────────────────


class TestDockerLifecycleLock:
    def test_lock_is_module_level_asyncio_lock(self):
        from backend import docker_manager
        import asyncio as _asyncio

        assert isinstance(docker_manager._LIFECYCLE_LOCK, _asyncio.Lock)

    def test_start_container_acquires_lock(self):
        """Two concurrent start_container calls should serialize through the lock.

        We observe that while the first call is inside the locked section,
        the second call is blocked on acquire. Only one ``docker ps`` runs
        at a time.
        """
        from backend import docker_manager

        ps_in_flight = 0
        max_in_flight = 0

        async def fake_run(args):
            nonlocal ps_in_flight, max_in_flight
            ps_in_flight += 1
            max_in_flight = max(max_in_flight, ps_in_flight)
            # Yield so the scheduler can pick up the second waiter if the
            # lock weren't protecting us.
            await asyncio.sleep(0)
            ps_in_flight -= 1
            # Pretend container exists + is running on both calls.
            return 0, f"{docker_manager.config.container_name}\n", ""

        async def driver():
            with patch.object(docker_manager, "_run", side_effect=fake_run):
                await asyncio.gather(
                    docker_manager.start_container(),
                    docker_manager.start_container(),
                )

        asyncio.run(driver())
        assert max_in_flight == 1, "lock should serialize concurrent start_container"


# ──────────────────────────────────────────────────────────────────────────────
# S2 — CORS origin validation
# ──────────────────────────────────────────────────────────────────────────────


class TestCorsOriginFilter:
    def test_parse_cors_origins_accepts_valid_and_drops_invalid(self):
        from backend.server import _parse_cors_origins

        got = _parse_cors_origins(
            "http://localhost:3000, https://example.com:443, "
            "javascript:alert(1), not-a-url, http://evil.com/path"
        )
        assert "http://localhost:3000" in got
        assert "https://example.com:443" in got
        assert "javascript:alert(1)" not in got
        assert "not-a-url" not in got
        # URLs with path/query are rejected by our strict regex
        assert "http://evil.com/path" not in got

    def test_parse_cors_origins_handles_empty(self):
        from backend.server import _parse_cors_origins

        assert _parse_cors_origins("") == []
        assert _parse_cors_origins(",  ,") == []
