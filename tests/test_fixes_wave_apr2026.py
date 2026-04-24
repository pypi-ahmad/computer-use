"""Tests for the April 2026 adapter-alignment wave.

Covers:
  * Fix 3: Claude adapter guards against an empty initial screenshot
    and emits an ``on_log("error", ...)`` diagnostic matching the
    OpenAI and Gemini adapters' shape.
  * Fix 4: Claude thinking mode branches on the tool version —
    ``computer_20251124`` models use ``{"type": "adaptive"}``, while
    legacy ``computer_20250124`` models keep the fixed-budget shape.
  * Fix 5: ``get_claude_scale_factor`` keeps current-tool Claude models
    on the 2576px / ~3.75 MP path and legacy models on the 1568px /
    1.15 MP path.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.engine import (
    CUActionResult,
    get_claude_scale_factor,
)


# ---------------------------------------------------------------------------
# Shared fake executor helpers
# ---------------------------------------------------------------------------


def _minimal_png(width: int = 1280, height: int = 800) -> bytes:
    """Return bytes large enough to pass the >=100 B guard."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00" * 8
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x00" * 100
    )


class _FakeExecutor:
    """Fake executor that returns a caller-controlled screenshot once."""

    screen_width = 1280
    screen_height = 800

    def __init__(self, screenshot: bytes) -> None:
        self._screenshot = screenshot

    async def capture_screenshot(self) -> bytes:
        return self._screenshot

    async def execute(self, name, args):
        return CUActionResult(name=name)

    def get_current_url(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# Fix 3 — Claude initial-screenshot guard
# ---------------------------------------------------------------------------


class TestClaudeInitialScreenshotGuard:
    """Empty screenshot bytes must terminate cleanly with an error
    log and a ``RunCompleted`` event whose ``final_text`` matches the
    OpenAI/Gemini convention, without ever calling
    ``resize_screenshot_for_claude`` or the Anthropic SDK."""

    @pytest.mark.asyncio
    async def test_empty_bytes_returns_error_and_logs(self):
        from backend.engine import ClaudeCUClient, RunCompleted

        logs: list[tuple[str, str]] = []

        with patch("anthropic.AsyncAnthropic"), \
             patch("backend.engine.claude.resize_screenshot_for_claude") as resize_mock:
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

            results = []
            async for evt in client.iter_turns(
                "noop",
                _FakeExecutor(screenshot=b""),
                turn_limit=1,
                on_log=lambda lvl, msg: logs.append((lvl, msg)),
            ):
                results.append(evt)

        # resize must not be reached on the empty-bytes path.
        resize_mock.assert_not_called()

        # Exactly one terminating event of the right shape.
        assert len(results) == 1
        assert isinstance(results[0], RunCompleted)
        assert results[0].final_text == "Error: Could not capture initial screenshot"

        # Log surface mirrors OpenAI + Gemini.
        assert ("error", "Initial screenshot capture failed or returned empty bytes") in logs

    @pytest.mark.asyncio
    async def test_short_bytes_returns_error(self):
        """Anything shorter than 100 B is treated as a capture failure."""
        from backend.engine import ClaudeCUClient, RunCompleted

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

            results = []
            async for evt in client.iter_turns(
                "noop",
                _FakeExecutor(screenshot=b"\x89PNG" + b"\x00" * 10),
                turn_limit=1,
            ):
                results.append(evt)

        assert len(results) == 1
        assert isinstance(results[0], RunCompleted)
        assert results[0].final_text == "Error: Could not capture initial screenshot"

    @pytest.mark.asyncio
    async def test_happy_path_reaches_sdk(self):
        """A valid PNG must still proceed to ``messages.create``."""
        from backend.engine import ClaudeCUClient

        captured: dict = {}

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

        with patch("anthropic.AsyncAnthropic") as AA:
            AA.return_value = FakeClient()
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

            async for _ in client.iter_turns(
                "noop", _FakeExecutor(screenshot=_minimal_png()), turn_limit=1
            ):
                pass

        assert captured, "Anthropic SDK must be reached on happy path"


# ---------------------------------------------------------------------------
# Fix 4 — model-gated thinking mode
# ---------------------------------------------------------------------------


class TestClaudeThinkingMode:
    """Current-tool Claude models use adaptive thinking; legacy models keep
    the fixed-budget ``enabled`` shape."""

    @staticmethod
    async def _capture_thinking(model: str) -> dict:
        from backend.engine import ClaudeCUClient

        captured: dict = {}

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

        with patch("anthropic.AsyncAnthropic") as AA:
            AA.return_value = FakeClient()
            client = ClaudeCUClient(api_key="k", model=model)
            async for _ in client.iter_turns(
                "noop", _FakeExecutor(screenshot=_minimal_png()), turn_limit=1
            ):
                pass

        return captured

    @pytest.mark.asyncio
    async def test_opus_47_uses_adaptive(self):
        captured = await self._capture_thinking("claude-opus-4-7")
        assert captured.get("thinking") == {"type": "adaptive"}
        # No sampling params must slip in (HTTP 400 on Opus 4.7).
        assert "temperature" not in captured
        assert "top_p" not in captured
        assert "top_k" not in captured

    @pytest.mark.asyncio
    async def test_legacy_model_uses_enabled_budget(self):
        captured = await self._capture_thinking("claude-3-5-sonnet-20241022")
        assert captured.get("thinking") == {"type": "enabled", "budget_tokens": 4096}


# ---------------------------------------------------------------------------
# Fix 5 — current-tool Claude models keep the high-res path
# ---------------------------------------------------------------------------


class TestClaudeScaleFactorOpus47:
    def test_opus_47_1440x900_is_identity(self):
        assert get_claude_scale_factor(1440, 900, "claude-opus-4-7") == 1.0

    def test_opus_47_long_edge_enforced(self):
        # 4000-px long edge must shrink to exactly 2576, regardless of
        # total-pixel count (8 MP here, far above the legacy 1.15 MP
        # cap and the 3.75 MP high-res cap).
        scale = get_claude_scale_factor(4000, 2000, "claude-opus-4-7")
        assert scale == pytest.approx(2576 / 4000)

    def test_sonnet_46_unchanged_at_typical_res(self):
        # Sonnet 4.6 stays on the high-res path (2576-px + 3.75 MP).
        # 1440x900 = 1.296 MP is under the 3.75 MP ceiling and
        # 1440 < 2576, so scale is still 1.0.
        assert get_claude_scale_factor(1440, 900, "claude-sonnet-4-6") == 1.0

    def test_opus_47_resize_identity_at_scale_1(self):
        from backend.engine import resize_screenshot_for_claude

        # Build a real tiny PNG so Pillow can round-trip it.
        from io import BytesIO

        pytest.importorskip("PIL")
        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (1440, 900), "white").save(buf, format="PNG")
        src = buf.getvalue()

        out_bytes, out_w, out_h = resize_screenshot_for_claude(src, 1.0)
        assert (out_w, out_h) == (1440, 900)
        # Decoding the returned bytes must yield the same dimensions.
        assert Image.open(BytesIO(out_bytes)).size == (1440, 900)
