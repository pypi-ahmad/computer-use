from __future__ import annotations
# === merged from tests/test_agent_service_action_gate.py ===
"""Tests for the in-container agent_service action-surface gate.

Verifies that ``CUA_ENABLE_LEGACY_ACTIONS`` controls which action names
reach the dispatcher, and that disabled actions surface as HTTP 404.
"""


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

    def test_action_id_replay_returns_cached_result_without_redispatch(self, agent_service):
        agent_service._ACTION_RESULT_CACHE.clear()
        body = {
            "action": "click",
            "mode": "desktop",
            "coordinates": [10, 20],
            "action_id": "replay-123:0",
        }

        first_handler, first_captured = _make_post_handler(agent_service, body)
        second_handler, second_captured = _make_post_handler(agent_service, body)

        with pytest.MonkeyPatch.context() as mp:
            calls = {"count": 0}

            def _dispatch(_self, _body):
                calls["count"] += 1
                return {"success": True, "message": "Clicked at (10, 20)"}

            mp.setattr(agent_service.AgentHandler, "_dispatch_action", _dispatch)
            first_handler.do_POST()
            second_handler.do_POST()

        assert calls["count"] == 1
        assert first_captured["status"] == 200
        assert second_captured["status"] == 200
        assert second_captured["payload"] == first_captured["payload"]


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

# === merged from tests/test_docker_cmd_policy.py ===
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
    path = Path(__file__).resolve().parents[2] / "docker" / "agent_service.py"
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

# === merged from tests/test_docker_production_hardening.py ===
"""SC8 — production-hardening source-scan regression guards for the Dockerfile.

We cannot run ``docker build`` in the test sandbox, so each invariant
below is asserted by reading the Dockerfile / entrypoint.sh as source
text. A future refactor that strips an OCI label or the signal-clean
``exec python ...`` tail will fail this test loudly.
"""


import re
from pathlib import Path

import pytest

_DOCKERFILE = Path(__file__).resolve().parent.parent.parent / "docker" / "Dockerfile"
_ENTRYPOINT = Path(__file__).resolve().parent.parent.parent / "docker" / "entrypoint.sh"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def entrypoint() -> str:
    return _ENTRYPOINT.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# OCI image labels
# ---------------------------------------------------------------------------

# The minimum OCI image-spec annotation keys we expect downstream
# registries / scanners to consume. Reference:
# https://github.com/opencontainers/image-spec/blob/main/annotations.md
_REQUIRED_OCI_LABELS = (
    "org.opencontainers.image.title",
    "org.opencontainers.image.description",
    "org.opencontainers.image.source",
    "org.opencontainers.image.licenses",
    "org.opencontainers.image.version",
    "org.opencontainers.image.revision",
    "org.opencontainers.image.created",
    "org.opencontainers.image.base.name",
)


class TestOciLabels:
    @pytest.mark.parametrize("label", _REQUIRED_OCI_LABELS)
    def test_required_label_present(self, label: str, dockerfile: str) -> None:
        assert label in dockerfile, (
            f"OCI label {label!r} missing from docker/Dockerfile"
        )

    def test_version_and_created_are_build_args(self, dockerfile: str) -> None:
        """Version / revision / created must be ARG-backed so a release
        pipeline can stamp them without editing the Dockerfile. Baking
        a hard-coded ``version=1.2.3`` in the source is a release-
        process smell."""
        assert re.search(r"^ARG\s+VERSION\b", dockerfile, re.MULTILINE), (
            "VERSION must be an ARG so build tooling can stamp it."
        )
        assert re.search(r"^ARG\s+BUILD_DATE\b", dockerfile, re.MULTILINE)
        assert re.search(r"^ARG\s+VCS_REF\b", dockerfile, re.MULTILINE)
        for key in ("${VERSION}", "${BUILD_DATE}", "${VCS_REF}"):
            assert key in dockerfile, f"LABEL value must interpolate {key}"


# ---------------------------------------------------------------------------
# Non-root user
# ---------------------------------------------------------------------------


class TestNonRootRuntime:
    def test_user_directive_is_non_root(self, dockerfile: str) -> None:
        users = re.findall(r"^USER\s+(\S+)", dockerfile, re.MULTILINE)
        assert users, "Dockerfile must contain a USER directive"
        # The LAST USER directive determines the runtime user.
        assert users[-1] != "root", (
            "Runtime USER must not be root — create and switch to a "
            "dedicated non-root user (agent)."
        )
        assert users[-1] == "agent", (
            f"Expected final USER to be 'agent', got {users[-1]!r}"
        )

    def test_agent_user_is_uid_1000(self, dockerfile: str) -> None:
        """The ``agent`` UID must be stable at 1000 so bind-mounted
        volumes from the host match ownership without manual chown."""
        assert re.search(
            r"useradd\s+.*-u\s+1000\s+.*\bagent\b", dockerfile,
        ), "useradd must pin agent to UID 1000"


# ---------------------------------------------------------------------------
# HEALTHCHECK
# ---------------------------------------------------------------------------


class TestHealthcheck:
    def test_healthcheck_present(self, dockerfile: str) -> None:
        assert "HEALTHCHECK" in dockerfile

    def test_healthcheck_targets_liveness_not_readiness(
        self, dockerfile: str,
    ) -> None:
        """The HEALTHCHECK must target the liveness endpoint, not the
        readiness aggregator — a transient docker-daemon or upstream
        provider hiccup should not mark the container itself unhealthy
        (that would trigger an orchestrator restart)."""
        hc_line = next(
            (l for l in dockerfile.splitlines() if l.strip().startswith("CMD")
             and "localhost" in l),
            None,
        )
        assert hc_line is not None, "HEALTHCHECK CMD line not found"
        assert "/health" in hc_line, (
            f"HEALTHCHECK must target /health (liveness), got: {hc_line!r}"
        )
        assert "/ready" not in hc_line, (
            "HEALTHCHECK must NOT target /ready — readiness is for "
            "orchestrator-level traffic gating, not container health."
        )


# ---------------------------------------------------------------------------
# Signal-clean shutdown
# ---------------------------------------------------------------------------


class TestSignalCleanShutdown:
    def test_entrypoint_exec_replaces_shell(self, entrypoint: str) -> None:
        """``exec python ...`` as the last meaningful line means the
        Python process becomes PID 1 and receives SIGTERM directly.
        Without ``exec``, SIGTERM hits the bash parent and the Python
        child survives until the 10 s grace period elapses."""
        # Find the last non-comment, non-empty line and assert it begins
        # with ``exec ``.
        tail = [
            l.strip() for l in entrypoint.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        assert tail, "entrypoint.sh has no executable commands"
        last = tail[-1]
        assert last.startswith("exec "), (
            f"entrypoint.sh must end with an exec'd process so SIGTERM "
            f"reaches Python as PID 1; last command was: {last!r}"
        )

    def test_agent_service_installs_signal_handlers(self) -> None:
        """``docker/agent_service.py`` must register SIGTERM/SIGINT
        handlers so ``docker stop`` produces a clean exit instead of
        sending SIGKILL after the 10 s grace period."""
        svc = (Path(__file__).resolve().parent.parent.parent
               / "docker" / "agent_service.py").read_text(encoding="utf-8")
        assert "signal.signal(signal.SIGTERM" in svc
        assert "signal.signal(signal.SIGINT" in svc
        assert "server.shutdown()" in svc, (
            "Signal handler must call ThreadingHTTPServer.shutdown()"
            " so the accept loop exits cleanly."
        )

# === merged from tests/test_docker_security.py ===
"""Tests for Docker manager security and lifecycle."""


import pytest

from backend.infra.docker import _validate_name


class TestDockerManagerSecurity:
    """Verify docker_manager input validation and security flags."""

    def test_validate_name_accepts_valid(self):
        _validate_name("cua-environment", "container")
        _validate_name("cua-ubuntu:latest", "image")
        _validate_name("my_container.v2", "label")

    def test_validate_name_rejects_empty(self):
        with pytest.raises(ValueError):
            _validate_name("", "container")

    def test_validate_name_rejects_metacharacters(self):
        with pytest.raises(ValueError):
            _validate_name("name; rm -rf /", "container")

    def test_validate_name_rejects_spaces(self):
        with pytest.raises(ValueError):
            _validate_name("name with spaces", "container")

    def test_validate_name_rejects_long_names(self):
        with pytest.raises(ValueError):
            _validate_name("a" * 200, "container")

    def test_validate_name_rejects_leading_special(self):
        with pytest.raises(ValueError):
            _validate_name(".hidden", "container")
        with pytest.raises(ValueError):
            _validate_name("-dash", "container")

    def test_start_container_args_have_security_flags(self):
        """Verify the source code includes --security-opt and resource limits."""
        import inspect
        from backend.infra import docker as docker_manager
        # Flags live in the inner locked helper; inspect the whole module
        # so the assertion stays robust if the split changes again.
        source = inspect.getsource(docker_manager)
        assert "--security-opt=no-new-privileges:true" in source
        assert "--memory=4g" in source
        assert "--cpus=2" in source

