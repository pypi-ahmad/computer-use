from __future__ import annotations
"""Regression tests for the second-wave audit fixes.

Each test pairs with a finding ID from the audit report so a future
refactor that reintroduces a fixed bug gets caught by name.
"""


import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.agent.prompts import get_system_prompt
from backend.infra.config import Config


# ── C1 / AI1 — prompt placeholder substitution ─────────────────────────────


class TestPromptViewportSubstitution:
    """C1: {viewport_width}/{viewport_height} must be replaced with the
    configured screen dimensions for every provider."""

    @pytest.mark.parametrize("provider", ["google", "anthropic", "openai"])
    def test_placeholders_expanded(self, provider, monkeypatch):
        # Force a non-default resolution. ``get_system_prompt`` does a
        # lazy import of ``backend.infra.config.config`` inside the function
        # body, so we patch the attribute on the ``backend.infra.config``
        # module to make the substitution pick up the new dimensions.
        import backend.infra.config as cfg_mod
        from backend.infra.config import Config as _Config

        cfg = _Config(screen_width=1920, screen_height=1080)
        monkeypatch.setattr(cfg_mod, "config", cfg, raising=False)

        prompt = get_system_prompt("computer_use", "desktop", provider=provider)

        assert "{viewport_width}" not in prompt
        assert "{viewport_height}" not in prompt
        assert "1920" in prompt
        assert "1080" in prompt

    def test_no_literal_1440x900_at_custom_resolution(self, monkeypatch):
        """If the user set SCREEN_WIDTH=1920, the prompt must not still
        claim the screen is 1440x900."""
        import backend.infra.config as cfg_mod
        from backend.infra.config import Config as _Config

        cfg = _Config(screen_width=1920, screen_height=1080)
        monkeypatch.setattr(cfg_mod, "config", cfg, raising=False)
        prompt = get_system_prompt("computer_use", "desktop", provider="google")
        assert "1440x900" not in prompt
        assert "1440×900" not in prompt


# ── C4 — path prefix check ─────────────────────────────────────────────────


class TestSessionsDbPathResolution:
    """C4: ``/home/alice`` allowlist must not accept ``/home/alice2/...``."""

    def test_lookalike_prefix_rejected(self, monkeypatch):
        from backend import server
        import shutil
        import uuid

        # Use a workspace-local base (not /tmp) so Linux's built-in
        # /tmp allowlist does not mask the lookalike-prefix check.
        base = Path.cwd() / f".tmp-c4-{uuid.uuid4().hex}"
        fake_home = base / "alice"
        neighbor = base / "alice2"
        fake_home.mkdir(parents=True)
        neighbor.mkdir(parents=True)
        bad_path = neighbor / "sessions.sqlite"

        try:
            monkeypatch.setattr(Path, "home", lambda: fake_home)
            monkeypatch.setenv("CUA_SESSIONS_DB", str(bad_path))
            monkeypatch.delenv("CUA_SESSIONS_DB_ALLOW_DIR", raising=False)

            resolved = server._resolve_sessions_db_path()
            # Must fall back to the default, NOT the neighbor dir.
            assert str(neighbor) not in resolved
            assert str(fake_home.resolve()) in resolved
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_non_sqlite_suffix_rejected(self, tmp_path, monkeypatch):
        from backend import server

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setenv("CUA_SESSIONS_DB", str(fake_home / "evil.db"))
        resolved = server._resolve_sessions_db_path()
        assert resolved.endswith(".sqlite")


# ── C5 — OpenAI scroll magnitude ───────────────────────────────────────────


class TestOpenAIScrollClamp:
    """C5: small scrolls must not be forced up to 200px."""

    @pytest.mark.asyncio
    async def test_small_scroll_preserved(self):
        from backend.engine.openai import OpenAICUClient

        captured = {}

        class _Exec:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                captured["args"] = args

                class _Result:
                    name = "scroll_at"
                    success = True
                    error = None
                    extra: dict = {}

                return _Result()

            async def capture_screenshot(self):
                return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

            def get_current_url(self):
                return ""

        with patch("backend.engine.openai.OpenAICUClient.__init__", return_value=None):
            client = OpenAICUClient.__new__(OpenAICUClient)
            client._model = "gpt-5.4"
            client._reasoning_effort = "low"
            await client._execute_openai_scroll(
                {"x": 500, "y": 500, "delta_y": 20}, _Exec()
            )
        assert captured["args"]["magnitude"] == 20

    @pytest.mark.asyncio
    async def test_large_scroll_capped(self):
        from backend.engine.openai import OpenAICUClient

        captured = {}

        class _Exec:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                captured["args"] = args

                class _R:
                    name = "scroll_at"
                    success = True
                    error = None
                    extra: dict = {}

                return _R()

            async def capture_screenshot(self):
                return b""

            def get_current_url(self):
                return ""

        client = OpenAICUClient.__new__(OpenAICUClient)
        client._model = "gpt-5.4"
        client._reasoning_effort = "low"
        await client._execute_openai_scroll(
            {"x": 0, "y": 0, "delta_y": 5000}, _Exec()
        )
        assert captured["args"]["magnitude"] == 999

    @pytest.mark.asyncio
    async def test_normal_scroll_preserved_unchanged(self):
        """A typical 200 px scroll must pass through unchanged — i.e. the
        old ``max(magnitude, 200)`` floor isn't sneaking back in as a
        coincidence at exactly 200."""
        from backend.engine.openai import OpenAICUClient

        captured = {}

        class _Exec:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                captured["args"] = args
                class _R:
                    name = "scroll_at"; success = True; error = None; extra: dict = {}
                return _R()

            async def capture_screenshot(self):
                return b""

            def get_current_url(self):
                return ""

        client = OpenAICUClient.__new__(OpenAICUClient)
        client._model = "gpt-5.4"
        client._reasoning_effort = "low"
        await client._execute_openai_scroll(
            {"x": 100, "y": 100, "delta_y": 200}, _Exec()
        )
        assert captured["args"]["magnitude"] == 200
        assert captured["args"]["direction"] == "down"

    @pytest.mark.asyncio
    async def test_zero_scroll_clamped_to_one(self):
        """``magnitude=0`` would be a no-op for xdotool; the lower bound
        of 1 keeps it as the smallest possible scroll instead of
        silently dropping the action."""
        from backend.engine.openai import OpenAICUClient

        captured = {}

        class _Exec:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                captured["args"] = args
                class _R:
                    name = "scroll_at"; success = True; error = None; extra: dict = {}
                return _R()

            async def capture_screenshot(self):
                return b""

            def get_current_url(self):
                return ""

        client = OpenAICUClient.__new__(OpenAICUClient)
        client._model = "gpt-5.4"
        client._reasoning_effort = "low"
        await client._execute_openai_scroll(
            {"x": 50, "y": 50, "delta_y": 0, "delta_x": 0}, _Exec()
        )
        assert captured["args"]["magnitude"] == 1

    @pytest.mark.asyncio
    async def test_horizontal_negative_scroll_direction(self):
        """When |dx| > |dy| the action is horizontal; negative dx is left.
        Magnitude must still be the absolute value, not signed."""
        from backend.engine.openai import OpenAICUClient

        captured = {}

        class _Exec:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                captured["args"] = args
                class _R:
                    name = "scroll_at"; success = True; error = None; extra: dict = {}
                return _R()

            async def capture_screenshot(self):
                return b""

            def get_current_url(self):
                return ""

        client = OpenAICUClient.__new__(OpenAICUClient)
        client._model = "gpt-5.4"
        client._reasoning_effort = "low"
        await client._execute_openai_scroll(
            {"x": 700, "y": 400, "delta_x": -75, "delta_y": 5}, _Exec()
        )
        assert captured["args"]["direction"] == "left"
        assert captured["args"]["magnitude"] == 75


# ── C8 — xdotool key-combo allowlist ───────────────────────────────────────


class TestKeyAllowlist:
    """C8: model-emitted key tokens are restricted to an explicit allowlist."""

    def test_simple_letters_accepted(self):
        from backend.engine import _is_allowed_key_token as ok

        for t in ("a", "b", "z", "A", "9"):
            assert ok(t), f"expected {t!r} accepted"

    def test_special_keys_accepted(self):
        from backend.engine import _is_allowed_key_token as ok

        for t in ("ctrl", "alt", "shift", "super", "Return", "Escape", "f1", "f12"):
            assert ok(t), f"expected {t!r} accepted"

    def test_arbitrary_symbols_rejected(self):
        from backend.engine import _is_allowed_key_token as ok

        # Long random tokens that xdotool would treat as unknown keysyms
        # but could still surface as disruptive combos.
        for t in ("xkill", "randomgarbage", "XF86Launch1"):
            assert not ok(t), f"expected {t!r} rejected"

    @pytest.mark.asyncio
    async def test_hold_key_rejects_disallowed_keysym(self):
        """C8 follow-up: ``hold_key`` must enforce the same allowlist as
        ``key_combination``. A prompt-injected ``XF86PowerOff`` keydown
        held for seconds would be far more disruptive than a single
        keypress."""
        from backend.engine import DesktopExecutor

        engine = DesktopExecutor.__new__(DesktopExecutor)
        engine._post_action = AsyncMock(return_value={})

        for bad in ("XF86PowerOff", "XF86Launch1", "ctrl+alt+BackSpace", "", "  "):
            result = await engine._act_hold_key({"key": bad, "duration": 1})
            assert result.get("success") is False, f"expected reject for {bad!r}"
            assert "Disallowed" in result.get("message", "")
        # No xdotool call should have been issued for any rejected token.
        engine._post_action.assert_not_called()

    @pytest.mark.asyncio
    async def test_hold_key_accepts_allowlisted_keysym(self):
        """Preserve normal supported behavior: ``Shift`` held for a
        second is the regression case the product needs to keep
        working (used by Claude's hold_key examples)."""
        from backend.engine import DesktopExecutor

        engine = DesktopExecutor.__new__(DesktopExecutor)
        engine._post_action = AsyncMock(return_value={"ok": True})

        result = await engine._act_hold_key({"key": "shift", "duration": 0.5})
        assert result.get("success") is not False
        assert result["key"] == "shift"
        assert result["duration"] == 0.5
        # Exactly one keydown + one keyup were dispatched.
        assert engine._post_action.await_count == 2
        first, second = engine._post_action.await_args_list
        assert first.args[0]["action"] == "keydown"
        assert second.args[0]["action"] == "keyup"
        assert first.args[0]["text"] == "shift"
        assert second.args[0]["text"] == "shift"


# ── C10 — screenshot fallback on HTTP 5xx ─────────────────────────────────


class TestScreenshotFallback:
    """C10: 5xx / error-payload responses should fall back to docker exec,
    but 401/403 must surface so token mismatches are visible."""

    @staticmethod
    def _response(status: int, body: dict | None = None) -> httpx.Response:
        """Build a Response that has its ``.request`` set so
        ``raise_for_status`` works (httpx requires this on 1.x)."""
        req = httpx.Request("GET", "http://127.0.0.1:9222/screenshot")
        return httpx.Response(status, json=body or {}, request=req)

    @pytest.mark.asyncio
    async def test_5xx_falls_back(self, monkeypatch):
        from backend.agent import loop as ss

        called = {}

        async def fake_fallback():
            called["fallback"] = True
            return "ZmFsbGJhY2s="  # base64 for "fallback"

        resp = self._response(500, {"error": "boom"})

        class _FakeClient:
            async def get(self, *_a, **_kw):
                return resp

            async def aclose(self):
                pass

            @property
            def is_closed(self):
                return False

        monkeypatch.setattr(ss, "_get_client", lambda: _FakeClient())
        monkeypatch.setattr(ss, "_fallback_docker_screenshot", fake_fallback)
        b64 = await ss.capture_screenshot()
        assert called.get("fallback") is True
        assert b64 == "ZmFsbGJhY2s="

    @pytest.mark.asyncio
    async def test_401_propagates(self, monkeypatch):
        from backend.agent import loop as ss

        resp = self._response(401, {"error": "unauthorized"})

        class _FakeClient:
            async def get(self, *_a, **_kw):
                return resp

            async def aclose(self):
                pass

            @property
            def is_closed(self):
                return False

        monkeypatch.setattr(ss, "_get_client", lambda: _FakeClient())
        with pytest.raises(httpx.HTTPStatusError):
            await ss.capture_screenshot()


# ── AI6 — secret scrubbing ─────────────────────────────────────────────────


class TestSecretScrubbing:
    def test_openai_key_redacted(self):
        from backend.engine import scrub_secrets

        s = "leaked: sk-proj-AAAAAAAAAAAAAAAAAAAA"
        out = scrub_secrets(s)
        assert "sk-proj" not in out

    def test_anthropic_key_redacted(self):
        from backend.engine import scrub_secrets

        s = "my key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ is here"
        out = scrub_secrets(s)
        assert "sk-ant-api03" not in out
        assert "[REDACTED" in out

    def test_google_key_redacted(self):
        from backend.engine import scrub_secrets

        s = "AIzaSyA12345678901234567890123456789 should be gone"
        out = scrub_secrets(s)
        assert "AIzaSy" not in out

    def test_plain_text_passthrough(self):
        from backend.engine import scrub_secrets

        assert scrub_secrets("nothing secret here") == "nothing secret here"


# ── AI4 — retry helper ─────────────────────────────────────────────────────


class TestCallWithRetry:
    @pytest.mark.asyncio
    async def test_retries_transient_then_succeeds(self):
        from backend.engine import _call_with_retry

        calls = {"n": 0}

        async def _inner():
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.TimeoutException("transient")
            return "ok"

        out = await _call_with_retry(_inner, provider="test", base_delay=0.01)
        assert out == "ok"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_non_transient_raises_immediately(self):
        from backend.engine import _call_with_retry

        calls = {"n": 0}

        async def _inner():
            calls["n"] += 1
            raise ValueError("permanent")

        with pytest.raises(ValueError):
            await _call_with_retry(_inner, provider="test", base_delay=0.01)
        # Non-transient should NOT retry.
        assert calls["n"] == 1


# ── C2 / S9 — Origin / Host header gating ──────────────────────────────────


class TestOriginGating:
    def test_rest_origin_ok_accepts_loopback_no_origin(self):
        from backend import server

        req = MagicMock()
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        assert server._rest_origin_ok(req)

    def test_rest_origin_ok_accepts_allowed_origin(self):
        from backend import server

        req = MagicMock()
        req.headers = {"origin": server._ALLOWED_ORIGINS[0]}
        req.client = MagicMock()
        req.client.host = "192.168.1.50"
        assert server._rest_origin_ok(req)

    def test_rest_origin_ok_rejects_foreign_origin(self):
        from backend import server

        req = MagicMock()
        req.headers = {"origin": "https://evil.example.com"}
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        assert not server._rest_origin_ok(req)


class TestWebSocketOriginGating:
    """C2/S1: /ws and /vnc/websockify must reject non-allowlisted Origins
    *before* accepting the upgrade. Browsers do not enforce CORS on
    WebSocket upgrades, so the server is the only gate."""

    def test_ws_origin_ok_accepts_allowed_origin(self):
        from backend import server

        ws = MagicMock()
        ws.headers = {"origin": server._ALLOWED_ORIGINS[0]}
        ws.client = MagicMock()
        ws.client.host = "192.168.1.50"  # non-loopback — Origin is what matters
        assert server._ws_origin_ok(ws)

    def test_ws_origin_ok_rejects_foreign_origin(self):
        from backend import server

        ws = MagicMock()
        ws.headers = {"origin": "https://evil.example.com"}
        ws.client = MagicMock()
        ws.client.host = "127.0.0.1"  # loopback must NOT rescue a bad Origin
        assert not server._ws_origin_ok(ws)

    def test_ws_origin_ok_accepts_no_origin_from_loopback(self):
        from backend import server

        ws = MagicMock()
        ws.headers = {}
        ws.client = MagicMock()
        ws.client.host = "127.0.0.1"
        # No token configured -> empty Origin from loopback is allowed
        # (curl/Python clients don't send Origin).
        assert server._ws_origin_ok(ws)

    def test_ws_origin_ok_rejects_no_origin_from_remote(self, monkeypatch):
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "")
        ws = MagicMock()
        ws.headers = {}
        ws.client = MagicMock()
        ws.client.host = "10.0.0.5"  # not in _LOOPBACK_HOSTS
        assert not server._ws_origin_ok(ws)

    def test_ws_origin_ok_rejects_null_origin_without_token(self, monkeypatch):
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "")
        ws = MagicMock()
        ws.headers = {"origin": "null"}
        ws.client = MagicMock()
        ws.client.host = "203.0.113.10"  # remote
        assert not server._ws_origin_ok(ws)


# ── S-WS-TOKEN — /vnc/websockify token parity with /ws ─────────────────────


class TestVncWebsockifyTokenGating:
    """When ``CUA_WS_TOKEN`` is set, /vnc/websockify must reject upgrades
    without a matching ``?token=`` — same close code (4401) and same
    rejection point (before ``ws.accept()`` and before any upstream
    socket is opened) as /ws. With the token unset, both surfaces must
    keep the default-open behaviour local dev relies on.

    Mirrors the MagicMock style used by ``TestWebSocketOriginGating``.
    """

    @staticmethod
    def _make_ws(token_param: str | None, *, origin: str | None = None):
        """Build a MagicMock WebSocket with the given ?token=... and Origin.

        ``origin=None`` → empty header dict (loopback/curl shape).
        ``origin="allowed"`` → first entry in ``_ALLOWED_ORIGINS``.
        """
        from backend import server

        ws = MagicMock()
        if origin == "allowed":
            ws.headers = {"origin": server._ALLOWED_ORIGINS[0]}
        elif origin is None:
            ws.headers = {}
        else:
            ws.headers = {"origin": origin}
        ws.client = MagicMock()
        ws.client.host = "127.0.0.1"
        ws.query_params = {} if token_param is None else {"token": token_param}
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        return ws

    # ── Helper: pure function, no I/O ───────────────────────────────

    def test_ws_token_ok_passes_when_unset(self, monkeypatch):
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "")
        ws = self._make_ws(token_param=None)
        assert server._ws_token_ok(ws) is True

    def test_ws_token_ok_rejects_missing_token(self, monkeypatch):
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "secret")
        ws = self._make_ws(token_param=None)
        assert server._ws_token_ok(ws) is False

    def test_ws_token_ok_rejects_wrong_token(self, monkeypatch):
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "secret")
        ws = self._make_ws(token_param="wrong")
        assert server._ws_token_ok(ws) is False

    def test_ws_token_ok_accepts_matching_token(self, monkeypatch):
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "secret")
        ws = self._make_ws(token_param="secret")
        assert server._ws_token_ok(ws) is True

    # ── Handler: vnc_ws_proxy must gate before accept() ─────────────

    def test_vnc_rejects_missing_token_with_4401_before_accept(self, monkeypatch):
        """Success criterion (1): CUA_WS_TOKEN set + no token →
        close(4401) BEFORE accept and BEFORE any upstream connect."""
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "secret")
        ws = self._make_ws(token_param=None, origin="allowed")

        # Upstream connect must never be called. Patch it to blow up
        # loudly if the gate ever lets this through to the data plane.
        def _must_not_connect(*a, **kw):
            raise AssertionError(
                "vnc_ws_proxy opened an upstream socket before "
                "rejecting an unauthenticated client"
            )

        with patch("websockets.connect", side_effect=_must_not_connect):
            asyncio.run(server.vnc_ws_proxy(ws))

        ws.close.assert_awaited_once_with(
            code=server._WS_AUTH_CLOSE_CODE,
            reason=server._WS_AUTH_CLOSE_REASON,
        )
        ws.accept.assert_not_called()

    def test_vnc_rejects_wrong_token_with_4401(self, monkeypatch):
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "secret")
        ws = self._make_ws(token_param="nope", origin="allowed")

        with patch("websockets.connect", side_effect=AssertionError("no upstream")):
            asyncio.run(server.vnc_ws_proxy(ws))

        ws.close.assert_awaited_once_with(
            code=server._WS_AUTH_CLOSE_CODE,
            reason=server._WS_AUTH_CLOSE_REASON,
        )
        ws.accept.assert_not_called()

    def test_vnc_accepts_matching_token(self, monkeypatch):
        """Success criterion (2): CUA_WS_TOKEN set + matching ?token=
        → proxy proceeds (reaches ws.accept and attempts upstream)."""
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "secret")
        ws = self._make_ws(token_param="secret", origin="allowed")

        # Short-circuit the upstream so the test doesn't need a real
        # websockify. ``websockets.connect`` is what the handler awaits
        # inside an ``async with`` block — raising here exits the
        # handler cleanly after accept().
        class _Boom(Exception):
            pass

        with patch("websockets.connect", side_effect=_Boom("no upstream in test")):
            asyncio.run(server.vnc_ws_proxy(ws))

        ws.accept.assert_awaited_once()
        # close() with 4401 must NOT have been issued on the authorised path
        for call in ws.close.await_args_list:
            assert call.kwargs.get("code") != 4401, (
                "Authorised /vnc/websockify upgrade was rejected as 4401"
            )

    def test_vnc_token_unset_preserves_default_open(self, monkeypatch):
        """Success criterion (3): CUA_WS_TOKEN unset → no token required
        on either surface. Default-open behaviour preserved."""
        from backend import server

        monkeypatch.setattr(server, "_WS_AUTH_TOKEN", "")
        ws = self._make_ws(token_param=None, origin="allowed")

        class _Boom(Exception):
            pass

        with patch("websockets.connect", side_effect=_Boom("no upstream in test")):
            asyncio.run(server.vnc_ws_proxy(ws))

        ws.accept.assert_awaited_once()
        for call in ws.close.await_args_list:
            assert call.kwargs.get("code") != 4401


# ── D-READY — container readiness is health-checked, not assumed ───────────


class TestContainerReadinessGating:
    """The previous behaviour returned success from ``_wait_for_service``
    whenever the container process was alive, even if ``/health`` never
    answered. The new contract is:

    * ``_wait_for_service`` returns False on health-check exhaustion.
    * ``get_state()`` reports ``agent="unready"`` after the failure.
    * ``POST /api/agent/start`` refuses to create a session with a 4xx
      status when the cached readiness says the agent isn't ready.
    """

    def test_wait_for_service_returns_false_when_health_never_recovers(self, monkeypatch):
        """Container process survives but /health always errors → False,
        and get_state() reports running=True, agent=unready with the
        last error preserved for operators to see."""
        from backend.infra import docker as dm
        # Tight budget so the test doesn't actually wait 30s. Also
        # shrink the backoff floor so the jitter doesn't dominate.
        monkeypatch.setattr(dm.config, "container_ready_timeout", 0.3)
        monkeypatch.setattr(dm.config, "container_ready_poll_base", 0.02)
        monkeypatch.setattr(dm.config, "container_ready_poll_cap", 0.05)

        # Simulate an upstream that refuses every connection.
        class _BrokenClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                raise httpx.ConnectError("connection refused")

        async def go():
            with patch.object(dm.httpx, "AsyncClient", _BrokenClient), \
                 patch.object(dm, "is_container_running", AsyncMock(return_value=True)):
                ok = await dm._wait_for_service("cua-test")
            return ok

        ok = asyncio.run(go())

        assert ok is False, (
            "Health-check exhaustion must report failure. Returning True "
            "when the container is merely running reintroduces the "
            "'session started against broken sandbox' bug."
        )
        state = dm.get_state()
        assert state["container"] == "running"
        assert state["agent"] == "unready"
        assert state["last_health_error"] is not None
        assert "ConnectError" in state["last_health_error"]

    def test_wait_for_service_returns_true_on_first_healthy_probe(self, monkeypatch):
        """Happy path: a 200 from /health flips state to ready and
        clears any prior last_health_error."""
        from backend.infra import docker as dm
        monkeypatch.setattr(dm.config, "container_ready_timeout", 0.5)
        monkeypatch.setattr(dm.config, "container_ready_poll_base", 0.01)

        class _OkClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw):
                return MagicMock(status_code=200)

        async def go():
            with patch.object(dm.httpx, "AsyncClient", _OkClient):
                return await dm._wait_for_service("cua-test")

        assert asyncio.run(go()) is True
        state = dm.get_state()
        assert state["agent"] == "ready"
        assert state["container"] == "running"
        assert state["last_health_error"] is None

    def test_start_agent_rejects_with_409_when_agent_unready(self, monkeypatch):
        """End-to-end: when the cached readiness says the sandbox isn't
        ready, POST /api/agent/start must refuse with a 4xx-range
        response instead of creating a doomed session."""
        from fastapi.testclient import TestClient

        from backend import server

        client = TestClient(server.app)

        # Pretend the container came up but the agent service is
        # unreachable — exactly the scenario this finding is about.
        fake_state = {
            "container": "running",
            "agent": "unready",
            "last_health_error": "ConnectError: connection refused",
        }
        with patch.object(server, "start_container", AsyncMock(return_value=True)), \
             patch.object(server, "get_container_state", return_value=fake_state), \
             patch.object(server._agent_start_limiter, "allow", return_value=True):
            resp = client.post(
                "/api/agent/start",
                json={
                    "task": "open a browser",
                    "api_key": "sk-test-1234-abcd",
                    "model": "claude-sonnet-4-6",
                    "max_steps": 5,
                    "mode": "desktop",
                    "engine": "computer_use",
                    "provider": "anthropic",
                    "execution_target": "docker",
                },
                headers={"origin": server._ALLOWED_ORIGINS[0]},
            )

        assert 400 <= resp.status_code < 500, (
            f"Expected 4xx refusal when agent not ready, got {resp.status_code}: {resp.text}"
        )
        # 409 is the documented code for this path.
        assert resp.status_code == 409
        body = resp.json()
        # Error message must mention the underlying reason so operators
        # can act — the whole point of the fix is that this is no
        # longer a silent warning.
        assert "not ready" in (body.get("error") or "").lower()

    def test_start_agent_rejects_with_409_when_running_container_stays_unready(self, monkeypatch):
        """If ``start_container()`` fails because an already-running
        container never passed the readiness probe, the start endpoint
        must surface a 409 not-ready error instead of a generic 503."""
        from fastapi.testclient import TestClient

        from backend import server

        client = TestClient(server.app)

        fake_state = {
            "container": "running",
            "agent": "unready",
            "last_health_error": "ConnectError: connection refused",
        }
        with patch.object(server, "start_container", AsyncMock(return_value=False)), \
             patch.object(server, "get_container_state", return_value=fake_state), \
             patch.object(server._agent_start_limiter, "allow", return_value=True):
            resp = client.post(
                "/api/agent/start",
                json={
                    "task": "open a browser",
                    "api_key": "sk-test-1234-abcd",
                    "model": "claude-sonnet-4-6",
                    "max_steps": 5,
                    "mode": "desktop",
                    "engine": "computer_use",
                    "provider": "anthropic",
                    "execution_target": "docker",
                },
                headers={"origin": server._ALLOWED_ORIGINS[0]},
            )

        assert resp.status_code == 409
        assert "not ready" in (resp.json().get("error") or "").lower()


# ── S-H — public-bind guardrail ────────────────────────────────────────────


class TestPublicBindGuardrail:
    """The default backend bind is ``127.0.0.1``. Binding to a non-loopback
    host without both ``CUA_ALLOW_PUBLIC_BIND=1`` and ``CUA_WS_TOKEN`` must
    refuse to start so a typo or a copy-pasted ``HOST=0.0.0.0`` doesn't
    accidentally publish an unauthenticated REST + WS surface to the LAN."""

    def test_default_host_is_loopback(self):

        # The class default — what callers get when no HOST env is set.
        assert Config.host == "127.0.0.1"

    def test_loopback_bind_is_allowed(self, monkeypatch):
        from backend import main

        monkeypatch.delenv("CUA_ALLOW_PUBLIC_BIND", raising=False)
        monkeypatch.delenv("CUA_WS_TOKEN", raising=False)
        # Should NOT call sys.exit for any loopback alias.
        for h in ("127.0.0.1", "localhost", "::1"):
            main._enforce_public_bind_guardrail(h)

    def test_external_bind_without_opt_in_exits(self, monkeypatch):
        import pytest

        from backend import main

        monkeypatch.delenv("CUA_ALLOW_PUBLIC_BIND", raising=False)
        monkeypatch.setenv("CUA_WS_TOKEN", "secret")  # token alone is not enough
        with pytest.raises(SystemExit) as excinfo:
            main._enforce_public_bind_guardrail("0.0.0.0")
        assert excinfo.value.code == 2

    def test_external_bind_without_token_exits(self, monkeypatch):
        import pytest

        from backend import main

        monkeypatch.setenv("CUA_ALLOW_PUBLIC_BIND", "1")
        monkeypatch.delenv("CUA_WS_TOKEN", raising=False)
        with pytest.raises(SystemExit) as excinfo:
            main._enforce_public_bind_guardrail("0.0.0.0")
        assert excinfo.value.code == 2

    def test_external_bind_with_both_envs_is_allowed(self, monkeypatch, caplog):
        from backend import main

        monkeypatch.setenv("CUA_ALLOW_PUBLIC_BIND", "1")
        monkeypatch.setenv("CUA_WS_TOKEN", "secret")
        with caplog.at_level("WARNING", logger="backend.main"):
            main._enforce_public_bind_guardrail("0.0.0.0")
        # Operator should still see a loud warning even when allowed.
        assert any("binding externally" in r.message for r in caplog.records)


# ── C13 — token env-file ───────────────────────────────────────────────────


class TestTokenEnvFile:
    def test_env_file_is_mode_0600(self, tmp_path, monkeypatch):
        from backend.infra import docker as dm
        path = dm._write_token_env_file("secret-value")
        try:
            assert os.path.exists(path)
            # On POSIX check file mode; on Windows os.stat doesn't expose
            # UNIX perms so we just verify the file exists and contains
            # the token.
            if os.name == "posix":
                mode = os.stat(path).st_mode & 0o777
                assert mode == 0o600, f"expected 0600, got {oct(mode)}"
            text = Path(path).read_text()
            assert "AGENT_SERVICE_TOKEN=secret-value" in text
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# ── AI2 — stuck-agent fingerprint detection ────────────────────────────────


class TestStuckAgentDetection:
    """3 consecutive identical action fingerprints trip request_stop."""

    @pytest.mark.asyncio
    async def test_three_identical_trips_stop(self):
        from backend.agent.loop import AgentLoop
        from backend.engine import CUActionResult, CUTurnRecord

        loop = AgentLoop(task="hello", api_key="k" * 16)
        # Stub out the callback that requires a real broadcaster.
        loop._on_log = None
        loop._on_step = None

        # Build three turns with identical click-at-same-coords fingerprint.
        # We invoke the private _on_turn from inside _run_computer_use_engine
        # without actually calling the engine. Easiest way: patch the
        # engine entirely and replay three fake turn records.
        from backend.engine import ComputerUseEngine

        async def _noop_execute_task(*_a, **_kw):
            # Synthesize three identical turns into the loop's on_turn.
            callback = _kw.get("on_turn")
            for i in range(1, 4):
                callback(CUTurnRecord(
                    turn=i, model_text="clicking",
                    actions=[CUActionResult(
                        name="click_at",
                        success=True,
                        extra={"pixel_x": 200, "pixel_y": 300},
                    )],
                ))
            return "done"

        with patch.object(ComputerUseEngine, "execute_task", new=_noop_execute_task):
            await loop._run_computer_use_engine()
        assert loop._stop_requested is True

    @pytest.mark.asyncio
    async def test_three_identical_actually_cancels_engine_task(self):
        """Flipping ``_stop_requested`` alone leaves the engine running
        until its turn limit. Detection must also cancel ``_run_task``
        so the next provider call raises ``CancelledError``."""
        from backend.agent.loop import AgentLoop
        from backend.engine import CUActionResult, CUTurnRecord
        from backend.engine import ComputerUseEngine

        loop = AgentLoop(task="hello", api_key="k" * 16)
        loop._on_log = None
        loop._on_step = None

        cancelled = asyncio.Event()
        identical = CUActionResult(
            name="click_at", success=True,
            extra={"pixel_x": 50, "pixel_y": 60},
        )

        async def _engine(*_a, **_kw):
            cb = _kw["on_turn"]
            try:
                for i in range(1, 50):  # would run far past the trip point
                    cb(CUTurnRecord(turn=i, model_text="x", actions=[identical]))
                    await asyncio.sleep(0)  # yield so cancel can land
                return "ran-too-long"
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with patch.object(ComputerUseEngine, "execute_task", new=_engine):
            await loop._run_computer_use_engine()

        assert loop._stop_requested is True
        assert cancelled.is_set(), "engine task must be cancelled when stuck"
        # Session ends in STOPPED, not COMPLETED.
        from backend.models.schemas import SessionStatus
        assert loop.session.status == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_long_workflow_with_varying_actions_not_stopped(self):
        """Preserve successful long-running workflows: many turns whose
        actions vary (different coords / text) must NOT trip detection."""
        from backend.agent.loop import AgentLoop
        from backend.engine import CUActionResult, CUTurnRecord
        from backend.engine import ComputerUseEngine

        loop = AgentLoop(task="hello", api_key="k" * 16)
        loop._on_log = None
        loop._on_step = None

        async def _engine(*_a, **_kw):
            cb = _kw["on_turn"]
            for i in range(1, 21):
                cb(CUTurnRecord(
                    turn=i, model_text=f"step {i}",
                    actions=[CUActionResult(
                        name="click_at", success=True,
                        # Varying coords — fingerprint differs every turn.
                        extra={"pixel_x": 100 + i, "pixel_y": 200 + i},
                    )],
                ))
            return "done"

        with patch.object(ComputerUseEngine, "execute_task", new=_engine):
            await loop._run_computer_use_engine()

        assert loop._stop_requested is False, (
            "varying actions must not be flagged as stuck"
        )


# ── E — Gemini native async only (no asyncio.to_thread fallback) ────────
class TestGeminiNativeAsync:
    """Phase E: provider calls must use the SDK's native async surface;
    the prior `asyncio.to_thread(sync_client.models.generate_content, ...)`
    fallback in `GeminiCUClient._generate` was dead code under the pinned
    `google-genai==1.67.0` and has been removed."""

    @pytest.mark.asyncio
    async def test_generate_calls_aio_models_directly(self):
        from backend.engine.gemini import GeminiCUClient

        client = GeminiCUClient.__new__(GeminiCUClient)
        client._model = "gemini-3-flash-preview"

        fake_models = MagicMock()
        fake_models.generate_content = AsyncMock(return_value="RESPONSE")
        fake_aio = MagicMock()
        fake_aio.models = fake_models
        fake_client = MagicMock()
        fake_client.aio = fake_aio
        client._client = fake_client

        result = await client._generate(contents=["hi"], config={"k": "v"})

        assert result == "RESPONSE"
        fake_models.generate_content.assert_awaited_once_with(
            model="gemini-3-flash-preview",
            contents=["hi"],
            config={"k": "v"},
        )

    def test_no_to_thread_in_generate_source(self):
        """Lock the regression: _generate must not reintroduce blocking
        `asyncio.to_thread` calls and must keep the native-async path."""
        import inspect

        from backend.engine.gemini import GeminiCUClient

        src = inspect.getsource(GeminiCUClient._generate)
        # Match the call form, not docstring mentions of the removed pattern.
        assert "to_thread(" not in src
        assert "aio.models.generate_content" in src

    def test_gemini_module_does_not_import_asyncio(self):
        """``asyncio`` is allowed in :mod:`backend.engine.gemini` only for\n        the file-search blocking-call wrappers (``asyncio.to_thread``\n        for ``create``/``upload``/``delete`` against the synchronous\n        google-genai file-search API per the official April 2026 docs).\n        The ``_generate`` path must remain native-async \u2014 see\n        ``test_no_to_thread_in_generate_source`` for the lock there."""
        import inspect

        import backend.engine.gemini as gem_mod
        from backend.engine.gemini import GeminiCUClient

        # ``_generate`` lock: native-async only, no to_thread fallback.
        assert "to_thread(" not in inspect.getsource(GeminiCUClient._generate)

        # Everywhere else ``asyncio.to_thread`` is permitted only for
        # the documented file-search wrappers.
        src = inspect.getsource(gem_mod)
        for occurrence_line in [
            ln.strip() for ln in src.splitlines() if "asyncio.to_thread(" in ln
        ]:
            assert (
                "_create_store_blocking" in occurrence_line
                or "_upload_blocking" in occurrence_line
                or "_delete_blocking" in occurrence_line
            ), (
                "Unexpected asyncio.to_thread use outside the file-search "
                f"blocking-call wrappers: {occurrence_line!r}"
            )
