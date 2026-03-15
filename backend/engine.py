"""Unified Computer Use engine — native CU protocol for Gemini & Claude.

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
        ├── PlaywrightExecutor  (browser actions via Playwright page)
        └── DesktopExecutor     (desktop via agent_service HTTP API → xdotool + scrot)

Usage::

    engine = ComputerUseEngine(
        provider=Provider.GEMINI,
        api_key="...",
        environment=Environment.BROWSER,
    )
    result = await engine.execute_task("Search for ...", page=playwright_page)
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_allowed_models_json() -> list[dict]:
    """Load the canonical model allowlist from allowed_models.json.

    Shared helper — used by both engine.py and server.py.
    """
    import json as _json
    from pathlib import Path as _Path

    fpath = _Path(__file__).resolve().parent / "allowed_models.json"
    with open(fpath, encoding="utf-8") as f:
        data = _json.load(f)
    return data.get("models", [])


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
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return {
            key: item for key, item in vars(value).items()
            if not key.startswith("_")
        }
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GEMINI_NORMALIZED_MAX = 1000  # Gemini CU outputs 0-999 normalized coords
DEFAULT_SCREEN_WIDTH = 1440
DEFAULT_SCREEN_HEIGHT = 900
DEFAULT_TURN_LIMIT = 25

# Anthropic coordinate scaling: images with longest edge >1568px or
# total pixels >1,150,000 are internally downsampled.  We pre-resize
# and scale coordinates to eliminate coordinate drift.
_CLAUDE_MAX_LONG_EDGE = 1568
_CLAUDE_MAX_PIXELS = 1_150_000

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
    """Execution environment — Playwright browser or xdotool desktop."""

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


def get_claude_scale_factor(width: int, height: int) -> float:
    """Compute Anthropic screenshot scale factor per official docs.

    Returns a factor <=1.0 that the screenshot should be pre-resized by.
    Claude's API internally downsamples images exceeding the thresholds;
    by pre-resizing and reporting the scaled dimensions, we ensure
    coordinates returned by Claude map 1:1 to the reported display size.
    """
    long_edge = max(width, height)
    total_pixels = width * height
    return min(
        1.0,
        _CLAUDE_MAX_LONG_EDGE / long_edge,
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
    """Interface that both Playwright and Desktop executors implement."""

    screen_width: int
    screen_height: int

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult: ...
    async def capture_screenshot(self) -> bytes: ...
    def get_current_url(self) -> str: ...


# ---------------------------------------------------------------------------
# PlaywrightExecutor — browser-scoped actions
# ---------------------------------------------------------------------------

class PlaywrightExecutor:
    """Translates CU actions into async Playwright calls.

    Implements every action from the Gemini CU supported-actions table:
    ``open_web_browser``, ``wait_5_seconds``, ``go_back``, ``go_forward``,
    ``search``, ``navigate``, ``click_at``, ``hover_at``, ``type_text_at``,
    ``key_combination``, ``scroll_document``, ``scroll_at``, ``drag_and_drop``.

    Gemini sends normalized 0-999 coords → denormalized here.
    Claude sends real pixel coords → passed through (``normalize_coords=False``).
    """

    def __init__(
        self,
        page: Any,
        screen_width: int = DEFAULT_SCREEN_WIDTH,
        screen_height: int = DEFAULT_SCREEN_HEIGHT,
        normalize_coords: bool = True,
    ):
        self.page = page
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._normalize = normalize_coords

    def _px(self, x: int, y: int) -> tuple[int, int]:
        """Convert raw coordinates to pixel values, denormalizing if needed."""
        if self._normalize:
            return denormalize_x(x, self.screen_width), denormalize_y(y, self.screen_height)
        return x, y

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult:
        """Dispatch a CU action by name, wait for DOM settle, and return the result."""
        safety = self._pop_safety(args)
        handler = getattr(self, f"_act_{name}", None)
        if handler is None:
            return CUActionResult(name=name, success=False,
                                  error=f"Unimplemented action: {name}", **safety)
        try:
            extra = await handler(args) or {}
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(0.4)  # UI settle delay
            return CUActionResult(name=name, success=True, extra=extra, **safety)
        except Exception as exc:
            logger.error("PlaywrightExecutor %s failed: %s", name, exc, exc_info=True)
            return CUActionResult(name=name, success=False, error=str(exc), **safety)

    @staticmethod
    def _pop_safety(args: dict) -> dict:
        """Extract and normalize Gemini safety metadata from action args."""
        sd = args.pop("safety_decision", None)
        if isinstance(sd, dict):
            return {
                "safety_decision": SafetyDecision(sd.get("decision", "allowed")),
                "safety_explanation": sd.get("explanation"),
            }
        return {}

    # ── Action implementations ────────────────────────────────────────

    async def _act_open_web_browser(self, a: dict) -> dict:
        """No-op — browser is already open."""
        return {}

    async def _act_wait_5_seconds(self, a: dict) -> dict:
        """Sleep for 5 seconds (model-requested pause)."""
        await asyncio.sleep(5)
        return {}

    async def _act_go_back(self, a: dict) -> dict:
        """Navigate one step back in browser history."""
        await self.page.go_back()
        return {}

    async def _act_go_forward(self, a: dict) -> dict:
        """Navigate one step forward in browser history."""
        await self.page.go_forward()
        return {}

    async def _act_search(self, a: dict) -> dict:
        """Open Google homepage to begin a search."""
        await self.page.goto("https://www.google.com")
        return {}

    async def _act_navigate(self, a: dict) -> dict:
        """Navigate to a specific URL."""
        url = a["url"]
        await self.page.goto(url)
        return {"url": url}

    async def _act_click_at(self, a: dict) -> dict:
        """Click at the given coordinates."""
        px, py = self._px(a["x"], a["y"])
        await self.page.mouse.click(px, py)
        return {"pixel_x": px, "pixel_y": py}

    async def _act_double_click(self, a: dict) -> dict:
        """Double-click at the given coordinates."""
        px, py = self._px(a["x"], a["y"])
        await self.page.mouse.dblclick(px, py)
        return {"pixel_x": px, "pixel_y": py}

    async def _act_right_click(self, a: dict) -> dict:
        """Right-click at the given coordinates."""
        px, py = self._px(a["x"], a["y"])
        await self.page.mouse.click(px, py, button="right")
        return {"pixel_x": px, "pixel_y": py}

    async def _act_middle_click(self, a: dict) -> dict:
        """Middle-click at the given coordinates."""
        px, py = self._px(a["x"], a["y"])
        await self.page.mouse.click(px, py, button="middle")
        return {"pixel_x": px, "pixel_y": py}

    async def _act_hover_at(self, a: dict) -> dict:
        """Move the mouse to the given coordinates without clicking."""
        px, py = self._px(a["x"], a["y"])
        await self.page.mouse.move(px, py)
        return {"pixel_x": px, "pixel_y": py}

    async def _act_move(self, a: dict) -> dict:
        """Alias for pointer movement used by OpenAI computer actions."""
        return await self._act_hover_at(a)

    async def _act_type_text_at(self, a: dict) -> dict:
        """Click at coordinates, optionally clear the field, type text, and optionally press Enter."""
        px, py = self._px(a["x"], a["y"])
        text = a["text"]
        press_enter = a.get("press_enter", True)
        clear_before = a.get("clear_before_typing", True)
        await self.page.mouse.click(px, py)
        if clear_before:
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Backspace")
        await self.page.keyboard.type(text)
        if press_enter:
            await self.page.keyboard.press("Enter")
        return {"pixel_x": px, "pixel_y": py, "text": text}

    async def _act_type_at_cursor(self, a: dict) -> dict:
        """Type text into the currently focused element without clicking first."""
        text = a["text"]
        press_enter = a.get("press_enter", False)
        await self.page.keyboard.type(text)
        if press_enter:
            await self.page.keyboard.press("Enter")
        return {"text": text}

    async def _act_key_combination(self, a: dict) -> dict:
        """Press a keyboard shortcut (e.g. ``Control+A``)."""
        keys = a["keys"]
        await self.page.keyboard.press(keys)
        return {"keys": keys}

    async def _act_scroll_document(self, a: dict) -> dict:
        """Scroll the page in the given direction."""
        direction = a["direction"]
        dx, dy = self._scroll_delta(direction)
        await self.page.mouse.wheel(dx, dy)
        return {"direction": direction}

    async def _act_scroll_at(self, a: dict) -> dict:
        """Move to coordinates and scroll via Playwright mouse wheel."""
        px, py = self._px(a["x"], a["y"])
        direction = a["direction"]
        magnitude = a.get("magnitude", 800)
        await self.page.mouse.move(px, py)
        dx, dy = self._scroll_delta(direction, magnitude)
        await self.page.mouse.wheel(dx, dy)
        return {"pixel_x": px, "pixel_y": py, "direction": direction}

    async def _act_drag_and_drop(self, a: dict) -> dict:
        """Drag from source to destination via Playwright mouse down/move/up."""
        sx, sy = self._px(a["x"], a["y"])
        dx, dy = self._px(a["destination_x"], a["destination_y"])
        await self.page.mouse.move(sx, sy)
        await self.page.mouse.down()
        await self.page.mouse.move(dx, dy, steps=10)
        await self.page.mouse.up()
        return {"from": (sx, sy), "to": (dx, dy)}

    @staticmethod
    def _scroll_delta(direction: str, magnitude: int = 800) -> tuple[int, int]:
        """Convert a scroll direction and normalized magnitude to pixel (dx, dy)."""
        pixel_mag = int(magnitude / GEMINI_NORMALIZED_MAX * DEFAULT_SCREEN_HEIGHT)
        return {
            "up": (0, -pixel_mag), "down": (0, pixel_mag),
            "left": (-pixel_mag, 0), "right": (pixel_mag, 0),
        }.get(direction, (0, pixel_mag))

    async def capture_screenshot(self) -> bytes:
        """Capture the current page as a PNG screenshot."""
        return await self.page.screenshot(type="png")

    def get_current_url(self) -> str:
        """Return the current page URL."""
        return self.page.url


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

    async def _post_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an action to the agent_service and return the JSON result."""
        client = await self._get_client()
        resp = await client.post(f"{self._service_url}/action", json=payload)
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
            await asyncio.sleep(0.3)  # UI settle delay
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
        """Scroll the page in the given direction via Playwright mouse wheel."""
        direction = a["direction"]
        await self._post_action({
            "action": "scroll", "text": direction, "mode": "desktop",
        })
        return {"direction": direction}

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
        """Sleep for 5 seconds (model-requested pause)."""
        await asyncio.sleep(5)
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
                f"{self._service_url}/screenshot", params={"mode": "desktop"},
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


def _prune_gemini_context(
    contents: list, types: Any, keep_recent: int,
) -> None:
    """Replace inline screenshot data in old turns with a text placeholder.

    Preserves the first message (goal + initial screenshot) and the last
    *keep_recent* Content entries.  Everything in between has its
    ``FunctionResponseBlob`` data stripped and image ``Part`` objects
    replaced with a text marker.
    """
    if len(contents) <= keep_recent + 1:
        return
    prune_end = len(contents) - keep_recent
    for idx in range(1, prune_end):
        content = contents[idx]
        if not hasattr(content, "parts") or not content.parts:
            continue
        new_parts = []
        pruned = False
        for part in content.parts:
            # Strip inline_data from FunctionResponsePart
            fr = getattr(part, "function_response", None)
            if fr is not None and hasattr(fr, "parts") and fr.parts:
                fr.parts.clear()
                pruned = True
            # Strip standalone image parts (from_bytes)
            if getattr(part, "inline_data", None) is not None:
                new_parts.append(types.Part(text="[screenshot omitted]"))
                pruned = True
            else:
                new_parts.append(part)
        if pruned:
            content.parts[:] = new_parts


class GeminiCUClient:
    """Native Gemini Computer Use tool protocol.

    API contract:
    - Declares ``types.Tool(computer_use=ComputerUse(...))``
    - Enables ``ThinkingConfig(thinking_level=\"high\")``
    - Sends screenshots inline in ``FunctionResponse`` parts
    - Handles ``safety_decision`` → ``require_confirmation``
    - Supports both ``ENVIRONMENT_BROWSER`` and ``ENVIRONMENT_DESKTOP``
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-flash-preview",
        environment: Environment = Environment.BROWSER,
        excluded_actions: list[str] | None = None,
        system_instruction: str | None = None,
    ):
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError as exc:
            raise ImportError(
                "google-genai is required. Install: pip install google-genai"
            ) from exc

        self._genai = genai
        self._types = genai_types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._environment = environment
        self._excluded = excluded_actions or []
        self._system_instruction = system_instruction

    def _get_env_enum(self) -> Any:
        """Map the Environment enum to the google-genai SDK environment constant."""
        types = self._types
        if self._environment == Environment.DESKTOP:
            desktop_env = getattr(types.Environment, "ENVIRONMENT_DESKTOP", None)
            if desktop_env is not None:
                return desktop_env
            logger.warning(
                "ENVIRONMENT_DESKTOP not available in google-genai SDK; "
                "falling back to ENVIRONMENT_BROWSER.  Desktop xdotool "
                "actions will still execute via DesktopExecutor."
            )
            return types.Environment.ENVIRONMENT_BROWSER
        return types.Environment.ENVIRONMENT_BROWSER

    def _build_config(self) -> Any:
        """Build the GenerateContentConfig with CU tools, safety, and thinking settings."""
        types = self._types
        tools = [
            types.Tool(
                computer_use=types.ComputerUse(
                    environment=self._get_env_enum(),
                    excluded_predefined_functions=self._excluded,
                )
            )
        ]

        # Relax safety thresholds so the model doesn't silently refuse when
        # seeing desktop screenshots that contain innocuous UI chrome the
        # safety classifier may over-flag (e.g. browser with sign-in pages,
        # system toolbars, ads).
        safety_settings = []
        _HarmCategory = getattr(types, "HarmCategory", None)
        _SafetySetting = getattr(types, "SafetySetting", None)
        _HarmBlockThreshold = getattr(types, "HarmBlockThreshold", None)
        if _HarmCategory and _SafetySetting and _HarmBlockThreshold:
            # Use BLOCK_ONLY_HIGH to avoid over-blocking desktop screenshots
            # while still filtering genuinely harmful content.
            block_level = getattr(_HarmBlockThreshold, "BLOCK_ONLY_HIGH", None)
            if block_level is not None:
                for cat_name in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                ):
                    cat = getattr(_HarmCategory, cat_name, None)
                    if cat is not None:
                        safety_settings.append(
                            _SafetySetting(category=cat, threshold=block_level)
                        )

        # Use thinking_level (recommended for Gemini 3) instead of
        # legacy include_thoughts / budget_tokens.
        _ThinkingConfig = types.ThinkingConfig
        _thinking_kwargs: dict[str, Any] = {}
        # Prefer thinking_level="high" if the SDK supports it
        import inspect as _inspect
        _tc_params = _inspect.signature(_ThinkingConfig).parameters
        if "thinking_level" in _tc_params:
            _thinking_kwargs["thinking_level"] = "high"
        else:
            # Fallback for older SDK versions
            _thinking_kwargs["include_thoughts"] = True

        kwargs: dict[str, Any] = {
            "tools": tools,
            "thinking_config": _ThinkingConfig(**_thinking_kwargs),
        }
        if safety_settings:
            kwargs["safety_settings"] = safety_settings
        if self._system_instruction:
            kwargs["system_instruction"] = self._system_instruction
        return self._genai.types.GenerateContentConfig(**kwargs)

    async def run_loop(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], bool] | None = None,
        on_turn: Callable[[CUTurnRecord], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Run the full Gemini CU agent loop.

        Args:
            goal: Natural language task.
            executor: PlaywrightExecutor or DesktopExecutor.
            turn_limit: Max loop iterations.
            on_safety: Callback(explanation) → bool. True=confirm, False=deny.
            on_turn: Progress callback per turn.
            on_log: Logging callback(level, message).

        Returns:
            Final text response from the model.
        """
        types = self._types
        config = self._build_config()

        # Initial screenshot
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            return "Error: Could not capture initial screenshot"

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(text=goal),
                    types.Part.from_bytes(data=screenshot_bytes, mime_type=_IMAGE_PNG),
                ],
            )
        ]

        final_text = ""

        for turn in range(turn_limit):
            if on_log:
                on_log("info", f"Gemini CU turn {turn + 1}/{turn_limit}")

            # Prune old screenshots to prevent unbounded context growth.
            # Keep the first message (goal + initial screenshot) and
            # the most recent _CONTEXT_PRUNE_KEEP_RECENT turns intact.
            _prune_gemini_context(contents, types, _CONTEXT_PRUNE_KEEP_RECENT)

            try:
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self._model,
                    contents=contents,
                    config=config,
                )
            except Exception as api_err:
                error_msg = str(api_err)
                if on_log:
                    on_log("error", f"Gemini API error at turn {turn + 1}: {error_msg}")
                # Try to provide actionable info for common error patterns
                if "INVALID_ARGUMENT" in error_msg:
                    if on_log:
                        on_log("error",
                            "INVALID_ARGUMENT usually means: (1) screenshot too large/corrupt, "
                            "(2) model doesn't support computer_use tool, or "
                            "(3) conversation context exceeded limits. "
                            f"Contents length: {len(contents)} turns, "
                            f"last screenshot: {len(screenshot_bytes)} bytes")
                final_text = f"Gemini API error: {error_msg}"
                break

            if not response.candidates:
                if on_log:
                    on_log("warning", f"Gemini returned no candidates at turn {turn + 1} — retrying with nudge")

                # Retry once: append a user nudge reminding the model to
                # use computer_use tools and re-send with a fresh screenshot.
                try:
                    retry_ss = await executor.capture_screenshot()
                except Exception:
                    retry_ss = screenshot_bytes

                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=(
                                    "Please continue using the computer_use tools to "
                                    "complete the task. Here is the current screen."
                                )
                            ),
                            types.Part.from_bytes(
                                data=retry_ss, mime_type=_IMAGE_PNG
                            ),
                        ],
                    )
                )
                try:
                    response = await asyncio.to_thread(
                        self._client.models.generate_content,
                        model=self._model,
                        contents=contents,
                        config=config,
                    )
                except Exception as retry_err:
                    if on_log:
                        on_log("error", f"Retry also failed: {retry_err}")
                    final_text = f"Error: Gemini returned no candidates and retry failed: {retry_err}"
                    break

                if not response.candidates:
                    if on_log:
                        on_log("error", f"Gemini returned no candidates even after retry at turn {turn + 1}")
                    final_text = "Error: Gemini returned no candidates (after retry)"
                    break

            candidate = response.candidates[0]
            contents.append(candidate.content)

            # Extract function calls and text
            function_calls = [
                p.function_call for p in candidate.content.parts if p.function_call
            ]
            text_parts = [p.text for p in candidate.content.parts if p.text]
            turn_text = " ".join(text_parts)

            # No function calls → model is done
            if not function_calls:
                final_text = turn_text
                if on_log:
                    on_log("info", f"Gemini CU completed: {final_text[:200]}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=turn_text, actions=[]))
                break

            # Execute each function call
            results: list[CUActionResult] = []
            terminated = False

            for fc in function_calls:
                args = dict(fc.args) if fc.args else {}

                # Extract safety_decision BEFORE passing args to executor.
                # This ensures the acknowledgement is tracked regardless of
                # which executor (Playwright or Desktop) is used.
                safety_confirmed = False
                if "safety_decision" in args:
                    sd = args.pop("safety_decision")
                    if isinstance(sd, dict) and sd.get("decision") == "require_confirmation":
                        confirmed = on_safety(sd.get("explanation", "")) if on_safety else False
                        if not confirmed:
                            if on_log:
                                on_log("warning", f"Safety denied for {fc.name}")
                            terminated = True
                            break
                        safety_confirmed = True

                result = await executor.execute(fc.name, args)
                # Stamp safety metadata so FunctionResponse includes
                # safety_acknowledgement when the user confirmed.
                if safety_confirmed:
                    result.safety_decision = SafetyDecision.REQUIRE_CONFIRMATION
                results.append(result)

            # Emit turn record
            try:
                screenshot_bytes = await executor.capture_screenshot()
            except Exception as ss_err:
                if on_log:
                    on_log("warning", f"Screenshot capture failed at turn {turn + 1}: {ss_err}")
                screenshot_bytes = b""

            screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode() if screenshot_bytes else ""
            if on_turn:
                on_turn(CUTurnRecord(
                    turn=turn + 1, model_text=turn_text,
                    actions=results, screenshot_b64=screenshot_b64 or None,
                ))

            if terminated:
                final_text = "Agent terminated: safety confirmation denied."
                break

            # Build FunctionResponses with inline screenshot per Gemini CU docs:
            # https://ai.google.dev/gemini-api/docs/computer-use
            # Each FunctionResponse embeds the screenshot via
            #   parts=[FunctionResponsePart(inline_data=FunctionResponseBlob(...))]
            # The screenshot must NOT be sent as a separate Part.from_bytes().
            current_url = executor.get_current_url()
            screenshot_ok = bool(screenshot_bytes) and len(screenshot_bytes) >= 100

            function_responses = []
            for r in results:
                resp_data: dict[str, Any] = {"url": current_url}
                if r.error:
                    resp_data["error"] = r.error
                if r.safety_decision == SafetyDecision.REQUIRE_CONFIRMATION:
                    resp_data["safety_acknowledgement"] = "true"
                # Merge extra data, converting non-serializable types (tuples → lists)
                for k, v in r.extra.items():
                    if isinstance(v, tuple):
                        resp_data[k] = list(v)
                    elif isinstance(v, (str, int, float, bool, type(None), list, dict)):
                        resp_data[k] = v
                    else:
                        resp_data[k] = str(v)

                fr_kwargs: dict[str, Any] = {"name": r.name, "response": resp_data}

                if screenshot_ok:
                    fr_kwargs["parts"] = [
                        types.FunctionResponsePart(
                            inline_data=types.FunctionResponseBlob(
                                mime_type=_IMAGE_PNG,
                                data=screenshot_bytes,
                            )
                        )
                    ]

                function_responses.append(types.FunctionResponse(**fr_kwargs))

            # IMPORTANT: send ONLY FunctionResponse parts — no separate image Part
            if not function_responses:
                if on_log:
                    on_log("warning", "No function responses to send; ending loop")
                break

            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(function_response=fr) for fr in function_responses],
                )
            )

        return final_text


# ---------------------------------------------------------------------------
# Claude Computer Use Client
# ---------------------------------------------------------------------------

class ClaudeCUClient:
    """Native Claude computer-use tool protocol.

    API contract:
    - Auto-detects tool version from model name:
      * Claude Sonnet 4.6 / Opus 4.6 / Opus 4.5 → ``computer_20251124``
        with beta header ``computer-use-2025-11-24``
      * All other CU models → ``computer_20250124``
        with beta header ``computer-use-2025-01-24``
    - Uses ``client.beta.messages.create()`` (beta endpoint required)
    - Enables thinking with a conservative token budget
    - Sends screenshots as base64 in ``tool_result`` content
    - Claude outputs real pixel coordinates (no normalization)
    - ``display_number`` is intentionally omitted (optional, often wrong)
    - Actions: screenshot, click, double_click, type, key, scroll,
      mouse_move, left_click_drag, triple_click, right_click,
      middle_click, left_mouse_down, left_mouse_up, hold_key, wait
    """

    # Models that require the newer computer_20251124 tool version.
    _NEW_TOOL_MODELS = (
        "claude-sonnet-4-6", "claude-sonnet-4.6",
        "claude-opus-4-6", "claude-opus-4.6",
        "claude-opus-4-5", "claude-opus-4.5",
    )

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        system_prompt: str | None = None,
        tool_version: str | None = None,
        beta_flag: str | None = None,
    ):
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic is required. Install: pip install anthropic"
            ) from exc

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt or ""

        # Use explicit values from allowed_models.json if provided,
        # otherwise auto-detect from model name (backwards compatibility).
        if tool_version and beta_flag:
            self._tool_version = tool_version
            self._beta_flag = beta_flag
        elif any(tag in model for tag in self._NEW_TOOL_MODELS):
            self._tool_version = "computer_20251124"
            self._beta_flag = "computer-use-2025-11-24"
        else:
            self._tool_version = "computer_20250124"
            self._beta_flag = "computer-use-2025-01-24"

    def _build_tools(self, sw: int, sh: int) -> list[dict]:
        """Build the Claude computer-use tool definition with display dimensions."""
        tool: dict[str, Any] = {
            "type": self._tool_version,
            "name": "computer",
            "display_width_px": sw,
            "display_height_px": sh,
        }
        # Enable zoom action for computer_20251124 tool version
        if self._tool_version == "computer_20251124":
            tool["enable_zoom"] = True
        return [tool]

    async def run_loop(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], bool] | None = None,
        on_turn: Callable[[CUTurnRecord], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Run the full Claude CU agent loop.

        Handles screenshot scaling, context pruning, safety refusals,
        and all Claude stop_reason variants. Returns final text.
        """
        # Compute screenshot scaling to prevent coordinate drift.
        scale = get_claude_scale_factor(executor.screen_width, executor.screen_height)
        scaled_w = int(executor.screen_width * scale)
        scaled_h = int(executor.screen_height * scale)
        if scale < 1.0 and on_log:
            on_log("info", f"Claude screenshot scale={scale:.3f} → {scaled_w}x{scaled_h}")

        tools = self._build_tools(scaled_w, scaled_h)

        screenshot_bytes = await executor.capture_screenshot()
        screenshot_bytes, _, _ = resize_screenshot_for_claude(screenshot_bytes, scale)
        screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": goal},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _IMAGE_PNG,
                            "data": screenshot_b64,
                        },
                    },
                ],
            }
        ]

        final_text = ""

        for turn in range(turn_limit):
            if on_log:
                on_log("info", f"Claude CU turn {turn + 1}/{turn_limit}")

            # Prune old screenshots to prevent unbounded context growth
            _prune_claude_context(messages, _CONTEXT_PRUNE_KEEP_RECENT)

            response = await asyncio.to_thread(
                self._client.beta.messages.create,
                model=self._model,
                max_tokens=16384,
                system=self._system_prompt,
                tools=tools,
                messages=messages,
                betas=[self._beta_flag],
                thinking={"type": "enabled", "budget_tokens": 4096},
            )

            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            tool_uses = [b for b in assistant_content if b.type == "tool_use"]
            text_blocks = [b.text for b in assistant_content
                          if hasattr(b, "text") and b.text]
            turn_text = " ".join(text_blocks)

            # Handle all stop_reason values explicitly
            stop = response.stop_reason
            if stop == "refusal":
                final_text = turn_text or "Model refused to continue (safety refusal)."
                if on_log:
                    on_log("warning", f"Claude refused: {final_text[:200]}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=final_text, actions=[]))
                break
            if stop == "model_context_window_exceeded":
                final_text = "Error: context window exceeded. Task too long."
                if on_log:
                    on_log("error", "Claude context window exceeded")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=final_text, actions=[]))
                break
            if stop in ("max_tokens", "stop_sequence"):
                final_text = turn_text or f"Response truncated (stop_reason={stop})."
                if on_log:
                    on_log("warning", f"Claude stop_reason={stop}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=final_text, actions=[]))
                break
            if stop == "end_turn" or not tool_uses:
                final_text = turn_text
                if on_log:
                    on_log("info", f"Claude CU completed: {final_text[:200]}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=turn_text, actions=[]))
                break

            # Execute tool uses
            tool_result_parts: list[dict[str, Any]] = []
            results: list[CUActionResult] = []

            for tu in tool_uses:
                result = await self._execute_claude_action(
                    tu.input, executor, scale_factor=scale,
                )
                results.append(result)

                screenshot_bytes = await executor.capture_screenshot()
                screenshot_bytes, _, _ = resize_screenshot_for_claude(
                    screenshot_bytes, scale,
                )
                screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()

                content: list[dict] = []
                if result.error:
                    content.append({"type": "text", "text": f"Error: {result.error}"})
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": _IMAGE_PNG,
                        "data": screenshot_b64,
                    },
                })

                tool_result_parts.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": content,
                })

            if on_turn:
                on_turn(CUTurnRecord(
                    turn=turn + 1, model_text=turn_text,
                    actions=results, screenshot_b64=screenshot_b64,
                ))

            messages.append({"role": "user", "content": tool_result_parts})

        return final_text

    async def _execute_claude_action(
        self, action_input: dict, executor: ActionExecutor,
        *, scale_factor: float = 1.0,
    ) -> CUActionResult:
        """Map Claude computer tool actions to executor calls.

        Claude actions (computer_20251124): screenshot, click, double_click,
        type, key, scroll, mouse_move, left_click_drag, triple_click,
        right_click, middle_click, left_mouse_down, left_mouse_up,
        hold_key, wait, zoom.

        Claude uses REAL pixel coordinates — no denormalization.
        When screenshot scaling is active, coordinates are upscaled by
        dividing by scale_factor before passing to the executor.
        """
        action = action_input.get("action", "")

        if action == "screenshot":
            return CUActionResult(name="screenshot")

        def _upscale_coord(coord: list[int] | None) -> list[int] | None:
            """Upscale Claude's coordinates back to real screen pixels."""
            if coord is None or scale_factor >= 1.0:
                return coord
            return [int(c / scale_factor) for c in coord]

        # Build args in the CU format the executor expects
        coord = _upscale_coord(action_input.get("coordinate"))
        args: dict[str, Any] = {}

        if action in ("click", "double_click", "right_click", "triple_click", "middle_click"):
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            if action in ("double_click", "right_click", "triple_click", "middle_click"):
                return await self._special_click(action, coord, executor)
            return await executor.execute("click_at", args)

        elif action == "type":
            text = action_input.get("text", "")
            page = getattr(executor, "page", None)
            if page:
                try:
                    await page.keyboard.type(text)
                    return CUActionResult(name="type", extra={"text": text})
                except Exception as exc:
                    return CUActionResult(name="type", success=False, error=str(exc))
            try:
                result = await executor.execute("type_at_cursor", {
                    "text": text,
                    "press_enter": False,
                })
                return CUActionResult(
                    name="type", success=result.success,
                    error=result.error, extra={"text": text},
                )
            except Exception as exc:
                return CUActionResult(name="type", success=False, error=str(exc))

        elif action == "key":
            key = action_input.get("key", "")
            KEY_MAP = {"Return": "Enter", "space": "Space"}
            args["keys"] = KEY_MAP.get(key, key)
            return await executor.execute("key_combination", args)

        elif action == "scroll":
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            args["direction"] = action_input.get("direction", "down")
            amount = action_input.get("amount", 3)
            args["magnitude"] = min(999, amount * 200)
            return await executor.execute("scroll_at", args)

        elif action == "mouse_move":
            if coord:
                args["x"], args["y"] = coord[0], coord[1]
            return await executor.execute("hover_at", args)

        elif action == "left_click_drag":
            start = _upscale_coord(
                action_input.get("start_coordinate", coord or [0, 0])
            )
            end = _upscale_coord(action_input.get("coordinate", [0, 0]))
            args["x"], args["y"] = start[0], start[1]
            args["destination_x"], args["destination_y"] = end[0], end[1]
            return await executor.execute("drag_and_drop", args)

        elif action == "left_mouse_down":
            page = getattr(executor, "page", None)
            if page:
                try:
                    await page.mouse.down()
                    return CUActionResult(name="left_mouse_down")
                except Exception as exc:
                    return CUActionResult(name="left_mouse_down", success=False, error=str(exc))
            return CUActionResult(name="left_mouse_down", success=False,
                                  error="left_mouse_down not supported on desktop executor")

        elif action == "left_mouse_up":
            page = getattr(executor, "page", None)
            if page:
                try:
                    await page.mouse.up()
                    return CUActionResult(name="left_mouse_up")
                except Exception as exc:
                    return CUActionResult(name="left_mouse_up", success=False, error=str(exc))
            return CUActionResult(name="left_mouse_up", success=False,
                                  error="left_mouse_up not supported on desktop executor")

        elif action == "hold_key":
            key = action_input.get("key", "")
            duration = action_input.get("duration", 1)
            page = getattr(executor, "page", None)
            if page:
                try:
                    await page.keyboard.down(key)
                    await asyncio.sleep(min(duration, 10))
                    await page.keyboard.up(key)
                    return CUActionResult(name="hold_key", extra={"key": key, "duration": duration})
                except Exception as exc:
                    return CUActionResult(name="hold_key", success=False, error=str(exc))
            return CUActionResult(name="hold_key", success=False,
                                  error="hold_key not supported on desktop executor")

        elif action == "wait":
            duration = action_input.get("duration", 5)
            await asyncio.sleep(min(duration, 30))
            return CUActionResult(name="wait", extra={"duration": duration})

        elif action == "zoom":
            # Zoom returns a cropped screenshot region — we acknowledge it
            # but the actual zoom behavior is handled by the API when
            # enable_zoom is set in the tool definition.
            return CUActionResult(name="zoom")

        else:
            return CUActionResult(name=action, success=False,
                                  error=f"Unknown Claude action: {action}")

    async def _special_click(
        self, action: str, coord: list[int] | None, executor: ActionExecutor,
    ) -> CUActionResult:
        """Handle double_click, right_click, triple_click, middle_click via Playwright/xdotool.

        Playwright path uses native dblclick / button="right" / click_count=3 / button="middle".
        Desktop path delegates to the executor's dedicated ``_act_*`` handlers.
        """
        x, y = (coord[0], coord[1]) if coord else (0, 0)
        page = getattr(executor, "page", None)

        if page:
            try:
                if action == "double_click":
                    await page.mouse.dblclick(x, y)
                elif action == "right_click":
                    await page.mouse.click(x, y, button="right")
                elif action == "triple_click":
                    await page.mouse.click(x, y, click_count=3)
                elif action == "middle_click":
                    await page.mouse.click(x, y, button="middle")
                return CUActionResult(name=action, extra={"x": x, "y": y})
            except Exception as exc:
                return CUActionResult(name=action, success=False, error=str(exc))

        # Desktop path — use executor.execute which dispatches to
        # _act_double_click / _act_right_click / _act_triple_click.
        try:
            return await executor.execute(action, {"x": x, "y": y})
        except Exception as exc:
            return CUActionResult(name=action, success=False, error=str(exc))


class OpenAICUClient:
    """OpenAI Responses API computer-use client.

    Uses the built-in ``computer`` tool with ``gpt-5.4`` or another
    allowlisted OpenAI model. The harness executes all returned actions and
    returns screenshots through ``computer_call_output`` items.
    """

    VALID_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4",
        system_prompt: str | None = None,
        reasoning_effort: str = "low",
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai is required. Install: pip install openai"
            ) from exc

        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt or ""
        if reasoning_effort not in self.VALID_REASONING_EFFORTS:
            reasoning_effort = "low"
        self._reasoning_effort = reasoning_effort

    async def _create_response(self, **kwargs: Any) -> Any:
        """Call the synchronous OpenAI SDK without blocking the event loop."""
        return await asyncio.to_thread(self._client.responses.create, **kwargs)

    async def run_loop(
        self,
        goal: str,
        executor: ActionExecutor,
        *,
        turn_limit: int = DEFAULT_TURN_LIMIT,
        on_safety: Callable[[str], bool] | None = None,
        on_turn: Callable[[CUTurnRecord], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Run the OpenAI native computer-use loop via the Responses API."""
        screenshot_bytes = await executor.capture_screenshot()
        if not screenshot_bytes or len(screenshot_bytes) < 100:
            if on_log:
                on_log("error", "Initial screenshot capture failed or returned empty bytes")
            return "Error: Could not capture initial screenshot"

        next_input: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": goal},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{base64.standard_b64encode(screenshot_bytes).decode()}",
                        "detail": "original",
                    },
                ],
            }
        ]
        previous_response_id: str | None = None
        final_text = ""

        for turn in range(turn_limit):
            if on_log:
                on_log("info", f"OpenAI CU turn {turn + 1}/{turn_limit}")

            request: dict[str, Any] = {
                "model": self._model,
                "input": next_input,
                "tools": [{"type": "computer"}],
                "parallel_tool_calls": False,
                "reasoning": {"effort": self._reasoning_effort},
                "truncation": "auto",
            }
            if previous_response_id:
                request["previous_response_id"] = previous_response_id
            if self._system_prompt:
                request["instructions"] = self._system_prompt

            response = await self._create_response(**request)
            response_error = getattr(response, "error", None)
            if response_error:
                raise RuntimeError(getattr(response_error, "message", str(response_error)))

            previous_response_id = getattr(response, "id", previous_response_id)
            output_items = list(getattr(response, "output", []) or [])
            turn_text = getattr(response, "output_text", "") or _extract_openai_output_text(output_items)
            computer_calls = [
                item for item in output_items
                if getattr(item, "type", None) == "computer_call"
            ]

            if not computer_calls:
                final_text = turn_text or "OpenAI completed without a final message."
                if on_log:
                    on_log("info", f"OpenAI CU completed: {final_text[:200]}")
                if on_turn:
                    on_turn(CUTurnRecord(turn=turn + 1, model_text=turn_text, actions=[]))
                break

            tool_outputs: list[dict[str, Any]] = []
            results: list[CUActionResult] = []
            screenshot_b64: str | None = None
            terminated = False

            for computer_call in computer_calls:
                acknowledged_safety_checks: list[dict[str, Any]] | None = None
                pending_checks = [
                    _to_plain_dict(check)
                    for check in (getattr(computer_call, "pending_safety_checks", None) or [])
                ]
                if pending_checks:
                    explanation = " | ".join(
                        check.get("message") or check.get("code") or "Safety acknowledgement required"
                        for check in pending_checks
                    )
                    confirmed = on_safety(explanation) if on_safety else False
                    if not confirmed:
                        final_text = "Agent terminated: safety confirmation denied."
                        terminated = True
                        break
                    acknowledged_safety_checks = []
                    for check in pending_checks:
                        ack: dict[str, Any] = {"id": check["id"]}
                        if check.get("code") is not None:
                            ack["code"] = check["code"]
                        if check.get("message") is not None:
                            ack["message"] = check["message"]
                        acknowledged_safety_checks.append(ack)

                actions = list(getattr(computer_call, "actions", None) or [])
                if not actions:
                    single_action = getattr(computer_call, "action", None)
                    if single_action is not None:
                        actions = [single_action]

                for action in actions:
                    result = await self._execute_openai_action(action, executor)
                    results.append(result)
                    # Inter-action delay matching official CUA sample (120ms)
                    if action is not actions[-1]:
                        await asyncio.sleep(0.12)

                screenshot_bytes = await executor.capture_screenshot()
                screenshot_b64 = base64.standard_b64encode(screenshot_bytes).decode()
                tool_outputs.append(
                    _build_openai_computer_call_output(
                        getattr(computer_call, "call_id"),
                        screenshot_b64,
                        acknowledged_safety_checks=acknowledged_safety_checks,
                    )
                )

            if on_turn:
                on_turn(CUTurnRecord(
                    turn=turn + 1,
                    model_text=turn_text,
                    actions=results,
                    screenshot_b64=screenshot_b64,
                ))

            if terminated:
                break
            if not tool_outputs:
                final_text = turn_text or "OpenAI returned no actionable computer calls."
                break

            next_input = tool_outputs
        else:
            final_text = f"OpenAI CU reached the turn limit ({turn_limit}) without a final response."

        return final_text

    async def _execute_openai_action(
        self,
        action: Any,
        executor: ActionExecutor,
    ) -> CUActionResult:
        """Translate OpenAI computer actions to the shared executor contract."""
        payload = _to_plain_dict(action)
        action_type = str(payload.get("type", ""))

        def _coords(*keys: str) -> tuple[int | None, ...]:
            values: list[int | None] = []
            for key in keys:
                raw = payload.get(key)
                values.append(int(raw) if isinstance(raw, (int, float)) else None)
            return tuple(values)

        if action_type == "screenshot":
            return CUActionResult(name="screenshot")

        if action_type == "click":
            x, y = _coords("x", "y")
            button = str(payload.get("button", "left")).lower()
            if x is None or y is None:
                return CUActionResult(name="click", success=False, error="Click action missing coordinates")
            if button == "right":
                return await executor.execute("right_click", {"x": x, "y": y})
            if button in {"middle", "wheel"}:
                return await executor.execute("middle_click", {"x": x, "y": y})
            return await executor.execute("click_at", {"x": x, "y": y})

        if action_type == "double_click":
            x, y = _coords("x", "y")
            if x is None or y is None:
                return CUActionResult(name="double_click", success=False, error="Double-click action missing coordinates")
            return await executor.execute("double_click", {"x": x, "y": y})

        if action_type == "move":
            x, y = _coords("x", "y")
            if x is None or y is None:
                return CUActionResult(name="move", success=False, error="Move action missing coordinates")
            return await executor.execute("move", {"x": x, "y": y})

        if action_type == "type":
            return await executor.execute("type_at_cursor", {
                "text": str(payload.get("text", "")),
                "press_enter": False,
            })

        if action_type == "keypress":
            keys = payload.get("keys")
            if not isinstance(keys, list):
                single_key = payload.get("key")
                keys = [single_key] if single_key else []
            normalized = "+".join(self._normalize_openai_keys(keys))
            if not normalized:
                return CUActionResult(name="keypress", success=False, error="Keypress action missing keys")
            return await executor.execute("key_combination", {"keys": normalized})

        if action_type == "wait":
            duration_ms = payload.get("ms")
            if not isinstance(duration_ms, (int, float)):
                duration_ms = payload.get("duration_ms")
            if not isinstance(duration_ms, (int, float)):
                duration_ms = 2000
            await asyncio.sleep(max(0.0, min(float(duration_ms), 30_000.0)) / 1000.0)
            return CUActionResult(name="wait", extra={"duration_ms": int(duration_ms)})

        if action_type == "scroll":
            return await self._execute_openai_scroll(payload, executor)

        if action_type == "drag":
            path = payload.get("path")
            start_x: int | None = None
            start_y: int | None = None
            end_x: int | None = None
            end_y: int | None = None
            if isinstance(path, list) and len(path) >= 2:
                first = _to_plain_dict(path[0])
                last = _to_plain_dict(path[-1])
                start_x = int(first.get("x")) if isinstance(first.get("x"), (int, float)) else None
                start_y = int(first.get("y")) if isinstance(first.get("y"), (int, float)) else None
                end_x = int(last.get("x")) if isinstance(last.get("x"), (int, float)) else None
                end_y = int(last.get("y")) if isinstance(last.get("y"), (int, float)) else None
            if start_x is None or start_y is None:
                start_x, start_y = _coords("x", "y")
            if end_x is None or end_y is None:
                end_x, end_y = _coords("destination_x", "destination_y")
            if None in {start_x, start_y, end_x, end_y}:
                return CUActionResult(name="drag", success=False, error="Drag action missing path coordinates")
            return await executor.execute("drag_and_drop", {
                "x": start_x,
                "y": start_y,
                "destination_x": end_x,
                "destination_y": end_y,
            })

        return CUActionResult(
            name=action_type or "unknown",
            success=False,
            error=f"Unsupported OpenAI action: {action_type}",
        )

    async def _execute_openai_scroll(
        self,
        payload: dict[str, Any],
        executor: ActionExecutor,
    ) -> CUActionResult:
        """Execute OpenAI pixel scroll actions in browser or desktop mode."""
        x = payload.get("x")
        y = payload.get("y")
        px = int(x) if isinstance(x, (int, float)) else None
        py = int(y) if isinstance(y, (int, float)) else None
        delta_x = payload.get("delta_x", payload.get("deltaX", 0))
        delta_y = payload.get("delta_y", payload.get("deltaY", payload.get("scroll_y", 0)))
        dx = int(delta_x) if isinstance(delta_x, (int, float)) else 0
        dy = int(delta_y) if isinstance(delta_y, (int, float)) else 0

        page = getattr(executor, "page", None)
        if page is not None:
            try:
                if px is not None and py is not None:
                    await page.mouse.move(px, py)
                await page.mouse.wheel(dx, dy)
                return CUActionResult(name="scroll", extra={
                    "x": px, "y": py, "delta_x": dx, "delta_y": dy,
                })
            except Exception as exc:
                return CUActionResult(name="scroll", success=False, error=str(exc))

        dominant_y = abs(dy) >= abs(dx)
        if dominant_y:
            direction = "down" if dy >= 0 else "up"
            magnitude = abs(dy)
        else:
            direction = "right" if dx >= 0 else "left"
            magnitude = abs(dx)
        args: dict[str, Any] = {
            "direction": direction,
            "magnitude": min(max(magnitude, 200), 999),
        }
        if px is not None and py is not None:
            args["x"] = px
            args["y"] = py
        return await executor.execute("scroll_at", args)

    @staticmethod
    def _normalize_openai_keys(keys: list[Any]) -> list[str]:
        """Normalize OpenAI keypress values for Playwright and xdotool."""
        key_map = {
            "SPACE": "Space",
            "ENTER": "Enter",
            "RETURN": "Enter",
            "ESC": "Escape",
            "ESCAPE": "Escape",
            "CTRL": "Control",
            "CMD": "Meta",
            "COMMAND": "Meta",
            "OPTION": "Alt",
            "PGUP": "PageUp",
            "PGDN": "PageDown",
        }
        normalized: list[str] = []
        for key in keys:
            if key is None:
                continue
            token = str(key).strip()
            if not token:
                continue
            normalized.append(key_map.get(token.upper(), token))
        return normalized


# ---------------------------------------------------------------------------
# Claude context pruning
# ---------------------------------------------------------------------------


def _prune_claude_context(messages: list[dict], keep_recent: int) -> None:
    """Replace base64 screenshot data in old turns with a placeholder.

    Keeps the first user message (goal + initial screenshot) and the last
    *keep_recent* message pairs intact.  Older tool_result images are
    replaced with ``[screenshot omitted]``.
    """
    if len(messages) <= keep_recent + 1:
        return
    prune_end = len(messages) - keep_recent
    for idx in range(1, prune_end):
        msg = messages[idx]
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            # tool_result → strip images inside "content" list
            if part.get("type") == "tool_result" and isinstance(part.get("content"), list):
                new_inner: list[dict] = []
                for inner in part["content"]:
                    if isinstance(inner, dict) and inner.get("type") == "image":
                        new_inner.append({"type": "text", "text": "[screenshot omitted]"})
                    else:
                        new_inner.append(inner)
                part["content"] = new_inner
            # Standalone images in user messages
            elif part.get("type") == "image":
                part.clear()
                part["type"] = "text"
                part["text"] = "[screenshot omitted]"


# ---------------------------------------------------------------------------
# Unified ComputerUseEngine facade
# ---------------------------------------------------------------------------

class ComputerUseEngine:
    """Single entry point for native Computer Use across providers and environments.

    The single engine for this application. Uses the native CU protocol
    from Gemini or Claude with Playwright (browser) or xdotool (desktop)
    executors.

    Usage::

        engine = ComputerUseEngine(
            provider=Provider.GEMINI,
            api_key="AIza...",
            environment=Environment.BROWSER,
        )
        final_text = await engine.execute_task(
            "Search for flights to Paris",
            page=playwright_page,
        )
    """

    def __init__(
        self,
        provider: Provider,
        api_key: str,
        model: str | None = None,
        environment: Environment = Environment.BROWSER,
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
        """Build PlaywrightExecutor (browser) or DesktopExecutor (desktop)."""
        # Gemini uses normalized 0-999 coords; Claude uses real pixels
        normalize = self.provider == Provider.GEMINI

        if self.environment == Environment.BROWSER:
            if page is None:
                raise ValueError("Browser environment requires a Playwright page")
            return PlaywrightExecutor(
                page=page,
                screen_width=self.screen_width,
                screen_height=self.screen_height,
                normalize_coords=normalize,
            )
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
            page: Playwright async Page (required for BROWSER, optional for DESKTOP).
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
