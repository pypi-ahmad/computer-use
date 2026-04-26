from __future__ import annotations
"""Playwright-backed action executor for browser-mode sessions.

The Google Gemini Computer Use docs (https://ai.google.dev/gemini-api/docs/computer-use)
use Playwright as the client-side action handler in their reference
implementation: the model emits normalized 0–999 coordinates which the
application denormalises and dispatches via ``page.mouse``,
``page.keyboard``, ``page.goto``, etc. Screenshots come from
``page.screenshot()`` and the current URL from ``page.url``.

To honour that contract **while preserving the unified Docker
sandbox**, this module does NOT launch a host-side browser. Instead
it connects to the Chromium that the container's ``entrypoint.sh``
pre-launches with ``--remote-debugging-port=9223``. Because
``docker-compose.yml`` already publishes ``127.0.0.1:9223:9223``, the
backend talks to the in-container Chromium through Playwright's
``connect_over_cdp(...)`` API. The browser remains sandboxed (UID
1000, dropped capabilities, no host filesystem) AND visible inside
the existing noVNC viewer; only Playwright's wire protocol crosses
the container boundary.
"""


import asyncio
import logging
import os
from typing import Any

from backend.engine import CUActionResult

logger = logging.getLogger(__name__)


# Default CDP endpoint exposed by ``docker/entrypoint.sh`` (in turn
# published to host loopback by ``docker-compose.yml``).
_DEFAULT_CDP_ENDPOINT = "http://127.0.0.1:9223"


def _cdp_endpoint() -> str:
    """Return the CDP endpoint the executor should connect to."""
    return (
        os.environ.get("CUA_BROWSER_CDP_ENDPOINT")
        or os.environ.get("CUA_GEMINI_CDP_ENDPOINT")
        or _DEFAULT_CDP_ENDPOINT
    )


def browser_playwright_enabled() -> bool:
    """Return True when browser-mode sessions should use Playwright.

    ``CUA_BROWSER_USE_PLAYWRIGHT=0`` is the shared opt-out. The legacy
    ``CUA_GEMINI_USE_PLAYWRIGHT=0`` flag remains supported when the
    shared flag is unset.
    """
    flag = os.environ.get("CUA_BROWSER_USE_PLAYWRIGHT")
    if flag is None:
        flag = os.environ.get("CUA_GEMINI_USE_PLAYWRIGHT")
    if flag is not None and flag.strip() == "0":
        return False
    try:
        import playwright  # noqa: F401
    except ImportError:
        logger.error(
            "Browser mode requested the Playwright Chromium layer but the "
            "`playwright` package is not installed in the backend venv. "
            "Install via `pip install -r requirements.txt`. Falling back "
            "to the desktop xdotool path for this session."
        )
        return False
    return True


class ChromiumPlaywrightExecutor:
    """``ActionExecutor`` that drives the sandbox Chromium via CDP.

    This is the provider-neutral Chromium browser layer used by browser-mode
    sessions. Gemini uses normalized coordinates; Anthropic and OpenAI use
    pixel coordinates.
    """

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        cdp_endpoint: str | None = None,
        normalize_coords: bool = False,
    ) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._cdp_endpoint = cdp_endpoint or _cdp_endpoint()
        self._normalize_coords = normalize_coords
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._start_lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def _ensure_started(self) -> None:
        """Lazily attach to the in-container Chromium on first use."""
        if self._page is not None:
            return
        async with self._start_lock:
            if self._page is not None:
                return
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(
                self._cdp_endpoint,
            )
            # Prefer an existing context+page so the user sees the same
            # tab the entrypoint pre-launched in noVNC. Fall back to a
            # fresh context/page if the browser came up empty.
            contexts = self._browser.contexts
            if contexts and contexts[0].pages:
                self._context = contexts[0]
                self._page = self._context.pages[0]
            else:
                self._context = (
                    contexts[0] if contexts else await self._browser.new_context(
                        viewport={
                            "width": self.screen_width,
                            "height": self.screen_height,
                        },
                    )
                )
                self._page = await self._context.new_page()
            try:
                await self._page.set_viewport_size({
                    "width": self.screen_width,
                    "height": self.screen_height,
                })
            except Exception:  # pragma: no cover - viewport is advisory
                pass

    async def aclose(self) -> None:
        """Detach from the in-container Chromium.

        We connected via CDP, so we don't kill the browser — the
        container owns its lifecycle. We just release Playwright's
        client-side handles.
        """
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:  # pragma: no cover - best-effort shutdown
            logger.debug("PlaywrightExecutor browser close failed", exc_info=True)
        finally:
            try:
                if self._pw is not None:
                    await self._pw.stop()
            except Exception:  # pragma: no cover
                logger.debug("PlaywrightExecutor stop failed", exc_info=True)
            self._page = None
            self._browser = None
            self._context = None
            self._pw = None

    # ── ActionExecutor protocol ──────────────────────────────────────

    def _px(self, x: int, y: int) -> tuple[float, float]:
        """Map model coordinates to viewport pixels."""
        if self._normalize_coords:
            return (x / 1000 * self.screen_width, y / 1000 * self.screen_height)
        return float(x), float(y)

    async def capture_screenshot(self) -> bytes:
        await self._ensure_started()
        return await self._page.screenshot(type="png")

    def get_current_url(self) -> str:
        if self._page is None:
            return ""
        try:
            return self._page.url or ""
        except Exception:
            return ""

    async def execute(self, name: str, args: dict[str, Any]) -> CUActionResult:
        await self._ensure_started()
        try:
            extra = await self._dispatch(name, args) or {}
            if name not in {
                "hover_at", "move", "scroll_document", "scroll_at",
                "left_mouse_down", "left_mouse_up", "hold_key", "zoom",
            }:
                try:
                    await self._page.wait_for_load_state(timeout=5000)
                except Exception:
                    pass
            return CUActionResult(name=name, success=True, extra=extra)
        except _UnimplementedAction as exc:
            return CUActionResult(name=name, success=False, error=str(exc))
        except Exception as exc:
            logger.error("PlaywrightExecutor %s failed: %s: %s",
                         name, type(exc).__name__, exc)
            return CUActionResult(name=name, success=False, error=str(exc))

    async def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        page = self._page
        if name == "open_web_browser":
            return {}
        if name == "wait_5_seconds":
            await asyncio.sleep(5)
            return {"duration_seconds": 5}
        if name == "go_back":
            await page.go_back()
            return {}
        if name == "go_forward":
            await page.go_forward()
            return {}
        if name == "search":
            await page.goto("https://www.google.com")
            return {"url": page.url}
        if name == "navigate":
            await page.goto(args["url"])
            return {"url": args["url"]}
        if name == "click_at":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.click(x, y)
            return {"pixel_x": x, "pixel_y": y}
        if name == "double_click":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.dblclick(x, y)
            return {"pixel_x": x, "pixel_y": y}
        if name == "right_click":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.click(x, y, button="right")
            return {"pixel_x": x, "pixel_y": y}
        if name == "middle_click":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.click(x, y, button="middle")
            return {"pixel_x": x, "pixel_y": y}
        if name == "triple_click":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.click(x, y, click_count=3)
            return {"pixel_x": x, "pixel_y": y}
        if name == "hover_at":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.move(x, y)
            return {"pixel_x": x, "pixel_y": y}
        if name == "move":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.move(x, y)
            return {"pixel_x": x, "pixel_y": y}
        if name == "type_text_at":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.click(x, y)
            if args.get("clear_before_typing", True):
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
            await page.keyboard.type(args.get("text", ""))
            if args.get("press_enter", True):
                await page.keyboard.press("Enter")
            return {"pixel_x": x, "pixel_y": y, "text": args.get("text", "")}
        if name == "type_at_cursor":
            await page.keyboard.type(args.get("text", ""))
            if args.get("press_enter", False):
                await page.keyboard.press("Enter")
            return {"text": args.get("text", "")}
        if name == "key_combination":
            await page.keyboard.press(_normalize_keys(args["keys"]))
            return {"keys": args["keys"]}
        if name == "scroll_document":
            dx, dy = _scroll_delta(args.get("direction", "down"), 800)
            await page.mouse.wheel(dx, dy)
            return {"direction": args.get("direction", "down")}
        if name == "scroll_at":
            raw_x = args.get("x")
            raw_y = args.get("y")
            if raw_x is not None and raw_y is not None:
                x, y = self._px(raw_x, raw_y)
                await page.mouse.move(x, y)
            magnitude = int(args.get("magnitude", 800))
            dx, dy = _scroll_delta(args.get("direction", "down"), magnitude)
            await page.mouse.wheel(dx, dy)
            extra = {
                "direction": args.get("direction", "down"),
                "magnitude": magnitude,
            }
            if raw_x is not None and raw_y is not None:
                extra["pixel_x"] = x
                extra["pixel_y"] = y
            return extra
        if name == "drag_and_drop":
            x1, y1 = self._px(args["x"], args["y"])
            x2, y2 = self._px(args["destination_x"], args["destination_y"])
            await page.mouse.move(x1, y1)
            await page.mouse.down()
            await page.mouse.move(x2, y2)
            await page.mouse.up()
            return {"from": (x1, y1), "to": (x2, y2)}
        if name == "left_mouse_down":
            await page.mouse.down()
            return {}
        if name == "left_mouse_up":
            await page.mouse.up()
            return {}
        if name == "hold_key":
            key = _normalize_keys(str(args.get("key", "")))
            duration = max(0.0, min(float(args.get("duration", 1)), 10.0))
            await page.keyboard.down(key)
            await asyncio.sleep(duration)
            await page.keyboard.up(key)
            return {"key": args.get("key", ""), "duration": duration}
        if name == "zoom":
            region = args.get("region") or []
            if len(region) != 4:
                raise _UnimplementedAction("zoom requires region=[x1, y1, x2, y2]")
            x1, y1, x2, y2 = [int(value) for value in region]
            clip = {
                "x": float(x1),
                "y": float(y1),
                "width": float(max(1, x2 - x1)),
                "height": float(max(1, y2 - y1)),
            }
            image_bytes = await page.screenshot(type="png", clip=clip)
            return {"region": [x1, y1, x2, y2], "image_bytes": image_bytes}
        raise _UnimplementedAction(f"Unimplemented playwright action: {name}")


class _UnimplementedAction(Exception):
    pass


def _scroll_delta(direction: str, magnitude: int) -> tuple[int, int]:
    table = {
        "up": (0, -magnitude),
        "down": (0, magnitude),
        "left": (-magnitude, 0),
        "right": (magnitude, 0),
    }
    return table.get(direction, (0, magnitude))


def _normalize_keys(keys: str) -> str:
    """Translate Gemini ``key_combination`` strings to Playwright form.

    Playwright expects modifier names with a leading capital
    (``Control``, ``Shift``, ``Alt``, ``Meta``) joined by ``+``. The
    docs examples use ``Control+C`` already; we also tolerate
    ``control+c`` from the model.
    """
    parts = keys.split("+")
    out: list[str] = []
    mods = {"control", "shift", "alt", "meta"}
    for part in parts:
        out.append(part.capitalize() if part.lower() in mods else part)
    return "+".join(out)


GeminiPlaywrightExecutor = ChromiumPlaywrightExecutor
