"""Regression tests for ``docker/agent_service.py``'s ``run_command`` policy.

These lock in the fix for the policy-vs-enforcement drift where
``_BLOCKED_CMD_PATTERNS`` was defined at module scope but never
consulted by the live ``run_command`` dispatch.

Live-engine note: ``backend/engine_capabilities.json`` does NOT
currently expose ``run_command`` in the action vocabulary, so the
production blast radius is bounded. These tests harden the service
layer ahead of any future re-exposure and catch a reviewer-trap
where the file documented a stronger policy than it enforced.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_agent_service():
    """Import docker/agent_service.py as a standalone module.

    Mirrors the loader in ``test_gap_coverage.py`` (the file is not on
    ``sys.path`` — it's designed to run inside the container).
    """
    sys.modules.pop("agent_service_cmd_policy", None)
    path = Path(__file__).resolve().parents[1] / "docker" / "agent_service.py"
    spec = importlib.util.spec_from_file_location("agent_service_cmd_policy", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def svc(monkeypatch):
    """Load the module with legacy actions enabled.

    ``run_command`` is intentionally legacy-gated by default, so tests
    that exercise its branch should do so under the same flag an
    operator would use when re-enabling it.
    """
    monkeypatch.setenv("CUA_ENABLE_LEGACY_ACTIONS", "1")
    return _load_agent_service()


# ── Unit: _blocked_cmd_match pure-function contract ────────────────────────


class TestBlockedCmdMatch:
    """Pure-function tests: _blocked_cmd_match must look at the whole
    argv, not just argv[0], and must be case-insensitive."""

    def test_empty_argv_returns_none(self, svc):
        assert svc._blocked_cmd_match([]) is None

    def test_direct_match_on_argv0(self, svc):
        # ``shutdown`` appears in argv[0]; must trip the gate.
        assert svc._blocked_cmd_match(["shutdown", "-h", "now"]) == "shutdown"

    def test_match_inside_later_arg(self, svc):
        """argv[0] alone is benign; the dangerous phrase hides in a
        later arg. The previous single-arg check would have missed this."""
        match = svc._blocked_cmd_match(["python3", "-c", "import os; os.system('shutdown')"])
        assert match == "shutdown"

    def test_case_insensitive(self, svc):
        """Pattern-matching must survive trivial ``SHUTDOWN`` /
        ``Rm -Rf /`` casing tricks."""
        assert svc._blocked_cmd_match(["bash", "-c", "RM -RF /"]) == "rm -rf /"
        assert svc._blocked_cmd_match(["SHUTDOWN"]) == "shutdown"

    def test_fork_bomb_pattern(self, svc):
        """``:(){`` is the classic fork-bomb prefix."""
        assert svc._blocked_cmd_match(["bash", "-c", ":(){ :|:& };:"]) == ":(){"

    def test_allowed_command_returns_none(self, svc):
        """Negative control: an ordinary ``ls /tmp`` must not trip."""
        assert svc._blocked_cmd_match(["ls", "-la", "/tmp"]) is None
        assert svc._blocked_cmd_match(["cat", "/etc/hostname"]) is None


# ── Integration: the run_command dispatch enforces the policy ──────────────


class TestRunCommandEnforcement:
    """Drive ``_dispatch_desktop`` through the ``run_command`` branch
    with a stub ``subprocess.run`` so we can observe which requests are
    refused (no call) vs executed (call made). The error shape returned
    on a pattern hit MUST equal the allowlist-denial shape so clients
    cannot tell which gate fired."""

    def _call(self, svc, text: str):
        """Invoke the ``run_command`` branch with minimal plumbing."""
        # Construct a bare handler; the dispatch uses ``self`` only as
        # a namespace for a couple of helpers that this branch does
        # not touch, so __new__ without __init__ is safe.
        handler = svc.AgentHandler.__new__(svc.AgentHandler)
        return handler._dispatch_desktop(
            action="run_command", x=0, y=0, text=text, coords=[], target=""
        )

    def test_disallowed_executable_denied(self, svc):
        """Baseline: the allowlist still denies ``curl`` (not in allow-set).
        Establishes the error-message shape we'll compare against."""
        with patch.object(svc.subprocess, "run") as run:
            res = self._call(svc, "curl http://example.com")
        assert res["success"] is False
        assert "Command not allowed" in res["message"]
        run.assert_not_called()

    def test_blocked_pattern_in_argv_denied(self, svc):
        """Executable IS on the allowlist (``bash`` isn't, use ``python3``),
        but the argv carries a blocked pattern — the new gate must fire
        before any subprocess call."""
        # ``python3`` is in _ALLOWED_COMMANDS; without the new check
        # this would reach subprocess.run.
        with patch.object(svc.subprocess, "run") as run:
            res = self._call(svc, "python3 -c \"import os; os.system('shutdown')\"")
        assert res["success"] is False
        assert "Command not allowed" in res["message"]
        # Critical safety property: NO subprocess dispatched.
        run.assert_not_called()

    def test_case_insensitive_block_in_argv(self, svc):
        """Trivial casing tricks must not bypass the gate."""
        with patch.object(svc.subprocess, "run") as run:
            res = self._call(svc, "python3 -c \"print('SHUTDOWN')\"")
        assert res["success"] is False
        assert "Command not allowed" in res["message"]
        run.assert_not_called()

    def test_error_shape_matches_allowlist_denial(self, svc):
        """Gate-type leakage check: the error payload for a blocked
        pattern on an allowlisted executable must be indistinguishable
        from an outright allowlist miss on the same client side."""
        with patch.object(svc.subprocess, "run"):
            allowlist_deny = self._call(svc, "curl http://example.com")
        with patch.object(svc.subprocess, "run"):
            pattern_deny = self._call(svc, "python3 -c \"os.system('shutdown')\"")
        # Both responses have the same keys, the same success flag,
        # and both messages start with the same ``Command not allowed``
        # prefix followed by the permitted-list enumeration — clients
        # cannot distinguish which check fired.
        assert set(allowlist_deny) == set(pattern_deny)
        assert allowlist_deny["success"] is False and pattern_deny["success"] is False
        assert allowlist_deny["message"].startswith("Command not allowed")
        assert pattern_deny["message"].startswith("Command not allowed")

    def test_clean_allowed_command_still_executes(self, svc):
        """Regression guard for criterion #2: a request that passes both
        gates must still reach ``subprocess.run``. Previously-passing
        ``run_command`` flows must not become collateral damage."""
        fake_proc = type("P", (), {"returncode": 0, "stdout": "hello\n", "stderr": ""})()
        with patch.object(svc.subprocess, "run", return_value=fake_proc) as run:
            res = self._call(svc, "echo hello")
        assert res["success"] is True
        # The gate order is: allowlist → block-pattern → (prlimit
        # wrap) → subprocess.run. Just assert the call happened.
        assert run.called
