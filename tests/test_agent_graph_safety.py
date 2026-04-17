"""Tests for backend.agent.safety and backend.agent.graph."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from backend.agent import safety
from backend.agent.graph import (
    get_runtime,
    init_runtime,
    load_session_snapshot,
    shutdown_runtime,
)


# ── safety.py ────────────────────────────────────────────────────────────────


class TestSafetyRegistry:
    """The safety registry stores per-session decisions and signals."""

    def setup_method(self):
        """Reset the module-level state before every test."""
        safety.events.clear()
        safety.decisions.clear()

    def test_get_or_create_event_returns_same_event(self):
        """Repeated lookups for the same session id return the same event."""
        evt1 = safety.get_or_create_event("s1")
        evt2 = safety.get_or_create_event("s1")
        assert evt1 is evt2

    def test_get_or_create_event_distinct_sessions(self):
        """Different session ids get distinct event objects."""
        evt1 = safety.get_or_create_event("s1")
        evt2 = safety.get_or_create_event("s2")
        assert evt1 is not evt2

    def test_clear_removes_event_and_decision(self):
        """clear() wipes both the event and the stored decision."""
        safety.get_or_create_event("s1")
        safety.decisions["s1"] = True
        safety.clear("s1")
        assert "s1" not in safety.events
        assert "s1" not in safety.decisions

    def test_clear_is_idempotent(self):
        """clear() on an unknown session id is a no-op."""
        safety.clear("never-existed")  # must not raise


# ── graph.py ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_sqlite_path():
    """Provide an isolated sqlite DB path for each test."""
    with tempfile.TemporaryDirectory() as d:
        yield str(Path(d) / "sessions.sqlite")


class TestGraphRuntime:
    """The GraphRuntime lifecycle wraps an AsyncSqliteSaver."""

    def test_get_runtime_raises_before_init(self):
        """get_runtime() fails cleanly when init_runtime was never called."""
        # Ensure any prior runtime is shut down so the guard fires.
        asyncio.run(shutdown_runtime())
        with pytest.raises(RuntimeError):
            get_runtime()

    def test_init_then_shutdown_is_idempotent(self, tmp_sqlite_path):
        """init_runtime can be followed by shutdown_runtime cleanly twice."""
        async def _go():
            await init_runtime(tmp_sqlite_path)
            await init_runtime(tmp_sqlite_path)  # second call is a no-op
            rt = get_runtime()
            assert rt.checkpointer is not None
            await shutdown_runtime()
            await shutdown_runtime()  # safe to call again

        asyncio.run(_go())

    def test_load_session_snapshot_missing_returns_none(self, tmp_sqlite_path):
        """load_session_snapshot returns None for unknown session ids."""
        async def _go():
            await init_runtime(tmp_sqlite_path)
            try:
                snapshot = await load_session_snapshot("does-not-exist")
                assert snapshot is None
            finally:
                await shutdown_runtime()

        asyncio.run(_go())

    def test_load_session_snapshot_without_init_returns_none(self):
        """load_session_snapshot before init is a safe no-op (returns None)."""
        async def _go():
            await shutdown_runtime()  # ensure clean state
            snapshot = await load_session_snapshot("anything")
            assert snapshot is None

        asyncio.run(_go())
