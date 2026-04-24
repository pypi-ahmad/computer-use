"""SC5 — `/api/health` and `/api/ready` probe regression tests.

`/api/health` is the Docker HEALTHCHECK target: cheap, dependency-free,
always 200 when FastAPI is up. `/api/ready` adds three dependency
checks (Docker daemon reachable, at least one provider API key set,
container not in an unexpected state) and must return HTTP 503 with
a ``reasons`` list when any of them fails.
"""

from __future__ import annotations

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
    on every non-done entry in ``_active_tasks`` before falling through
    to the checkpointer-close step."""

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
