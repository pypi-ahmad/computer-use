"""Docker container lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import stat
import tempfile

import httpx

from backend.config import config

logger = logging.getLogger(__name__)

# Only allow safe characters in container/image names
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]*$")

# Serialize container lifecycle operations so concurrent start/stop/build
# calls (e.g. two UI tabs, or UI + WS client) can't race each other into
# ``docker rm -f`` against a partially-started container. All public
# lifecycle entry points must acquire this lock.
_LIFECYCLE_LOCK = asyncio.Lock()


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
        logger.info("Container %s is already running", container)
        return True

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
        return False

    ready = await _wait_for_service(container)
    if not ready:
        # Tear down the half-started container so the next attempt starts clean
        logger.warning("Container %s never became ready; removing", container)
        await _run(["docker", "rm", "-f", container])
        return False
    return True


async def _wait_for_service(container: str) -> bool:
    """Wait for the agent service to become ready."""
    logger.info("Waiting for container environment...")
    for attempt in range(10):
        await asyncio.sleep(2)
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{config.agent_service_url}/health")
                if resp.status_code == 200:
                    logger.info("Container %s is ready (agent service up)", container)
                    return True
        except Exception:
            pass
        logger.debug("Waiting for agent service... (attempt %d)", attempt + 1)

    # Even if agent service isn't responding, container may still be usable
    if await is_container_running(container):
        logger.warning("Container running but agent service not confirmed healthy")
        return True

    logger.error("Container failed to become ready")
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
        return True


async def get_container_status(name: str | None = None) -> dict:
    """Return a dict with container running state and service health."""
    container = name or config.container_name
    running = await is_container_running(container)

    service_healthy = False
    if running:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{config.agent_service_url}/health")
                service_healthy = resp.status_code == 200
        except Exception:
            pass

    return {
        "name": container,
        "running": running,
        "image": config.container_image,
        "agent_service": service_healthy,
    }
