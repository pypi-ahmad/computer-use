"""Tests for PlaywrightExecutor action dispatch (Gemini CU actions)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.engine import PlaywrightExecutor, CUActionResult


@pytest.fixture
def executor(mock_page):
    """Create a PlaywrightExecutor with normalized Gemini coords for dispatch tests."""
    return PlaywrightExecutor(
        page=mock_page,
        screen_width=1440,
        screen_height=900,
        normalize_coords=True,
    )


class TestPlaywrightExecutor:
    """Test Gemini CU action dispatching via PlaywrightExecutor."""

    @pytest.mark.asyncio
    async def test_click_at(self, executor, mock_page):
        result = await executor.execute("click_at", {"x": 500, "y": 500})
        assert result.success
        mock_page.mouse.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_hover_at(self, executor, mock_page):
        result = await executor.execute("hover_at", {"x": 100, "y": 200})
        assert result.success
        mock_page.mouse.move.assert_called_once()

    @pytest.mark.asyncio
    async def test_type_text_at(self, executor, mock_page):
        result = await executor.execute("type_text_at", {
            "x": 500, "y": 500, "text": "hello",
            "press_enter": False, "clear_before_typing": False,
        })
        assert result.success
        mock_page.keyboard.type.assert_called_once_with("hello")

    @pytest.mark.asyncio
    async def test_key_combination(self, executor, mock_page):
        result = await executor.execute("key_combination", {"keys": "Control+C"})
        assert result.success
        mock_page.keyboard.press.assert_called_once_with("Control+C")

    @pytest.mark.asyncio
    async def test_navigate(self, executor, mock_page):
        result = await executor.execute("navigate", {"url": "https://example.com"})
        assert result.success
        mock_page.goto.assert_called_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_go_back(self, executor, mock_page):
        result = await executor.execute("go_back", {})
        assert result.success
        mock_page.go_back.assert_called_once()

    @pytest.mark.asyncio
    async def test_go_forward(self, executor, mock_page):
        result = await executor.execute("go_forward", {})
        assert result.success
        mock_page.go_forward.assert_called_once()

    @pytest.mark.asyncio
    async def test_scroll_document(self, executor, mock_page):
        result = await executor.execute("scroll_document", {"direction": "down"})
        assert result.success
        mock_page.mouse.wheel.assert_called_once()

    @pytest.mark.asyncio
    async def test_scroll_at(self, executor, mock_page):
        result = await executor.execute("scroll_at", {
            "x": 500, "y": 500, "direction": "up", "magnitude": 500,
        })
        assert result.success
        mock_page.mouse.wheel.assert_called_once()

    @pytest.mark.asyncio
    async def test_drag_and_drop(self, executor, mock_page):
        result = await executor.execute("drag_and_drop", {
            "x": 100, "y": 100, "destination_x": 500, "destination_y": 500,
        })
        assert result.success

    @pytest.mark.asyncio
    async def test_wait_5_seconds(self, executor):
        # Patch sleep to avoid actual wait
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", AsyncMock())
            result = await executor.execute("wait_5_seconds", {})
        assert result.success

    @pytest.mark.asyncio
    async def test_unimplemented_action(self, executor):
        result = await executor.execute("nonexistent_action", {})
        assert not result.success
        assert "Unimplemented" in result.error

    @pytest.mark.asyncio
    async def test_coordinate_denormalization(self, executor, mock_page):
        """Gemini coords (0-999) should be denormalized to pixels."""
        await executor.execute("click_at", {"x": 0, "y": 0})
        args = mock_page.mouse.click.call_args[0]
        assert args == (0, 0)

    @pytest.mark.asyncio
    async def test_capture_screenshot(self, executor, mock_page):
        result = await executor.capture_screenshot()
        assert result == b"\x89PNG" + b"\x00" * 100

    def test_get_current_url(self, executor, mock_page):
        assert executor.get_current_url() == "https://example.com"

    @pytest.mark.asyncio
    async def test_safety_pop(self, executor, mock_page):
        """Safety decision should be extracted from args and returned in result."""
        result = await executor.execute("click_at", {
            "x": 500, "y": 500,
            "safety_decision": {"decision": "require_confirmation", "explanation": "test"},
        })
        assert result.success
        assert result.safety_decision is not None
