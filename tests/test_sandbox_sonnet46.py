"""Sonnet 4.6 shares Opus 4.7's sandbox contract.

Prompt S1 established the Anthropic-reference package baseline + 1440x900
viewport + opt-in ``CUA_OPUS47_HIRES`` gate.  Sonnet 4.6 rides the same
``computer_20251124`` tool version and ``computer-use-2025-11-24`` beta
header but does NOT inherit Opus 4.7's 2576 px / 1:1 improvements — it
stays on the 1568 px / 1.15 MP ceiling and downscales internally.

These tests lock the shared-sandbox contract so a future refactor that
forks the Dockerfile per-model or accidentally extends
``CUA_OPUS47_HIRES`` to Sonnet 4.6 fails loudly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.engine import CUActionResult


_DOCKERFILE = Path("docker/Dockerfile")


class TestSonnet46SharesSandbox:
    def test_sonnet46_uses_shared_1440x900_default(self):
        """No per-model viewport fork: the single 1440x900 default
        from S1 covers Sonnet 4.6 as well."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV SCREEN_WIDTH=1440" in text
        assert "ENV SCREEN_HEIGHT=900" in text
        # The viewport comment block must call out Sonnet 4.6 so the
        # shared-default intent is documented at the source.
        assert "Sonnet 4.6" in text, (
            "Dockerfile viewport comment must document that Sonnet 4.6 "
            "shares the 1440x900 default"
        )

    def test_sonnet46_uses_shared_packages(self):
        """The Anthropic computer-use-demo reference package set from
        S1 is the single source of truth for all Claude models.  There
        must be NO Sonnet-4.6-specific package branch."""
        text = _DOCKERFILE.read_text(encoding="utf-8")
        # Sanity: the S1 reference packages are still present.
        for pkg in (
            "xdotool", "scrot", "xvfb", "x11vnc", "mutter",
            "firefox-esr", "imagemagick",
        ):
            assert pkg in text, f"reference package {pkg!r} missing"

        # Negative: no model-specific install line.  If someone later
        # adds ``RUN ... sonnet`` or a Sonnet-only apt block, this
        # catches it.
        lowered = text.lower()
        assert "sonnet-4-6" not in lowered
        assert "claude-sonnet" not in lowered


class TestSonnet46HiresFlagIgnored:
    @pytest.mark.asyncio
    async def test_sonnet46_does_not_honor_opus47_hires_flag(
        self, monkeypatch,
    ):
        """``CUA_OPUS47_HIRES=1`` must be a no-op for Sonnet 4.6.
        Even at 2560x1600 Sonnet 4.6 must still downscale per the
        default ``get_claude_scale_factor`` (pixel-count cap of
        3.75 MP on the computer_20251124 tool path)."""
        from backend.engine import ClaudeCUClient

        monkeypatch.setenv("CUA_OPUS47_HIRES", "1")

        screen_w, screen_h = 2560, 1600
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

        with patch(
            "backend.engine.claude.resize_screenshot_for_claude",
            return_value=(png, screen_w, screen_h),
        ), patch("anthropic.AsyncAnthropic") as AA:
            AA.return_value = FakeClient()
            client = ClaudeCUClient(api_key="k", model="claude-sonnet-4-6")
            async for _ in client.iter_turns(
                "noop", FakeExecutor(), turn_limit=1,
                on_log=lambda lvl, msg: logs.append((lvl, msg)),
            ):
                pass

        tools = captured["tools"]
        # Downscaled: strictly smaller than the native framebuffer.
        assert tools[0]["display_width_px"] < screen_w
        assert tools[0]["display_height_px"] < screen_h
        # And the Opus-4.7 hi-res gate log line must NOT appear.
        assert not any(
            "CUA_OPUS47_HIRES" in msg for _lvl, msg in logs
        ), "CUA_OPUS47_HIRES must be ignored for Sonnet 4.6"
