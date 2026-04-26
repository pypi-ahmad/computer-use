"""Internal agent service — runs INSIDE the Docker container.

Provides a lightweight HTTP API for desktop automation using xdotool and
scrot. Applications run inside the X11 desktop and are controlled through
desktop input events only.
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
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
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

_lock = Lock()

SCREEN_WIDTH = int(os.environ.get("SCREEN_WIDTH", "1440"))
SCREEN_HEIGHT = int(os.environ.get("SCREEN_HEIGHT", "900"))
SERVICE_PORT = int(os.environ.get("AGENT_SERVICE_PORT", "9222"))
DEFAULT_MODE = os.environ.get("AGENT_MODE", "desktop")
ACTION_DELAY = float(os.environ.get("ACTION_DELAY", "0.05"))
AGENT_SERVICE_TOKEN = os.environ.get("AGENT_SERVICE_TOKEN", "").strip()


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

# ── Attack-surface reduction (PR <docker-action-trim>) ───────────────────────
#
# The engine (``backend/engine/__init__.py::DesktopExecutor``) emits a
# fixed set of action names on ``POST /action``. Everything outside that
# set is drift from earlier experiments — window management, browser-tab
# shortcuts, shell-exec, region screenshots, upload helpers. Serving
# them by default grows the attack surface and review burden without
# improving live behaviour.
#
# The default posture is minimal: only ``_ENGINE_ACTIONS`` reach the
# dispatcher. Setting ``CUA_ENABLE_LEGACY_ACTIONS=1`` opts in to the
# legacy handlers for debug / migration flows. Unknown or disabled
# action names return HTTP 404 (not 400) so callers get an unambiguous
# signal rather than a generic "failed" response.
#
# DO NOT expand ``_ENGINE_ACTIONS`` without a matching change to the
# engine adapters and a short note in ``docker/SECURITY_NOTES.md``.
_ENGINE_ACTIONS: frozenset[str] = frozenset({
    "click", "double_click", "right_click", "middle_click", "hover",
    "type", "hotkey", "key", "keydown", "keyup",
    "scroll", "left_mouse_down", "left_mouse_up", "drag",
    "open_url",
    # ``zoom`` is a ``computer_20251124``-era action (Claude Opus 4.7
    # et al.) — always on when the adapter advertises enable_zoom.
    "zoom",
})

_LEGACY_ACTIONS: frozenset[str] = frozenset({
    # Window management (wmctrl / xdotool)
    "focus_window", "window_activate", "close_window", "search_window",
    "window_minimize", "window_maximize", "window_move", "window_resize",
    "focus_click", "focus_mouse", "mousemove",
    # App launch
    "open_app", "open_terminal",
    # Clipboard / alternate input
    "paste", "copy", "type_slow",
    # Form helpers (desktop approximation)
    "fill", "clear_input", "select_option",
    # Browser-like navigation via keyboard shortcuts
    "reload", "go_back", "go_forward",
    "new_tab", "close_tab", "switch_tab", "scroll_to",
    # DOM / JS stubs (never implemented for desktop)
    "get_text", "find_element", "evaluate_js", "wait_for",
    # Scrolling directional variants
    "scroll_up", "scroll_down",
    # Vision
    "screenshot", "screenshot_full", "screenshot_region",
    # Shell / wait
    "run_command", "wait",
})

LEGACY_ACTIONS_ENABLED = _env_bool("CUA_ENABLE_LEGACY_ACTIONS", False)


def _is_action_enabled(action: str) -> bool:
    """Return True when *action* is served by this build."""
    if action in _ENGINE_ACTIONS:
        return True
    if LEGACY_ACTIONS_ENABLED and action in _LEGACY_ACTIONS:
        return True
    return False


# ── Security constants ────────────────────────────────────────────────────────

_MAX_BODY_SIZE = 1_000_000  # 1 MB request body limit

# Uniform subprocess timeout (seconds) for all synchronous xdotool / scrot /
# wmctrl / xrandr / xprop calls (P2). A single constant keeps bounds
# predictable — any short-running X11 helper that hangs longer than this is
# treated as a failure. The shell-exec path in run_command uses its own
# explicit 30 s timeout because user-supplied commands can legitimately
# block longer than an X11 helper.
_SUBPROCESS_TIMEOUT = 10

# Dangerous shell patterns blocked in run_command (defense-in-depth).
# Enforcement lives in :func:`_blocked_cmd_match`, which the
# ``run_command`` dispatch calls AFTER the allowlist check and BEFORE
# any subprocess invocation. Patterns are matched case-insensitively
# against both the executable and the full space-joined argv so
# obfuscation like ``bash -c 'RM -RF /'`` or ``python -c "import os;
# os.system('shutdown')"`` is caught as well as a bare match on
# ``argv[0]``. This list MUST remain the single source of truth —
# tests reach in via ``docker/agent_service._BLOCKED_CMD_PATTERNS``.
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


def _blocked_cmd_match(args: list[str]) -> str | None:
    """Return the first blocked pattern matching *args*, or None.

    The match is case-insensitive and runs against the executable
    (``args[0]``) AND the space-joined argv. Joining is required so a
    dangerous phrase hidden in a later arg (``bash -c 'rm -rf /'``,
    ``python -c "import os; os.system('shutdown')"``) still trips the
    gate — ``argv[0]``-only checks are what we're trying to strengthen
    away from. Returning the matched pattern lets the caller log
    ``pattern=<name>`` at WARN without echoing the full argv (which
    may contain secrets from prompt-injected commands).
    """
    if not args:
        return None
    haystack = (args[0] + " " + " ".join(args[1:])).lower()
    for pattern in _BLOCKED_CMD_PATTERNS:
        if pattern.lower() in haystack:
            return pattern
    return None

# Allowed directories for file upload operations
_UPLOAD_ALLOWED_PREFIXES = ("/tmp", "/app", "/home")


def _is_safe_upload_path(target: str) -> bool:
    """Return True if *target* resolves inside an allowed upload prefix.

    Prefix-only ``startswith`` checks are unsafe for two reasons:
    ``/tmp/foo`` could be a symlink to ``/etc/passwd``, and
    ``/tmp2/secret`` string-matches ``/tmp`` as a prefix. We canonicalise
    with ``Path.resolve`` (which follows symlinks) and then use
    ``Path.is_relative_to`` — the path-component-aware primitive — so
    ``/tmp`` only matches ``/tmp`` or ``/tmp/<rest>``, never ``/tmp2/...``.
    """
    if not target or not isinstance(target, str):
        return False
    try:
        # ``strict=False`` so we can validate not-yet-existing upload
        # destinations; ``resolve`` still follows symlinks on every
        # existing component, which is the property we care about.
        real = Path(target).resolve(strict=False)
    except (OSError, ValueError):
        return False
    # Also resolve the parent to catch a symlinked directory whose
    # final component hasn't been created yet.
    try:
        parent_real = Path(os.path.dirname(target) or "/").resolve(strict=False)
    except (OSError, ValueError):
        return False

    def _under(child: Path, root_str: str) -> bool:
        """True iff ``child`` is ``root`` or a descendant — by path
        components, not by string prefix."""
        try:
            root = Path(root_str).resolve(strict=False)
        except (OSError, ValueError):
            return False
        if child == root:
            return True
        try:
            return child.is_relative_to(root)
        except AttributeError:  # pragma: no cover — Python < 3.9
            try:
                child.relative_to(root)
                return True
            except ValueError:
                return False

    for prefix in _UPLOAD_ALLOWED_PREFIXES:
        if _under(real, prefix) and _under(parent_real, prefix):
            return True
    return False

# Strict allowlist of commands permitted in run_command.
# Note: curl/wget are intentionally excluded (S2) — they double as
# exfil channels for a prompt-injected VLM. Use xdg-open for web
# navigation instead.
_ALLOWED_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "grep", "find", "wc", "echo",
    "pwd", "whoami", "id", "date", "env", "printenv",
    "which", "file", "stat", "df", "du", "free",
    "uname", "hostname", "uptime",
    "python3", "python", "pip", "pip3", "node", "npm", "npx",
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



# ══════════════════════════════════════════════════════════════════════════════
#  Screenshots
# ══════════════════════════════════════════════════════════════════════════════


def _screenshot_desktop() -> str:
    """Capture the full Xvfb display via scrot (works with any app)."""
    subprocess.run(
        ["scrot", "-z", "-o", "/tmp/screenshot.png"],
        check=True, timeout=_SUBPROCESS_TIMEOUT,
    )
    with open("/tmp/screenshot.png", "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


# ══════════════════════════════════════════════════════════════════════════════
#  Actions — xdotool (desktop mode, works with any X11 app)
# ══════════════════════════════════════════════════════════════════════════════

def _read_int_env(name: str, default: int) -> int:
    """Read a non-negative integer env var, falling back to *default*."""
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# Compensation sleep applied after stripping ``--sync`` from xdotool calls.
# Read once at import — these knobs are tuning constants, not per-request
# overrides. Restart the service to change them.
_XDO_SYNC_SLEEP_S = _read_int_env("XDO_SYNC_SLEEP_MS", 75) / 1000.0
_XDO_WINDOW_SLEEP_S = _read_int_env("XDO_WINDOW_SLEEP_MS", 400) / 1000.0


def _xdo(args: list[str]) -> str:
    """Run an xdotool command, return stdout.

    The ``--sync`` flag is automatically stripped because it hangs
    indefinitely in Xvfb environments that lack a compositor (the X
    event that ``--sync`` waits for is never delivered).  A small
    ``time.sleep`` replaces it so callers stay unchanged.

    The compensation sleep is configurable via ``XDO_SYNC_SLEEP_MS``
    (mousemove/click) and ``XDO_WINDOW_SLEEP_MS`` (windowactivate)
    so slow hosts / CI runners can bump the defaults without a code
    change (C7). Values are read once at import.
    """
    had_sync = "--sync" in args
    if had_sync:
        args = [a for a in args if a != "--sync"]

    result = subprocess.run(
        ["xdotool"] + args,
        capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)} failed: {result.stderr.strip()}")

    # Compensate for removed --sync with a short delay
    if had_sync:
        cmd = args[0] if args else ""
        if cmd == "windowactivate":
            time.sleep(_XDO_WINDOW_SLEEP_S)
        else:
            time.sleep(_XDO_SYNC_SLEEP_S)

    return result.stdout.strip()


def _xdo_search_window_ids(identifier: str) -> list[str]:
    """Return xdotool window IDs matching *identifier* by name."""
    if not identifier:
        return []
    result = subprocess.run(
        ["xdotool", "search", "--name", identifier],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
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
            timeout=_SUBPROCESS_TIMEOUT,
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
        subprocess.run(["wmctrl", "-ir", wids[0], "-b", "add,maximized_vert,maximized_horz"], check=False, timeout=_SUBPROCESS_TIMEOUT)
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


def _xdo_left_mouse_down() -> dict:
    """Hold the left mouse button down."""
    _xdo(["mousedown", "1"])
    return {"success": True, "message": "Left mouse button down"}


def _xdo_left_mouse_up() -> dict:
    """Release the left mouse button."""
    _xdo(["mouseup", "1"])
    return {"success": True, "message": "Left mouse button up"}


# ── Deterministic browser launch (shared by xdotool open_url) ─────────────

# Pre-created profile directory (seeded at build-time in Dockerfile)
_CHROME_PROFILE_DIR = "/tmp/chrome-profile"

# Chrome flags that suppress ALL first-run UI, keyring dialogs, and sync
# prompts. ``--disable-extensions`` and ``--disable-file-system`` are
# explicitly required by the OpenAI Computer Use guide's browser-security
# posture (Option 1 / Chromium path) — a compromised renderer with file-
# system access would be a direct jailbreak out of the sandbox.
_CHROME_FLAGS: list[str] = [
    "--no-sandbox",
    "--no-first-run",
    "--disable-first-run-ui",
    "--disable-sync",
    "--disable-extensions",
    "--disable-file-system",
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


# Minimal environment for the browser subprocess. The OpenAI CU guide
# recommends dropping the host environment entirely to prevent a
# compromised renderer from reading operator secrets (API keys,
# AGENT_SERVICE_TOKEN, etc.) out of the process env. We still need
# DISPLAY / HOME / PATH / LANG / XDG_RUNTIME_DIR for the X11 client
# and Chrome's profile paths to resolve, so those are whitelisted.
def _browser_minimal_env() -> dict[str, str]:
    """Return a minimal env for the browser subprocess — whitelist
    only what X11 + Chrome profile loading need. No host leakage."""
    return {
        "DISPLAY": os.environ.get("DISPLAY", ":99"),
        "HOME": os.environ.get("HOME", "/home/agent"),
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/tmp/xdg-runtime-dir"),
    }

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
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
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
                        capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
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
            env=_browser_minimal_env(),
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
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
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
        input=text.encode(), check=True, timeout=_SUBPROCESS_TIMEOUT,
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
        capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
    )
    if result.returncode == 0:
        return {"success": True, "message": f"Closed window: {identifier}"}
    return {"success": False, "message": f"Failed to close window: {identifier} — {result.stderr.strip()}"}


def _xdo_screenshot_full() -> str:
    """Capture the full screen via scrot."""
    subprocess.run(
        ["scrot", "-z", "-o", "/tmp/full.png"],
        check=True, timeout=_SUBPROCESS_TIMEOUT,
    )
    with open("/tmp/full.png", "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _xdo_screenshot_region(x: int, y: int, w: int, h: int) -> str:
    """Capture a region of the screen via scrot."""
    subprocess.run(
        ["scrot", "-z", "-o", "-a", f"{x},{y},{w},{h}", "/tmp/region.png"],
        check=True, timeout=_SUBPROCESS_TIMEOUT,
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
    """HTTP handler for the supported desktop automation mode."""

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

    def _authorized(self) -> bool:
        """Return True when shared-secret auth passes (or is disabled).

        /health remains unauthenticated for container readiness probes.
        """
        if not AGENT_SERVICE_TOKEN:
            return True  # auth disabled (dev / legacy)
        if self.path == "/health" or self.path.startswith("/health?"):
            return True
        supplied = self.headers.get("X-Agent-Token", "")
        import hmac
        return hmac.compare_digest(supplied, AGENT_SERVICE_TOKEN)

    # ── GET ───────────────────────────────────────────────────────────────

    def do_GET(self):
        """Handle GET requests (/health, /screenshot)."""
        if not self._authorized():
            self._respond(401, {"error": "unauthorized"})
            return
        if self.path == "/health":
            self._respond(200, {
                "status": "ok",
                "browser": False,
                "default_mode": DEFAULT_MODE,
                "supported_modes": ["desktop"],
            })
            return

        if self.path.startswith("/screenshot"):
            # Parse ?mode=desktop from query string
            mode = self._parse_mode_from_query()
            with _lock:
                try:
                    if mode != "desktop":
                        self._respond(400, {"error": "Browser mode is no longer supported"})
                        return
                    b64 = _screenshot_desktop()
                    self._respond(200, {"screenshot": b64, "method": "desktop"})
                except Exception as e:
                    self._respond(500, {"error": str(e)})
            return

        self._respond(404, {"error": "not found"})

    # ── POST ──────────────────────────────────────────────────────────────

    def do_POST(self):
        """Handle POST requests (/action, /mode)."""
        if not self._authorized():
            self._respond(401, {"error": "unauthorized"})
            return
        body = self._read_body()

        if self.path == "/action":
            # Attack-surface gate: unknown or legacy-disabled actions
            # return HTTP 404 BEFORE any handler runs, so callers get a
            # clean "not found" signal instead of a generic failure
            # response. See ``_ENGINE_ACTIONS`` above.
            raw_action = body.get("action", "")
            resolved = resolve_action(raw_action)
            if not _is_action_enabled(resolved):
                logger.info(
                    "action rejected: action=%r resolved=%r legacy_enabled=%s",
                    raw_action, resolved, LEGACY_ACTIONS_ENABLED,
                )
                self._respond(404, {
                    "success": False,
                    # Keep the standard /action error envelope so
                    # debug callers can treat a gated action like any
                    # other action failure while the 404 status still
                    # makes reachability explicit.
                    "message": f"Unknown or disabled action: {resolved!r}",
                })
                return

            # Handle 'wait' outside the lock so it doesn't block
            # screenshots and other concurrent requests for up to 10 s.
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
            new_mode = body.get("mode", "desktop").lower()
            if new_mode != "desktop":
                self._respond(400, {"error": "Browser mode is no longer supported"})
                return
            global DEFAULT_MODE
            DEFAULT_MODE = "desktop"
            logger.info("Default mode confirmed: %s", DEFAULT_MODE)
            self._respond(200, {"mode": DEFAULT_MODE})
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
                    if val == "desktop":
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
            else:
                if mode != "desktop":
                    result = {"success": False, "message": "Browser mode is no longer supported"}
                else:
                    result = self._dispatch_desktop(action, x, y, text, coords, target)
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
        elif action == "left_mouse_down":
            return _xdo_left_mouse_down()
        elif action == "left_mouse_up":
            return _xdo_left_mouse_up()
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
            # Defense-in-depth: even an allowlisted executable can be
            # weaponised via an inline script (``bash -c 'rm -rf /'``,
            # ``python -c "...os.system('shutdown')..."``). Scan the
            # full argv against ``_BLOCKED_CMD_PATTERNS``. On a hit,
            # return the SAME error shape as the allowlist denial so
            # clients can't enumerate which gate fired. Log the matched
            # pattern name but NOT the argv — a prompt-injected command
            # could have pasted a secret in there.
            matched = _blocked_cmd_match(args)
            if matched is not None:
                logger.warning(
                    "run_command blocked: pattern=%r executable=%s",
                    matched, args[0],
                )
                return {"success": False, "message": f"Command not allowed: {args[0]}. Permitted: {', '.join(sorted(_ALLOWED_COMMANDS))}"}
            # S5: wrap in prlimit when available so a runaway child
            # can't burn unbounded CPU / memory inside the container.
            # 20 CPU-seconds and 1 GiB address-space is more than
            # enough for any legitimate shell helper the agent runs.
            _prlimit = shutil.which("prlimit")
            exec_args = (
                [_prlimit, "--cpu=20", "--as=1073741824", "--nofile=256", "--"] + args
                if _prlimit else args
            )
            try:
                result = subprocess.run(
                    exec_args, shell=False, capture_output=True, text=True, timeout=30,
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
        elif action == "zoom":
            # Claude ``computer_20251124`` zoom action.  Expects
            # ``region=[x1, y1, x2, y2]`` with top-left to bottom-right
            # corners.  The adapter has already validated shape, clamped
            # to display bounds, and rejected inverted rectangles — we
            # trust the input here and translate to the scrot region
            # shape ``x, y, width, height``.  On scrot failure, fall
            # back to a full-screen screenshot so the model can still
            # make progress.
            if len(coords) >= 4:
                x1, y1, x2, y2 = coords[0], coords[1], coords[2], coords[3]
                w, h = max(1, x2 - x1), max(1, y2 - y1)
                try:
                    b64 = _xdo_screenshot_region(x1, y1, w, h)
                    return {"success": True, "message": "Zoom region captured", "screenshot": b64}
                except Exception as exc:
                    logger.warning("zoom scrot failed: %s; falling back to full screen", exc)
                    b64 = _xdo_screenshot_full()
                    return {
                        "success": False,
                        "message": f"zoom fallback to full screen: {exc}",
                        "screenshot": b64,
                    }
            return {"success": False, "message": "zoom needs 4 coords [x1, y1, x2, y2]"}
        else:
            return {"success": False, "message": f"Unsupported action '{action}' in desktop engine"}


def main():
    """Start the HTTP agent service for desktop automation."""
    logger.info("Starting agent service on port %d (default_mode=%s)", SERVICE_PORT, DEFAULT_MODE)

    # S-B: prlimit is the only thing that bounds CPU/memory of run_command's
    # children. If it's missing we still serve, but loudly so operators
    # don't ship a degraded sandbox unknowingly.
    if shutil.which("prlimit") is None:
        logger.warning(
            "prlimit not found on PATH \u2014 run_command children will execute "
            "WITHOUT CPU/AS/nofile rlimits. Install util-linux in the image."
        )

    server = ThreadingHTTPServer(("0.0.0.0", SERVICE_PORT), AgentHandler)
    logger.info("Agent service listening on 0.0.0.0:%d", SERVICE_PORT)

    def _handle_signal(sig, frame):
        """Gracefully shut down the server on SIGTERM/SIGINT."""
        logger.info("Shutting down...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
