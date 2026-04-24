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
