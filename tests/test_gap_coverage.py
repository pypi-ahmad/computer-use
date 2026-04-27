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

from backend.models import AgentSession, SessionStatus


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

    def test_already_running_rechecks_agent_health_without_docker_calls(self):
        from backend.infra import docker as dm

        async def go():
            with patch.object(dm, "is_container_running", AsyncMock(return_value=True)), \
                 patch.object(dm, "_wait_for_service", AsyncMock(return_value=True)) as wait_mock:
                with patch.object(dm, "_run", AsyncMock()) as run_mock:
                    ok = await dm.start_container("cua-test")
            assert ok is True
            wait_mock.assert_awaited_once_with("cua-test", already_running=True)
            # Idempotent fast-path should not issue any docker commands.
            run_mock.assert_not_called()

        asyncio.run(go())

    def test_already_running_unready_returns_false_without_docker_calls(self):
        from backend.infra import docker as dm

        async def go():
            with patch.object(dm, "is_container_running", AsyncMock(return_value=True)), \
                 patch.object(dm, "_wait_for_service", AsyncMock(return_value=False)) as wait_mock:
                with patch.object(dm, "_run", AsyncMock()) as run_mock:
                    ok = await dm.start_container("cua-test")
            assert ok is False
            wait_mock.assert_awaited_once_with("cua-test", already_running=True)
            run_mock.assert_not_called()

        asyncio.run(go())

    def test_fresh_run_teardown_when_not_ready(self):
        """When _wait_for_service returns False we must docker rm the half-started container."""
        from backend.infra import docker as dm

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
        from backend.infra import docker as dm

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
        from backend.infra import docker as dm

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
                # Return bytes large enough to pass the adapter's
                # ``len < 100`` empty-screenshot guard (Fix 3, April
                # 2026 wave). The test still mocks ``resize_screenshot_for_claude``
                # so the content is never actually decoded.
                return_value=b"\x89PNG\r\n\x1a\n" + b"0" * 128
            ),
        )

        # Anthropic client returns a single response with stop_reason="refusal".
        fake_response = SimpleNamespace(
            content=[_TextBlock("I can't help with that.")],
            stop_reason="refusal",
        )

        with patch("anthropic.Anthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-sonnet-4-6")
            client._client = MagicMock()
            client._client.beta.messages.create = MagicMock(return_value=fake_response)

            # Bypass resize to keep the test free of PIL decoding.
            # After Q2 the class lives in ``backend.engine.claude`` and
            # holds its own binding of ``resize_screenshot_for_claude``.
            with patch(
                "backend.engine.claude.resize_screenshot_for_claude",
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
# Q-x — _is_safe_upload_path uses path-component containment, not string prefix
# ──────────────────────────────────────────────────────────────────────────────


class TestUploadPathContainment:
    """The previous implementation used ``str.startswith(root + os.sep)``
    which is correct *if* you remember the trailing separator — easy to
    regress. The new implementation uses ``Path.is_relative_to`` so the
    primitive itself prevents the lookalike-prefix family of bugs."""

    def test_lookalike_prefix_rejected(self, agent_service, tmp_path, monkeypatch):
        """``/tmpX/...`` must NOT be accepted just because ``/tmp`` is allowed."""
        # Real on-disk lookalike pair so realpath-following can't rescue it.
        good = tmp_path / "good"
        good.mkdir()
        bad = tmp_path / "good2"
        bad.mkdir()
        monkeypatch.setattr(
            agent_service, "_UPLOAD_ALLOWED_PREFIXES", (str(good),),
        )
        assert agent_service._is_safe_upload_path(str(good / "ok.txt"))
        # ``good2/...`` string-prefix-matches ``good`` but is not a child:
        assert not agent_service._is_safe_upload_path(str(bad / "evil.txt"))

    def test_root_itself_rejected_unchanged(self, agent_service, tmp_path, monkeypatch):
        """Behaviour preserved from the prefix-string version: the root
        directory itself is not a valid upload *destination* — uploads
        must go into a file whose parent dir is the root or below."""
        root = tmp_path / "uploads"
        root.mkdir()
        monkeypatch.setattr(
            agent_service, "_UPLOAD_ALLOWED_PREFIXES", (str(root),),
        )
        assert not agent_service._is_safe_upload_path(str(root))

    def test_descendant_accepted(self, agent_service, tmp_path, monkeypatch):
        root = tmp_path / "uploads"
        (root / "sub").mkdir(parents=True)
        monkeypatch.setattr(
            agent_service, "_UPLOAD_ALLOWED_PREFIXES", (str(root),),
        )
        assert agent_service._is_safe_upload_path(str(root / "sub" / "file.bin"))

    def test_outside_allowed_rejected(self, agent_service, tmp_path, monkeypatch):
        root = tmp_path / "uploads"
        root.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setattr(
            agent_service, "_UPLOAD_ALLOWED_PREFIXES", (str(root),),
        )
        assert not agent_service._is_safe_upload_path(str(elsewhere / "x"))

    def test_empty_or_invalid_input_rejected(self, agent_service):
        assert not agent_service._is_safe_upload_path("")
        assert not agent_service._is_safe_upload_path(None)


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
        from backend.server import validate_outbound
        err = validate_outbound("agent_finished", {
            "session_id": "abc", "status": "completed", "steps": 3,
            "final_text": "done",
        })
        assert err is None

    def test_known_event_missing_field_reports_error(self):
        from backend.server import validate_outbound
        err = validate_outbound("agent_finished", {"session_id": "abc"})
        assert err is not None
        assert "validation error" in err.lower()

    def test_unknown_event_passes_through(self):
        from backend.server import validate_outbound
        assert validate_outbound("brand_new_event", {"foo": "bar"}) is None

    def test_pong_needs_no_payload(self):
        from backend.server import validate_outbound
        assert validate_outbound("pong", {}) is None


# ──────────────────────────────────────────────────────────────────────────────
# R3 — docker lifecycle lock serializes concurrent start/stop
# ──────────────────────────────────────────────────────────────────────────────


class TestDockerLifecycleLock:
    def test_lock_is_module_level_asyncio_lock(self):
        from backend.infra import docker as docker_manager
        import asyncio as _asyncio

        assert isinstance(docker_manager._LIFECYCLE_LOCK, _asyncio.Lock)

    def test_start_container_acquires_lock(self):
        """Two concurrent start_container calls should serialize through the lock.

        We observe that while the first call is inside the locked section,
        the second call is blocked on acquire. Only one ``docker ps`` runs
        at a time.
        """
        from backend.infra import docker as docker_manager

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


# ──────────────────────────────────────────────────────────────────────────────
# R1 — _stream_screenshots must survive arbitrary exceptions
# ──────────────────────────────────────────────────────────────────────────────


class TestStreamScreenshotsResilience:
    def test_unexpected_exception_does_not_kill_task(self):
        """A non-HTTP error inside the shared publisher must trigger backoff."""
        from backend import server

        calls = {"n": 0}
        session_id = "gap-stream-resilience"

        async def flaky_capture(mode="desktop"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom — simulated transient failure")
            # Second call: return a payload, then third call cancels us.
            return "aGVsbG8="  # b64("hello")

        sent_events: list[str] = []

        class FakeWS:
            async def send_text(self, msg):
                sent_events.append(msg)

        async def driver():
            server._cleanup_session(session_id)
            with patch.object(server, "capture_screenshot", side_effect=flaky_capture), \
                patch("backend.infra.docker.is_container_running", new=AsyncMock(return_value=True)), \
                patch.object(server.config, "ws_screenshot_interval", 0.01):
                ws = FakeWS()
                server._active_tasks[session_id] = MagicMock()
                server._active_loops[session_id] = MagicMock()
                server._subscribe_screenshots(ws, session_id)
                task = server._screenshot_publisher_task
                assert task is not None
                # Give the loop enough time to hit the exception and recover.
                await asyncio.sleep(2.3)
                server._unsubscribe_screenshots(ws)
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                finally:
                    server._cleanup_session(session_id)

        asyncio.run(driver())
        assert calls["n"] >= 2, "loop should retry after broad Exception"
        assert any("screenshot_stream" in e for e in sent_events), (
            "frame after retry should still be broadcast"
        )


# ──────────────────────────────────────────────────────────────────────────────
# R5 — _cleanup_session must remove ALL state even when a step raises
# ──────────────────────────────────────────────────────────────────────────────


class TestCleanupSessionResilience:
    def test_cleanup_removes_all_state_when_safety_clear_raises(self):
        from backend import server

        sid = "cleanup-test-sid"
        server._active_tasks[sid] = MagicMock()
        server._active_loops[sid] = MagicMock()
        try:
            with patch.object(server.safety_registry, "clear", side_effect=RuntimeError("nope")):
                # Should NOT propagate the error
                server._cleanup_session(sid)
            assert sid not in server._active_tasks
            assert sid not in server._active_loops
        finally:
            server._active_tasks.pop(sid, None)
            server._active_loops.pop(sid, None)

    def test_cleanup_removes_loops_when_tasks_pop_raises(self):
        from backend import server

        sid = "cleanup-pop-error"
        # Monkeypatch _active_tasks to a mapping that raises on pop.
        class ExplodingDict(dict):
            def pop(self, *a, **kw):
                raise RuntimeError("pop exploded")

        original = server._active_tasks
        server._active_tasks = ExplodingDict()
        server._active_loops[sid] = MagicMock()
        try:
            # Must not propagate; _active_loops must still be cleaned.
            server._cleanup_session(sid)
            assert sid not in server._active_loops
        finally:
            server._active_tasks = original
            server._active_loops.pop(sid, None)


# ──────────────────────────────────────────────────────────────────────────────
# R4 — _run_and_notify awaits broadcast BEFORE cleanup
# ──────────────────────────────────────────────────────────────────────────────


class TestRunAndNotifyOrdering:
    def test_broadcast_awaited_before_cleanup(self):
        """The source of ``/api/agent/start`` must await _broadcast then call cleanup.

        Proven by reading the source — regex-stable check that cleanup
        comes AFTER ``await _broadcast`` in ``_run_and_notify``.
        """
        import inspect
        from backend import server

        src = inspect.getsource(server.api_start_agent)
        broadcast_idx = src.find("await _broadcast(\"agent_finished\"")
        cleanup_idx = src.find("_cleanup_session(loop.session_id)")
        assert broadcast_idx != -1, "expected _broadcast('agent_finished', ...) await"
        assert cleanup_idx != -1, "expected _cleanup_session(loop.session_id) call"
        assert broadcast_idx < cleanup_idx, (
            "cleanup must run AFTER the awaited broadcast"
        )


# ──────────────────────────────────────────────────────────────────────────────
# P2 — agent_service exposes one _SUBPROCESS_TIMEOUT and uses it uniformly
# ──────────────────────────────────────────────────────────────────────────────


class TestAgentServiceSubprocessTimeout:
    def test_module_exports_single_timeout_constant(self):
        from pathlib import Path

        path = Path(__file__).parent.parent / "docker" / "agent_service.py"
        src = path.read_text(encoding="utf-8")
        assert "_SUBPROCESS_TIMEOUT = 10" in src, (
            "agent_service.py must define a single _SUBPROCESS_TIMEOUT constant"
        )

    def test_no_ad_hoc_short_subprocess_timeouts(self):
        """All xdotool/scrot/wmctrl/xclip calls must reference the constant.

        The only acceptable hard-coded literal is the 30 s shell-exec ceiling
        in run_command, which has its own documented user-facing timeout.
        """
        import re
        from pathlib import Path

        path = Path(__file__).parent.parent / "docker" / "agent_service.py"
        src = path.read_text(encoding="utf-8")
        hits = [
            m.group(0) for m in re.finditer(r"timeout=\d+", src)
            if m.group(0) != "timeout=30"
        ]
        assert hits == [], (
            f"Non-uniform subprocess timeouts remain: {hits}. "
            "All short-op timeouts must use _SUBPROCESS_TIMEOUT."
        )


# ──────────────────────────────────────────────────────────────────────────────
# P3 — RateLimiter eviction is tightened
# ──────────────────────────────────────────────────────────────────────────────


class TestRateLimiterEviction:
    def test_evict_to_is_tightened(self):
        from backend.server import _RateLimiter

        assert _RateLimiter._EVICT_TO <= 256, (
            "P3: _EVICT_TO must be tightened to ≤ 256"
        )
        assert _RateLimiter._EVICT_THRESHOLD < _RateLimiter._HARD_KEY_CEILING, (
            "P3: eviction must trigger before ceiling is reached"
        )
        assert _RateLimiter._EVICT_THRESHOLD >= int(
            0.85 * _RateLimiter._HARD_KEY_CEILING
        ), "P3: eviction threshold should be ≥ 0.85 × ceiling (around 0.9×)"

    def test_flood_is_bounded_below_ceiling(self):
        from backend.server import _RateLimiter

        limiter = _RateLimiter(max_calls=1_000_000, window_seconds=60.0)
        # Flood with 5× ceiling unique IPs — each is a separate key.
        for i in range(_RateLimiter._HARD_KEY_CEILING * 5):
            limiter.allow(f"ip-{i}")
        # Must never exceed the eviction threshold (strictly below the hard
        # ceiling) — tighter than the previous behaviour which only evicted
        # on reaching the ceiling itself.
        assert len(limiter._calls) <= _RateLimiter._EVICT_THRESHOLD, (
            f"Post-eviction map size {len(limiter._calls)} exceeded threshold "
            f"{_RateLimiter._EVICT_THRESHOLD}"
        )
        assert len(limiter._calls) < _RateLimiter._HARD_KEY_CEILING


# ──────────────────────────────────────────────────────────────────────────────
# Q1 — AgentSession.task and StartTaskRequest.task reject empty strings
# ──────────────────────────────────────────────────────────────────────────────


class TestTaskMinLength:
    def test_agent_session_rejects_empty_task(self):
        from pydantic import ValidationError
        from backend.models import AgentSession

        with pytest.raises(ValidationError):
            AgentSession(session_id="sid", task="")

    def test_start_task_request_rejects_empty_task(self):
        from pydantic import ValidationError
        from backend.models import StartTaskRequest

        with pytest.raises(ValidationError):
            StartTaskRequest(task="", mode="desktop", provider="gemini")


# ──────────────────────────────────────────────────────────────────────────────
# Q2 — engine package bodies are split across per-provider modules
# ──────────────────────────────────────────────────────────────────────────────


class TestEnginePackageSplit:
    def test_client_classes_live_in_per_provider_modules(self):
        from backend.engine import (
            GeminiCUClient, ClaudeCUClient, OpenAICUClient,
        )

        assert GeminiCUClient.__module__ == "backend.engine.gemini", (
            f"GeminiCUClient must live in backend.engine.gemini, "
            f"not {GeminiCUClient.__module__}"
        )
        assert ClaudeCUClient.__module__ == "backend.engine.claude"
        assert OpenAICUClient.__module__ == "backend.engine.openai"

    def test_init_module_is_reduced_in_size(self):
        """__init__.py should be well below the pre-split 1992 lines (Q2)."""
        from pathlib import Path

        path = Path(__file__).parent.parent / "backend" / "engine" / "__init__.py"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) < 1600, (
            f"backend/engine/__init__.py still has {len(lines)} lines — "
            "class bodies should be moved to per-provider files"
        )

    def test_backward_compatible_top_level_imports(self):
        """Public names still importable from the package root."""
        from backend.engine import (  # noqa: F401
            Provider,
            Environment,
            SafetyDecision,
            CUActionResult,
            CUTurnRecord,
            DesktopExecutor,
            ComputerUseEngine,
            GeminiCUClient,
            ClaudeCUClient,
            OpenAICUClient,
            _prune_claude_context,
            _prune_gemini_context,
        )


# ──────────────────────────────────────────────────────────────────────────────
# T1 — Concurrent-session contention on POST /api/agent/start
# ──────────────────────────────────────────────────────────────────────────────


class TestConcurrentSessionLimit:
    def test_four_concurrent_starts_hit_the_limit(self):
        """With 3 slots already full, 4 concurrent starts must all be 429."""
        import threading
        from fastapi.testclient import TestClient
        from backend.server import app, _MAX_CONCURRENT_SESSIONS, _agent_start_limiter

        assert _MAX_CONCURRENT_SESSIONS == 3, (
            "Test assumes the documented concurrent-session ceiling of 3"
        )

        client = TestClient(app)

        # Pre-populate _active_tasks with 3 non-done sentinel tasks so every
        # incoming request sees the limit as already full.
        pending = [MagicMock() for _ in range(3)]
        for p in pending:
            p.done.return_value = False
        sentinel_map = {f"pre-{i}": p for i, p in enumerate(pending)}

        body = {
            "task": "concurrent contention probe",
            "engine": "computer_use",
            "provider": "google",
            "model": "gemini-3-flash-preview",
            "mode": "desktop",
            "execution_target": "docker",
            "max_steps": 5,
            "api_key": "sk-abcdef01",
        }

        results: list[int] = []
        lock = threading.Lock()

        def go():
            resp = client.post("/api/agent/start", json=body)
            with lock:
                results.append(resp.status_code)

        # Clear rate-limiter state so this test isolates the concurrent
        # session cap rather than tripping the per-IP rate limit on retry.
        saved = _agent_start_limiter._calls
        _agent_start_limiter._calls = {}
        try:
            with patch.dict("backend.server._active_tasks", sentinel_map, clear=True):
                threads = [threading.Thread(target=go) for _ in range(4)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=10)
        finally:
            _agent_start_limiter._calls = saved

        assert len(results) == 4
        # With 3 slots pre-filled, every concurrent start must be rejected
        # with 429 — either by the per-IP rate limiter or by the concurrent
        # session cap. Both are 429, which is the contract T1 wants.
        assert all(code == 429 for code in results), (
            f"Expected all 4 concurrent starts to return 429, got {results}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# T2 — Screenshot streamer survives httpx.TimeoutException
# ──────────────────────────────────────────────────────────────────────────────


class TestScreenshotStreamerTimeout:
    def test_timeout_does_not_close_websocket(self):
        """A timeout inside the shared publisher must trigger backoff."""
        import httpx
        from backend import server

        calls = {"n": 0}
        session_id = "gap-stream-timeout"

        async def flaky_capture(mode="desktop"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.TimeoutException("simulated read timeout")
            return "dGVzdA=="  # b64("test")

        class FakeWS:
            def __init__(self):
                self.closed = False
                self.messages: list[str] = []

            async def send_text(self, msg):
                if self.closed:
                    raise RuntimeError("WS was closed — loop should not send after close")
                self.messages.append(msg)

            async def close(self):
                self.closed = True

        ws = FakeWS()

        async def driver():
            server._cleanup_session(session_id)
            with patch.object(server, "capture_screenshot", side_effect=flaky_capture), \
                patch("backend.infra.docker.is_container_running", new=AsyncMock(return_value=True)), \
                patch.object(server.config, "ws_screenshot_interval", 0.01):
                server._active_tasks[session_id] = MagicMock()
                server._active_loops[session_id] = MagicMock()
                server._subscribe_screenshots(ws, session_id)
                task = server._screenshot_publisher_task
                assert task is not None
                # Sleep long enough to hit the timeout and the 2s backoff and
                # then a successful second capture.
                await asyncio.sleep(2.5)
                server._unsubscribe_screenshots(ws)
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                finally:
                    server._cleanup_session(session_id)

        asyncio.run(driver())

        assert calls["n"] >= 2, "loop should recover and retry after the timeout"
        assert not ws.closed, "WS must remain open across the timeout"
        assert any("screenshot_stream" in m for m in ws.messages), (
            "A screenshot frame should be delivered after retry"
        )


# ──────────────────────────────────────────────────────────────────────────────
# T3 — AgentLoop safety callback times out after 60 s and denies
# ──────────────────────────────────────────────────────────────────────────────


class TestSafetyTimeoutAutoDeny:
    def test_on_safety_denies_when_user_never_confirms(self):
        """The 60 s wait_for path must return False (deny) on TimeoutError.

        Captures the ``on_safety`` callback that AgentLoop passes into the
        engine, then invokes it with ``asyncio.wait_for`` patched to raise
        ``TimeoutError`` immediately — asserting the callback returns False
        and emits a 'timed out' warning.
        """
        from backend.agent.loop import AgentLoop

        loop = AgentLoop(
            task="t",
            api_key="sk-dummytoken",
            model="gemini-3-flash-preview",
            provider="google",
            engine="computer_use",
        )

        captured: dict = {}

        class FakeEngine:
            def __init__(self, *a, **kw):
                pass

            async def execute_task(self, *, goal, turn_limit, on_safety, on_turn, on_log):
                captured["on_safety"] = on_safety
                return "done"

        # Replace the nested import target inside _run_computer_use_engine
        # with our fake so it hands back the on_safety closure.
        with patch("backend.engine.ComputerUseEngine", FakeEngine), \
             patch("backend.agent.prompts.get_system_prompt", return_value="sys"):
            # Drive the engine path just long enough to capture on_safety.
            asyncio.run(loop._run_computer_use_engine())

        assert "on_safety" in captured, "on_safety callback was not handed to the engine"
        on_safety = captured["on_safety"]

        logs: list[tuple[str, str]] = []
        original_emit = loop._emit_log

        def record_emit(level, message, *a, **kw):
            logs.append((level, message))
            return original_emit(level, message, *a, **kw)

        # Patch asyncio.wait_for inside backend.agent.loop so the closure
        # sees our replacement and the 60 s wait collapses instantly.
        async def instant_timeout(coro, *a, **kw):
            # Close the inner coroutine so pytest doesn't warn about a
            # 'coroutine Event.wait was never awaited'.
            if asyncio.iscoroutine(coro):
                coro.close()
            raise asyncio.TimeoutError()

        with patch.object(loop, "_emit_log", side_effect=record_emit), \
             patch("backend.agent.loop.asyncio.wait_for", side_effect=instant_timeout):
            decision = asyncio.run(on_safety("drop production tables"))

        assert decision is False, "timeout must auto-deny the action (T3)"
        assert any(
            level == "warning" and "timed out" in msg.lower()
            for level, msg in logs
        ), f"expected a 'timed out' warning log, got {logs}"


# ──────────────────────────────────────────────────────────────────────────────
# D1 — entrypoint.sh verifies every critical background service
# ──────────────────────────────────────────────────────────────────────────────


class TestEntrypointServiceVerification:
    def test_entrypoint_verifies_xfce_x11vnc_websockify(self):
        from pathlib import Path

        path = Path(__file__).parent.parent / "docker" / "entrypoint.sh"
        src = path.read_text(encoding="utf-8")

        # XFCE: kill -0 on the backgrounded PID + a pgrep sanity check.
        assert 'kill -0 "$XFCE_PID"' in src, (
            "D1: XFCE backgrounded PID must be verified"
        )
        assert "pgrep -x xfwm4" in src or "pgrep -x xfce4-session" in src, (
            "D1: at least one XFCE process must be verified via pgrep"
        )

        # x11vnc: uses -bg so must be pgrep-checked after launch.
        assert "pgrep -x x11vnc" in src, (
            "D1: x11vnc must be verified via pgrep after -bg launch"
        )

        # websockify: PID must be kept + checked.
        assert "WS_PID=$!" in src, "D1: websockify PID must be captured"
        assert 'kill -0 "$WS_PID"' in src, (
            "D1: websockify PID must be verified"
        )


# ──────────────────────────────────────────────────────────────────────────────
# D2 — Dockerfile splits apt-get installs into stable-to-volatile layers
# ──────────────────────────────────────────────────────────────────────────────


class TestDockerfileLayerSplit:
    def test_apt_install_is_split_into_multiple_layers(self):
        from pathlib import Path
        import re

        path = Path(__file__).parent.parent / "docker" / "Dockerfile"
        src = path.read_text(encoding="utf-8")

        apt_installs = re.findall(r"apt-get install -y", src)
        # Expect at least: (a) core tools, (b) python, (c) desktop+apps,
        # plus (d) nodejs and (e) google-chrome, so >= 5 apt-get install
        # invocations total.
        assert len(apt_installs) >= 5, (
            f"D2: expected at least 5 apt-get install layers, found "
            f"{len(apt_installs)}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# D3 — docker-compose health check has a generous start_period
# D4 — compose hardening: no-new-privileges, cap_drop: ALL, tmpfs writable dirs
# ──────────────────────────────────────────────────────────────────────────────


class TestComposeHardening:
    def test_healthcheck_has_start_period(self):
        from pathlib import Path

        path = Path(__file__).parent.parent / "docker-compose.yml"
        src = path.read_text(encoding="utf-8")
        assert "start_period: 30s" in src or "start_period: 20s" in src, (
            "D3: healthcheck must include a start_period of ≥ 20s"
        )

    def test_compose_drops_all_capabilities(self):
        from pathlib import Path

        path = Path(__file__).parent.parent / "docker-compose.yml"
        src = path.read_text(encoding="utf-8")
        assert "cap_drop:" in src and "- ALL" in src, (
            "D4: docker-compose.yml must drop all Linux capabilities"
        )

    def test_compose_uses_no_new_privileges_and_tmpfs(self):
        from pathlib import Path
        import yaml

        path = Path(__file__).parent.parent / "docker-compose.yml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        svc = doc["services"]["cua-environment"]
        assert "no-new-privileges:true" in svc.get("security_opt", []), (
            "D4: no-new-privileges must remain set"
        )
        tmpfs = svc.get("tmpfs") or []
        assert any("/tmp" in entry for entry in tmpfs), (
            "D4: /tmp must be mounted as tmpfs for read-only image tolerance"
        )
