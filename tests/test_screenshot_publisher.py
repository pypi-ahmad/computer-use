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

from __future__ import annotations

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
      * ``backend.agent.screenshot.capture_screenshot`` → returns a
        tiny fake PNG-base64 string. We also clear the server's bound
        import so the freshly-patched callable is picked up.
      * ``backend.docker_manager.is_container_running`` → True, so the
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
    import backend.docker_manager as dm
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
