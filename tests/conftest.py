"""Shared fixtures for computer-use tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_page():
    """Create a mock Playwright page with common methods."""
    page = AsyncMock()
    page.mouse = AsyncMock()
    page.mouse.click = AsyncMock()
    page.mouse.dblclick = AsyncMock()
    page.mouse.move = AsyncMock()
    page.mouse.down = AsyncMock()
    page.mouse.up = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.keyboard = AsyncMock()
    page.keyboard.type = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.down = AsyncMock()
    page.keyboard.up = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG" + b"\x00" * 100)
    page.url = "https://example.com"
    page.goto = AsyncMock()
    page.go_back = AsyncMock()
    page.go_forward = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    return page
