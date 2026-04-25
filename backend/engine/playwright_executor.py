"""Playwright-backed action executor for Gemini browser-mode sessions.

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

from __future__ import annotations

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
    return os.environ.get("CUA_GEMINI_CDP_ENDPOINT", _DEFAULT_CDP_ENDPOINT)


class GeminiPlaywrightExecutor:
    """``ActionExecutor`` that drives the sandbox Chromium via CDP.

    Supports the Gemini browser-mode UI actions documented at
    https://ai.google.dev/gemini-api/docs/computer-use#supported-ui-actions
    : ``open_web_browser``, ``wait_5_seconds``, ``go_back``,
    ``go_forward``, ``search``, ``navigate``, ``click_at``,
    ``hover_at``, ``type_text_at``, ``key_combination``,
    ``scroll_document``, ``scroll_at``, and ``drag_and_drop``.
    Coordinate args use Gemini's 0–999 normalized grid and are
    denormalised against the configured ``screen_width`` /
    ``screen_height`` per the docs example.
    """

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        cdp_endpoint: str | None = None,
    ) -> None:
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._cdp_endpoint = cdp_endpoint or _cdp_endpoint()
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
        """Denormalise Gemini 0-999 coords to viewport pixels."""
        return (x / 1000 * self.screen_width, y / 1000 * self.screen_height)

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
            await self._dispatch(name, args)
            try:
                await self._page.wait_for_load_state(timeout=5000)
            except Exception:
                pass
            return CUActionResult(name=name, success=True)
        except _UnimplementedAction as exc:
            return CUActionResult(name=name, success=False, error=str(exc))
        except Exception as exc:
            logger.error("PlaywrightExecutor %s failed: %s: %s",
                         name, type(exc).__name__, exc)
            return CUActionResult(name=name, success=False, error=str(exc))

    async def _dispatch(self, name: str, args: dict[str, Any]) -> None:
        page = self._page
        if name == "open_web_browser":
            return  # browser is already open in the sandbox
        if name == "wait_5_seconds":
            await asyncio.sleep(5)
            return
        if name == "go_back":
            await page.go_back()
            return
        if name == "go_forward":
            await page.go_forward()
            return
        if name == "search":
            await page.goto("https://www.google.com")
            return
        if name == "navigate":
            await page.goto(args["url"])
            return
        if name == "click_at":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.click(x, y)
            return
        if name == "hover_at":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.move(x, y)
            return
        if name == "type_text_at":
            x, y = self._px(args["x"], args["y"])
            await page.mouse.click(x, y)
            if args.get("clear_before_typing", True):
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
            await page.keyboard.type(args.get("text", ""))
            if args.get("press_enter", True):
                await page.keyboard.press("Enter")
            return
        if name == "key_combination":
            await page.keyboard.press(_normalize_keys(args["keys"]))
            return
        if name == "scroll_document":
            dx, dy = _scroll_delta(args.get("direction", "down"), 800)
            await page.mouse.wheel(dx, dy)
            return
        if name == "scroll_at":
            x, y = self._px(args["x"], args["y"])
            magnitude = int(args.get("magnitude", 800))
            dx, dy = _scroll_delta(args.get("direction", "down"), magnitude)
            await page.mouse.move(x, y)
            await page.mouse.wheel(dx, dy)
            return
        if name == "drag_and_drop":
            x1, y1 = self._px(args["x"], args["y"])
            x2, y2 = self._px(args["destination_x"], args["destination_y"])
            await page.mouse.move(x1, y1)
            await page.mouse.down()
            await page.mouse.move(x2, y2)
            await page.mouse.up()
            return
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
