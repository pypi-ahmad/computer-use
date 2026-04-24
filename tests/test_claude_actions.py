"""Tests for Claude action dispatch in ClaudeCUClient._execute_claude_action."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.engine import ClaudeCUClient, CUActionResult


class FakeExecutor:
    """Minimal executor stub used to verify Claude action translation."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name, payload):
        self.calls.append((name, payload))
        return CUActionResult(name=name, extra=payload)


@pytest.fixture
def executor():
    """Create a desktop-style executor stub with real-pixel coordinates."""
    return FakeExecutor()


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
    async def test_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("click_at", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_double_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "double_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("double_click", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_right_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "right_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("right_click", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_middle_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "middle_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("middle_click", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_triple_click(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "triple_click", "coordinate": [100, 200]},
            executor,
        )
        assert result.success
        assert executor.calls == [("triple_click", {"x": 100, "y": 200})]

    @pytest.mark.asyncio
    async def test_type(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "type", "text": "hello"},
            executor,
        )
        assert result.success
        assert executor.calls == [("type_at_cursor", {"text": "hello", "press_enter": False})]

    @pytest.mark.asyncio
    async def test_key(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "key", "key": "Return"},
            executor,
        )
        assert result.success
        assert executor.calls == [("key_combination", {"keys": "Enter"})]

    @pytest.mark.asyncio
    async def test_scroll(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "scroll", "coordinate": [500, 500], "direction": "down", "amount": 3},
            executor,
        )
        assert result.success
        assert executor.calls == [("scroll_at", {"x": 500, "y": 500, "direction": "down", "magnitude": 600})]

    @pytest.mark.asyncio
    async def test_mouse_move(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "mouse_move", "coordinate": [300, 400]},
            executor,
        )
        assert result.success
        assert executor.calls == [("hover_at", {"x": 300, "y": 400})]

    @pytest.mark.asyncio
    async def test_left_mouse_down(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "left_mouse_down"},
            executor,
        )
        assert result.success
        assert executor.calls == [("left_mouse_down", {})]

    @pytest.mark.asyncio
    async def test_left_mouse_up(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "left_mouse_up"},
            executor,
        )
        assert result.success
        assert executor.calls == [("left_mouse_up", {})]

    @pytest.mark.asyncio
    async def test_hold_key(self, client, executor):
        result = await client._execute_claude_action(
            {"action": "hold_key", "key": "Shift", "duration": 0.1},
            executor,
        )
        assert result.success
        assert executor.calls == [("hold_key", {"key": "Shift", "duration": 0.1})]

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
    async def test_coordinate_upscaling(self, client, executor):
        """When scaling is active, coordinates should be upscaled."""
        scale = 0.8
        result = await client._execute_claude_action(
            {"action": "click", "coordinate": [80, 160]},
            executor,
            scale_factor=scale,
        )
        assert result.success
        assert executor.calls == [("click_at", {"x": 100, "y": 200})]


class TestClaudeToolConfig:
    """Test ClaudeCUClient tool configuration."""

    def test_tool_version_auto_detect_sonnet(self):
        with patch("anthropic.Anthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-sonnet-4-6")
        assert client._tool_version == "computer_20251124"
        assert client._beta_flag == "computer-use-2025-11-24"

    def test_tool_version_auto_detect_opus(self):
        with patch("anthropic.Anthropic"):
            client = ClaudeCUClient(api_key="test", model="claude-opus-4-7")
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
