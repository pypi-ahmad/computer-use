"""Tests for Claude action dispatch in ClaudeCUClient._execute_claude_action."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.engine import ClaudeCUClient, PlaywrightExecutor


@pytest.fixture
def executor(mock_page):
    """Create a PlaywrightExecutor with real-pixel coords for Claude dispatch tests."""
    return PlaywrightExecutor(
        page=mock_page,
        screen_width=1440,
        screen_height=900,
        normalize_coords=False,  # Claude uses real pixels
    )


@pytest.fixture
def client():
    """Create a ClaudeCUClient without a real API key (for action dispatch tests)."""
    with patch("anthropic.Anthropic"):
        return ClaudeCUClient(
            api_key="test-key",
            model="claude-sonnet-4-6",
        )


class TestClaudeActionDispatch:
    """Test _execute_claude_action for all supported Claude actions."""

    @pytest.mark.asyncio
    async def test_click(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        mock_page.mouse.click.assert_called_once_with(100, 200)

    @pytest.mark.asyncio
    async def test_double_click(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "double_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        mock_page.mouse.dblclick.assert_called_once_with(100, 200)

    @pytest.mark.asyncio
    async def test_right_click(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "right_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        mock_page.mouse.click.assert_called_once_with(100, 200, button="right")

    @pytest.mark.asyncio
    async def test_middle_click(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "middle_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        mock_page.mouse.click.assert_called_once_with(100, 200, button="middle")

    @pytest.mark.asyncio
    async def test_triple_click(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "triple_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        mock_page.mouse.click.assert_called_once_with(100, 200, click_count=3)

    @pytest.mark.asyncio
    async def test_type(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "type", "text": "hello"},
            executor,
        )
        assert result.success
        mock_page.keyboard.type.assert_called_once_with("hello")

    @pytest.mark.asyncio
    async def test_key(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "key", "key": "Return"},
            executor,
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_scroll(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "scroll", "coordinate": [500, 500], "direction": "down", "amount": 3},
            executor,
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_mouse_move(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "mouse_move", "coordinate": [300, 400]},
            executor,
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_left_mouse_down(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "left_mouse_down"},
            executor,
        )
        assert result.success
        mock_page.mouse.down.assert_called_once()

    @pytest.mark.asyncio
    async def test_left_mouse_up(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "left_mouse_up"},
            executor,
        )
        assert result.success
        mock_page.mouse.up.assert_called_once()

    @pytest.mark.asyncio
    async def test_hold_key(self, client, executor, mock_page):
        result = await client._execute_claude_action(
            {"action": "hold_key", "key": "Shift", "duration": 0.1},
            executor,
        )
        assert result.success
        mock_page.keyboard.down.assert_called_once_with("Shift")
        mock_page.keyboard.up.assert_called_once_with("Shift")

    @pytest.mark.asyncio
    async def test_wait(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "wait", "duration": 0.01},
            executor,
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_zoom(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "zoom"},
            executor,
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_screenshot_is_noop(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "screenshot"},
            executor,
        )
        assert result.success
        assert result.name == "screenshot"

    @pytest.mark.asyncio
    async def test_unknown_action_fails(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "nonexistent_action"},
            executor,
        )
        assert not result.success
        assert "Unknown" in result.error

    @pytest.mark.asyncio
    async def test_coordinate_upscaling(self, client, executor, mock_page):
        """When scaling is active, coordinates should be upscaled."""
        scale = 0.8
        result = await client._execute_claude_action(
            {"action": "click", "coordinate": [80, 160]},
            executor,
            scale_factor=scale,
        )
        assert result.success
        # Coords should be upscaled: 80/0.8=100, 160/0.8=200
        call_args = mock_page.mouse.click.call_args[0]
        assert call_args == (100, 200)


class TestClaudeToolConfig:
    """Test ClaudeCUClient tool configuration."""

    def test_tool_version_auto_detect_sonnet(self):
        with patch("anthropic.Anthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-sonnet-4-6")
        assert client._tool_version == "computer_20251124"
        assert client._beta_flag == "computer-use-2025-11-24"

    def test_tool_version_auto_detect_opus(self):
        with patch("anthropic.Anthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-opus-4-6")
        assert client._tool_version == "computer_20251124"
        assert client._beta_flag == "computer-use-2025-11-24"

    def test_explicit_tool_version_overrides(self):
        with patch("anthropic.Anthropic"):
            client = ClaudeCUClient(
                api_key="test", model="some-model",
                tool_version="computer_20250124",
                beta_flag="computer-use-2025-01-24",
            )
        assert client._tool_version == "computer_20250124"
        assert client._beta_flag == "computer-use-2025-01-24"

    def test_build_tools_includes_zoom(self):
        with patch("anthropic.Anthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-sonnet-4-6")
        tools = client._build_tools(1200, 800)
        assert tools[0]["type"] == "computer_20251124"
        assert tools[0]["enable_zoom"] is True
        assert tools[0]["display_width_px"] == 1200
        assert tools[0]["display_height_px"] == 800
