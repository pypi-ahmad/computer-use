"""Targeted regression tests for the April 2026 six-fix wave."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.engine import CUActionResult


def _minimal_png(width: int = 1280, height: int = 800) -> bytes:
    """Return a minimal PNG header large enough to pass screenshot guards."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00" * 8
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x00" * 100
    )


class _FakeExecutor:
    """Minimal executor stub for Claude adapter tests."""

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


@pytest.mark.asyncio
async def test_fix3_claude_initial_screenshot_guard_and_happy_path():
    """Empty bytes return a logged error string; a valid PNG still reaches Claude."""
    from backend.engine import ClaudeCUClient

    logs: list[tuple[str, str]] = []

    with patch("anthropic.AsyncAnthropic"), \
         patch("backend.engine.claude.resize_screenshot_for_claude") as resize_mock:
        client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")
        final_text = await client.run_loop(
            "noop",
            _FakeExecutor(screenshot=b""),
            turn_limit=1,
            on_log=lambda level, message: logs.append((level, message)),
        )

    resize_mock.assert_not_called()
    assert final_text == "Error: Could not capture initial screenshot"
    assert ("error", "Initial screenshot capture failed or returned empty bytes") in logs

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

    with patch("anthropic.AsyncAnthropic") as anthropic_async:
        anthropic_async.return_value = FakeClient()
        client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")
        final_text = await client.run_loop(
            "noop",
            _FakeExecutor(screenshot=_minimal_png()),
            turn_limit=1,
        )

    assert captured, "Anthropic SDK must be reached on the happy path"
    assert final_text == ""


@pytest.mark.asyncio
async def test_fix4_claude_opus_47_uses_adaptive_thinking_only():
    """Opus 4.7 uses adaptive thinking; earlier Claude models keep budgeted mode."""
    from backend.engine import ClaudeCUClient

    async def capture_request(model: str) -> dict:
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

        with patch("anthropic.AsyncAnthropic") as anthropic_async:
            anthropic_async.return_value = FakeClient()
            client = ClaudeCUClient(api_key="k", model=model)
            await client.run_loop(
                "noop",
                _FakeExecutor(screenshot=_minimal_png()),
                turn_limit=1,
            )

        return captured

    opus_47 = await capture_request("claude-opus-4-7")
    sonnet_46 = await capture_request("claude-sonnet-4-6")
    opus_46 = await capture_request("claude-opus-4-6")

    assert opus_47["thinking"] == {"type": "adaptive"}
    assert "temperature" not in opus_47
    assert "top_p" not in opus_47
    assert "top_k" not in opus_47
    assert sonnet_46["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert opus_46["thinking"] == {"type": "enabled", "budget_tokens": 4096}


def test_fix5_opus_47_scale_factor_uses_long_edge_only():
    """Opus 4.7 skips the legacy total-pixel cap; earlier models do not."""
    from backend.engine import (
        _CLAUDE_MAX_LONG_EDGE,
        _CLAUDE_MAX_PIXELS,
        get_claude_scale_factor,
        resize_screenshot_for_claude,
    )

    assert get_claude_scale_factor(1440, 900, "claude-opus-4-7") == 1.0

    src = _minimal_png(1440, 900)
    out_bytes, out_w, out_h = resize_screenshot_for_claude(src, 1.0)
    assert out_bytes == src
    assert (out_w, out_h) == (1440, 900)

    scale = get_claude_scale_factor(4000, 2000, "claude-opus-4-7")
    assert scale == pytest.approx(2576 / 4000)

    sonnet_scale = get_claude_scale_factor(1440, 900, "claude-sonnet-4-6")
    assert sonnet_scale == pytest.approx(
        min(1.0, _CLAUDE_MAX_LONG_EDGE / 1440, (_CLAUDE_MAX_PIXELS / (1440 * 900)) ** 0.5)
    )
