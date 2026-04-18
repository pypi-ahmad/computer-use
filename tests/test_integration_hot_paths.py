"""Focused integration tests for the highest-risk hot paths.

These complement the unit tests by exercising end-to-end glue that
unit tests cannot catch:

  * ``POST /api/agent/start`` → background ``_run_and_notify`` task →
    ``agent_finished`` broadcast → ``_cleanup_session``.
  * ``WebSocket /ws`` accept → ping/pong → ``_broadcast`` fan-out.
  * ``GET /api/screenshot`` round-trip with the agent_service capture
    layer mocked at the boundary.
  * ``OpenAICUClient._execute_openai_action`` dispatch path: a single
    ``computer_call`` payload reaches the executor with the right
    pixel coordinates.

Hard rules: deterministic, no network, no real container, no real LLM
call, no test-harness changes, no new conftest plumbing.
"""

from __future__ import annotations

import base64
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.server import app
    return TestClient(app)


# ── 1. Agent start → background run → agent_finished broadcast ────────────


class TestAgentStartFinishIntegration:
    """``/api/agent/start`` must hand off to a background task that
    actually broadcasts ``agent_finished`` on completion and removes
    the session from the active registries. The validation tests mock
    ``asyncio.create_task`` away, so this lifecycle is otherwise
    untested."""

    def test_start_then_run_completes_and_broadcasts_finished(self):
        from backend import server
        from backend.models import AgentSession, SessionStatus

        # Build a fake session that the loop "completes" with one step.
        finished_session = AgentSession(
            session_id="sess-int-1",
            task="hi",
            model="gpt-5.4",
            status=SessionStatus.COMPLETED,
            max_steps=3,
        )

        fake_loop = SimpleNamespace(
            session_id="sess-int-1",
            session=finished_session,
            run=AsyncMock(return_value=finished_session),
        )

        broadcasts: list[tuple[str, dict]] = []

        async def _capture_broadcast(event, data):
            broadcasts.append((event, data))

        with patch.dict(server._active_tasks, {}, clear=True), \
             patch.dict(server._active_loops, {}, clear=True), \
             patch("backend.server.resolve_api_key",
                   return_value=("sk-test-int", "ui")), \
             patch("backend.server.start_container",
                   new_callable=AsyncMock, return_value=True), \
             patch("backend.server.AgentLoop", return_value=fake_loop), \
             patch("backend.server._broadcast",
                 new=AsyncMock(side_effect=_capture_broadcast)):

            with TestClient(server.app) as c:
                resp = c.post("/api/agent/start", json={
                    "task": "open a page",
                    "engine": "computer_use",
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "mode": "desktop",
                    "execution_target": "docker",
                    "max_steps": 3,
                })

            assert resp.status_code == 200
            assert resp.json()["session_id"] == "sess-int-1"

            # Wait briefly for the background _run_and_notify task to run
            # while the TestClient event loop is still alive.
            for _ in range(50):
                if fake_loop.run.await_count > 0:
                    break
                time.sleep(0.01)

        # Background run completed → loop.run was awaited.
        fake_loop.run.assert_awaited_once()

        # Background run completed → agent_finished broadcast fired.
        finished_events = [b for b in broadcasts if b[0] == "agent_finished"]
        assert finished_events, f"no agent_finished in {broadcasts!r}"
        evt_data = finished_events[-1][1]
        assert evt_data["session_id"] == "sess-int-1"
        assert evt_data["status"] == "completed"

        # Session was cleaned up from the registries.
        assert "sess-int-1" not in server._active_tasks
        assert "sess-int-1" not in server._active_loops


# ── 2. WebSocket end-to-end: connect → ping/pong → broadcast delivery ─────


class TestWebSocketHotPath:
    """Unit tests cover the ``_ws_origin_ok`` gate; this integration
    test covers the rest of the ``/ws`` lifecycle: handshake, ping
    handling, and ``_broadcast`` actually reaching a connected client."""

    def test_ping_pong_and_broadcast_delivery(self, client):
        from backend import server

        # Make sure no stale streaming task interferes with the test.
        with patch("backend.server._stream_screenshots",
                   new=AsyncMock(return_value=None)):
            with client.websocket_connect("/ws") as ws:
                ws.send_text('{"type": "ping"}')
                pong = ws.receive_json()
                assert pong == {"event": "pong"}

                # Trigger a server-side broadcast on the same anyio
                # portal TestClient uses for this websocket session.
                # This avoids cross-loop behavior that can fail on
                # some Python/anyio combinations (notably CI py3.11).
                ws.portal.call(
                    server._broadcast,
                    "custom_test_event",
                    {"value": 42},
                )

                msg = ws.receive_json()
                assert msg["event"] == "custom_test_event"
                assert msg["value"] == 42


# ── 3. /api/screenshot round-trip with capture mocked at the boundary ─────


class TestScreenshotRoundtrip:
    """``GET /api/screenshot`` runs through the origin gate and the
    capture helper. We mock only the leaf (``capture_screenshot``) so
    the routing, gating, and JSON shape are all real."""

    def test_screenshot_endpoint_returns_capture_b64(self, client):
        sample_b64 = base64.b64encode(b"\x89PNG\r\n").decode()
        with patch("backend.server.capture_screenshot",
                   new=AsyncMock(return_value=sample_b64)):
            resp = client.get("/api/screenshot")
        assert resp.status_code == 200
        assert resp.json() == {"screenshot": sample_b64}

    def test_screenshot_endpoint_5xx_on_capture_failure(self, client):
        with patch("backend.server.capture_screenshot",
                   new=AsyncMock(side_effect=RuntimeError("boom"))):
            resp = client.get("/api/screenshot")
        assert resp.status_code == 500


# ── 4. OpenAI engine: one computer_call → executor.execute round-trip ─────


class TestOpenAIActionDispatchIntegration:
    """The OpenAI engine translates raw ``computer_call`` payloads into
    executor calls. A single click action is the simplest non-trivial
    path: it exercises ``_to_plain_dict`` coercion, the ``click`` →
    ``click_at`` mapping, and the ActionExecutor protocol contract."""

    @pytest.mark.asyncio
    async def test_left_click_payload_dispatches_to_click_at(self):
        from backend.engine import CUActionResult
        from backend.engine.openai import OpenAICUClient

        engine = OpenAICUClient.__new__(OpenAICUClient)
        engine._model = "gpt-5.4"

        executor = MagicMock()
        executor.execute = AsyncMock(
            return_value=CUActionResult(name="click_at", success=True),
        )

        click_action = SimpleNamespace(type="click", x=120, y=240, button="left")
        result = await engine._execute_openai_action(click_action, executor)

        executor.execute.assert_awaited_once_with(
            "click_at", {"x": 120, "y": 240},
        )
        assert result.success is True
        assert result.name == "click_at"

    @pytest.mark.asyncio
    async def test_right_click_payload_dispatches_to_right_click(self):
        from backend.engine import CUActionResult
        from backend.engine.openai import OpenAICUClient

        engine = OpenAICUClient.__new__(OpenAICUClient)
        engine._model = "gpt-5.4"

        executor = MagicMock()
        executor.execute = AsyncMock(
            return_value=CUActionResult(name="right_click", success=True),
        )

        right_click = SimpleNamespace(type="click", x=10, y=20, button="right")
        await engine._execute_openai_action(right_click, executor)

        executor.execute.assert_awaited_once_with(
            "right_click", {"x": 10, "y": 20},
        )
