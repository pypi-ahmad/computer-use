"""Unified Computer Use engine — native CU protocol for supported providers.

Replaces ad-hoc text-parsing of model responses with the structured
``computer_use`` tool protocol that both Gemini 3 Flash and Claude 4.6
Sonnet support natively.

Architecture
~~~~~~~~~~~~
::

    ComputerUseEngine
    ├── GeminiCUClient   (google-genai  types.Tool(computer_use=...))
    ├── ClaudeCUClient   (anthropic     computer_2025XXYY tool, auto-detected)
    └── Executors
        └── DesktopExecutor     (desktop via agent_service HTTP API → xdotool + scrot)

Usage::

    engine = ComputerUseEngine(
        provider=Provider.GEMINI,
        api_key="...",
        environment=Environment.DESKTOP,
    )
    result = await engine.execute_task("Search for ...")
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

import httpx

from backend.config import config as _app_config
from backend._models_loader import load_allowed_models_json as _load_allowed_models_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup_claude_cu_config(model_id: str) -> tuple[str | None, str | None]:
    """Look up cu_tool_version / cu_betas from allowed_models.json.

    Returns (tool_version, beta_flag) or (None, None) if not found,
    letting ClaudeCUClient fall back to auto-detection.
    """
    try:
        for m in _load_allowed_models_json():
            if m.get("model_id") == model_id and m.get("provider") == "anthropic":
                tv = m.get("cu_tool_version")
                betas = m.get("cu_betas")
                bf = betas[0] if isinstance(betas, list) and betas else None
                if tv and bf:
                    return tv, bf
    except Exception:
        pass
    return None, None


def _to_plain_dict(value: Any) -> dict[str, Any]:
    """Convert SDK objects or typed dict-like values into a plain dict."""
    def _to_plain_value(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: _to_plain_value(value) for key, value in item.items()}
        if isinstance(item, list):
            return [_to_plain_value(value) for value in item]
        if isinstance(item, tuple):
            return [_to_plain_value(value) for value in item]
        if hasattr(item, "model_dump"):
            return _to_plain_value(item.model_dump())
        if hasattr(item, "dict"):
            return _to_plain_value(item.dict())
        if hasattr(item, "__dict__"):
            return {
                key: _to_plain_value(value) for key, value in vars(item).items()
                if not key.startswith("_")
            }
        return item

    plain = _to_plain_value(value)
    if isinstance(plain, dict):
        return plain
    return {}


def _extract_openai_output_text(output_items: list[Any]) -> str:
    """Collect assistant text blocks from a Responses API output list."""
    text_parts: list[str] = []
    for item in output_items:
        if getattr(item, "type", None) != "message":
            continue
        for content_part in getattr(item, "content", []) or []:
            if getattr(content_part, "type", None) != "output_text":
                continue
            text = getattr(content_part, "text", None)
            if text:
                text_parts.append(str(text).strip())
    return "\n\n".join(part for part in text_parts if part)


def _build_openai_computer_call_output(
    call_id: str,
    screenshot_b64: str,
    *,
    acknowledged_safety_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the Responses API follow-up item for a computer call."""
    payload: dict[str, Any] = {
        "type": "computer_call_output",
        "call_id": call_id,
        "output": {
            "type": "computer_screenshot",
            "image_url": f"data:image/png;base64,{screenshot_b64}",
            "detail": "original",
        },
    }
    if acknowledged_safety_checks:
        payload["acknowledged_safety_checks"] = acknowledged_safety_checks
    return payload


def _sanitize_openai_response_item_for_replay(item: Any) -> dict[str, Any]:
    """Strip output-only fields before replaying Responses items statelessly."""
    item_dict = _to_plain_dict(item)
    item_type = str(item_dict.get("type", ""))

    if item_type == "message":
        sanitized: dict[str, Any] = {
            "type": "message",
            "role": item_dict.get("role", "assistant"),
            "content": [],
        }
        for part in item_dict.get("content", []) or []:
            if not isinstance(part, dict):
                continue
            part_dict = dict(part)
            part_dict.pop("annotations", None)
            part_dict.pop("logprobs", None)
            sanitized["content"].append(part_dict)
        if item_dict.get("phase") is not None:
            sanitized["phase"] = item_dict["phase"]
        return sanitized

    if item_type == "computer_call":
        sanitized = {
            "type": "computer_call",
            "call_id": item_dict.get("call_id"),
        }
        if item_dict.get("action") is not None:
            sanitized["action"] = item_dict["action"]
        if item_dict.get("actions") is not None:
            sanitized["actions"] = item_dict["actions"]
        return sanitized

    item_dict.pop("status", None)
    item_dict.pop("pending_safety_checks", None)
    if isinstance(item_dict.get("content"), list):
        for part in item_dict["content"]:
            if isinstance(part, dict):
                part.pop("annotations", None)
                part.pop("logprobs", None)
    return item_dict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GEMINI_NORMALIZED_MAX = 1000  # Gemini CU outputs 0-999 normalized coords
DEFAULT_SCREEN_WIDTH = 1440
DEFAULT_SCREEN_HEIGHT = 900
DEFAULT_TURN_LIMIT = 25


async def _invoke_safety(
    callback: "Callable[[str], Any] | None",
    explanation: str,
) -> bool:
    """Invoke a safety callback that may be sync or async. Returns False if None."""
    if callback is None:
        return False
    result = callback(explanation)
    if asyncio.iscoroutine(result):
        result = await result
    return bool(result)

# Anthropic coordinate scaling: images with longest edge >1568px or
# total pixels >1,150,000 are internally downsampled.  We pre-resize
# and scale coordinates to eliminate coordinate drift.
_CLAUDE_MAX_LONG_EDGE = 1568
_CLAUDE_MAX_PIXELS = 1_150_000

# Claude Opus 4.7 supports higher resolution — up to 2576px on the
# long edge with 1:1 coordinates (no scale-factor conversion required).
_CLAUDE_OPUS_47_MAX_LONG_EDGE = 2576

# Models that use the higher resolution limit (no downscaling needed
# at typical screen resolutions).
_CLAUDE_HIGH_RES_MODELS = (
    "claude-opus-4-7", "claude-opus-4.7",
)

# Context pruning: replace screenshots older than this many turns with
# a placeholder to prevent unbounded context growth.
_CONTEXT_PRUNE_KEEP_RECENT = 3

_IMAGE_PNG = "image/png"

# xdotool key tokens that should NOT be lowercased when normalizing
# key combinations for DesktopExecutor.  Built once at module level.
_XDOTOOL_SPECIAL_KEYS: frozenset[str] = frozenset({
    "return", "enter", "backspace", "tab", "escape", "delete",
    "space", "home", "end", "insert", "pause",
    "left", "right", "up", "down",
    "page_up", "page_down", "pageup", "pagedown",
    "print", "scroll_lock", "num_lock", "caps_lock",
    "super", "ctrl", "alt", "shift",
    *(f"f{i}" for i in range(1, 25)),
})


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Provider(str, Enum):
    """LLM provider selection for the CU agent loop."""

    GEMINI = "gemini"
    CLAUDE = "claude"
    OPENAI = "openai"


class Environment(str, Enum):
    """Execution environment selector for Computer Use execution."""

    BROWSER = "browser"
    DESKTOP = "desktop"


class SafetyDecision(str, Enum):
    """Gemini safety-gate verdict attached to CU actions."""

    ALLOWED = "allowed"
    REQUIRE_CONFIRMATION = "require_confirmation"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CUActionResult:
    """Result of executing a single CU action."""
    name: str
    success: bool = True
    error: str | None = None
    safety_decision: SafetyDecision | None = None
    safety_explanation: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CUTurnRecord:
    """Record of one agent-loop turn, emitted via on_turn callback."""
    turn: int
    model_text: str
    actions: list[CUActionResult]
    screenshot_b64: str | None = None


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def denormalize_x(x: int, screen_width: int = DEFAULT_SCREEN_WIDTH) -> int:
    """Convert Gemini normalized x (0-999) to pixel coordinate."""
    return int(x / GEMINI_NORMALIZED_MAX * screen_width)


def denormalize_y(y: int, screen_height: int = DEFAULT_SCREEN_HEIGHT) -> int:
    """Convert Gemini normalized y (0-999) to pixel coordinate."""
    return int(y / GEMINI_NORMALIZED_MAX * screen_height)


def get_claude_scale_factor(width: int, height: int, model: str = "") -> float:
    """Compute Anthropic screenshot scale factor per official docs.

    Returns a factor <=1.0 that the screenshot should be pre-resized by.
    Claude's API internally downsamples images exceeding the thresholds;
    by pre-resizing and reporting the scaled dimensions, we ensure
    coordinates returned by Claude map 1:1 to the reported display size.

    Claude Opus 4.7 supports up to 2576px on the long edge with native
    1:1 coordinates, so it uses a higher threshold.
    """
    max_long_edge = (
        _CLAUDE_OPUS_47_MAX_LONG_EDGE
        if model in _CLAUDE_HIGH_RES_MODELS
        else _CLAUDE_MAX_LONG_EDGE
    )
    long_edge = max(width, height)
    total_pixels = width * height
    return min(
        1.0,
        max_long_edge / long_edge,
        math.sqrt(_CLAUDE_MAX_PIXELS / total_pixels),
    )


def resize_screenshot_for_claude(
    png_bytes: bytes, scale: float,
) -> tuple[bytes, int, int]:
    """Resize a PNG screenshot by *scale* factor.

    Returns (resized_png_bytes, new_width, new_height).
    Uses Pillow if available; returns original bytes if scale >= 1.0.
    """
    if scale >= 1.0:
        # No resize needed — extract dimensions from PNG header
        # PNG IHDR: bytes 16-20 = width, 20-24 = height (big-endian)
        w = int.from_bytes(png_bytes[16:20], "big")
        h = int.from_bytes(png_bytes[20:24], "big")
        return png_bytes, w, h

    try:
        from PIL import Image
    except ImportError:
        logger.warning(
            "Pillow not installed — cannot resize screenshots for Claude. "
            "Coordinate drift may occur. Install: pip install Pillow"
        )
        w = int.from_bytes(png_bytes[16:20], "big")
        h = int.from_bytes(png_bytes[20:24], "big")
        return png_bytes, w, h

    img = Image.open(io.BytesIO(png_bytes))
    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    img_resized.save(buf, format="PNG")
    return buf.getvalue(), new_w, new_h


# ---------------------------------------------------------------------------
# Executor Protocol
# ---------------------------------------------------------------------------

class ActionExecutor(Protocol):
    """Interface implemented by the supported computer-use executor."""

    screen_width: int
    screen_height: int

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult: ...
    async def capture_screenshot(self) -> bytes: ...
    def get_current_url(self) -> str: ...


# ---------------------------------------------------------------------------
# DesktopExecutor — remote execution via agent_service HTTP API
# ---------------------------------------------------------------------------


class DesktopExecutor:
    """Translates CU actions into ``POST /action`` calls to the agent_service.

    All commands are executed inside the Docker container by sending
    HTTP requests to the agent_service (port 9222 by default), so the
    backend can run on **any host OS** — including Windows — while
    ``xdotool`` and ``scrot`` run in the Linux container.

    Screenshots are retrieved via ``GET /screenshot?mode=desktop`` on the
    same agent_service.  If the service is unreachable, a ``docker exec``
    fallback is used for screenshots only.
    """

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
        self._client: httpx.AsyncClient | None = None

    def _px(self, x: int, y: int) -> tuple[int, int]:
        """Convert raw coordinates to pixel values, denormalizing if needed."""
        if self._normalize:
            return denormalize_x(x, self.screen_width), denormalize_y(y, self.screen_height)
        return x, y

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating one if needed."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        """Return the shared-secret header for authenticated agent_service calls."""
        token = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()
        return {"X-Agent-Token": token} if token else {}

    async def _post_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an action to the agent_service and return the JSON result."""
        client = await self._get_client()
        resp = await client.post(
            f"{self._service_url}/action",
            json=payload,
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ── ActionExecutor interface ──────────────────────────────────────

    async def aclose(self) -> None:
        """Close the underlying httpx client to prevent resource leaks."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult:
        """Map a CU action to the agent_service ``/action`` endpoint."""
        handler = getattr(self, f"_act_{name}", None)
        if handler is None:
            return CUActionResult(
                name=name, success=False,
                error=f"Unimplemented desktop action: {name}",
            )
        try:
            extra = await handler(args) or {}
            # Detect agent_service returning {"success": false}
            if isinstance(extra, dict) and extra.get("success") is False:
                return CUActionResult(
                    name=name, success=False,
                    error=extra.get("message", "Action failed"),
                    extra=extra,
                )
            await asyncio.sleep(_app_config.ui_settle_delay)  # UI settle delay
            return CUActionResult(name=name, success=True, extra=extra)
        except Exception as exc:
            logger.error("DesktopExecutor %s failed: %s", name, exc, exc_info=True)
            return CUActionResult(name=name, success=False, error=str(exc))

    # ── Desktop-level actions (via agent_service) ─────────────────────

    async def _act_click_at(self, a: dict) -> dict:
        """Click at coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_double_click(self, a: dict) -> dict:
        """Double-click at coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "double_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_right_click(self, a: dict) -> dict:
        """Right-click at coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "right_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_middle_click(self, a: dict) -> dict:
        """Middle-click at coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "middle_click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_triple_click(self, a: dict) -> dict:
        """Simulate triple-click (select paragraph/line) via 3 rapid clicks."""
        px, py = self._px(a["x"], a["y"])
        await self._post_action({
            "action": "double_click", "coordinates": [px, py], "mode": "desktop",
        })
        result = await self._post_action({
            "action": "click", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_hover_at(self, a: dict) -> dict:
        """Move cursor to coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        result = await self._post_action({
            "action": "hover", "coordinates": [px, py], "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, **result}

    async def _act_move(self, a: dict) -> dict:
        """Alias for pointer movement used by OpenAI computer actions."""
        return await self._act_hover_at(a)

    async def _act_type_text_at(self, a: dict) -> dict:
        """Click at coordinates via agent_service, clear field, type text, and optionally press Enter."""
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
        """Press a key combination via agent_service xdotool, normalizing modifier names."""
        keys = a["keys"]
        xdo_keys = (keys.replace("Control", "ctrl").replace("Alt", "alt")
                        .replace("Shift", "shift").replace("Meta", "super"))
        parts = xdo_keys.split("+")
        normalized = []
        for part in parts:
            stripped = part.strip()
            if len(stripped) == 1 and stripped.isalpha():
                normalized.append(stripped.lower())
            elif stripped.lower() in _XDOTOOL_SPECIAL_KEYS:
                normalized.append(stripped)
            else:
                normalized.append(stripped)
        xdo_keys = "+".join(normalized)
        await self._post_action({
            "action": "key", "text": xdo_keys, "mode": "desktop",
        })
        return {"keys": keys}

    async def _act_scroll_document(self, a: dict) -> dict:
        """Scroll the page in the given direction via desktop input events."""
        direction = a["direction"]
        await self._post_action({
            "action": "scroll", "text": direction, "mode": "desktop",
        })
        return {"direction": direction}

    async def _act_left_mouse_down(self, a: dict) -> dict:
        """Hold the left mouse button down at the current cursor position."""
        result = await self._post_action({
            "action": "left_mouse_down", "mode": "desktop",
        })
        return result

    async def _act_left_mouse_up(self, a: dict) -> dict:
        """Release the left mouse button at the current cursor position."""
        result = await self._post_action({
            "action": "left_mouse_up", "mode": "desktop",
        })
        return result

    async def _act_hold_key(self, a: dict) -> dict:
        """Hold a key for a short duration via xdotool keydown/keyup."""
        key = str(a.get("key", ""))
        duration = min(max(float(a.get("duration", 1)), 0.0), 10.0)
        await self._post_action({
            "action": "keydown", "text": key, "mode": "desktop",
        })
        await asyncio.sleep(duration)
        result = await self._post_action({
            "action": "keyup", "text": key, "mode": "desktop",
        })
        return {"key": key, "duration": duration, **result}

    async def _act_scroll_at(self, a: dict) -> dict:
        """Scroll at specific coordinates via agent_service xdotool."""
        px, py = self._px(a["x"], a["y"])
        direction = a["direction"]
        await self._post_action({
            "action": "scroll", "coordinates": [px, py],
            "text": direction, "mode": "desktop",
        })
        return {"pixel_x": px, "pixel_y": py, "direction": direction}

    async def _act_drag_and_drop(self, a: dict) -> dict:
        """Drag from source to destination via agent_service xdotool."""
        sx, sy = self._px(a["x"], a["y"])
        dx, dy = self._px(a["destination_x"], a["destination_y"])
        await self._post_action({
            "action": "drag", "coordinates": [sx, sy, dx, dy], "mode": "desktop",
        })
        return {"from": (sx, sy), "to": (dx, dy)}

    async def _act_navigate(self, a: dict) -> dict:
        """Open a URL via agent_service desktop browser."""
        url = a["url"]
        await self._post_action({
            "action": "open_url", "text": url, "mode": "desktop",
        })
        return {"url": url}

    async def _act_open_web_browser(self, a: dict) -> dict:
        """Open Google homepage via agent_service desktop browser."""
        await self._post_action({
            "action": "open_url", "text": "https://www.google.com", "mode": "desktop",
        })
        return {}

    async def _act_wait_5_seconds(self, a: dict) -> dict:
        """Sleep for post_action_screenshot_delay seconds (model-requested pause)."""
        await asyncio.sleep(_app_config.post_action_screenshot_delay)
        return {}

    async def _act_go_back(self, a: dict) -> dict:
        """Press Alt+Left to go back in desktop browser history."""
        await self._post_action({
            "action": "key", "text": "alt+Left", "mode": "desktop",
        })
        return {}

    async def _act_go_forward(self, a: dict) -> dict:
        """Press Alt+Right to go forward in desktop browser history."""
        await self._post_action({
            "action": "key", "text": "alt+Right", "mode": "desktop",
        })
        return {}

    async def _act_type_at_cursor(self, a: dict) -> dict:
        """Type text at the current cursor position without clicking."""
        text = a["text"]
        press_enter = a.get("press_enter", False)
        await self._post_action({
            "action": "type", "text": text, "mode": "desktop",
        })
        if press_enter:
            await self._post_action({
                "action": "key", "text": "Return", "mode": "desktop",
            })
        return {"text": text}

    async def _act_search(self, a: dict) -> dict:
        """Open Google homepage via agent_service desktop browser."""
        await self._post_action({
            "action": "open_url", "text": "https://www.google.com", "mode": "desktop",
        })
        return {}

    # ── Screenshot ────────────────────────────────────────────────────

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
            b64 = data["screenshot"]
            return base64.b64decode(b64)
        except Exception as exc:
            logger.warning(
                "Agent service screenshot failed (%s), falling back to docker exec", exc,
            )
            return await self._fallback_screenshot()

    async def _fallback_screenshot(self) -> bytes:
        """Grab a screenshot via ``docker exec scrot`` as last resort."""
        path = "/tmp/cu_screenshot.png"
        # Run scrot inside the container
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec",
            "-e", "DISPLAY=:99",
            self._container, "scrot", "-z", "-o", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        # Read the resulting PNG back
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
        """Desktop executor has no URL context — always empty."""
        return ""


# ---------------------------------------------------------------------------
# Gemini Computer Use Client
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Unified ComputerUseEngine facade
# ---------------------------------------------------------------------------

class ComputerUseEngine:
    """Single entry point for native Computer Use across providers and environments.

    The single engine for this application. Uses the native CU protocol
    from Gemini, Claude, or OpenAI with the desktop executor.

    Usage::

        engine = ComputerUseEngine(
            provider=Provider.GEMINI,
            api_key="AIza...",
            environment=Environment.DESKTOP,
        )
        final_text = await engine.execute_task(
            "Search for flights to Paris",
        )
    """

    def __init__(
        self,
        provider: Provider,
        api_key: str,
        model: str | None = None,
        environment: Environment = Environment.DESKTOP,
        screen_width: int = DEFAULT_SCREEN_WIDTH,
        screen_height: int = DEFAULT_SCREEN_HEIGHT,
        system_instruction: str | None = None,
        excluded_actions: list[str] | None = None,
        container_name: str = "cua-environment",
        agent_service_url: str = "http://127.0.0.1:9222",
        reasoning_effort: str | None = None,
    ):
        self.provider = provider
        self.environment = environment
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._container_name = container_name
        self._agent_service_url = agent_service_url

        if provider == Provider.GEMINI:
            self._client: Any = GeminiCUClient(
                api_key=api_key,
                model=model or "gemini-3-flash-preview",
                environment=environment,
                excluded_actions=excluded_actions,
                system_instruction=system_instruction,
            )
        elif provider == Provider.CLAUDE:
            # Look up tool_version / beta_flag from allowed_models.json
            # if available, so the canonical allowlist drives the config
            # instead of relying solely on model-name auto-detection.
            _tv, _bf = _lookup_claude_cu_config(model or "claude-sonnet-4-6")
            self._client = ClaudeCUClient(
                api_key=api_key,
                model=model or "claude-sonnet-4-6",
                system_prompt=system_instruction,
                tool_version=_tv,
                beta_flag=_bf,
            )
        elif provider == Provider.OPENAI:
            self._client = OpenAICUClient(
                api_key=api_key,
                model=model or "gpt-5.4",
                system_prompt=system_instruction,
                reasoning_effort=reasoning_effort or "low",
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _build_executor(self, page: Any = None) -> ActionExecutor:
        """Build the supported desktop executor."""
        # Gemini uses normalized 0-999 coords; Claude uses real pixels
        normalize = self.provider == Provider.GEMINI

        if self.environment == Environment.BROWSER:
            raise ValueError("Browser mode is no longer supported. Use Environment.DESKTOP.")
        return DesktopExecutor(
            screen_width=self.screen_width,
            screen_height=self.screen_height,
            normalize_coords=normalize,
            agent_service_url=self._agent_service_url,
            container_name=self._container_name,
        )

    async def execute_task(
        self,
        goal: str,
        page: Any = None,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], bool] | None = None,
        on_turn: Callable[[CUTurnRecord], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Execute a CU task end-to-end using the native tool protocol.

        Args:
            goal: Natural language task description.
            page: Unused legacy parameter retained for compatibility.
            turn_limit: Maximum agent loop iterations.
            on_safety: Callback for safety confirmations.
            on_turn: Progress callback per turn.
            on_log: Logging callback(level, message).

        Returns:
            Final text response from the model.
        """
        executor = self._build_executor(page)
        try:
            return await self._client.run_loop(
                goal=goal,
                executor=executor,
                turn_limit=turn_limit,
                on_safety=on_safety,
                on_turn=on_turn,
                on_log=on_log,
            )
        finally:
            # Close httpx client to prevent resource leaks
            if hasattr(executor, 'aclose'):
                try:
                    await executor.aclose()
                except Exception:
                    logger.debug("Error closing executor", exc_info=True)


# ---------------------------------------------------------------------------
# Per-provider client re-exports (Q2 — class bodies live in their own files)
# ---------------------------------------------------------------------------

from backend.engine.gemini import GeminiCUClient, _prune_gemini_context  # noqa: E402
from backend.engine.claude import (  # noqa: E402
    ClaudeCUClient,
    _prune_claude_context,
)
from backend.engine.openai import OpenAICUClient  # noqa: E402

__all__ = [
    "Provider",
    "Environment",
    "SafetyDecision",
    "CUActionResult",
    "CUTurnRecord",
    "ActionExecutor",
    "DesktopExecutor",
    "GeminiCUClient",
    "ClaudeCUClient",
    "OpenAICUClient",
    "ComputerUseEngine",
    "denormalize_x",
    "denormalize_y",
    "get_claude_scale_factor",
    "resize_screenshot_for_claude",
    "_prune_claude_context",
    "_prune_gemini_context",
    "_extract_openai_output_text",
    "_build_openai_computer_call_output",
    "_sanitize_openai_response_item_for_replay",
    "_lookup_claude_cu_config",
]
