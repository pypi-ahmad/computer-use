"""Thin wrappers around the in-container agent-service screenshot endpoints."""

from __future__ import annotations

import asyncio
import base64
import logging
import os

import httpx

from backend.infra.config import config

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _agent_headers() -> dict[str, str] | None:
    token = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()
    if not token:
        return None
    return {"X-Agent-Token": token}


def _get_client() -> httpx.AsyncClient:
    """Return a reusable agent-service HTTP client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def _fallback_docker_screenshot() -> str:
    """Grab a screenshot with docker exec + scrot when the service is unhealthy."""
    path = "/tmp/cu_screenshot.png"
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        "-e",
        "DISPLAY=:99",
        config.container_name,
        "scrot",
        "-z",
        "-o",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    proc_read = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        config.container_name,
        "cat",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc_read.communicate()
    if proc_read.returncode != 0 or not stdout:
        raise RuntimeError(
            f"Fallback screenshot failed: {stderr.decode(errors='replace')}"
        )
    return base64.b64encode(stdout).decode("ascii")


async def check_service_health() -> bool:
    """Return True when the desktop agent-service health endpoint is reachable."""
    try:
        response = await _get_client().get(
            f"{config.agent_service_url}/health",
            headers=_agent_headers(),
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("status") == "ok"
    except Exception:
        return False


async def capture_screenshot(*, mode: str = "desktop") -> str:
    """Fetch the latest screenshot as base64, with docker fallback on 5xx/unreachable."""
    try:
        response = await _get_client().get(
            f"{config.agent_service_url}/screenshot",
            params={"mode": mode},
            headers=_agent_headers(),
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Agent service screenshot request failed (%s), falling back to docker exec",
            exc,
        )
        return await _fallback_docker_screenshot()

    if response.status_code >= 500:
        logger.warning(
            "Agent service screenshot returned %s, falling back to docker exec",
            response.status_code,
        )
        return await _fallback_docker_screenshot()

    response.raise_for_status()
    payload = response.json()
    screenshot = payload.get("screenshot")
    if not isinstance(screenshot, str) or not screenshot:
        raise RuntimeError("Agent service returned no screenshot payload")
    return screenshot
