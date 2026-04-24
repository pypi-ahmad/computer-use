"""SC8 — production-hardening source-scan regression guards for the Dockerfile.

We cannot run ``docker build`` in the test sandbox, so each invariant
below is asserted by reading the Dockerfile / entrypoint.sh as source
text. A future refactor that strips an OCI label or the signal-clean
``exec python ...`` tail will fail this test loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCKERFILE = Path(__file__).resolve().parent.parent / "docker" / "Dockerfile"
_ENTRYPOINT = Path(__file__).resolve().parent.parent / "docker" / "entrypoint.sh"


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
        svc = (Path(__file__).resolve().parent.parent
               / "docker" / "agent_service.py").read_text(encoding="utf-8")
        assert "signal.signal(signal.SIGTERM" in svc
        assert "signal.signal(signal.SIGINT" in svc
        assert "server.shutdown()" in svc, (
            "Signal handler must call ThreadingHTTPServer.shutdown()"
            " so the accept loop exits cleanly."
        )
