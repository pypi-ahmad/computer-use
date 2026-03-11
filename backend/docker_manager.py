"""Docker container lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from backend.config import config

logger = logging.getLogger(__name__)

# Only allow safe characters in container/image names
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]*$")


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
    """Start the CUA Docker container with Xvfb + agent service."""
    container = name or config.container_name
    _validate_name(container, "container_name")
    _validate_name(config.container_image, "container_image")

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
            # Container started successfully, skip creation
            logger.info("Existing container started")
            return await _wait_for_service(container)

    # Remove any stopped container with the same name (if inspect failed or start failed)
    await _run(["docker", "rm", "-f", container])

    args = [
        "docker", "run", "-d",
        "--name", container,
        "-e", "DISPLAY=:99",
        "-e", f"SCREEN_WIDTH={config.screen_width}",
        "-e", f"SCREEN_HEIGHT={config.screen_height}",
        "-e", f"AGENT_SERVICE_PORT={config.agent_service_port}",
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
    rc, out, err = await _run(args)

    if rc != 0:
        logger.error("Failed to start container: %s", err)
        return False

    return await _wait_for_service(container)


async def _wait_for_service(container: str) -> bool:
    """Wait for the agent service to become ready."""
    logger.info("Waiting for container environment...")
    for attempt in range(10):
        await asyncio.sleep(2)
        # Check if agent service is responding
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

    # Check agent service health if running
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
