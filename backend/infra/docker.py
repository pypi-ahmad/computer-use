"""Docker container lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import secrets
import stat
import tempfile
import time

import httpx

from backend.infra.config import config

logger = logging.getLogger(__name__)

# Only allow safe characters in container/image names
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]*$")

# Serialize container lifecycle operations so concurrent start/stop/build
# calls (e.g. two UI tabs, or UI + WS client) can't race each other into
# ``docker rm -f`` against a partially-started container. All public
# lifecycle entry points must acquire this lock.
_LIFECYCLE_LOCK = asyncio.Lock()

# D-READY — most recent readiness observation, updated by
# :func:`_wait_for_service` and read by :func:`get_state` (and the
# ``/api/container/status`` REST endpoint). Plain dict instead of a
# dataclass so callers and tests can mutate or patch it cheaply.
#
# ``container``  — "running" | "stopped" | "starting"
# ``agent``      — "ready"   | "unready" | "unknown"
# ``last_health_error`` — short string describing the most recent
#                  failed ``/health`` probe, or None on success/idle.
_readiness_state: dict[str, str | None] = {
    "container": "stopped",
    "agent": "unknown",
    "last_health_error": None,
}


def _ensure_agent_token() -> str:
    """Return (and lazily generate) the shared secret used between host and
    the in-container agent service. The token is stored in the process
    environment so all engine clients and the spawned container pick it up.
    """
    token = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()
    if not token:
        token = secrets.token_urlsafe(32)
        os.environ["AGENT_SERVICE_TOKEN"] = token
    return token


def _write_token_env_file(token: str) -> str:
    """Write an env-file with the agent-service token, owner-read-only (0600).

    Returned path should be passed to ``docker run --env-file`` and
    ``os.unlink``-ed immediately after. Keeping the token out of the
    ``docker inspect`` metadata removes a common local-enumeration
    disclosure path.

    On Windows ``os.chmod(path, 0o600)`` only toggles the read-only bit
    — ACLs are NOT changed and other local accounts may still be able
    to read the file before it's unlinked. We log a warning so operators
    on Windows hosts can decide whether the ~1-second exposure window is
    acceptable; production deployments should run the backend on Linux.
    """
    fd, path = tempfile.mkstemp(prefix="cua-env-", suffix=".env")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 on POSIX
        if os.name == "nt":
            logger.warning(
                "AGENT_SERVICE_TOKEN env-file %s — Windows os.chmod cannot "
                "set ACLs; other local users may be able to read the file "
                "before it is unlinked. Run the backend on Linux for "
                "production deployments.",
                path,
            )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"AGENT_SERVICE_TOKEN={token}\n")
    except Exception:
        # Best-effort: if chmod/write failed, drop the file before
        # leaking the descriptor or a world-readable artefact.
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def _validate_name(name: str, label: str = "name") -> None:
    """Reject names containing shell metacharacters."""
    if not name or len(name) > 128 or not _SAFE_NAME_RE.match(name):
        raise ValueError(f"Invalid {label}: {name!r}")


async def _run(args: list[str]) -> tuple[int, str, str]:
    """Run a command as an explicit argument list (no shell interpretation)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


async def build_image() -> bool:
    """Build the CUA Docker image from docker/Dockerfile."""
    _validate_name(config.container_image, "container_image")
    async with _LIFECYCLE_LOCK:
        logger.info("Building Docker image: %s", config.container_image)
        rc, out, err = await _run(
            ["docker", "build", "-t", config.container_image, "-f", "docker/Dockerfile", "."]
        )
        if rc != 0:
            logger.error("Docker build failed: %s", err)
            return False
        logger.info("Docker image built successfully")
        return True


async def is_container_running(name: str | None = None) -> bool:
    """Return True if the named container is currently running."""
    container = name or config.container_name
    _validate_name(container, "container_name")
    rc, out, _ = await _run(
        ["docker", "ps", "--filter", f"name=^/{container}$", "--format", "{{.Names}}"]
    )
    return container in out


async def start_container(name: str | None = None) -> bool:
    """Start the CUA Docker container with Xvfb + agent service.

    Holds :data:`_LIFECYCLE_LOCK` for the entire checkâ†’inspectâ†’run
    sequence so concurrent callers don't ``docker rm -f`` each other's
    in-flight starts.
    """
    container = name or config.container_name
    _validate_name(container, "container_name")
    _validate_name(config.container_image, "container_image")

    async with _LIFECYCLE_LOCK:
        return await _start_container_locked(container)


async def _start_container_locked(container: str) -> bool:
    """Inner start routine. Caller must hold :data:`_LIFECYCLE_LOCK`."""
    # Check if container is running
    if await is_container_running(container):
        logger.info(
            "Container %s is already running; confirming agent service readiness",
            container,
        )
        return await _wait_for_service(container, already_running=True)

    # Check if container exists but is stopped
    rc, _, _ = await _run(["docker", "inspect", container])
    if rc == 0:
        logger.info("Container %s exists but is stopped. Starting...", container)
        rc, _, err = await _run(["docker", "start", container])
        if rc != 0:
            logger.error("Failed to start existing container: %s", err)
            # Try to remove and recreate if start fails
            await _run(["docker", "rm", "-f", container])
        else:
            logger.info("Existing container started")
            return await _wait_for_service(container)

    # Remove any stopped container with the same name (if inspect failed or start failed)
    await _run(["docker", "rm", "-f", container])

    # C13: the AGENT_SERVICE_TOKEN used to be passed via ``-e``, which
    # put the secret into ``docker inspect`` output. We now write it to
    # a 0600 env-file that's unlinked immediately after ``docker run``
    # returns, so the only persistent store of the token is the
    # container's own env (readable via ``docker exec`` by anyone with
    # Docker socket access — still not public, but no longer indexed
    # into the daemon's inspect metadata).
    env_file_path = _write_token_env_file(_ensure_agent_token())
    args = [
        "docker", "run", "-d",
        "--name", container,
        "-e", "DISPLAY=:99",
        "-e", f"SCREEN_WIDTH={config.screen_width}",
        "-e", f"SCREEN_HEIGHT={config.screen_height}",
        "-e", f"AGENT_SERVICE_PORT={config.agent_service_port}",
        "--env-file", env_file_path,
        "-p", "127.0.0.1:5900:5900",
        "-p", "127.0.0.1:6080:6080",
        "-p", f"127.0.0.1:{config.agent_service_port}:{config.agent_service_port}",
        "-p", "127.0.0.1:9223:9223",
        "--shm-size=2g",
        "--security-opt=no-new-privileges:true",
        "--memory=4g",
        "--cpus=2",
        config.container_image,
    ]
    logger.info("Starting container: %s", container)
    try:
        rc, out, err = await _run(args)
    finally:
        # Best-effort cleanup: token file is 0600 so only this user can
        # read it, and we remove it immediately to minimise the window
        # where a disk snapshot could capture it.
        try:
            os.unlink(env_file_path)
        except OSError:
            logger.debug("Could not remove env-file %s", env_file_path, exc_info=True)

    if rc != 0:
        logger.error("Failed to start container: %s", err)
        _readiness_state["container"] = "stopped"
        _readiness_state["agent"] = "unknown"
        _readiness_state["last_health_error"] = None
        return False

    ready = await _wait_for_service(container)
    if not ready:
        # Tear down the half-started container so the next attempt starts clean
        logger.warning("Container %s never became ready; removing", container)
        rm_rc, _, rm_err = await _run(["docker", "rm", "-f", container])
        if rm_rc != 0:
            logger.warning(
                "Could not remove unready container %s: %s",
                container,
                rm_err.strip() or "docker rm failed",
            )
            _readiness_state["container"] = (
                "running" if await is_container_running(container) else "stopped"
            )
        else:
            _readiness_state["container"] = "stopped"
        return False
    return True


async def _wait_for_service(container: str, *, already_running: bool = False) -> bool:
    """Wait for the in-container agent service's ``/health`` to return 200.

    D-READY — previously this function returned ``True`` whenever the
    container process was still running after the poll loop, even if
    the agent service never answered. That produced "session started"
    in the UI followed by cryptic screenshot / action failures. The
    new contract is strict:

    * ``True``  — ``/health`` returned 200 within the budget.
    * ``False`` — budget exhausted; caller MUST treat the sandbox as
      unusable. The container process staying alive is NOT a positive
      signal on its own.

    Budget is :attr:`config.container_ready_timeout` (default 30 s).
    Between attempts we sleep for an exponentially-growing delay with
    0.5–1.0× jitter so parallel starts don't synchronise their probes.
    Every failure updates :data:`_readiness_state` so :func:`get_state`
    can report the most recent health error to operators.

    When *already_running* is True, the caller is confirming readiness
    for an existing container rather than waiting for a fresh boot.
    That path stays idempotent (no Docker commands are issued) but no
    longer conflates "container process exists" with "agent is ready".
    """
    deadline = time.monotonic() + config.container_ready_timeout
    delay = config.container_ready_poll_base
    attempt = 0
    last_error: str | None = None
    _readiness_state["container"] = "running" if already_running else "starting"
    _readiness_state["agent"] = "unknown"
    _readiness_state["last_health_error"] = None

    logger.info(
        "%s container %s agent service (budget=%.1fs)",
        "Confirming" if already_running else "Waiting for",
        container,
        config.container_ready_timeout,
    )
    while True:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{config.agent_service_url}/health")
                if resp.status_code == 200:
                    logger.info(
                        "Container %s is ready (agent service up, attempt=%d)",
                        container, attempt,
                    )
                    _readiness_state["container"] = "running"
                    _readiness_state["agent"] = "ready"
                    _readiness_state["last_health_error"] = None
                    return True
                last_error = f"HTTP {resp.status_code}"
        except Exception as exc:  # httpx.ConnectError, timeouts, DNS, etc.
            last_error = f"{type(exc).__name__}: {exc}"
        _readiness_state["last_health_error"] = last_error

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Exponential backoff with 0.5–1.0× jitter, capped at the
        # remaining budget so we don't oversleep past the deadline.
        sleep_for = min(
            delay * (0.5 + random.random() * 0.5),
            config.container_ready_poll_cap,
            remaining,
        )
        logger.debug(
            "Agent service not ready (attempt=%d, err=%s); retrying in %.2fs",
            attempt, last_error, sleep_for,
        )
        await asyncio.sleep(sleep_for)
        delay = min(delay * 2.0, config.container_ready_poll_cap)

    # Budget exhausted. Record the reason and report NOT ready — the
    # caller (``_start_container_locked``) tears down the half-started
    # container so the next attempt starts clean.
    running = await is_container_running(container)
    _readiness_state["container"] = "running" if running else "stopped"
    _readiness_state["agent"] = "unready"
    _readiness_state["last_health_error"] = last_error
    logger.error(
        "Container %s agent service failed health check within %.1fs "
        "(attempts=%d, last_error=%s, container_running=%s)",
        container, config.container_ready_timeout, attempt, last_error, running,
    )
    return False


async def stop_container(name: str | None = None) -> bool:
    """Force-remove the CUA Docker container."""
    container = name or config.container_name
    _validate_name(container, "container_name")
    async with _LIFECYCLE_LOCK:
        logger.info("Stopping container: %s", container)
        rc, _, err = await _run(["docker", "rm", "-f", container])
        if rc != 0:
            logger.error("Failed to stop container: %s", err)
            return False
        _readiness_state["container"] = "stopped"
        _readiness_state["agent"] = "unknown"
        _readiness_state["last_health_error"] = None
        return True


async def get_container_status(name: str | None = None) -> dict:
    """Return a dict with container running state and service health.

    The ``ready`` key is the preferred readiness signal for callers
    that gate new work (session creation, action dispatch) on the
    sandbox being usable. It is True only when the container is
    running AND ``/health`` currently returns 200. The legacy
    ``running`` / ``agent_service`` keys are kept for backward
    compatibility with the existing frontend status card.
    """
    container = name or config.container_name
    running = await is_container_running(container)

    service_healthy = False
    last_error: str | None = None
    if running:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{config.agent_service_url}/health")
                service_healthy = resp.status_code == 200
                if not service_healthy:
                    last_error = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    # Reflect the live probe into the cached readiness state so
    # ``get_state()`` stays consistent with what this endpoint just
    # returned. Do NOT overwrite ``last_health_error`` when the probe
    # succeeded — preserve the last-known failure context while the
    # service is healthy is unhelpful, so clear it.
    _readiness_state["container"] = "running" if running else "stopped"
    if running and service_healthy:
        _readiness_state["agent"] = "ready"
        _readiness_state["last_health_error"] = None
    elif running:
        _readiness_state["agent"] = "unready"
        _readiness_state["last_health_error"] = last_error
    else:
        _readiness_state["agent"] = "unknown"

    return {
        "name": container,
        "running": running,
        "ready": bool(running and service_healthy),
        "image": config.container_image,
        "agent_service": service_healthy,
        "last_health_error": last_error,
    }


def get_state() -> dict[str, str | None]:
    """Return the most recent container/agent readiness snapshot.

    Preferred accessor for server code that needs to decide whether
    the sandbox is usable *right now* without paying for a fresh
    subprocess + HTTP round-trip. Values are updated by
    :func:`_wait_for_service` during startup and by
    :func:`get_container_status` on every status poll.

    Shape::

        {
            "container": "running" | "stopped" | "starting",
            "agent":     "ready"   | "unready" | "unknown",
            "last_health_error": str | None,
        }
    """
    # Return a shallow copy so callers can't mutate module state.
    return dict(_readiness_state)
