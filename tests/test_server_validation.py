"""Tests for server-side validation: models, providers, rate limiting, safety."""

from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient


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


class TestAgentStartValidation:
    """Test input validation on POST /api/agent/start."""

    def test_invalid_engine_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test", "engine": "invalid", "provider": "google",
            "model": "gemini-3-flash-preview", "mode": "browser",
        })
        assert resp.status_code == 400
        assert "engine" in resp.json().get("error", "").lower()

    def test_invalid_provider_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test", "engine": "computer_use", "provider": "invalid",
            "model": "gemini-3-flash-preview", "mode": "browser",
        })
        assert resp.status_code == 400
        assert "provider" in resp.json().get("error", "").lower()

    def test_invalid_model_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test", "engine": "computer_use", "provider": "google",
            "model": "nonexistent-model", "mode": "browser",
        })
        assert resp.status_code == 400
        assert "not allowed" in resp.json().get("error", "").lower()

    def test_invalid_execution_target_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "test", "engine": "computer_use", "provider": "google",
            "model": "gemini-3-flash-preview", "mode": "browser",
            "execution_target": "local",
        })
        assert resp.status_code == 400
        assert "execution_target" in resp.json().get("error", "").lower()

    def test_empty_task_rejected(self, client):
        resp = client.post("/api/agent/start", json={
            "task": "   ", "engine": "computer_use", "provider": "google",
            "model": "gemini-3-flash-preview", "mode": "browser",
        })
        assert resp.status_code == 400

    def test_missing_api_key_rejected(self, client):
        """Without any API key source, should get a clear error."""
        with patch("backend.server.resolve_api_key", return_value=("", "none")):
            resp = client.post("/api/agent/start", json={
                "task": "test task", "engine": "computer_use", "provider": "google",
                "model": "gemini-3-flash-preview", "mode": "browser",
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
