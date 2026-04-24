"""Sandbox / Opus 4.7 hi-res tests.

Covers:
  * ``docker/Dockerfile`` installs the union of Anthropic reference
    packages (xvfb, xdotool, scrot, imagemagick, mutter, x11vnc,
    firefox-esr, xterm, x11-apps, xpdf, tint2, sudo,
    build-essential, software-properties-common, netcat-openbsd)
    alongside the existing XFCE4 stack.
  * Default viewport is 1440x900 (the union-of-best-practice across
    all four CU providers) and ``WIDTH`` / ``HEIGHT`` aliases are set.
  * ``CUA_OPUS47_HIRES=1`` drops the 3.75 MP total-pixel cap for
    Opus 4.7 so 2560x1600 keeps 1:1 coordinates, and is ignored for
    every other model.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Dockerfile pins
# ---------------------------------------------------------------------------


_DOCKERFILE = Path("docker/Dockerfile")

# Anthropic computer-use-demo reference packages that must be present.
# Order is the install-order they appear in the Dockerfile; mutter /
# xfce4 coexist per the repo's WM choice.
_REQUIRED_PACKAGES = (
    # Core / network / build
    "build-essential",
    "netcat-openbsd",
    "software-properties-common",
    "sudo",
    # Desktop / X11 / apps
    "firefox-esr",
    "imagemagick",
    "mutter",
    "scrot",
    "tint2",
    "x11-apps",
    "x11vnc",
    "xdotool",
    "xpdf",
    "xterm",
    "xvfb",
)


class TestDockerfileReferencePackages:
    def test_dockerfile_has_anthropic_reference_packages(self):
        text = _DOCKERFILE.read_text(encoding="utf-8")
        missing = [pkg for pkg in _REQUIRED_PACKAGES if pkg not in text]
        assert not missing, (
            f"docker/Dockerfile missing Anthropic reference packages: "
            f"{missing}"
        )

    def test_dockerfile_preserves_xfce4_wm(self):
        """XFCE4 is the project's WM; mutter is installed alongside
        for Anthropic-reference parity but XFCE4 must NOT be removed."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "xfce4" in text
        assert "xfce4-goodies" in text


class TestDockerfileViewportDefault:
    def test_dockerfile_default_viewport_1440x900(self):
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV SCREEN_WIDTH=1440" in text
        assert "ENV SCREEN_HEIGHT=900" in text

    def test_dockerfile_width_height_aliases(self):
        """``WIDTH`` / ``HEIGHT`` are exposed as aliases of
        ``SCREEN_WIDTH`` / ``SCREEN_HEIGHT`` for parity with the
        Anthropic quickstart container."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV WIDTH=1440" in text
        assert "ENV HEIGHT=900" in text

    def test_entrypoint_xvfb_uses_screen_dims(self):
        text = Path("docker/entrypoint.sh").read_text(encoding="utf-8")
        assert "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" in text


# ---------------------------------------------------------------------------
# Opus 4.7 hi-res env flag
# ---------------------------------------------------------------------------


class TestOpus47HiresEnvFlag:
    @staticmethod
    async def _capture_scale_log(model: str, screen_w: int, screen_h: int,
                                  env_value: str | None) -> dict:
        """Drive one turn through ``iter_turns`` and capture the
        ``tools=[{display_width_px, display_height_px}]`` block + any
        CUA_OPUS47_HIRES log line."""
        from backend.engine import ClaudeCUClient

        # Lightweight 1x1 PNG that passes the >=100 B guard.
        png = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00" * 8
            + screen_w.to_bytes(4, "big")
            + screen_h.to_bytes(4, "big")
            + b"\x00" * 100
        )

        class FakeExecutor:
            def __init__(self):
                self.screen_width = screen_w
                self.screen_height = screen_h

            async def capture_screenshot(self):
                return png

            async def execute(self, name, args):
                from backend.engine import CUActionResult
                return CUActionResult(name=name)

            def get_current_url(self):
                return ""

        captured: dict = {}
        logs: list[tuple[str, str]] = []

        class FakeResponse:
            stop_reason = "end_turn"
            content: list = []

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return FakeResponse()

        class FakeMessages:
            create = staticmethod(fake_create)

        class FakeBeta:
            messages = FakeMessages()

        class FakeClient:
            beta = FakeBeta()

        with patch.dict("os.environ",
                        {"CUA_OPUS47_HIRES": env_value} if env_value is not None
                        else {}, clear=False):
            # If env_value is None, make sure the key is truly absent.
            if env_value is None:
                import os as _os
                _os.environ.pop("CUA_OPUS47_HIRES", None)

            with patch("backend.engine.claude.resize_screenshot_for_claude",
                       return_value=(png, screen_w, screen_h)):
                with patch("anthropic.AsyncAnthropic") as AA:
                    AA.return_value = FakeClient()
                    client = ClaudeCUClient(api_key="k", model=model)
                    async for _ in client.iter_turns(
                        "noop", FakeExecutor(), turn_limit=1,
                        on_log=lambda lvl, msg: logs.append((lvl, msg)),
                    ):
                        pass

        return {"create_kwargs": captured, "logs": logs}

    @pytest.mark.asyncio
    async def test_opus47_hires_flag_allows_2560x1600(self, monkeypatch):
        """Opus 4.7 with CUA_OPUS47_HIRES=1 at 2560x1600 keeps 1:1
        coordinates — default ``get_claude_scale_factor`` would have
        downscaled because 2560*1600=4.10 MP exceeds the 3.75 MP cap."""
        monkeypatch.setenv("CUA_OPUS47_HIRES", "1")
        result = await self._capture_scale_log(
            "claude-opus-4-7", 2560, 1600, "1",
        )
        tools = result["create_kwargs"]["tools"]
        # Long edge 2560 <= 2576, so scale is 1.0 and reported dims
        # equal the native framebuffer.
        assert tools[0]["display_width_px"] == 2560
        assert tools[0]["display_height_px"] == 1600

        # A log line confirms the gate took effect.
        assert any("CUA_OPUS47_HIRES" in msg for lvl, msg in result["logs"])

    @pytest.mark.asyncio
    async def test_opus47_hires_flag_clamps_at_2576(self, monkeypatch):
        """Long edge > 2576 still clamps — the flag only drops the
        pixel-count cap, not the long-edge ceiling."""
        monkeypatch.setenv("CUA_OPUS47_HIRES", "1")
        result = await self._capture_scale_log(
            "claude-opus-4-7", 4000, 2500, "1",
        )
        tools = result["create_kwargs"]["tools"]
        # 2576/4000 = 0.644 -> 2576 x 1610.
        assert tools[0]["display_width_px"] == 2576
        assert tools[0]["display_height_px"] == 1610

    @pytest.mark.asyncio
    async def test_opus47_hires_flag_ignored_for_sonnet_46(self, monkeypatch):
        """Sonnet 4.6 must NOT honor CUA_OPUS47_HIRES — it downsamples
        internally and the 3.75 MP ceiling is mandatory."""
        monkeypatch.setenv("CUA_OPUS47_HIRES", "1")
        result = await self._capture_scale_log(
            "claude-sonnet-4-6", 2560, 1600, "1",
        )
        tools = result["create_kwargs"]["tools"]
        # Default get_claude_scale_factor picks sqrt(3.75e6 / 4.096e6)
        # ≈ 0.9566 -> 2448 x 1530 (floored).
        assert tools[0]["display_width_px"] < 2560
        assert tools[0]["display_height_px"] < 1600
        # And no hi-res log line.
        assert not any(
            "CUA_OPUS47_HIRES" in msg for lvl, msg in result["logs"]
        )

    @pytest.mark.asyncio
    async def test_opus47_hires_flag_off_by_default(self, monkeypatch):
        """Without the flag, Opus 4.7 at 2560x1600 still downscales
        per the default ``get_claude_scale_factor`` pixel-count cap."""
        monkeypatch.delenv("CUA_OPUS47_HIRES", raising=False)
        result = await self._capture_scale_log(
            "claude-opus-4-7", 2560, 1600, None,
        )
        tools = result["create_kwargs"]["tools"]
        assert tools[0]["display_width_px"] < 2560
