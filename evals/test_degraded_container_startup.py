"""Eval: starting a session against a degraded sandbox must 409.

Scenario:

* The docker container is "running" but the in-container agent
  service is still ``unready`` (PR 03 readiness model).
* :func:`backend.server.api_start_agent` must return **HTTP 409**
  and must NOT register a session in ``_active_loops`` /
  ``_active_tasks``.

Closes the loop on the readiness gate that only exists at the HTTP
layer — a pure-graph test can't catch a regression in
``api_start_agent``'s ordering between ``start_container`` and
``get_container_state``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.server import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_active_registries():
    """Reset the active-session registries before each eval run."""
    from backend import server
    server._active_loops.clear()
    server._active_tasks.clear()
    yield
    server._active_loops.clear()
    server._active_tasks.clear()


def _start_payload() -> dict:
    return {
        "task": "open a tab and type hello",
        "engine": "computer_use",
        "provider": "openai",
        "model": "gpt-5.4",
        "mode": "desktop",
        "execution_target": "docker",
        "max_steps": 3,
    }


class TestDegradedContainerStartup:
    """``POST /api/agent/start`` must refuse when the sandbox is unready."""

    def test_unready_agent_returns_409_and_no_session_row(self, client):
        from backend import server

        unready_state = {
            "container": "running",
            "agent": "unready",
            "last_health_error": "agent_service /health timeout after 30s",
        }
        with patch("backend.server.resolve_api_key",
                   return_value=("sk-eval-key-0123456789", "ui")), \
             patch("backend.server.start_container",
                   new=AsyncMock(return_value=True)), \
             patch("backend.server.get_container_state",
                   return_value=unready_state):
            resp = client.post("/api/agent/start", json=_start_payload())

        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert "not ready" in str(body).lower()

        # No session was registered.
        assert server._active_loops == {}
        assert server._active_tasks == {}

    def test_ready_agent_does_not_409(self, client):
        """Sanity check: same payload, ``agent=ready`` → not a 409."""
        from types import SimpleNamespace
        from backend.models import AgentSession, SessionStatus

        ready_state = {
            "container": "running",
            "agent": "ready",
            "last_health_error": None,
        }
        finished = AgentSession(
            session_id="eval-ready-sid",
            task="t",
            model="gpt-5.4",
            status=SessionStatus.COMPLETED,
            max_steps=3,
        )
        fake_loop = SimpleNamespace(
            session_id="eval-ready-sid",
            session=finished,
            run=AsyncMock(return_value=finished),
        )
        with patch("backend.server.resolve_api_key",
                   return_value=("sk-eval-key-0123456789", "ui")), \
             patch("backend.server.start_container",
                   new=AsyncMock(return_value=True)), \
             patch("backend.server.get_container_state",
                   return_value=ready_state), \
             patch("backend.server.AgentLoop", return_value=fake_loop), \
             patch("backend.server._broadcast", new=AsyncMock()):
            resp = client.post("/api/agent/start", json=_start_payload())

        assert resp.status_code != 409, resp.text
        assert resp.status_code == 200
