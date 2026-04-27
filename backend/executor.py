"""Desktop Computer Use executor.

This module owns screenshot capture plus click/type/scroll/key/action
dispatch against the sandbox agent_service. Provider clients depend on
the small ``ActionExecutor`` protocol; the engine package re-exports
these names for older imports.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import httpx

from backend.infra.config import config as _app_config

logger = logging.getLogger(__name__)

GEMINI_NORMALIZED_MAX = 1000
DEFAULT_SCREEN_WIDTH = 1440
DEFAULT_SCREEN_HEIGHT = 900


def denormalize_x(x: int, screen_width: int = DEFAULT_SCREEN_WIDTH) -> int:
    """Convert Gemini normalized x (0-999) to a pixel coordinate."""
    return int(x / GEMINI_NORMALIZED_MAX * screen_width)


def denormalize_y(y: int, screen_height: int = DEFAULT_SCREEN_HEIGHT) -> int:
    """Convert Gemini normalized y (0-999) to a pixel coordinate."""
    return int(y / GEMINI_NORMALIZED_MAX * screen_height)


_XDOTOOL_SPECIAL_KEYS: frozenset[str] = frozenset({
    "return", "enter", "backspace", "tab", "escape", "delete",
    "space", "home", "end", "insert", "pause",
    "left", "right", "up", "down",
    "page_up", "page_down", "pageup", "pagedown",
    "print", "scroll_lock", "num_lock", "caps_lock",
    "super", "ctrl", "alt", "shift",
    *(f"f{i}" for i in range(1, 25)),
})

_ALLOWED_KEY_PUNCTUATION: frozenset[str] = frozenset({
    "minus", "plus", "equal", "comma", "period", "slash", "backslash",
    "semicolon", "apostrophe", "grave", "bracketleft", "bracketright",
    "underscore", "asterisk", "at", "hash", "dollar", "percent",
    "ampersand", "question", "exclam", "colon", "parenleft",
    "parenright", "braceleft", "braceright", "quotedbl",
})


def _is_allowed_key_token(token: str) -> bool:
    """Return True if *token* is an allowlisted xdotool keysym."""
    t = token.strip()
    if not t:
        return False
    lower = t.lower()
    if len(t) == 1 and (t.isalnum() or t in "-=[];',./`\\"):
        return True
    if lower in _XDOTOOL_SPECIAL_KEYS:
        return True
    if lower in _ALLOWED_KEY_PUNCTUATION:
        return True
    if lower in {"menu", "prtsc", "prtscr", "printscreen", "capslock", "numlock"}:
        return True
    return False


class SafetyDecision(str, Enum):
    """Gemini safety-gate verdict attached to CU actions."""

    ALLOWED = "allowed"
    REQUIRE_CONFIRMATION = "require_confirmation"
    BLOCKED = "blocked"


@dataclass
class CUActionResult:
    """Result of executing a single CU action."""

    name: str
    success: bool = True
    error: str | None = None
    safety_decision: SafetyDecision | None = None
    safety_explanation: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ActionExecutor(Protocol):
    """Interface implemented by supported computer-use executors."""

    screen_width: int
    screen_height: int

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult: ...
    async def capture_screenshot(self) -> bytes: ...
    def get_current_url(self) -> str: ...


_SHARED_HTTPX_CLIENTS: dict[str, httpx.AsyncClient] = {}
_SHARED_HTTPX_LOCK = asyncio.Lock()
_SCREENSHOT_CLIENT: httpx.AsyncClient | None = None


def _agent_headers() -> dict[str, str] | None:
    token = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()
    if not token:
        return None
    return {"X-Agent-Token": token}


def _get_client() -> httpx.AsyncClient:
    """Return a reusable agent-service HTTP client for lightweight probes."""
    global _SCREENSHOT_CLIENT
    if _SCREENSHOT_CLIENT is None or _SCREENSHOT_CLIENT.is_closed:
        _SCREENSHOT_CLIENT = httpx.AsyncClient(timeout=30.0)
    return _SCREENSHOT_CLIENT


async def _fallback_docker_screenshot() -> str:
    """Grab a screenshot with docker exec + scrot when the service is unhealthy."""
    path = "/tmp/cu_screenshot.png"
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        "-e",
        "DISPLAY=:99",
        _app_config.container_name,
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
        _app_config.container_name,
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
            f"{_app_config.agent_service_url}/health",
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
            f"{_app_config.agent_service_url}/screenshot",
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


async def close_shared_executor_clients() -> None:
    """Close every shared httpx client. Wire into FastAPI shutdown."""
    async with _SHARED_HTTPX_LOCK:
        for url, client in list(_SHARED_HTTPX_CLIENTS.items()):
            try:
                if not client.is_closed:
                    await client.aclose()
            except Exception:
                logger.debug("Failed closing shared httpx client for %s", url)
            _SHARED_HTTPX_CLIENTS.pop(url, None)


class DesktopExecutor:
    """Translate CU actions into agent_service ``/action`` calls."""

    def __init__(
        self,
        screen_width: int = DEFAULT_SCREEN_WIDTH,
        screen_height: int = DEFAULT_SCREEN_HEIGHT,
        normalize_coords: bool = True,
        agent_service_url: str = "http://127.0.0.1:9222",
        container_name: str = "cua-environment",
    ):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._normalize = normalize_coords
        self._service_url = agent_service_url
        self._container = container_name
        self._current_action_id: str | None = None
        self._current_action_substep: int = 0

    def _px(self, x: int, y: int) -> tuple[int, int]:
        """Convert raw coordinates to pixel values, denormalizing if needed."""
        if self._normalize:
            return denormalize_x(x, self.screen_width), denormalize_y(y, self.screen_height)
        return x, y

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the per-service-URL shared httpx client."""
        async with _SHARED_HTTPX_LOCK:
            client = _SHARED_HTTPX_CLIENTS.get(self._service_url)
            if client is None or client.is_closed:
                client = httpx.AsyncClient(timeout=15.0)
                _SHARED_HTTPX_CLIENTS[self._service_url] = client
            return client

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        """Return the shared-secret header for authenticated agent_service calls."""
        token = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()
        return {"X-Agent-Token": token} if token else {}

    async def _post_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an action to the agent_service and return the JSON result."""
        client = await self._get_client()
        final_payload = dict(payload)
        if self._current_action_id:
            final_payload["action_id"] = f"{self._current_action_id}:{self._current_action_substep}"
            self._current_action_substep += 1
        resp = await client.post(
            f"{self._service_url}/action",
            json=final_payload,
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        """No-op: shared httpx clients are closed at app shutdown."""
        return None

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult:
        """Map a CU action to the agent_service ``/action`` endpoint."""
        handler = getattr(self, f"_act_{name}", None)
        if handler is None:
            return CUActionResult(
                name=name, success=False,
                error=f"Unimplemented desktop action: {name}",
            )
        try:
            handler_args = dict(args or {})
            previous_action_id = self._current_action_id
            previous_substep = self._current_action_substep
            self._current_action_id = str(handler_args.pop("action_id", "") or "") or None
            self._current_action_substep = 0
            try:
                extra = await handler(handler_args) or {}
            finally:
                self._current_action_id = previous_action_id
                self._current_action_substep = previous_substep
            if isinstance(extra, dict) and extra.get("success") is False:
                return CUActionResult(
                    name=name,
                    success=False,
                    error=extra.get("message", "Action failed"),
                    extra=extra,
                )
            await asyncio.sleep(_app_config.ui_settle_delay)
            return CUActionResult(name=name, success=True, extra=extra)
        except Exception as exc:
            if _app_config.debug or os.getenv("CUA_DEBUG_TB") == "1":
                logger.error("DesktopExecutor %s failed: %s", name, exc, exc_info=True)
            else:
                logger.error(
                    "DesktopExecutor %s failed: %s: %s",
                    name,
                    type(exc).__name__,
                    exc,
                )
            return CUActionResult(name=name, success=False, error=str(exc))

    async def _act_click_at(self, a: dict) -> dict:
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_double_click(self, a: dict) -> dict:
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "double_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_right_click(self, a: dict) -> dict:
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "right_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_middle_click(self, a: dict) -> dict:
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "middle_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_triple_click(self, a: dict) -> dict:
        px, py = self._px(a["x"], a["y"])
        await self._post_action({
            "action": "double_click", "coordinates": [px, py], "mode": "desktop",
        })
        result = await self._post_action({
            "action": "click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_hover_at(self, a: dict) -> dict:
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "hover", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_move(self, a: dict) -> dict:
        return await self._act_hover_at(a)

    async def _act_type_text_at(self, a: dict) -> dict:
        px, py = self._px(a["x"], a["y"])
        text = a["text"]
        press_enter = a.get("press_enter", True)
        clear_before = a.get("clear_before_typing", True)
        await self._post_action({
            "action": "click", "coordinates": [px, py], "mode": "desktop",
        })
        if clear_before:
            await self._post_action({
                "action": "hotkey", "text": "ctrl+a", "mode": "desktop",
            })
            await self._post_action({
                "action": "key", "text": "BackSpace", "mode": "desktop",
            })
        await self._post_action({
            "action": "type", "text": text, "mode": "desktop",
        })
        if press_enter:
            await self._post_action({
                "action": "key", "text": "Return", "mode": "desktop",
            })
        return {"pixel_x": px, "pixel_y": py, "text": text}

    async def _act_key_combination(self, a: dict) -> dict:
        keys = a["keys"]
        xdo_keys = (
            keys.replace("Control", "ctrl")
            .replace("Alt", "alt")
            .replace("Shift", "shift")
            .replace("Meta", "super")
        )
        normalized = []
        for part in xdo_keys.split("+"):
            stripped = part.strip()
            if len(stripped) == 1 and stripped.isalpha():
                normalized.append(stripped.lower())
            elif stripped.lower() in _XDOTOOL_SPECIAL_KEYS:
                normalized.append(stripped)
            else:
                normalized.append(stripped)
        for part in normalized:
            if not _is_allowed_key_token(part):
                logger.warning("Rejected disallowed key token: %r (full combo=%r)", part, keys)
                return {
                    "success": False,
                    "message": f"Disallowed key token: {part!r}",
                }
        await self._post_action({
            "action": "key", "text": "+".join(normalized), "mode": "desktop",
        })
        return {"keys": keys}

    async def _act_scroll_document(self, a: dict) -> dict:
        direction = a["direction"]
        await self._post_action({
            "action": "scroll", "text": direction, "mode": "desktop",
        })
        return {"direction": direction}

    async def _act_left_mouse_down(self, a: dict) -> dict:
        return await self._post_action({"action": "left_mouse_down", "mode": "desktop"})

    async def _act_left_mouse_up(self, a: dict) -> dict:
        return await self._post_action({"action": "left_mouse_up", "mode": "desktop"})

    async def _act_hold_key(self, a: dict) -> dict:
        key = str(a.get("key", "")).strip()
        if "+" in key or not _is_allowed_key_token(key):
            logger.warning("Rejected disallowed hold_key token: %r", key)
            return {
                "success": False,
                "message": f"Disallowed key token: {key!r}",
            }
        duration = min(max(float(a.get("duration", 1)), 0.0), 10.0)
        await self._post_action({"action": "keydown", "text": key, "mode": "desktop"})
        await asyncio.sleep(duration)
        result = await self._post_action({
            "action": "keyup", "text": key, "mode": "desktop",
        })
        return {"key": key, "duration": duration, **result}

    async def _act_scroll_at(self, a: dict) -> dict:
        px, py = self._px(a["x"], a["y"])
        direction = a["direction"]
        await self._post_action({
            "action": "scroll",
            "coordinates": [px, py],
            "text": direction,
            "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, "direction": direction}

    async def _act_drag_and_drop(self, a: dict) -> dict:
        sx, sy = self._px(a["x"], a["y"])
        dx, dy = self._px(a["destination_x"], a["destination_y"])
        await self._post_action({
            "action": "drag", "coordinates": [sx, sy, dx, dy], "mode": "desktop",
        })
        return {"from": (sx, sy), "to": (dx, dy)}

    async def _act_navigate(self, a: dict) -> dict:
        url = a["url"]
        await self._post_action({
            "action": "open_url", "text": url, "mode": "desktop",
        })
        return {"url": url}

    async def _act_open_web_browser(self, a: dict) -> dict:
        await self._post_action({
            "action": "open_url", "text": "https://www.google.com", "mode": "desktop",
        })
        return {}

    async def _act_wait_5_seconds(self, a: dict) -> dict:
        await asyncio.sleep(_app_config.post_action_screenshot_delay)
        return {}

    async def _act_zoom(self, a: dict) -> dict:
        region = a.get("region") or []
        if len(region) != 4:
            return {"success": False, "message": "zoom requires region=[x1,y1,x2,y2]"}
        result = await self._post_action({
            "action": "zoom",
            "coordinates": [int(region[0]), int(region[1]), int(region[2]), int(region[3])],
            "mode": "desktop",
        })
        extra: dict[str, Any] = {
            "region": [int(region[0]), int(region[1]), int(region[2]), int(region[3])]
        }
        if isinstance(result, dict):
            extra.update({k: v for k, v in result.items() if k != "screenshot"})
            b64 = result.get("screenshot")
            if b64:
                try:
                    extra["image_bytes"] = base64.b64decode(b64)
                except Exception:
                    pass
        return extra

    async def _act_go_back(self, a: dict) -> dict:
        await self._post_action({
            "action": "key", "text": "alt+Left", "mode": "desktop",
        })
        return {}

    async def _act_go_forward(self, a: dict) -> dict:
        await self._post_action({
            "action": "key", "text": "alt+Right", "mode": "desktop",
        })
        return {}

    async def _act_type_at_cursor(self, a: dict) -> dict:
        text = a["text"]
        press_enter = a.get("press_enter", False)
        await self._post_action({"action": "type", "text": text, "mode": "desktop"})
        if press_enter:
            await self._post_action({
                "action": "key", "text": "Return", "mode": "desktop",
            })
        return {"text": text}

    async def _act_search(self, a: dict) -> dict:
        await self._post_action({
            "action": "open_url", "text": "https://www.google.com", "mode": "desktop",
        })
        return {}

    async def capture_screenshot(self) -> bytes:
        """Capture a screenshot via the agent_service, with docker exec fallback."""
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self._service_url}/screenshot",
                params={"mode": "desktop"},
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return base64.b64decode(data["screenshot"])
        except Exception as exc:
            logger.warning(
                "Agent service screenshot failed (%s), falling back to docker exec",
                exc,
            )
            return await self._fallback_screenshot()

    async def _fallback_screenshot(self) -> bytes:
        """Grab a screenshot via ``docker exec scrot`` as last resort."""
        path = "/tmp/cu_screenshot.png"
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec",
            "-e", "DISPLAY=:99",
            self._container, "scrot", "-z", "-o", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        proc_read = await asyncio.create_subprocess_exec(
            "docker", "exec", self._container, "cat", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc_read.communicate()
        if proc_read.returncode != 0 or not stdout:
            raise RuntimeError(
                f"Fallback screenshot failed: {stderr.decode(errors='replace')}"
            )
        return stdout

    def get_current_url(self) -> str:
        """Desktop executor has no URL context."""
        return ""
