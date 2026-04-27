"""Tests for server-side validation: models, providers, rate limiting, safety."""

from __future__ import annotations

from types import SimpleNamespace
import pytest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from backend.models import AgentAction, AgentSession, ActionType, SessionStatus, StepRecord


@pytest.fixture(scope="module")
def client():
    """Create a FastAPI TestClient for backend.server.app."""
    from backend.server import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_server_rate_limiters():
    """Keep per-process rate limiters from leaking state across tests."""
    from backend.server import _agent_start_limiter, _validate_key_limiter

    _agent_start_limiter._calls.clear()
    _validate_key_limiter._calls.clear()
    yield
    _agent_start_limiter._calls.clear()
    _validate_key_limiter._calls.clear()


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

    def test_search_options_require_toggle(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test",
            "engine": "computer_use",
            "provider": "openai",
            "model": "gpt-5.4",
            "mode": "desktop",
            "search_max_uses": 3,
        })
        assert resp.status_code == 400
        assert "use_builtin_search=true" in resp.json().get("error", "")

    def test_anthropic_both_domain_lists_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test",
            "engine": "computer_use",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "mode": "desktop",
            "use_builtin_search": True,
            "search_allowed_domains": ["docs.python.org"],
            "search_blocked_domains": ["bad.test"],
        })
        assert resp.status_code == 400
        assert "search_allowed_domains or search_blocked_domains" in resp.json().get("error", "")

    def test_anthropic_builtin_search_no_longer_requires_ack_env(self, client):
        from backend.server import _agent_start_limiter

        _agent_start_limiter._calls.clear()
        fake_loop = SimpleNamespace(session_id="session-anthropic-search-1", run=AsyncMock())
        fake_task = Mock()
        fake_task.done.return_value = False

        def fake_create_task(coro):
            coro.close()
            return fake_task

        with patch.dict("backend.server._active_tasks", {}, clear=True), \
             patch.dict("backend.server._active_loops", {}, clear=True), \
             patch("backend.server.resolve_api_key", return_value=("sk-ant-test-12345678", "ui")), \
             patch("backend.server.start_container", new_callable=AsyncMock, return_value=True), \
             patch("backend.server.get_container_state",
                   return_value={"container": "running", "agent": "ready",
                                 "last_health_error": None}), \
             patch("backend.server.AgentLoop", return_value=fake_loop) as mock_agent_loop, \
             patch("backend.server.asyncio.create_task", side_effect=fake_create_task):
            resp = client.post("/api/agent/start", json={
                "task": "test",
                "engine": "computer_use",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "mode": "desktop",
                "use_builtin_search": True,
            })

        assert resp.status_code == 200
        assert mock_agent_loop.call_args.kwargs["use_builtin_search"] is True

    def test_anthropic_allowed_callers_passes_through_to_agent_loop(self, client):
        from backend.server import _agent_start_limiter

        _agent_start_limiter._calls.clear()
        fake_loop = SimpleNamespace(session_id="session-anthropic-search-2", run=AsyncMock())
        fake_task = Mock()
        fake_task.done.return_value = False

        def fake_create_task(coro):
            coro.close()
            return fake_task

        with patch.dict("backend.server._active_tasks", {}, clear=True), \
             patch.dict("backend.server._active_loops", {}, clear=True), \
             patch("backend.server.resolve_api_key", return_value=("sk-ant-test-12345678", "ui")), \
             patch("backend.server.start_container", new_callable=AsyncMock, return_value=True), \
             patch("backend.server.get_container_state",
                   return_value={"container": "running", "agent": "ready",
                                 "last_health_error": None}), \
             patch("backend.server.AgentLoop", return_value=fake_loop) as mock_agent_loop, \
             patch("backend.server.asyncio.create_task", side_effect=fake_create_task):
            resp = client.post("/api/agent/start", json={
                "task": "test",
                "engine": "computer_use",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "mode": "desktop",
                "use_builtin_search": True,
                "allowed_callers": ["direct"],
            })

        assert resp.status_code == 200
        assert mock_agent_loop.call_args.kwargs["allowed_callers"] == ["direct"]

    def test_openai_minimal_reasoning_search_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test",
            "engine": "computer_use",
            "provider": "openai",
            "model": "gpt-5.4",
            "mode": "desktop",
            "use_builtin_search": True,
            "reasoning_effort": "minimal",
        })
        assert resp.status_code == 400
        assert "minimal reasoning" in resp.json().get("error", "")

    def test_gemini_search_options_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test",
            "engine": "computer_use",
            "provider": "google",
            "model": "gemini-3-flash-preview",
            "mode": "desktop",
            "use_builtin_search": True,
            "search_allowed_domains": ["example.com"],
        })
        assert resp.status_code == 400
        assert "does not support search_max_uses or domain filters" in resp.json().get("error", "")

    def test_gemini_reference_files_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test",
            "engine": "computer_use",
            "provider": "google",
            "model": "gemini-3-flash-preview",
            "mode": "desktop",
            "attached_files": ["f_example123"],
        })
        assert resp.status_code == 400
        assert "Gemini File Search cannot be combined with Computer Use" in resp.json().get("error", "")

    def test_gemini_builtin_search_sdk_support_required(self, client):
        with patch("backend.engine._get_gemini_builtin_search_sdk_error", return_value="sdk support missing"):
            resp = client.post("/api/agent/start", json={
                "task": "test",
                "engine": "computer_use",
                "provider": "google",
                "model": "gemini-3-flash-preview",
                "mode": "desktop",
                "use_builtin_search": True,
            })
        assert resp.status_code == 400
        assert resp.json().get("error") == "sdk support missing"

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

    def test_openai_gpt55_defaults_reasoning_effort_to_medium(self, client):
        fake_loop = SimpleNamespace(session_id="session-openai-effort-55", run=AsyncMock())
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
        assert mock_agent_loop.call_args.kwargs["reasoning_effort"] == "medium"

    def test_openai_gpt54_defaults_reasoning_effort_to_none(self, client):
        fake_loop = SimpleNamespace(session_id="session-openai-effort-54", run=AsyncMock())
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
                "model": "gpt-5.4",
                "mode": "desktop",
                "execution_target": "docker",
                "max_steps": 5,
            })

        assert resp.status_code == 200
        assert mock_agent_loop.call_args.kwargs["reasoning_effort"] == "none"

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
