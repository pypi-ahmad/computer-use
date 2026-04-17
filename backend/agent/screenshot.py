"""Screenshot capture via the internal agent service.

Calls the HTTP API exposed by the agent service running inside the
container. The supported runtime path is desktop capture via scrot.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os

import httpx

from backend.config import config

logger = logging.getLogger(__name__)

# Reusable async client
_http_client: httpx.AsyncClient | None = None


def _auth_headers() -> dict[str, str]:
    """Return the shared-secret header for authenticated agent_service calls."""
    token = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()
    return {"X-Agent-Token": token} if token else {}


def _get_client() -> httpx.AsyncClient:
    """Return or create the module-level reusable httpx client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=15.0)
    return _http_client


async def capture_screenshot(mode: str = "desktop") -> str:
    """Capture a PNG screenshot and return base64 string.

    The screenshot comes from the in-container agent service.

    Args:
        mode: 'desktop'. Browser mode is no longer supported.

    Returns:
        Base64-encoded PNG string.
    """
    # ── Default: screenshot via agent_service ─────────────────────────────
    url = f"{config.agent_service_url}/screenshot?mode={mode}"
    client = _get_client()

    try:
        resp = await client.get(url, headers=_auth_headers())
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise RuntimeError(data["error"])

        b64 = data["screenshot"]
        method = data.get("method", "unknown")
        logger.debug("Screenshot captured via %s (%d chars)", method, len(b64))
        return b64

    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("Agent service unreachable, falling back to docker exec: %s", e)
        return await _fallback_docker_screenshot()


async def _fallback_docker_screenshot() -> str:
    """Fallback: grab screenshot via docker exec + scrot."""
    name = config.container_name

    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", name, "scrot", "-z", "-o", "/tmp/screenshot.png",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", name, "import", "-window", "root", "/tmp/screenshot.png",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Screenshot capture failed: {stderr.decode().strip()}")

    proc_read = await asyncio.create_subprocess_exec(
        "docker", "exec", name, "cat", "/tmp/screenshot.png",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc_read.communicate()

    if proc_read.returncode != 0 or not stdout:
        raise RuntimeError(f"Failed to read screenshot: {stderr.decode().strip()}")

    b64 = base64.b64encode(stdout).decode("ascii")
    logger.info("Screenshot via fallback: %d bytes", len(stdout))
    return b64


async def check_service_health() -> bool:
    """Check if the internal agent service is responsive."""
    url = f"{config.agent_service_url}/health"
    client = _get_client()
    try:
        resp = await client.get(url, timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False
