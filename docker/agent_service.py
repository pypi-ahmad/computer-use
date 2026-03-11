"""Internal agent service — runs INSIDE the Docker container.

Provides a lightweight HTTP API with two automation modes:

  • browser  — Playwright (page.mouse.click, page.keyboard.type, page.goto)
  • desktop  — xdotool + scrot (works with ANY X11 application)

The backend selects the mode per-request via a `mode` field.  Screenshots
and actions are dispatched to the appropriate handler automatically.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Lock

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
)
logger = logging.getLogger("agent_service")

try:
    from backend.action_aliases import resolve_action
except ImportError:
    # Fallback if backend not available (e.g. local dev outside docker)
    logger.warning("backend.action_aliases not found, alias resolution disabled")

    def resolve_action(a):
        """Identity fallback when backend.tools is unavailable."""
        return a

# ── Globals ───────────────────────────────────────────────────────────────────

_playwright = None
_browser = None
_context = None
_page = None
_lock = Lock()

SCREEN_WIDTH = int(os.environ.get("SCREEN_WIDTH", "1440"))
SCREEN_HEIGHT = int(os.environ.get("SCREEN_HEIGHT", "900"))
SERVICE_PORT = int(os.environ.get("AGENT_SERVICE_PORT", "9222"))
DEFAULT_MODE = os.environ.get("AGENT_MODE", "browser")  # "browser" or "desktop"
ACTION_DELAY = float(os.environ.get("ACTION_DELAY", "0.05"))


def _env_bool(name: str, default: bool = True) -> bool:
    """Parse a boolean-like environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


WINDOW_NORMALIZE_ENABLED = _env_bool("CUA_WINDOW_NORMALIZE", True)
WINDOW_NORMALIZE_X = int(os.environ.get("CUA_WINDOW_X", "100"))
WINDOW_NORMALIZE_Y = int(os.environ.get("CUA_WINDOW_Y", "80"))
WINDOW_NORMALIZE_W = int(os.environ.get("CUA_WINDOW_W", "560"))
WINDOW_NORMALIZE_H = int(os.environ.get("CUA_WINDOW_H", "760"))

# ── Security constants ────────────────────────────────────────────────────────

_MAX_BODY_SIZE = 1_000_000  # 1 MB request body limit

# Dangerous shell patterns blocked in run_command (defense-in-depth)
_BLOCKED_CMD_PATTERNS = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs.",
    "dd if=/dev/",
    ":(){",
    "shutdown",
    "reboot",
    "halt ",
    "poweroff",
    "chmod -R 777 /",
    "> /dev/sda",
    "mv /* ",
    "mv / ",
)

# Allowed directories for file upload operations
_UPLOAD_ALLOWED_PREFIXES = ("/tmp", "/app", "/home")

# Strict allowlist of commands permitted in run_command
_ALLOWED_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "grep", "find", "wc", "echo",
    "pwd", "whoami", "id", "date", "env", "printenv",
    "which", "file", "stat", "df", "du", "free",
    "uname", "hostname", "uptime",
    "python3", "python", "pip", "pip3", "node", "npm", "npx",
    "curl", "wget",
    "xdg-open", "xdotool", "xclip", "scrot", "wmctrl",
    "xfce4-terminal", "xterm",
    # Desktop apps accessible via accessibility / run_command
    "gnome-control-center", "gnome-settings", "gnome-calculator",
    "gnome-text-editor", "gedit", "gnome-system-monitor",
    "xfce4-settings-manager", "xfce4-settings-editor",
    "xfce4-taskmanager", "thunar", "mousepad",
    "firefox", "google-chrome",
    # Browsers added via Dockerfile
    "brave-browser", "microsoft-edge", "microsoft-edge-stable",
    # Desktop apps added via Dockerfile
    "vlc", "libreoffice", "soffice",
    "evince", "gnome-terminal", "flameshot", "xournalpp",
    "htop",
})

# Ensure DISPLAY is set for all subprocesses (Critical Desktop Fix)
os.environ["DISPLAY"] = ":99"



# ── Playwright lifecycle ──────────────────────────────────────────────────────

def _init_browser():
    """Launch Playwright + Chromium synchronously."""
    global _playwright, _browser, _context, _page

    try:
        subprocess.run(["xclip", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logger.warning("xclip not found - clipboard actions (paste/copy) in desktop mode may fail")
    
    from playwright.sync_api import sync_playwright

    logger.info("Initializing Playwright...")
    _playwright = sync_playwright().start()

    _browser = _playwright.chromium.launch(
        headless=False,  # Visible on Xvfb
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            f"--window-size={SCREEN_WIDTH - 100},{SCREEN_HEIGHT - 80}",
            "--window-position=50,10",
            "--disable-extensions",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--force-device-scale-factor=1",
            "--remote-debugging-port=9223",
        ],
    )

    _context = _browser.new_context(
        viewport={"width": SCREEN_WIDTH - 100, "height": SCREEN_HEIGHT - 80},
        device_scale_factor=1,
    )

    _page = _context.new_page()
    _page.goto("about:blank")

    logger.info("Playwright browser ready: %dx%d", SCREEN_WIDTH, SCREEN_HEIGHT)


def _get_page():
    """Return the active page, creating if needed."""
    global _page
    if _page is None or _page.is_closed():
        if _context:
            _page = _context.new_page()
            _page.goto("about:blank")
    return _page


def _shutdown_browser():
    """Tear down Playwright browser, context, and page."""
    global _playwright, _browser, _context, _page
    try:
        if _page and not _page.is_closed():
            _page.close()
        if _context:
            _context.close()
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()
    except Exception as e:
        logger.warning("Shutdown error: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
#  Screenshots
# ══════════════════════════════════════════════════════════════════════════════

def _screenshot_playwright() -> str:
    """Capture current browser tab as PNG via Playwright."""
    page = _get_page()
    png_bytes = page.screenshot(type="png", full_page=False)
    return base64.b64encode(png_bytes).decode("ascii")


def _screenshot_desktop() -> str:
    """Capture the full Xvfb display via scrot (works with any app)."""
    subprocess.run(
        ["scrot", "-z", "-o", "/tmp/screenshot.png"],
        check=True, timeout=5,
    )
    with open("/tmp/screenshot.png", "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


# ══════════════════════════════════════════════════════════════════════════════
#  Actions — Playwright (browser mode)
# ══════════════════════════════════════════════════════════════════════════════

def _pw_click(x: int, y: int) -> dict:
    """Click at pixel coordinates via Playwright."""
    page = _get_page()
    page.mouse.click(x, y)
    return {"success": True, "message": f"Clicked at ({x}, {y})"}


def _pw_double_click(x: int, y: int) -> dict:
    """Double-click at pixel coordinates via Playwright."""
    page = _get_page()
    page.mouse.dblclick(x, y)
    return {"success": True, "message": f"Double-clicked at ({x}, {y})"}


def _pw_right_click(x: int, y: int) -> dict:
    """Right-click at pixel coordinates via Playwright."""
    page = _get_page()
    page.mouse.click(x, y, button="right")
    return {"success": True, "message": f"Right-clicked at ({x}, {y})"}


def _pw_type(text: str, coords: list = None, selector: str = None) -> dict:
    """Type text, ensuring focus if coordinates or selector are provided."""
    page = _get_page()
    
    # 1. Try to click coordinates or selector if provided to ensure focus
    if selector:
        try:
            page.click(selector)
            time.sleep(0.1)
        except Exception as e:
            logger.warning(f"Safe type click selector failed: {e}")
    elif coords and len(coords) >= 2:
        try:
            page.mouse.click(coords[0], coords[1])
            time.sleep(0.1)
        except Exception as e:
            logger.warning(f"Safe type click coords failed: {e}")

    # 2. Verify focus (optional but good for debugging)
    try:
        active_tag = page.evaluate("document.activeElement ? document.activeElement.tagName : ''")
        if active_tag == 'BODY':
            logger.warning("Typing with focus on BODY - input might fail")
    except Exception:
        pass

    # 3. Type directly (retry/fallback handled by executor)
    page.keyboard.type(text, delay=50)
    time.sleep(ACTION_DELAY)
    return {"success": True, "message": f"Typed: {text[:50]}", "error_type": None}


def _pw_scroll(x: int, y: int, direction: str) -> dict:
    """Scroll at the given coordinates in the specified direction."""
    page = _get_page()
    page.mouse.move(x, y)
    delta = -300 if direction == "up" else 300
    page.mouse.wheel(0, delta)
    return {"success": True, "message": f"Scrolled {direction} at ({x}, {y})"}


def _pw_navigate(url: str) -> dict:
    """Navigate the browser to *url*, prepending https:// if needed."""
    page = _get_page()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    return {"success": True, "message": f"Navigated to {url}"}


def _pw_key(key: str) -> dict:
    """Press a keyboard key or combo via Playwright."""
    page = _get_page()
    combo = _map_key_combo(key)
    page.keyboard.press(combo)
    return {"success": True, "message": f"Pressed key: {key}"}


def _pw_drag(x1: int, y1: int, x2: int, y2: int) -> dict:
    """Drag from (x1,y1) to (x2,y2) via Playwright mouse."""
    page = _get_page()
    page.mouse.move(x1, y1)
    page.mouse.down()
    page.mouse.move(x2, y2, steps=10)
    page.mouse.up()
    return {"success": True, "message": f"Dragged ({x1},{y1}) → ({x2},{y2})"}


def _pw_hover(x: int, y: int) -> dict:
    """Move the mouse to (x,y) without clicking."""
    page = _get_page()
    page.mouse.move(x, y)
    return {"success": True, "message": f"Hovered at ({x}, {y})"}


def _pw_middle_click(x: int, y: int) -> dict:
    """Middle-click at pixel coordinates via Playwright."""
    page = _get_page()
    page.mouse.click(x, y, button="middle")
    return {"success": True, "message": f"Middle-clicked at ({x}, {y})"}


def _pw_fill(selector: str, text: str) -> dict:
    """Fill an input using Playwright's reliable fill() — clears first.

    On selector failure, auto-discovers available form fields and returns
    them in the error message so the model can retry with the correct selector.
    """
    page = _get_page()
    try:
        page.fill(selector, text, timeout=5000)
        return {"success": True, "message": f"Filled '{selector}' with: {text[:50]}"}
    except Exception as fill_err:
        # Auto-discover available form fields so the model can self-correct
        try:
            fields_json = page.evaluate(
                "[...document.querySelectorAll('input,textarea,select')]"
                ".map(e=>({tag:e.tagName,name:e.name,id:e.id,type:e.type,"
                "placeholder:e.placeholder||''}))"
            )
            field_hints = json.dumps(fields_json, separators=(",", ":"))
            if len(field_hints) > 1500:
                field_hints = field_hints[:1500] + "…]"
        except Exception:
            field_hints = "(could not discover fields)"
        return {
            "success": False,
            "message": (
                f"fill('{selector}') failed: {fill_err}. "
                f"Available fields: {field_hints}"
            ),
        }


def _pw_hotkey(keys: list[str]) -> dict:
    """Press multiple keys simultaneously (e.g. ['ctrl','shift','t'])."""
    page = _get_page()
    combo = "+".join(_map_key_combo(k) for k in keys)
    page.keyboard.press(combo)
    return {"success": True, "message": f"Hotkey: {'+'.join(keys)}"}


def _pw_clear_input(selector: str) -> dict:
    """Clear an input field by filling it with an empty string."""
    page = _get_page()
    page.locator(selector).fill("")
    return {"success": True, "message": f"Cleared input: {selector}"}


def _pw_select_option(selector: str, value: str) -> dict:
    """Select a <select> option by value via Playwright."""
    page = _get_page()
    page.select_option(selector, value, timeout=5000)
    return {"success": True, "message": f"Selected '{value}' in {selector}"}


def _pw_paste(text: str) -> dict:
    """Clipboard-based paste (reliable fallback for typing)."""
    page = _get_page()
    page.evaluate(f"navigator.clipboard.writeText({json.dumps(text)})")
    page.keyboard.press("Control+v")
    time.sleep(0.1)
    return {"success": True, "message": f"Pasted: {text[:50]}"}


def _pw_copy() -> dict:
    """Copy the current selection to clipboard via Ctrl+C."""
    page = _get_page()
    page.keyboard.press("Control+c")
    return {"success": True, "message": "Copied selection to clipboard"}


def _pw_reload() -> dict:
    """Reload the current page."""
    page = _get_page()
    page.reload(wait_until="domcontentloaded", timeout=30000)
    return {"success": True, "message": "Page reloaded"}


def _pw_go_back() -> dict:
    """Navigate back one page."""
    page = _get_page()
    page.go_back(wait_until="domcontentloaded", timeout=30000)
    return {"success": True, "message": "Navigated back"}


def _pw_go_forward() -> dict:
    """Navigate forward one page."""
    page = _get_page()
    page.go_forward(wait_until="domcontentloaded", timeout=30000)
    return {"success": True, "message": "Navigated forward"}


def _pw_new_tab(url: str = "") -> dict:
    """Open a new browser tab, optionally navigating to *url*."""
    global _page
    target = url or "about:blank"
    if url and not url.startswith(("http://", "https://")):
        target = "https://" + url
    page = _context.new_page()
    page.goto(target)
    _page = page
    return {"success": True, "message": f"Opened new tab: {target}"}


def _pw_close_tab() -> dict:
    """Close the currently active browser tab."""
    global _page
    page = _get_page()
    page.close()
    pages = _context.pages
    _page = pages[-1] if pages else None
    return {"success": True, "message": "Closed current tab"}


def _pw_switch_tab(identifier: str) -> dict:
    """Switch tab by index (0-based) or partial title/URL match."""
    global _page
    pages = _context.pages
    # Try numeric index first
    try:
        idx = int(identifier)
        if 0 <= idx < len(pages):
            _page = pages[idx]
            _page.bring_to_front()
            return {"success": True, "message": f"Switched to tab {idx}: {_page.url}"}
    except ValueError:
        pass
    # Search by title or URL
    needle = identifier.lower()
    for p in pages:
        if needle in p.title().lower() or needle in p.url.lower():
            _page = p
            _page.bring_to_front()
            return {"success": True, "message": f"Switched to tab: {_page.url}"}
    return {"success": False, "message": f"No tab matching '{identifier}'"}


def _pw_scroll_to(selector: str) -> dict:
    """Scroll the element matching *selector* into view."""
    page = _get_page()
    page.evaluate(f"document.querySelector({json.dumps(selector)})?.scrollIntoView({{behavior:'smooth',block:'center'}})")
    return {"success": True, "message": f"Scrolled to: {selector}"}


def _pw_get_text(selector: str) -> dict:
    """Extract text content from the element matching *selector*."""
    page = _get_page()
    text = page.text_content(selector, timeout=5000) or ""
    return {"success": True, "message": f"Text: {text[:500]}"}


def _pw_find_element(description: str) -> dict:
    """Find element by text content and return its bounding box."""
    page = _get_page()
    loc = page.get_by_text(description, exact=False).first
    box = loc.bounding_box(timeout=5000)
    if box:
        cx = int(box["x"] + box["width"] / 2)
        cy = int(box["y"] + box["height"] / 2)
        return {"success": True, "message": f"Found '{description}' at center ({cx}, {cy}), box={box}"}
    return {"success": False, "message": f"Element '{description}' not found"}


def _pw_evaluate_js(script: str) -> dict:
    """Evaluate arbitrary JavaScript in the page context."""
    page = _get_page()
    result = page.evaluate(script)
    return {"success": True, "message": f"JS result: {str(result)[:500]}"}


def _pw_wait_for(selector: str) -> dict:
    """Wait up to 10 s for a DOM element matching *selector* to appear."""
    page = _get_page()
    page.wait_for_selector(selector, timeout=10000)
    return {"success": True, "message": f"Element appeared: {selector}"}


def _pw_screenshot_region(x: int, y: int, w: int, h: int) -> str:
    """Capture a region of the page as base64 PNG."""
    page = _get_page()
    png_bytes = page.screenshot(
        type="png",
        clip={"x": x, "y": y, "width": w, "height": h},
    )
    return base64.b64encode(png_bytes).decode("ascii")


def _pw_screenshot_viewport() -> str:
    """Capture the current viewport as base64 PNG."""
    page = _get_page()
    png_bytes = page.screenshot(type="png", full_page=False)
    return base64.b64encode(png_bytes).decode("ascii")


def _pw_screenshot_element(selector: str) -> str:
    """Capture a specific element as base64 PNG."""
    page = _get_page()
    try:
        element = page.locator(selector).first
        if element.is_visible():
            png_bytes = element.screenshot(type="png")
            return base64.b64encode(png_bytes).decode("ascii")
    except Exception as e:
        logger.warning(f"Failed to screenshot element {selector}: {e}")
    return ""  # Return empty string on failure, handled by caller


def _pw_scroll_into_view(selector: str) -> dict:
    """Scroll the element into the visible viewport if needed."""
    page = _get_page()
    try:
        page.locator(selector).first.scroll_into_view_if_needed(timeout=5000)
        return {"success": True, "message": f"Scrolled into view: {selector}"}
    except Exception as e:
        return {"success": False, "message": f"Failed to scroll to {selector}: {e}"}


def _pw_set_viewport(width: int, height: int) -> dict:
    """Resize the Playwright viewport to *width* x *height*."""
    page = _get_page()
    page.set_viewport_size({"width": width, "height": height})
    return {"success": True, "message": f"Viewport set to {width}x{height}"}


def _pw_zoom(level: float) -> dict:
    """Set zoom level (e.g. 1.0, 1.5, 0.8). Playwright uses CSS zoom or scale."""
    page = _get_page()
    page.evaluate(f"document.body.style.zoom = '{level}'")
    return {"success": True, "message": f"Zoom set to {level}"}


def _pw_block_resource(resource_type: str) -> dict:
    """Block resource types (image, font, css, etc.)."""
    page = _get_page()
    normalized = (resource_type or "").strip().lower()
    if not normalized:
        return {"success": False, "message": "block_resource requires a resource type"}

    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type == normalized
        else route.continue_(),
    )
    return {"success": True, "message": f"Blocked resource type: {resource_type}"}


def _pw_export_page_pdf() -> str:
    """Export page as PDF (base64)."""
    page = _get_page()
    pdf_bytes = page.pdf()
    return base64.b64encode(pdf_bytes).decode("ascii")


def _pw_get_html(selector: str = None) -> dict:
    """Return inner HTML of *selector*, or full page HTML if omitted."""
    page = _get_page()
    if selector:
        html = page.inner_html(selector, timeout=5000)
    else:
        html = page.content()
    return {"success": True, "message": f"HTML: {html[:500]}"}


def _pw_get_attribute(selector: str, attribute: str) -> dict:
    """Read a DOM attribute from the element matching *selector*."""
    page = _get_page()
    val = page.get_attribute(selector, attribute, timeout=5000)
    return {"success": True, "message": f"Attribute {attribute}: {val}"}


def _pw_query_selector(selector: str) -> dict:
    """Check whether a DOM element matching *selector* exists."""
    page = _get_page()
    element = page.query_selector(selector)
    if element:
        return {"success": True, "message": f"Element found: {selector}"}
    return {"success": False, "message": f"Element not found: {selector}"}


def _pw_query_all(selector: str) -> dict:
    """Count all DOM elements matching *selector*."""
    page = _get_page()
    elements = page.query_selector_all(selector)
    return {"success": True, "message": f"Found {len(elements)} elements matching: {selector}"}


def _pw_get_bounding_box(selector: str) -> dict:
    """Return the bounding box of the first element matching *selector*."""
    page = _get_page()
    element = page.query_selector(selector)
    if element:
        box = element.bounding_box()
        return {"success": True, "message": f"Bounding box for {selector}: {box}"}
    return {"success": False, "message": f"Element not found: {selector}"}


def _pw_get_visible_elements(selector: str) -> dict:
    """Count visible elements matching *selector*."""
    page = _get_page()
    elements = page.query_selector_all(selector)
    visible_count = sum(1 for el in elements if el.is_visible())
    return {"success": True, "message": f"Found {visible_count} visible elements matching: {selector}"}


def _pw_evaluate_on_selector(selector: str, script: str) -> dict:
    """Evaluate *script* in the context of the element matching *selector*."""
    page = _get_page()
    try:
        result = page.eval_on_selector(selector, script)
        return {"success": True, "message": f"JS result on {selector}: {str(result)[:500]}"}
    except Exception as e:
        return {"success": False, "message": f"Failed to evaluate on {selector}: {e}"}


def _pw_wait_for_navigation() -> dict:
    """Block until the page reaches the 'load' state."""
    page = _get_page()
    page.wait_for_load_state("load", timeout=30000)
    return {"success": True, "message": "Navigation complete"}


def _pw_upload_file(selector: str, file_path: str) -> dict:
    """Set input file on the element matching *selector* (path-restricted)."""
    page = _get_page()
    # Directory traversal protection
    resolved = os.path.realpath(file_path)
    if not any(resolved.startswith(p) for p in _UPLOAD_ALLOWED_PREFIXES):
        return {"success": False, "message": f"Upload restricted to {_UPLOAD_ALLOWED_PREFIXES}"}
    page.set_input_files(selector, file_path)
    return {"success": True, "message": f"Uploaded {file_path} to {selector}"}


def _pw_download_file(selector: str) -> dict:
    """Download a file (stub — not yet implemented)."""
    return {"success": False, "message": "Not implemented yet"}


def _pw_set_cookies(cookies: list) -> dict:
    """Add cookies to the current browser context."""
    global _context
    _context.add_cookies(cookies)
    return {"success": True, "message": "Cookies set"}


def _pw_get_cookies() -> dict:
    """Retrieve all cookies from the current browser context."""
    global _context
    cookies = _context.cookies()
    return {"success": True, "message": f"Got {len(cookies)} cookies"}


def _pw_clear_cookies() -> dict:
    """Clear all cookies from the current browser context."""
    global _context
    _context.clear_cookies()
    return {"success": True, "message": "Cookies cleared"}


def _pw_new_context() -> dict:
    """Create a fresh Playwright browser context (clears state)."""
    global _playwright, _browser, _context, _page
    if _context:
        _context.close()
    _context = _browser.new_context(
        viewport={"width": SCREEN_WIDTH - 100, "height": SCREEN_HEIGHT - 80},
        device_scale_factor=1,
    )
    _page = _context.new_page()
    _page.goto("about:blank")
    return {"success": True, "message": "New context created"}


def _pw_switch_context(identifier: str) -> dict:
    """Switch browser context (stub — not yet implemented)."""
    return {"success": False, "message": "Not implemented yet"}


def _pw_close_context() -> dict:
    """Close the current browser context and release its page."""
    global _context, _page
    if _context:
        _context.close()
    _context = None
    _page = None
    return {"success": True, "message": "Context closed"}


# ══════════════════════════════════════════════════════════════════════════════
#  Actions — xdotool (desktop mode, works with any X11 app)
# ══════════════════════════════════════════════════════════════════════════════

def _xdo(args: list[str]) -> str:
    """Run an xdotool command, return stdout.

    The ``--sync`` flag is automatically stripped because it hangs
    indefinitely in Xvfb environments that lack a compositor (the X
    event that ``--sync`` waits for is never delivered).  A small
    ``time.sleep`` replaces it so callers stay unchanged.
    """
    had_sync = "--sync" in args
    if had_sync:
        args = [a for a in args if a != "--sync"]

    result = subprocess.run(
        ["xdotool"] + args,
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)} failed: {result.stderr.strip()}")

    # Compensate for removed --sync with a short delay
    if had_sync:
        cmd = args[0] if args else ""
        if cmd == "windowactivate":
            time.sleep(0.3)   # window activation needs more time
        else:
            time.sleep(0.05)  # mousemove / other commands

    return result.stdout.strip()


def _xdo_search_window_ids(identifier: str) -> list[str]:
    """Return xdotool window IDs matching *identifier* by name."""
    if not identifier:
        return []
    result = subprocess.run(
        ["xdotool", "search", "--name", identifier],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return []
    return [wid.strip() for wid in result.stdout.splitlines() if wid.strip()]


def _xdo_get_window_geometry(wid: str) -> dict:
    """Get window geometry via xdotool getwindowgeometry --shell."""
    raw = _xdo(["getwindowgeometry", "--shell", wid])
    geometry: dict[str, int] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"X", "Y", "WIDTH", "HEIGHT"}:
            try:
                geometry[key] = int(value.strip())
            except ValueError:
                continue
    return geometry


def _xdo_normalize_window(wid: str) -> str:
    """Move/resize window to deterministic geometry for stable coordinates."""
    if not WINDOW_NORMALIZE_ENABLED:
        return "window normalization disabled"
    try:
        subprocess.run(
            ["wmctrl", "-ir", wid, "-b", "remove,maximized_vert,maximized_horz"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        _xdo(["windowmove", wid, str(WINDOW_NORMALIZE_X), str(WINDOW_NORMALIZE_Y)])
        _xdo(["windowsize", wid, str(WINDOW_NORMALIZE_W), str(WINDOW_NORMALIZE_H)])
        geo = _xdo_get_window_geometry(wid)
        if geo:
            return (
                f"normalized to x={geo.get('X')}, y={geo.get('Y')}, "
                f"w={geo.get('WIDTH')}, h={geo.get('HEIGHT')}"
            )
        return "normalized"
    except Exception as e:
        return f"normalization skipped: {e}"


def _expand_app_launch_candidates(app_name: str) -> list[str]:
    """Expand semantic app names into concrete launch candidates."""
    requested = (app_name or "").strip()
    if not requested:
        return []

    lowered = requested.lower()
    candidates: list[str] = [requested]

    if any(term in lowered for term in ("calculator", "calc", "xcalc", "kcalc", "galculator")):
        candidates.extend([
            "gnome-calculator",
            "galculator",
            "xfce4-calculator",
            "mate-calc",
            "kcalc",
            "xcalc",
        ])

    if any(term in lowered for term in ("file explorer", "files", "file manager", "explorer", "nautilus", "thunar", "pcmanfm")):
        candidates.extend([
            "nautilus",
            "thunar",
            "pcmanfm",
            "dolphin",
            "nemo",
            "xfe",
        ])

    # Preserve order while deduping
    seen = set()
    deduped: list[str] = []
    for item in candidates:
        key = item.strip().lower()
        if key and key not in seen:
            deduped.append(item.strip())
            seen.add(key)
    return deduped


def _xdo_click(x: int, y: int) -> dict:
    """Click at (x,y) via xdotool."""
    _xdo(["mousemove", "--sync", str(x), str(y)])
    _xdo(["click", "1"])
    return {"success": True, "message": f"Clicked at ({x}, {y})"}


def _xdo_double_click(x: int, y: int) -> dict:
    """Double-click at (x,y) via xdotool."""
    _xdo(["mousemove", "--sync", str(x), str(y)])
    _xdo(["click", "--repeat", "2", "--delay", "80", "1"])
    return {"success": True, "message": f"Double-clicked at ({x}, {y})"}


def _xdo_right_click(x: int, y: int) -> dict:
    """Right-click at (x,y) via xdotool."""
    _xdo(["mousemove", "--sync", str(x), str(y)])
    _xdo(["click", "3"])
    return {"success": True, "message": f"Right-clicked at ({x}, {y})"}


def _xdo_type(text: str) -> dict:
    """Type text via xdotool with modifier-key safety.

    Strategy:
    1. Try ``xdotool type`` (sends KeyPress/KeyRelease per character).
    2. If the focused window is an Athena-widget app (e.g. xcalc) that
       ignores synthetic type events, fall back to sending individual
       ``xdotool key`` events per character which uses keysym dispatch
       and works more reliably with legacy X11 toolkit widgets.
    """
    # 1. Ensure window focus
    wid = ""
    try:
        wid = _xdo(["getwindowfocus"]).strip()
        if wid:
            _xdo(["windowactivate", "--sync", wid])
    except Exception:
        pass

    # 2. Clear potentially stuck modifier keys
    try:
        _xdo(["keyup", "shift"])
        _xdo(["keyup", "ctrl"])
        _xdo(["keyup", "alt"])
    except Exception:
        pass
    time.sleep(ACTION_DELAY)

    # 3. Try normal xdotool type first
    try:
        _xdo(["type", "--clearmodifiers", "--delay", "25", "--", text])
    except Exception as exc:
        logger.warning("xdotool type failed (%s), falling back to key-per-char", exc)
        _xdo_type_key_per_char(text)
        return {"success": True, "message": f"Typed (key-per-char fallback): {text[:50]}"}

    # 4. Post-type verification: send key-per-char as reinforcement for
    #    Athena/Xaw widget apps (xcalc, xedit, etc.) which silently
    #    ignore xdotool-type events.  This is cheap and idempotent for
    #    apps that already accepted the type events (the duplicate input
    #    can be cleared by the agent if needed).
    #    We only do this when the text is short (≤40 chars) to avoid
    #    doubling long pastes.
    if len(text) <= 40:
        try:
            win_name = _xdo(["getwindowfocus", "getwindowname"]).strip().lower()
        except Exception:
            win_name = ""
        # Heuristic: Athena-widget apps typically have generic titles
        _ATHENA_HINTS = ("xcalc", "calculator", "xedit", "bitmap", "editres")
        if any(h in win_name for h in _ATHENA_HINTS):
            logger.info("Athena-widget window detected ('%s') — reinforcing with key-per-char", win_name)
            _xdo_type_key_per_char(text)

    return {"success": True, "message": f"Typed: {text[:50]}"}


# Character-to-xdotool-keysym map for key-per-char fallback
_CHAR_KEYSYM: dict[str, str] = {
    " ": "space", "!": "exclam", '"': "quotedbl", "#": "numbersign",
    "$": "dollar", "%": "percent", "&": "ampersand", "'": "apostrophe",
    "(": "parenleft", ")": "parenright", "*": "asterisk", "+": "plus",
    ",": "comma", "-": "minus", ".": "period", "/": "slash",
    ":": "colon", ";": "semicolon", "<": "less", "=": "equal",
    ">": "greater", "?": "question", "@": "at", "[": "bracketleft",
    "\\": "backslash", "]": "bracketright", "^": "asciicircum",
    "_": "underscore", "`": "grave", "{": "braceleft", "|": "bar",
    "}": "braceright", "~": "asciitilde",
    "\n": "Return", "\t": "Tab",
}


def _xdo_type_key_per_char(text: str) -> None:
    """Send *text* one character at a time via ``xdotool key``.

    This bypasses the ``xdotool type`` path which relies on XStringToKeysym
    translation that some legacy Athena/Xaw widgets silently ignore.
    """
    for ch in text:
        if ch in _CHAR_KEYSYM:
            keysym = _CHAR_KEYSYM[ch]
        elif ch.isalpha():
            # xdotool key accepts lowercase letter names directly
            keysym = ch.lower()
        elif ch.isdigit():
            keysym = ch  # xdotool handles "0".."9" directly
        else:
            keysym = ch  # last resort — pass through
        try:
            _xdo(["key", "--clearmodifiers", keysym])
        except Exception:
            logger.warning("key-per-char: failed to send keysym '%s' for char '%s'", keysym, ch)
        time.sleep(0.03)  # 30 ms inter-key delay


def _open_terminal() -> dict:
    """Launch a terminal emulator (xfce4-terminal or xterm fallback)."""
    terminal_candidates = ["xfce4-terminal", "xterm"]
    for terminal in terminal_candidates:
        try:
            subprocess.Popen(
                [terminal],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "DISPLAY": ":99"},
            )
            time.sleep(1)
            return {"success": True, "message": f"Opened terminal ({terminal})"}
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.warning("Failed to open %s: %s", terminal, e)
            continue
    return {"success": False, "message": "No terminal emulator available (tried xfce4-terminal, xterm)"}


def _xdo_scroll(x: int, y: int, direction: str) -> dict:
    """Scroll at (x,y) via xdotool button events."""
    _xdo(["mousemove", "--sync", str(x), str(y)])
    btn = "4" if direction == "up" else "5"
    _xdo(["click", "--repeat", "5", "--delay", "40", btn])
    return {"success": True, "message": f"Scrolled {direction} at ({x}, {y})"}


def _xdo_scroll_up() -> dict:
    """Scroll up at the current cursor position."""
    _xdo(["click", "--repeat", "5", "--delay", "40", "4"])
    return {"success": True, "message": "Scrolled up"}


def _xdo_scroll_down() -> dict:
    """Scroll down at the current cursor position."""
    _xdo(["click", "--repeat", "5", "--delay", "40", "5"])
    return {"success": True, "message": "Scrolled down"}


def _xdo_window_minimize(identifier: str) -> dict:
    """Minimise the window matching *identifier*."""
    wids = _xdo(["search", "--name", identifier]).split("\n")
    if wids and wids[0]:
        _xdo(["windowminimize", wids[0]])
        return {"success": True, "message": f"Minimized window: {identifier}"}
    return {"success": False, "message": f"Window not found: {identifier}"}


def _xdo_window_maximize(identifier: str) -> dict:
    """Maximise the window matching *identifier* via wmctrl."""
    wids = _xdo(["search", "--name", identifier]).split("\n")
    if wids and wids[0]:
        subprocess.run(["wmctrl", "-ir", wids[0], "-b", "add,maximized_vert,maximized_horz"], check=False, timeout=5)
        return {"success": True, "message": f"Maximized window: {identifier}"}
    return {"success": False, "message": f"Window not found: {identifier}"}


def _xdo_window_move(identifier: str, x: int, y: int) -> dict:
    """Move the window matching *identifier* to (x,y)."""
    wids = _xdo(["search", "--name", identifier]).split("\n")
    if wids and wids[0]:
        _xdo(["windowmove", wids[0], str(x), str(y)])
        return {"success": True, "message": f"Moved window {identifier} to {x},{y}"}
    return {"success": False, "message": f"Window not found: {identifier}"}


def _xdo_window_resize(identifier: str, w: int, h: int) -> dict:
    """Resize the window matching *identifier* to w x h."""
    wids = _xdo(["search", "--name", identifier]).split("\n")
    if wids and wids[0]:
        _xdo(["windowsize", wids[0], str(w), str(h)])
        return {"success": True, "message": f"Resized window {identifier} to {w}x{h}"}
    return {"success": False, "message": f"Window not found: {identifier}"}


def _xdo_search_window(identifier: str) -> dict:
    """Search for a window by name and return its window IDs."""
    try:
        wids = _xdo(["search", "--name", identifier]).split("\n")
        if wids and wids[0]:
            return {"success": True, "message": f"Found window: {identifier} (wids: {wids})", "wids": wids}
    except Exception:
        pass
    return {"success": False, "message": f"Window not found: {identifier}"}


def _xdo_keydown(key: str) -> dict:
    """Hold a key down via xdotool."""
    combo = _map_key_combo_xdotool(key)
    _xdo(["keydown", combo])
    return {"success": True, "message": f"Key down: {key}"}


def _xdo_keyup(key: str) -> dict:
    """Release a held key via xdotool."""
    combo = _map_key_combo_xdotool(key)
    _xdo(["keyup", combo])
    return {"success": True, "message": f"Key up: {key}"}


def _xdo_type_slow(text: str) -> dict:
    """Type text with a larger inter-key delay (150 ms)."""
    _xdo(["type", "--clearmodifiers", "--delay", "150", "--", text])
    return {"success": True, "message": f"Typed slow: {text[:50]}"}


def _xdo_key(key: str) -> dict:
    """Press and release a key combo via xdotool."""
    combo = _map_key_combo_xdotool(key)
    _xdo(["key", "--clearmodifiers", combo])
    return {"success": True, "message": f"Pressed key: {key}"}


def _xdo_drag(x1: int, y1: int, x2: int, y2: int) -> dict:
    """Drag from (x1,y1) to (x2,y2) via xdotool."""
    _xdo(["mousemove", "--sync", str(x1), str(y1)])
    _xdo(["mousedown", "1"])
    _xdo(["mousemove", "--sync", str(x2), str(y2)])
    _xdo(["mouseup", "1"])
    return {"success": True, "message": f"Dragged ({x1},{y1}) → ({x2},{y2})"}


# ── Deterministic browser launch (shared by xdotool open_url) ─────────────

# Pre-created profile directory (seeded at build-time in Dockerfile)
_CHROME_PROFILE_DIR = "/tmp/chrome-profile"

# Chrome flags that suppress ALL first-run UI, keyring dialogs, and sync prompts
_CHROME_FLAGS: list[str] = [
    "--no-sandbox",
    "--no-first-run",
    "--disable-first-run-ui",
    "--disable-sync",
    "--disable-extensions",
    "--disable-default-apps",
    "--disable-popup-blocking",
    "--disable-translate",
    "--disable-background-networking",
    "--password-store=basic",          # avoid gnome-keyring / kwallet prompts
    "--disable-infobars",
    "--no-default-browser-check",
    f"--user-data-dir={_CHROME_PROFILE_DIR}",
    f"--window-size={SCREEN_WIDTH},{SCREEN_HEIGHT}",
]

# Known modal window titles that should be auto-dismissed after browser launch
_KNOWN_MODAL_TITLES = (
    "Welcome to Google Chrome",
    "Sign in to Chrome",
    "Chrome is being controlled by automated test software",
    "Choose password for new keyring",
    "Unlock Keyring",
    "Set as default browser",
    "Default Browser",
    "Unlock Login Keyring",
)


def _resolve_browser_binary() -> tuple[str, list[str]] | None:
    """Return (binary, extra_flags) for the first available browser.

    Preference: google-chrome > chromium-browser > chromium > firefox.
    Returns None when no browser is found.
    """
    chrome_candidates = ("google-chrome", "google-chrome-stable",
                         "chromium-browser", "chromium")
    for name in chrome_candidates:
        path = shutil.which(name)
        if path:
            return (path, list(_CHROME_FLAGS))

    # Firefox fallback — different flag set
    for name in ("firefox", "firefox-esr"):
        path = shutil.which(name)
        if path:
            return (path, [
                "--new-window",
                f"--width={SCREEN_WIDTH}",
                f"--height={SCREEN_HEIGHT}",
            ])

    return None


def _dismiss_known_modals() -> list[str]:
    """Detect and close known first-run / keyring modal windows via wmctrl.

    Returns a list of window titles that were closed.
    """
    dismissed: list[str] = []
    try:
        result = subprocess.run(
            ["wmctrl", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return dismissed

        for line in result.stdout.strip().splitlines():
            # wmctrl -l format: <wid> <desktop> <host> <title>
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            title = parts[3]
            for modal_title in _KNOWN_MODAL_TITLES:
                if modal_title.lower() in title.lower():
                    subprocess.run(
                        ["wmctrl", "-c", title],
                        capture_output=True, text=True, timeout=3,
                    )
                    dismissed.append(title)
                    logger.info("Auto-dismissed modal window: %s", title)
                    break
    except Exception as exc:
        logger.warning("Modal dismissal scan failed: %s", exc)
    return dismissed


def _open_url_in_browser(url: str) -> dict:
    """Open *url* in a real browser with deterministic, modal-free startup.

    1. Resolve browser binary + flags.
    2. Launch with first-run / keyring suppression.
    3. Wait for window, normalise geometry.
    4. Auto-dismiss any residual modal dialogs.
    5. Fall back to xdg-open only as last resort.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    browser = _resolve_browser_binary()
    if browser is None:
        # Ultimate fallback
        logger.warning("No browser binary found — falling back to xdg-open")
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"success": True, "message": f"Opened URL (xdg-open fallback): {url}"}

    binary, flags = browser
    cmd = [binary] + flags + [url]
    logger.info("Launching browser: %s", " ".join(cmd))

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": ":99"},
        )
    except Exception as exc:
        logger.error("Browser launch failed: %s — falling back to xdg-open", exc)
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"success": True, "message": f"Opened URL (xdg-open fallback after error): {url}"}

    # Give the browser time to create its window
    time.sleep(2.0)

    # Auto-dismiss any first-run / keyring modals
    dismissed = _dismiss_known_modals()
    if dismissed:
        time.sleep(0.5)
        # Dismiss again in case closing one modal spawned another
        _dismiss_known_modals()

    # Find and normalise the browser window
    norm_msg = ""
    for hint in ("chrome", "chromium", "firefox", "mozilla", "navigator"):
        wids = _xdo_search_window_ids(hint)
        if wids:
            wid = wids[-1]
            try:
                _xdo(["windowactivate", "--sync", wid])
                time.sleep(0.3)
                norm_msg = _xdo_normalize_window(wid)
            except Exception:
                norm_msg = "window normalisation skipped"
            break

    dismiss_info = f" (dismissed modals: {dismissed})" if dismissed else ""
    return {
        "success": True,
        "message": f"Opened URL: {url} via {binary}. {norm_msg}{dismiss_info}",
    }


def _xdo_open_url(url: str) -> dict:
    """Open URL in a deterministic browser — avoids xdg-open first-run problems."""
    return _open_url_in_browser(url)


def _xdo_hover(x: int, y: int) -> dict:
    """Move the mouse to (x,y) without clicking."""
    _xdo(["mousemove", "--sync", str(x), str(y)])
    return {"success": True, "message": f"Hovered at ({x}, {y})"}


def _xdo_middle_click(x: int, y: int) -> dict:
    """Middle-click at (x,y) via xdotool."""
    _xdo(["mousemove", "--sync", str(x), str(y)])
    _xdo(["click", "2"])
    return {"success": True, "message": f"Middle-clicked at ({x}, {y})"}


def _is_terminal_focused() -> bool:
    """Check if the currently focused window is a terminal emulator.

    Terminals interpret Ctrl+V as a literal control character; paste
    requires Ctrl+Shift+V instead.
    """
    try:
        name = subprocess.run(
            ["xdotool", "getwindowfocus", "getwindowname"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip().lower()
        _TERMINAL_HINTS = (
            "terminal", "xterm", "konsole", "alacritty", "kitty",
            "tmux", "bash", "zsh", "sh —", "fish",
        )
        return any(hint in name for hint in _TERMINAL_HINTS)
    except Exception:
        return False


def _xdo_paste(text: str) -> dict:
    """Copy text to clipboard then paste via Ctrl+V (or Ctrl+Shift+V in terminals)."""
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode(), check=True, timeout=5,
    )
    if _is_terminal_focused():
        _xdo(["key", "--clearmodifiers", "ctrl+shift+v"])
    else:
        _xdo(["key", "--clearmodifiers", "ctrl+v"])
    return {"success": True, "message": f"Pasted: {text[:50]}"}


def _xdo_copy() -> dict:
    """Copy the current selection to clipboard via xdotool."""
    _xdo(["key", "--clearmodifiers", "ctrl+c"])
    return {"success": True, "message": "Copied selection to clipboard"}


def _xdo_hotkey(keys: list[str]) -> dict:
    """Press a multi-key combo via xdotool."""
    combo = "+".join(_map_key_combo_xdotool(k) for k in keys)
    _xdo(["key", "--clearmodifiers", combo])
    return {"success": True, "message": f"Hotkey: {'+'.join(keys)}"}


def _xdo_focus_window(identifier: str) -> dict:
    """Focus a window by name or class."""
    wids = _xdo_search_window_ids(identifier)
    if wids:
        wid = wids[0]
        _xdo(["windowactivate", "--sync", wid])
        normalization_msg = _xdo_normalize_window(wid)
        return {
            "success": True,
            "message": f"Focused window: {identifier} (wid={wid}; {normalization_msg})",
        }
    return {"success": False, "message": f"Window not found: {identifier}"}


def _xdo_open_app(app_name: str) -> dict:
    """Launch an application by command name.

    After a successful launch the new window is automatically found,
    activated, and normalised to a deterministic geometry so that
    subsequent coordinate-based actions are reliable.
    """
    candidates = _expand_app_launch_candidates(app_name)
    if not candidates:
        return {"success": False, "message": "open_app requires a non-empty app command"}

    failures: list[str] = []
    for candidate in candidates:
        parts = shlex.split(candidate)
        if not parts:
            continue
        binary = parts[0]
        if shutil.which(binary) is None:
            failures.append(f"{candidate}: command not found")
            continue
        try:
            subprocess.Popen(
                parts,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "DISPLAY": ":99"},
            )
            time.sleep(1.5)  # give the window time to materialise

            # ── Post-launch: find, activate & normalise the new window ──
            norm_msg = _post_launch_normalize(candidate)
            return {"success": True, "message": f"Launched: {candidate}. {norm_msg}"}
        except Exception as e:
            failures.append(f"{candidate}: {e}")

    reason = "; ".join(failures) if failures else "no launch candidates"
    return {
        "success": False,
        "message": f"Failed to launch '{app_name}'. Tried: {', '.join(candidates)}. Reasons: {reason}",
    }


def _post_launch_normalize(hint: str) -> str:
    """Find the most-recently-created window, activate it, and normalise.

    *hint* is used for a name-based search first; if that fails the
    currently-active window is normalised instead.
    """
    wids = _xdo_search_window_ids(hint)
    if not wids:
        # Fallback: try the currently-active window
        try:
            wid = _xdo(["getactivewindow"]).strip()
            if wid:
                wids = [wid]
        except Exception:
            pass
    if not wids:
        return "window not found for normalisation"

    wid = wids[-1]  # most recent match
    try:
        _xdo(["windowactivate", "--sync", wid])
        # Brief extra settle time after activation
        time.sleep(0.3)
        norm_msg = _xdo_normalize_window(wid)
        return f"Window activated (wid={wid}); {norm_msg}"
    except Exception as e:
        return f"post-launch normalisation failed: {e}"


def _wmctrl_close_window(identifier: str) -> dict:
    """Gracefully close a window via EWMH using wmctrl -c."""
    result = subprocess.run(
        ["wmctrl", "-c", identifier],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        return {"success": True, "message": f"Closed window: {identifier}"}
    return {"success": False, "message": f"Failed to close window: {identifier} — {result.stderr.strip()}"}


def _xdo_screenshot_full() -> str:
    """Capture the full screen via scrot."""
    subprocess.run(
        ["scrot", "-z", "-o", "/tmp/full.png"],
        check=True, timeout=5,
    )
    with open("/tmp/full.png", "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _xdo_screenshot_region(x: int, y: int, w: int, h: int) -> str:
    """Capture a region of the screen via scrot."""
    subprocess.run(
        ["scrot", "-z", "-o", "-a", f"{x},{y},{w},{h}", "/tmp/region.png"],
        check=True, timeout=5,
    )
    with open("/tmp/region.png", "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _xdo_focus_click(identifier: str, x: int, y: int) -> dict:
    """Focus a window and then click at x, y."""
    # 1. Focus the window
    wids = _xdo(["search", "--name", identifier]).split("\n")
    if not wids or not wids[0]:
        return {"success": False, "message": f"Window not found: {identifier}"}
    
    _xdo(["windowactivate", "--sync", wids[0]])
    time.sleep(0.2)
    
    # 2. Click relative to that window (or absolute if just screen coords provided)
    # The command provided assumes x,y are screen coordinates.
    # To click safely after focus, we just move and click.
    _xdo(["mousemove", "--sync", str(x), str(y)])
    _xdo(["click", "1"])
    
    return {"success": True, "message": f"Focused {identifier} and clicked at ({x}, {y})"}


# ══════════════════════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

# ── wmctrl-based window management (DISPLAY-independent layer) ────────────────

def _wmctrl_focus_window(identifier: str) -> dict:
    """Focus a window by partial title match using wmctrl (no xdotool)."""
    result = subprocess.run(
        ["wmctrl", "-a", identifier],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        return {"success": True, "message": f"Focused window: {identifier}"}
    return {"success": False, "message": f"Window not found: {identifier} — {result.stderr.strip()}"}


def _wmctrl_search_window(identifier: str) -> dict:
    """Search for windows by title via wmctrl -l."""
    result = subprocess.run(
        ["wmctrl", "-l"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        return {"success": False, "message": f"wmctrl list failed: {result.stderr.strip()}"}
    matches = [line for line in result.stdout.splitlines() if identifier.lower() in line.lower()]
    if matches:
        return {"success": True, "message": f"Found {len(matches)} window(s) matching '{identifier}': {'; '.join(matches[:5])}"}
    return {"success": False, "message": f"No windows matching: {identifier}"}


def _wmctrl_minimize_window(identifier: str) -> dict:
    """Minimize a window by title using wmctrl."""
    # wmctrl doesn't have a direct minimize; use -b add,hidden
    result = subprocess.run(
        ["wmctrl", "-r", identifier, "-b", "add,hidden"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        return {"success": True, "message": f"Minimized window: {identifier}"}
    return {"success": False, "message": f"Failed to minimize: {identifier} — {result.stderr.strip()}"}


def _wmctrl_maximize_window(identifier: str) -> dict:
    """Maximize a window by title using wmctrl."""
    result = subprocess.run(
        ["wmctrl", "-r", identifier, "-b", "add,maximized_vert,maximized_horz"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        return {"success": True, "message": f"Maximized window: {identifier}"}
    return {"success": False, "message": f"Failed to maximize: {identifier} — {result.stderr.strip()}"}


def _wmctrl_move_window(identifier: str, x: int, y: int) -> dict:
    """Move a window to (x,y) using wmctrl."""
    # wmctrl -r <name> -e <gravity>,<x>,<y>,<w>,<h>  (-1 = unchanged)
    result = subprocess.run(
        ["wmctrl", "-r", identifier, "-e", f"0,{x},{y},-1,-1"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        return {"success": True, "message": f"Moved window {identifier} to ({x},{y})"}
    return {"success": False, "message": f"Failed to move: {identifier} — {result.stderr.strip()}"}


def _wmctrl_resize_window(identifier: str, w: int, h: int) -> dict:
    """Resize a window using wmctrl."""
    result = subprocess.run(
        ["wmctrl", "-r", identifier, "-e", f"0,-1,-1,{w},{h}"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0:
        return {"success": True, "message": f"Resized window {identifier} to {w}x{h}"}
    return {"success": False, "message": f"Failed to resize: {identifier} — {result.stderr.strip()}"}



_KEY_MAP = {
    "enter": "Enter", "return": "Enter",
    "tab": "Tab",
    "escape": "Escape", "esc": "Escape",
    "backspace": "Backspace",
    "delete": "Delete",
    "space": " ",
    "up": "ArrowUp", "down": "ArrowDown",
    "left": "ArrowLeft", "right": "ArrowRight",
    "home": "Home", "end": "End",
    "pageup": "PageUp", "pagedown": "PageDown",
}

_KEY_MAP_XDO = {
    "enter": "Return", "return": "Return",
    "tab": "Tab",
    "escape": "Escape", "esc": "Escape",
    "backspace": "BackSpace",
    "delete": "Delete",
    "space": "space",
    "up": "Up", "down": "Down",
    "left": "Left", "right": "Right",
    "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next",
}


def _map_key_combo(key: str) -> str:
    """Map a user key string to a Playwright key combo."""
    if "+" in key:
        parts = [p.strip() for p in key.split("+")]
        modifiers = []
        for p in parts[:-1]:
            pl = p.lower()
            if pl in ("ctrl", "control"):
                modifiers.append("Control")
            elif pl == "alt":
                modifiers.append("Alt")
            elif pl == "shift":
                modifiers.append("Shift")
            elif pl in ("meta", "super", "win", "cmd"):
                modifiers.append("Meta")
            else:
                # Add other modifiers if any, or just ignore
                pass
        
        # Handle the last part (the actual key)
        last_part = parts[-1]
        # Normalize last part
        final = _KEY_MAP.get(last_part.lower(), last_part)
        
        # Special case: ensure single char keys are proper case if needed?
        # Playwright usually handles "a", "A", etc.
        
        return "+".join(modifiers + [final])
    
    # Single key
    k = key.lower()
    if k in ("ctrl", "control"): return "Control"
    if k == "alt": return "Alt"
    if k == "shift": return "Shift"
    if k in ("meta", "super", "win", "cmd"): return "Meta"
    
    return _KEY_MAP.get(k, key)


def _map_key_combo_xdotool(key: str) -> str:
    """Map a user key string to an xdotool key combo."""
    if "+" in key:
        parts = [p.strip() for p in key.split("+")]
        mapped = []
        for p in parts:
            pl = p.lower()
            if pl in ("ctrl", "control"):
                mapped.append("ctrl")
            elif pl == "alt":
                mapped.append("alt")
            elif pl == "shift":
                mapped.append("shift")
            elif pl in ("meta", "super", "win", "cmd"):
                mapped.append("super")
            else:
                mapped.append(_KEY_MAP_XDO.get(pl, p))
        return "+".join(mapped)
    return _KEY_MAP_XDO.get(key.lower(), key)


def _do_wait(duration: float) -> dict:
    """Sleep for *duration* seconds (clamped to 0.1–10s)."""
    capped = min(max(duration, 0.1), 10.0)
    time.sleep(capped)
    return {"success": True, "message": f"Waited {capped:.1f}s"}


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP Server
# ══════════════════════════════════════════════════════════════════════════════

class AgentHandler(BaseHTTPRequestHandler):
    """HTTP handler supporting both browser and desktop modes."""

    def log_message(self, fmt, *args):
        """Redirect HTTP request logging to the module logger."""
        logger.debug("HTTP %s", fmt % args)

    def _respond(self, code: int, data: dict):
        """Send a JSON response with the given status code."""
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        """Parse the JSON request body, enforcing a size limit."""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > _MAX_BODY_SIZE:
            raise ValueError(f"Request body too large: {length} bytes (max {_MAX_BODY_SIZE})")
        raw = self.rfile.read(length)
        return json.loads(raw)

    # ── GET ───────────────────────────────────────────────────────────────

    def do_GET(self):
        """Handle GET requests (/health, /screenshot)."""
        if self.path == "/health":
            self._respond(200, {
                "status": "ok",
                "browser": _browser is not None,
                "default_mode": DEFAULT_MODE,
                "cdp_url": "http://127.0.0.1:9223",
            })
            return

        if self.path.startswith("/screenshot"):
            # Parse ?mode=desktop|browser from query string
            mode = self._parse_mode_from_query()
            with _lock:
                try:
                    if mode == "desktop":
                        b64 = _screenshot_desktop()
                        self._respond(200, {"screenshot": b64, "method": "desktop"})
                    else:
                        try:
                            b64 = _screenshot_playwright()
                            self._respond(200, {"screenshot": b64, "method": "playwright"})
                        except Exception:
                            b64 = _screenshot_desktop()
                            self._respond(200, {"screenshot": b64, "method": "desktop_fallback"})
                except Exception as e:
                    self._respond(500, {"error": str(e)})
            return

        self._respond(404, {"error": "not found"})

    # ── POST ──────────────────────────────────────────────────────────────

    def do_POST(self):
        """Handle POST requests (/action, /mode)."""
        body = self._read_body()

        if self.path == "/action":
            # Handle 'wait' outside the lock so it doesn't block
            # screenshots and other concurrent requests for up to 10 s.
            raw_action = body.get("action", "")
            resolved = resolve_action(raw_action)
            if resolved == "wait":
                dur = 2.0
                t = body.get("text", "")
                if t:
                    try:
                        dur = float(t)
                    except ValueError:
                        pass
                self._respond(200, _do_wait(dur))
                return

            with _lock:
                try:
                    result = self._dispatch_action(body)
                    self._respond(200, result)
                except Exception as e:
                    logger.exception("Action failed")
                    self._respond(500, {"success": False, "message": str(e)})
            return

        if self.path == "/mode":
            # Switch default mode at runtime
            new_mode = body.get("mode", "").lower()
            if new_mode in ("browser", "desktop"):
                global DEFAULT_MODE
                DEFAULT_MODE = new_mode
                logger.info("Default mode switched to: %s", DEFAULT_MODE)
                self._respond(200, {"mode": DEFAULT_MODE})
            else:
                self._respond(400, {"error": "mode must be 'browser' or 'desktop'"})
            return

        self._respond(404, {"error": "not found"})

    # ── Helpers ───────────────────────────────────────────────────────────

    def _parse_mode_from_query(self) -> str:
        """Extract mode from ?mode=... query param, or use DEFAULT_MODE."""
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for part in qs.split("&"):
                if part.startswith("mode="):
                    val = part.split("=", 1)[1].lower()
                    if val in ("browser", "desktop"):
                        return val
        return DEFAULT_MODE

    def _dispatch_action(self, body: dict) -> dict:
        """Route an incoming action to the correct engine dispatcher."""
        start_time = time.time()
        
        # 1. Resolve alias
        raw_action = body.get("action", "")
        action = resolve_action(raw_action)
        
        coords = body.get("coordinates", [])
        text = body.get("text", "")
        target = body.get("target", "")
        mode = body.get("mode", DEFAULT_MODE).lower()

        x = coords[0] if len(coords) >= 1 else SCREEN_WIDTH // 2
        y = coords[1] if len(coords) >= 2 else SCREEN_HEIGHT // 2

        result = {"success": False, "message": "Unknown error"}

        try:
            if action == "wait":
                duration = 2.0
                if text:
                    try:
                        duration = float(text)
                    except ValueError:
                        pass
                result = _do_wait(duration)
            
            # Dispatch to mode-specific handlers
            elif mode == "desktop":
                result = self._dispatch_desktop(action, x, y, text, coords, target)
            else:
                result = self._dispatch_browser(action, x, y, text, coords, target)
        except Exception as e:
            logger.exception(f"Action {action} failed")
            result = {"success": False, "message": str(e)}

        # Structured logging
        latency = (time.time() - start_time) * 1000
        log_entry = {
            "action": action,
            "engine": mode,
            "success": result.get("success", False),
            "latency_ms": latency,
            "raw_action": raw_action
        }
        logger.info(json.dumps(log_entry))
        
        return result

    def _dispatch_browser(self, action: str, x: int, y: int, text: str, coords: list, target: str = "") -> dict:
        """Dispatch a single action to the Playwright browser engine."""
        # ── Mouse / Interaction ───────────────────────────────────────
        if action == "click":
            return _pw_click(x, y)
        elif action == "double_click":
            return _pw_double_click(x, y)
        elif action == "right_click":
            return _pw_right_click(x, y)
        elif action == "middle_click":
            return _pw_middle_click(x, y)
        elif action == "hover":
            return _pw_hover(x, y)
        elif action == "drag":
            if len(coords) >= 4:
                return _pw_drag(coords[0], coords[1], coords[2], coords[3])
            return {"success": False, "message": "drag requires 4 coordinates [x1, y1, x2, y2]"}
        # ── Input ─────────────────────────────────────────────────────
        elif action == "type":
            selector = target or (text.split("|")[0] if "|" in text else "")
            value = text.split("|")[1] if "|" in text else text
            if not target and not "|" in text:
                 return _pw_type(text, coords)
            return _pw_type(value, coords, selector)
        elif action == "fill":
            selector = target or (text.split("|")[0] if "|" in text else "")
            value = text.split("|")[1] if "|" in text else text
            if not selector:
                return {"success": False, "message": "fill requires target (CSS selector)"}
            return _pw_fill(selector, value)
        elif action == "key":
            return _pw_key(text)
        elif action == "hotkey":
            keys = [k.strip() for k in text.split("+")]
            return _pw_hotkey(keys)
        elif action == "clear_input":
            selector = target or text
            if not selector:
                return {"success": False, "message": "clear_input requires target (CSS selector)"}
            return _pw_clear_input(selector)
        elif action == "select_option":
            selector = target or ""
            if not selector:
                return {"success": False, "message": "select_option requires target (CSS selector)"}
            return _pw_select_option(selector, text)
        elif action == "paste":
            return _pw_paste(text)
        elif action == "copy":
            return _pw_copy()
        # ── Navigation ────────────────────────────────────────────────
        elif action == "open_url":
            return _pw_navigate(text)
        elif action == "reload":
            return _pw_reload()
        elif action == "go_back":
            return _pw_go_back()
        elif action == "go_forward":
            return _pw_go_forward()
        # ── Tabs ──────────────────────────────────────────────────────
        elif action == "new_tab":
            return _pw_new_tab(text)
        elif action == "close_tab":
            return _pw_close_tab()
        elif action == "switch_tab":
            return _pw_switch_tab(text or target)
        # ── Scrolling ─────────────────────────────────────────────────
        elif action == "scroll":
            direction = text.lower() if text else "down"
            return _pw_scroll(x, y, direction)
        elif action == "scroll_to":
            selector = target or text
            if not selector:
                return {"success": False, "message": "scroll_to requires target (CSS selector)"}
            return _pw_scroll_to(selector)
        elif action == "scroll_into_view":
            selector = target or text
            if not selector:
                return {"success": False, "message": "scroll_into_view requires target (CSS selector)"}
            return _pw_scroll_into_view(selector)
        # ── DOM / Semantic ────────────────────────────────────────────
        elif action == "get_text":
            selector = target or text
            if not selector:
                return {"success": False, "message": "get_text requires target (CSS selector)"}
            return _pw_get_text(selector)
        elif action == "find_element":
            description = target or text
            if not description:
                return {"success": False, "message": "find_element requires target (text description)"}
            return _pw_find_element(description)
        # ── JavaScript ────────────────────────────────────────────────
        elif action == "evaluate_js":
            if not text:
                return {"success": False, "message": "evaluate_js requires text (JS code)"}
            return _pw_evaluate_js(text)
        # ── Control ───────────────────────────────────────────────────
        elif action == "wait_for":
            selector = target or text
            if not selector:
                return {"success": False, "message": "wait_for requires target (CSS selector)"}
            return _pw_wait_for(selector)
        elif action == "wait_for_navigation":
            return _pw_wait_for_navigation()
        elif action == "screenshot_region":
            if len(coords) >= 4:
                b64 = _pw_screenshot_region(coords[0], coords[1], coords[2], coords[3])
                return {"success": True, "message": f"Region screenshot captured", "screenshot": b64}
            return {"success": False, "message": "screenshot_region needs 4 coords [x, y, width, height]"}
        elif action == "screenshot_viewport":
            b64 = _pw_screenshot_viewport()
            return {"success": True, "message": "Viewport screenshot captured", "screenshot": b64}
        elif action == "screenshot_element":
            selector = target or text
            if not selector:
                return {"success": False, "message": "screenshot_element requires target"}
            b64 = _pw_screenshot_element(selector)
            if b64:
                return {"success": True, "message": "Element screenshot captured", "screenshot": b64}
            return {"success": False, "message": "Failed to capture element screenshot"}
        elif action == "get_html":
            selector = target or text
            return _pw_get_html(selector)
        elif action == "get_attribute":
            selector = target or (text.split("|")[0] if "|" in text else "")
            attribute = text.split("|")[1] if "|" in text else ""
            if not selector or not attribute:
                return {"success": False, "message": "get_attribute requires target (selector) and attribute (in text as selector|attribute)"}
            return _pw_get_attribute(selector, attribute)
        elif action == "query_selector":
            selector = target or text
            if not selector:
                return {"success": False, "message": "query_selector requires target (CSS selector)"}
            return _pw_query_selector(selector)
        elif action == "query_all":
            selector = target or text
            if not selector:
                return {"success": False, "message": "query_all requires target (CSS selector)"}
            return _pw_query_all(selector)
        elif action == "get_bounding_box":
            selector = target or text
            if not selector:
                return {"success": False, "message": "get_bounding_box requires target (CSS selector)"}
            return _pw_get_bounding_box(selector)
        elif action == "get_visible_elements":
            selector = target or text
            if not selector:
                return {"success": False, "message": "get_visible_elements requires target (CSS selector)"}
            return _pw_get_visible_elements(selector)
        elif action == "evaluate_on_selector":
            selector = target or (text.split("|")[0] if "|" in text else "")
            script = text.split("|")[1] if "|" in text else ""
            if not selector or not script:
                 return {"success": False, "message": "evaluate_on_selector requires target and script"}
            return _pw_evaluate_on_selector(selector, script)
        elif action == "upload_file":
            selector = target or (text.split("|")[0] if "|" in text else "")
            file_path = text.split("|")[1] if "|" in text else text
            if not selector or not file_path:
                 return {"success": False, "message": "upload_file requires target and file path"}
            return _pw_upload_file(selector, file_path)
        elif action == "download_file":
            selector = target or text
            return _pw_download_file(selector)
        elif action == "handle_file_chooser":
            return {"success": False, "message": "Not implemented yet"}
        elif action == "set_cookies":
            try:
                cookies = json.loads(text)
                return _pw_set_cookies(cookies)
            except Exception as e:
                return {"success": False, "message": f"set_cookies requires JSON text: {e}"}
        elif action == "get_cookies":
            return _pw_get_cookies()
        elif action == "clear_cookies":
            return _pw_clear_cookies()
        elif action == "storage_state":
            return {"success": False, "message": "Not implemented yet"}
        elif action == "new_context":
            return _pw_new_context()
        elif action == "switch_context":
            identifier = target or text
            return _pw_switch_context(identifier)
        elif action == "close_context":
            return _pw_close_context()
        elif action == "intercept_request":
            return {"success": False, "message": "Not implemented yet"}
        elif action == "monitor_requests":
            return {"success": False, "message": "Not implemented yet"}
        elif action == "get_response_body":
            return {"success": False, "message": "Not implemented yet"}
        elif action == "assert_element_present":
            selector = target or text
            if not selector:
                return {"success": False, "message": "assert_element_present requires target (CSS selector)"}
            return _pw_query_selector(selector)
        elif action == "verify_text":
            selector = target or "body"
            needle = text or ""
            if not needle:
                return {"success": False, "message": "verify_text requires text to verify"}
            res = _pw_get_text(selector)
            if not res.get("success"):
                return res
            found = needle.lower() in res.get("message", "").lower()
            return {"success": found, "message": f"verify_text={'ok' if found else 'not found'}: {needle[:120]}"}
        elif action == "retry_last_action":
            return {"success": False, "message": "Not implemented yet"}
        elif action == "fallback_strategy":
            return {"success": False, "message": "Not implemented yet"}
        elif action == "set_viewport":
            if len(coords) >= 2:
                return _pw_set_viewport(coords[0], coords[1])
            return {"success": False, "message": "set_viewport requires width and height"}
        elif action == "zoom":
            try:
                level = float(text)
                return _pw_zoom(level)
            except ValueError:
                return {"success": False, "message": "zoom requires a float value"}
        elif action == "block_resource":
            return _pw_block_resource(text)
        elif action == "type_at":
            if not coords or len(coords) < 2:
                 return {"success": False, "message": "type_at requires coordinates [x, y]"}
            return _pw_type(text, coords)
        elif action == "press_sequential":
            # Stub: treat as type
            return _pw_type(text, coords)
        elif action == "scroll_to_selector":
            selector = target or text
            if not selector:
                return {"success": False, "message": "scroll_to_selector requires target"}
            return _pw_scroll_to(selector)
        elif action == "export_page_pdf":
            b64 = _pw_export_page_pdf()
            return {"success": True, "message": "PDF exported", "pdf": b64}
        else:
            return {"success": False, "message": f"Unsupported action '{action}' in browser mode", "hint": "Check engine capability mapping"}

    def _dispatch_desktop(self, action: str, x: int, y: int, text: str, coords: list, target: str = "") -> dict:
        """Dispatch a single action to the xdotool desktop engine."""
        # ── Mouse / Interaction ───────────────────────────────────────
        if action == "click":
            return _xdo_click(x, y)
        elif action == "double_click":
            return _xdo_double_click(x, y)
        elif action == "right_click":
            return _xdo_right_click(x, y)
        elif action == "middle_click":
            return _xdo_middle_click(x, y)
        elif action == "hover":
            return _xdo_hover(x, y)
        elif action == "drag":
            if len(coords) >= 4:
                return _xdo_drag(coords[0], coords[1], coords[2], coords[3])
            return {"success": False, "message": "drag requires 4 coordinates [x1, y1, x2, y2]"}
        # ── Input ─────────────────────────────────────────────────────
        elif action == "type":
            if coords and len(coords) >= 2:
                _xdo_click(coords[0], coords[1])
                time.sleep(0.1)
            try:
                return _xdo_type(text)
            except Exception:
                logger.warning("xdotool type failed, trying paste fallback")
                return _xdo_paste(text)
        elif action == "key":
            return _xdo_key(text)
        elif action == "keydown":
            return _xdo_keydown(text)
        elif action == "keyup":
            return _xdo_keyup(text)
        elif action == "type_slow":
            if coords and len(coords) >= 2:
                _xdo_click(coords[0], coords[1])
                time.sleep(0.1)
            try:
                return _xdo_type_slow(text)
            except Exception:
                logger.warning("xdotool type_slow failed, trying paste fallback")
                return _xdo_paste(text)
        elif action == "hotkey":
            keys = [k.strip() for k in text.split("+")]
            return _xdo_hotkey(keys)
        elif action == "paste":
            return _xdo_paste(text)
        elif action == "copy":
            return _xdo_copy()
        # ── Navigation ────────────────────────────────────────────────
        elif action == "open_url":
            return _xdo_open_url(text)
        # ── Scrolling ─────────────────────────────────────────────────
        elif action == "scroll":
            direction = text.lower() if text else "down"
            return _xdo_scroll(x, y, direction)
        elif action == "scroll_up":
            return _xdo_scroll_up()
        elif action == "scroll_down":
            return _xdo_scroll_down()
        # ── Desktop / Window ──────────────────────────────────────────
        elif action == "focus_window":
            identifier = target or text
            if not identifier:
                return {"success": False, "message": "focus_window requires target (window name)"}
            return _xdo_focus_window(identifier)
        elif action == "window_activate":
            identifier = target or text
            if not identifier:
                return {"success": False, "message": "window_activate requires target"}
            return _xdo_focus_window(identifier)
        elif action == "focus_mouse":
            _xdo(["mousemove", "--sync", str(x), str(y)])
            return {"success": True, "message": f"Focused mouse at ({x}, {y})"}
        elif action == "mousemove":
            if len(coords) >= 2:
                _xdo(["mousemove", "--sync", str(coords[0]), str(coords[1])])
                return {"success": True, "message": f"Moved mouse to ({coords[0]}, {coords[1]})"}
            return {"success": False, "message": "mousemove requires coordinates [x, y]"}
        elif action == "open_app":
            app = target or text
            if not app:
                return {"success": False, "message": "open_app requires target (app command)"}
            return _xdo_open_app(app)
        elif action == "close_window":
            identifier = target or text
            if not identifier:
                return {"success": False, "message": "close_window requires target (window title or class)"}
            return _wmctrl_close_window(identifier)
        elif action == "window_minimize":
            identifier = target or text
            if not identifier:
                return {"success": False, "message": "window_minimize requires target"}
            return _xdo_window_minimize(identifier)
        elif action == "window_maximize":
            identifier = target or text
            if not identifier:
                return {"success": False, "message": "window_maximize requires target"}
            return _xdo_window_maximize(identifier)
        elif action == "window_move":
            identifier = target or text
            if not identifier or not coords or len(coords) < 2:
                return {"success": False, "message": "window_move requires target and 2 coordinates"}
            return _xdo_window_move(identifier, coords[0], coords[1])
        elif action == "window_resize":
            identifier = target or text
            if not identifier or not coords or len(coords) < 2:
                return {"success": False, "message": "window_resize requires target and 2 coordinates"}
            return _xdo_window_resize(identifier, coords[0], coords[1])
        elif action == "search_window":
            identifier = target or text
            if not identifier:
                return {"success": False, "message": "search_window requires target"}
            return _xdo_search_window(identifier)
        elif action == "focus_click":
            identifier = target or text
            if not identifier:
                 return {"success": False, "message": "focus_click requires target (window name)"}
            return _xdo_focus_click(identifier, x, y)
        # ── Fill / Clear (desktop approximation via keyboard) ─────────
        elif action == "fill":
            # In desktop mode, fill = click + wait + clear stuck mods + select all + delete + type
            if coords and len(coords) >= 2:
                _xdo_click(coords[0], coords[1])
                time.sleep(0.1)
            try:
                _xdo(["keyup", "shift"])
                _xdo(["keyup", "ctrl"])
                _xdo(["keyup", "alt"])
            except Exception:
                pass
            time.sleep(0.05)
            _xdo(["key", "--clearmodifiers", "ctrl+a"])
            time.sleep(0.05)
            _xdo(["key", "--clearmodifiers", "Delete"])
            time.sleep(0.1)
            value = text or ""
            try:
                _xdo(["type", "--clearmodifiers", "--delay", "25", "--", value])
            except Exception:
                logger.warning("Desktop fill type failed, using paste fallback")
                return _xdo_paste(value)
            return {"success": True, "message": f"Filled (desktop): {value[:50]}"}
        elif action == "clear_input":
            _xdo(["key", "--clearmodifiers", "ctrl+a"])
            time.sleep(0.05)
            _xdo(["key", "--clearmodifiers", "Delete"])
            return {"success": True, "message": "Cleared input (desktop)"}
        elif action == "select_option":
            return {"success": False, "message": "select_option not supported in desktop mode — use click"}
        # ── Browser-like navigation via keyboard shortcuts ─────────────
        elif action == "reload":
            _xdo(["key", "--clearmodifiers", "F5"])
            return {"success": True, "message": "Reloaded (F5)"}
        elif action == "go_back":
            _xdo(["key", "--clearmodifiers", "alt+Left"])
            return {"success": True, "message": "Navigated back (Alt+Left)"}
        elif action == "go_forward":
            _xdo(["key", "--clearmodifiers", "alt+Right"])
            return {"success": True, "message": "Navigated forward (Alt+Right)"}
        elif action == "new_tab":
            _xdo(["key", "--clearmodifiers", "ctrl+t"])
            time.sleep(0.5)
            if text:
                _xdo(["type", "--clearmodifiers", "--delay", "30", "--", text])
                _xdo(["key", "--clearmodifiers", "Return"])
            return {"success": True, "message": f"New tab (Ctrl+T){': ' + text[:50] if text else ''}"}
        elif action == "close_tab":
            _xdo(["key", "--clearmodifiers", "ctrl+w"])
            return {"success": True, "message": "Closed tab (Ctrl+W)"}
        elif action == "switch_tab":
            identifier = target or text or ""
            try:
                idx = int(identifier)
                # Ctrl+1..9 to switch by tab index
                if 1 <= idx <= 9:
                    _xdo(["key", "--clearmodifiers", f"ctrl+{idx}"])
                    return {"success": True, "message": f"Switched to tab {idx}"}
            except ValueError:
                pass
            _xdo(["key", "--clearmodifiers", "ctrl+Next"])
            return {"success": True, "message": "Switched to next tab (Ctrl+PageDown)"}
        # ── Scroll to (approximate via scroll) ────────────────────────
        elif action == "scroll_to":
            return {"success": False, "message": "scroll_to not supported in desktop mode — use scroll"}
        # ── DOM / Semantic (not available in desktop mode) ─────────────
        elif action == "get_text":
            return {"success": False, "message": "get_text not supported in desktop mode"}
        elif action == "find_element":
            return {"success": False, "message": "find_element not supported in desktop mode"}
        elif action == "evaluate_js":
            return {"success": False, "message": "evaluate_js not supported in desktop mode"}
        elif action == "wait_for":
            return _do_wait(3.0)  # Approximate: just wait a few seconds
        # ── Shell / Terminal ────────────────────────────────────────────
        elif action == "run_command":
            cmd = text or target
            if not cmd:
                return {"success": False, "message": "run_command requires text (shell command)"}
            try:
                args = shlex.split(cmd)
            except ValueError as e:
                return {"success": False, "message": f"Invalid command syntax: {e}"}
            if not args:
                return {"success": False, "message": "Empty command"}
            if args[0] not in _ALLOWED_COMMANDS:
                return {"success": False, "message": f"Command not allowed: {args[0]}. Permitted: {', '.join(sorted(_ALLOWED_COMMANDS))}"}
            try:
                result = subprocess.run(
                    args, shell=False, capture_output=True, text=True, timeout=30,
                    env={**os.environ, "DISPLAY": ":99"},
                )
                output = (result.stdout + result.stderr).strip()[:2000]
                return {"success": result.returncode == 0, "message": output or f"Command exited with code {result.returncode}"}
            except subprocess.TimeoutExpired:
                return {"success": False, "message": "Command timed out after 30s"}
        elif action == "open_terminal":
            return _open_terminal()
        # ── Vision ────────────────────────────────────────────────────
        elif action in ("screenshot", "screenshot_full"):
            b64 = _xdo_screenshot_full()
            return {"success": True, "message": "Full screenshot captured", "screenshot": b64}
        elif action == "screenshot_region":
            if len(coords) >= 4:
                b64 = _xdo_screenshot_region(coords[0], coords[1], coords[2], coords[3])
                return {"success": True, "message": "Region screenshot captured", "screenshot": b64}
            return {"success": False, "message": "screenshot_region needs 4 coords [x, y, width, height]"}
        else:
            return {"success": False, "message": f"Unsupported action '{action}' in desktop engine"}


def main():
    """Start the HTTP agent service and initialise the browser."""
    logger.info("Starting agent service on port %d (default_mode=%s)", SERVICE_PORT, DEFAULT_MODE)

    # Initialize Playwright browser (available even in desktop mode for fallback)
    try:
        _init_browser()
    except Exception as e:
        logger.warning("Playwright init failed (desktop mode still works): %s", e)

    server = HTTPServer(("0.0.0.0", SERVICE_PORT), AgentHandler)
    logger.info("Agent service listening on 0.0.0.0:%d", SERVICE_PORT)

    def _handle_signal(sig, frame):
        """Gracefully shut down the server on SIGTERM/SIGINT."""
        logger.info("Shutting down...")
        _shutdown_browser()
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_browser()
        server.server_close()


if __name__ == "__main__":
    main()
