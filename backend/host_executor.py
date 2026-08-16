"""Host-desktop Computer Use executor.

Same ActionExecutor surface as DesktopExecutor, but screenshots and
input target the operator machine instead of cua-environment.
Sandbox path is unchanged. Opt-in via execution_target='host'.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import subprocess
import sys
import time
import webbrowser
from typing import Any

from backend.executor import DesktopExecutor

logger = logging.getLogger(__name__)

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_ABSOLUTE = 0x8000
_WHEEL_DELTA = 120
_KEYEVENTF_KEYUP = 0x0002

_VK: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "return": 0x0D,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "caps_lock": 0x14,
    "escape": 0x1B,
    "space": 0x20,
    "page_up": 0x21,
    "pageup": 0x21,
    "page_down": 0x22,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "super": 0x5B,
    "print": 0x2C,
    "scroll_lock": 0x91,
    "num_lock": 0x90,
    "minus": 0xBD,
    "plus": 0xBB,
    "equal": 0xBB,
    "comma": 0xBC,
    "period": 0xBE,
    "slash": 0xBF,
    "backslash": 0xDC,
    "semicolon": 0xBA,
    "apostrophe": 0xDE,
    "grave": 0xC0,
    "bracketleft": 0xDB,
    "bracketright": 0xDD,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}


def detect_host_screen() -> tuple[int, int]:
    """Return the primary display size in pixels."""
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            logger.debug("SetProcessDPIAware unavailable", exc_info=True)
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
        if width > 0 and height > 0:
            return width, height
    from PIL import ImageGrab

    image = ImageGrab.grab()
    return int(image.size[0]), int(image.size[1])


class HostDesktopExecutor(DesktopExecutor):
    """DesktopExecutor that applies actions on the host OS."""

    def __init__(
        self,
        screen_width: int | None = None,
        screen_height: int | None = None,
        normalize_coords: bool = True,
        agent_service_url: str = "http://127.0.0.1:9222",
        container_name: str = "cua-environment",
    ) -> None:
        if screen_width is None or screen_height is None:
            screen_width, screen_height = detect_host_screen()
        super().__init__(
            screen_width=screen_width,
            screen_height=screen_height,
            normalize_coords=normalize_coords,
            agent_service_url=agent_service_url,
            container_name=container_name,
        )
        self._plat = sys.platform

    async def _post_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.to_thread(self._apply, payload)
        if self._include_screenshot:
            png = await self.capture_screenshot()
            result["screenshot"] = base64.b64encode(png).decode("ascii")
        return result

    async def capture_screenshot(self) -> bytes:
        return await asyncio.to_thread(self._grab_png)

    async def _fallback_screenshot(self) -> bytes:
        return await self.capture_screenshot()

    def _grab_png(self) -> bytes:
        from PIL import ImageGrab

        image = ImageGrab.grab()
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        data = buf.getvalue()
        if not data:
            raise RuntimeError("Host screenshot was empty")
        return data

    def _apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        handler = getattr(self, f"_host_{action}", None)
        if handler is None:
            raise RuntimeError(f"Unsupported host action: {action}")
        handler(payload)
        return {"ok": True, "action": action}

    def _coords(self, payload: dict[str, Any]) -> list[int]:
        raw = payload.get("coordinates") or []
        return [int(value) for value in raw]

    def _host_click(self, payload: dict[str, Any]) -> None:
        x, y = self._coords(payload)[:2]
        self._click_at(x, y, button=1, count=1)

    def _host_double_click(self, payload: dict[str, Any]) -> None:
        x, y = self._coords(payload)[:2]
        self._click_at(x, y, button=1, count=2)

    def _host_triple_click(self, payload: dict[str, Any]) -> None:
        x, y = self._coords(payload)[:2]
        self._click_at(x, y, button=1, count=3)

    def _host_right_click(self, payload: dict[str, Any]) -> None:
        x, y = self._coords(payload)[:2]
        self._click_at(x, y, button=3, count=1)

    def _host_middle_click(self, payload: dict[str, Any]) -> None:
        x, y = self._coords(payload)[:2]
        self._click_at(x, y, button=2, count=1)

    def _host_hover(self, payload: dict[str, Any]) -> None:
        x, y = self._coords(payload)[:2]
        self._move(x, y)

    def _host_left_mouse_down(self, payload: dict[str, Any]) -> None:
        self._mouse_button(1, down=True)

    def _host_left_mouse_up(self, payload: dict[str, Any]) -> None:
        self._mouse_button(1, down=False)

    def _host_type(self, payload: dict[str, Any]) -> None:
        self._type_text(str(payload.get("text") or ""))

    def _host_type_text_at(self, payload: dict[str, Any]) -> None:
        x, y = self._coords(payload)[:2]
        self._click_at(x, y, button=1, count=1)
        if payload.get("clear_before", True):
            self._key_combo(["ctrl", "a"])
            self._tap_key("backspace")
        self._type_text(str(payload.get("text") or ""))
        if payload.get("press_enter", True):
            self._tap_key("return")

    def _host_key(self, payload: dict[str, Any]) -> None:
        tokens = [
            part.strip() for part in str(payload.get("text") or "").split("+") if part.strip()
        ]
        if tokens:
            self._key_combo(tokens)

    def _host_keydown(self, payload: dict[str, Any]) -> None:
        self._key_event(str(payload.get("text") or ""), up=False)

    def _host_keyup(self, payload: dict[str, Any]) -> None:
        self._key_event(str(payload.get("text") or ""), up=True)

    def _host_scroll(self, payload: dict[str, Any]) -> None:
        coords = self._coords(payload)
        if len(coords) >= 2:
            self._move(coords[0], coords[1])
        direction = str(payload.get("text") or "down").lower()
        amount = int(payload.get("magnitude") or 3)
        self._scroll(direction, amount)

    def _host_drag(self, payload: dict[str, Any]) -> None:
        coords = self._coords(payload)
        if len(coords) < 4:
            raise RuntimeError("drag requires [x1,y1,x2,y2]")
        self._move(coords[0], coords[1])
        self._mouse_button(1, down=True)
        time.sleep(0.03)
        self._move(coords[2], coords[3])
        time.sleep(0.03)
        self._mouse_button(1, down=False)

    def _host_open_url(self, payload: dict[str, Any]) -> None:
        url = str(payload.get("text") or "").strip()
        if not url:
            raise RuntimeError("open_url missing URL")
        webbrowser.open(url)

    def _host_zoom(self, payload: dict[str, Any]) -> None:
        return None

    def _click_at(self, x: int, y: int, *, button: int, count: int) -> None:
        self._move(x, y)
        for _ in range(max(1, count)):
            self._mouse_button(button, down=True)
            self._mouse_button(button, down=False)
            time.sleep(0.04)

    def _move(self, x: int, y: int) -> None:
        x = max(0, min(int(x), self.screen_width - 1))
        y = max(0, min(int(y), self.screen_height - 1))
        if self._plat == "win32":
            import ctypes

            ax = int(x * 65535 / max(self.screen_width - 1, 1))
            ay = int(y * 65535 / max(self.screen_height - 1, 1))
            ctypes.windll.user32.mouse_event(
                _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE, ax, ay, 0, 0
            )
            return
        if self._plat.startswith("linux"):
            self._run("xdotool", "mousemove", "--sync", str(x), str(y))
            return
        self._run("cliclick", f"m:{x},{y}")

    def _mouse_button(self, button: int, *, down: bool) -> None:
        if self._plat == "win32":
            import ctypes

            flags = {
                (1, True): _MOUSEEVENTF_LEFTDOWN,
                (1, False): _MOUSEEVENTF_LEFTUP,
                (2, True): _MOUSEEVENTF_MIDDLEDOWN,
                (2, False): _MOUSEEVENTF_MIDDLEUP,
                (3, True): _MOUSEEVENTF_RIGHTDOWN,
                (3, False): _MOUSEEVENTF_RIGHTUP,
            }.get((button, down))
            if flags is None:
                raise RuntimeError(f"Unsupported mouse button {button}")
            ctypes.windll.user32.mouse_event(flags, 0, 0, 0, 0)
            return
        if self._plat.startswith("linux"):
            verb = "mousedown" if down else "mouseup"
            self._run("xdotool", verb, str(button))
            return
        action = "dd" if down else "du"
        if button != 1:
            raise RuntimeError("macOS host executor only supports the left button")
        self._run("cliclick", action)

    def _scroll(self, direction: str, amount: int) -> None:
        amount = max(1, min(amount, 20))
        if self._plat == "win32":
            import ctypes

            delta = _WHEEL_DELTA * amount
            if direction in {"down", "south"}:
                delta = -delta
            elif direction not in {"up", "north"}:
                if direction in {"left", "west"}:
                    self._key_combo(["left"] * min(amount, 5))
                    return
                if direction in {"right", "east"}:
                    self._key_combo(["right"] * min(amount, 5))
                    return
            ctypes.windll.user32.mouse_event(_MOUSEEVENTF_WHEEL, 0, 0, delta & 0xFFFFFFFF, 0)
            return
        if self._plat.startswith("linux"):
            button = "4" if direction in {"up", "north"} else "5"
            for _ in range(amount):
                self._run("xdotool", "click", button)
            return
        for _ in range(amount):
            self._tap_key("page_up" if direction in {"up", "north"} else "page_down")

    def _type_text(self, text: str) -> None:
        if not text:
            return
        if self._plat == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            for char in text:
                if char == "\n":
                    self._tap_key("return")
                    continue
                scan = user32.VkKeyScanW(ord(char))
                if scan == -1:
                    continue
                vk = scan & 0xFF
                shift = bool(scan & 0x100)
                if shift:
                    self._key_event("shift", up=False)
                user32.keybd_event(vk, 0, 0, 0)
                user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
                if shift:
                    self._key_event("shift", up=True)
            return
        if self._plat.startswith("linux"):
            self._run("xdotool", "type", "--clearmodifiers", "--", text)
            return
        self._run("cliclick", f"t:{text}")

    def _key_combo(self, tokens: list[str]) -> None:
        lowered = [token.lower() for token in tokens]
        for token in lowered:
            self._key_event(token, up=False)
        for token in reversed(lowered):
            self._key_event(token, up=True)

    def _tap_key(self, token: str) -> None:
        self._key_event(token, up=False)
        self._key_event(token, up=True)

    def _key_event(self, token: str, *, up: bool) -> None:
        name = token.lower()
        if self._plat == "win32":
            import ctypes

            vk = _VK.get(name)
            if vk is None and len(name) == 1:
                vk = ord(name.upper())
            if vk is None:
                raise RuntimeError(f"Unsupported host key: {token}")
            flags = _KEYEVENTF_KEYUP if up else 0
            ctypes.windll.user32.keybd_event(vk, 0, flags, 0)
            return
        if self._plat.startswith("linux"):
            verb = "keyup" if up else "keydown"
            self._run("xdotool", verb, self._xdotool_key(name))
            return
        if up:
            return
        self._run("cliclick", f"kp:{name}")

    @staticmethod
    def _xdotool_key(name: str) -> str:
        mapping = {
            "return": "Return",
            "enter": "Return",
            "ctrl": "ctrl",
            "alt": "alt",
            "shift": "shift",
            "super": "super",
            "page_up": "Page_Up",
            "pageup": "Page_Up",
            "page_down": "Page_Down",
            "pagedown": "Page_Down",
            "left": "Left",
            "right": "Right",
            "up": "Up",
            "down": "Down",
            "backspace": "BackSpace",
            "escape": "Escape",
        }
        return mapping.get(name, name)

    @staticmethod
    def _run(*cmd: str) -> None:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"{cmd[0]} failed: {err or completed.returncode}")
