"""Tests for the in-container agent_service action-surface gate.

Verifies that ``CUA_ENABLE_LEGACY_ACTIONS`` controls which action names
reach the dispatcher, and that disabled actions surface as HTTP 404.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def _make_post_handler(agent_service, body: dict):
    """Construct a minimal POST /action handler and capture its response."""
    captured: dict[str, object] = {}
    handler = agent_service.AgentHandler.__new__(agent_service.AgentHandler)
    handler.path = "/action"
    handler._authorized = lambda: True
    handler._read_body = lambda: body
    handler._respond = lambda status, payload: captured.update(
        {"status": status, "payload": payload}
    )
    return handler, captured


@pytest.fixture
def agent_service(monkeypatch):
    """Import docker.agent_service with a clean env and return the module."""
    # Module caches env at import time, so reload under the desired env.
    monkeypatch.delenv("CUA_ENABLE_LEGACY_ACTIONS", raising=False)
    if "docker.agent_service" in sys.modules:
        del sys.modules["docker.agent_service"]
    mod = importlib.import_module("docker.agent_service")
    return mod


@pytest.fixture
def agent_service_legacy(monkeypatch):
    monkeypatch.setenv("CUA_ENABLE_LEGACY_ACTIONS", "1")
    if "docker.agent_service" in sys.modules:
        del sys.modules["docker.agent_service"]
    mod = importlib.import_module("docker.agent_service")
    yield mod
    # Reset for other tests: reload with flag off.
    monkeypatch.delenv("CUA_ENABLE_LEGACY_ACTIONS", raising=False)
    if "docker.agent_service" in sys.modules:
        del sys.modules["docker.agent_service"]


class TestEngineActionSet:
    """The hard-coded engine action set must stay in sync with the
    names ``DesktopExecutor`` actually POSTs to ``/action``. If this
    test fails, either the engine grew a new action (update
    ``_ENGINE_ACTIONS``) or the gate drifted from the engine."""

    EXPECTED = frozenset({
        "click", "double_click", "right_click", "middle_click", "hover",
        "type", "hotkey", "key", "keydown", "keyup",
        "scroll", "left_mouse_down", "left_mouse_up", "drag",
        "open_url",
        # ``zoom`` is a ``computer_20251124``-era action (Opus 4.7):
        # always on when the adapter advertises ``enable_zoom``.
        "zoom",
    })

    def test_engine_action_set_matches_expected(self, agent_service):
        assert agent_service._ENGINE_ACTIONS == self.EXPECTED

    def test_engine_action_set_disjoint_from_legacy(self, agent_service):
        assert not (
            agent_service._ENGINE_ACTIONS & agent_service._LEGACY_ACTIONS
        )


class TestActionGate:
    """``_is_action_enabled`` is the single source of truth the handler
    consults before dispatch."""

    def test_live_actions_always_enabled(self, agent_service):
        for action in agent_service._ENGINE_ACTIONS:
            assert agent_service._is_action_enabled(action), action

    def test_legacy_disabled_by_default(self, agent_service):
        assert not agent_service.LEGACY_ACTIONS_ENABLED
        for action in ("run_command", "open_terminal", "window_minimize",
                       "paste", "screenshot_region", "evaluate_js"):
            assert not agent_service._is_action_enabled(action), action

    def test_unknown_action_disabled(self, agent_service):
        assert not agent_service._is_action_enabled("definitely_not_real")
        assert not agent_service._is_action_enabled("")

    def test_legacy_enabled_when_flag_set(self, agent_service_legacy):
        assert agent_service_legacy.LEGACY_ACTIONS_ENABLED
        for action in ("run_command", "open_terminal", "window_minimize"):
            assert agent_service_legacy._is_action_enabled(action), action

    def test_unknown_still_blocked_with_legacy_flag(self, agent_service_legacy):
        assert not agent_service_legacy._is_action_enabled("definitely_not_real")

    def test_disabled_action_404_preserves_action_error_envelope(self, agent_service):
        handler, captured = _make_post_handler(
            agent_service, {"action": "run_command", "mode": "desktop"},
        )
        handler.do_POST()
        assert captured["status"] == 404
        assert captured["payload"] == {
            "success": False,
            "message": "Unknown or disabled action: 'run_command'",
        }


class TestDefensesPreserved:
    """The command allowlist and blocked-pattern defenses from PR 05
    are load-bearing even when the ``run_command`` action is reachable
    (legacy flag). Ensure they stay present in the module."""

    def test_allowed_commands_present(self, agent_service):
        assert isinstance(agent_service._ALLOWED_COMMANDS, frozenset)
        assert len(agent_service._ALLOWED_COMMANDS) > 0

    def test_blocked_patterns_present(self, agent_service):
        assert isinstance(agent_service._BLOCKED_CMD_PATTERNS, tuple)
        assert len(agent_service._BLOCKED_CMD_PATTERNS) > 0
