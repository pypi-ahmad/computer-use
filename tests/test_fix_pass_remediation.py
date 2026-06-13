from __future__ import annotations
"""Regression tests for the fix-pass remediation (T6 + supporting).

Covers the retry-policy hardening (B7/E8) and the safety-confirm
per-session-nonce authorization (S7).
"""

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.engine as engine
from backend import safety as safety_registry
from backend.server import app


# ── B7/E8: retry policy class resolution ────────────────────────────────────
class TestRetryPolicyClassResolution:
    def test_transient_types_non_empty_and_no_broad_exception(self):
        classes = engine._collect_transient_error_types()
        assert classes, "expected vendor transient classes to resolve"
        assert Exception not in classes, "broad Exception must never be in the retry set"
        assert not engine._RETRY_DISABLED

    def test_includes_expected_sdk_classes(self):
        classes = engine._collect_transient_error_types()
        assert httpx.TimeoutException in classes
        assert httpx.ConnectError in classes

    @pytest.mark.asyncio
    async def test_retries_transient_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(engine.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.TimeoutException("transient")
            return "ok"

        result = await engine._call_with_retry(factory, attempts=3)
        assert result == "ok"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_non_transient_propagates_immediately(self, monkeypatch):
        monkeypatch.setattr(engine.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            raise ValueError("client error, do not retry")

        with pytest.raises(ValueError):
            await engine._call_with_retry(factory, attempts=3)
        assert calls["n"] == 1


# ── S7: safety-confirm per-session nonce authorization ───────────────────────
@pytest.fixture
def client():
    return TestClient(app)


class _FakeLoop:
    pass


class TestSafetyConfirmAuthz:
    def _register(self, monkeypatch, sid):
        import backend.server as server
        monkeypatch.setitem(server._active_loops, sid, _FakeLoop())

    def test_valid_nonce_confirms(self, client, monkeypatch):
        sid = str(uuid.uuid4())
        self._register(monkeypatch, sid)
        nonce, _evt = safety_registry.arm(sid)
        try:
            resp = client.post("/api/agent/safety-confirm",
                               json={"session_id": sid, "confirm": True, "nonce": nonce})
            assert resp.status_code == 200
            assert resp.json()["confirmed"] is True
        finally:
            safety_registry.clear(sid)

    def test_missing_or_wrong_nonce_rejected(self, client, monkeypatch):
        sid = str(uuid.uuid4())
        self._register(monkeypatch, sid)
        safety_registry.arm(sid)
        try:
            assert client.post("/api/agent/safety-confirm",
                               json={"session_id": sid, "confirm": True}).status_code == 403
            assert client.post("/api/agent/safety-confirm",
                               json={"session_id": sid, "confirm": True, "nonce": "wrong"}).status_code == 403
        finally:
            safety_registry.clear(sid)

    def test_foreign_session_nonce_rejected(self, client, monkeypatch):
        sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
        self._register(monkeypatch, sid_a)
        self._register(monkeypatch, sid_b)
        nonce_a, _ = safety_registry.arm(sid_a)
        safety_registry.arm(sid_b)
        try:
            # A's nonce must not resolve B's prompt.
            resp = client.post("/api/agent/safety-confirm",
                               json={"session_id": sid_b, "confirm": True, "nonce": nonce_a})
            assert resp.status_code == 403
        finally:
            safety_registry.clear(sid_a)
            safety_registry.clear(sid_b)
