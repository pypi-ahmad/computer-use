"""Tests for the Computer Use Engine facade and configuration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.engine import (
    ComputerUseEngine,
    Environment,
    Provider,
    _lookup_claude_cu_config,
    _CONTEXT_PRUNE_KEEP_RECENT,
)


class TestComputerUseEngine:
    """Test the unified ComputerUseEngine facade."""

    def test_gemini_provider_creates_gemini_client(self):
        with patch("google.genai.Client"):
            engine = ComputerUseEngine(
                provider=Provider.GEMINI,
                api_key="test-key",
                model="gemini-3-flash-preview",
            )
        assert engine.provider == Provider.GEMINI

    def test_claude_provider_creates_claude_client(self):
        with patch("anthropic.Anthropic"):
            engine = ComputerUseEngine(
                provider=Provider.CLAUDE,
                api_key="test-key",
                model="claude-sonnet-4-6",
            )
        assert engine.provider == Provider.CLAUDE

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported provider"):
            ComputerUseEngine(provider="invalid", api_key="test")

    def test_default_gemini_model(self):
        with patch("google.genai.Client"):
            engine = ComputerUseEngine(
                provider=Provider.GEMINI,
                api_key="test-key",
            )
        assert engine._client._model == "gemini-3-flash-preview"

    def test_default_claude_model(self):
        with patch("anthropic.Anthropic"):
            engine = ComputerUseEngine(
                provider=Provider.CLAUDE,
                api_key="test-key",
            )
        assert engine._client._model == "claude-sonnet-4-6"

    def test_browser_env_requires_page(self):
        with patch("anthropic.Anthropic"):
            engine = ComputerUseEngine(
                provider=Provider.CLAUDE,
                api_key="test-key",
                environment=Environment.BROWSER,
            )
        with pytest.raises(ValueError, match="requires a Playwright page"):
            engine._build_executor(page=None)

    def test_desktop_env_creates_desktop_executor(self):
        with patch("anthropic.Anthropic"):
            engine = ComputerUseEngine(
                provider=Provider.CLAUDE,
                api_key="test-key",
                environment=Environment.DESKTOP,
            )
        executor = engine._build_executor(page=None)
        from backend.engine import DesktopExecutor
        assert isinstance(executor, DesktopExecutor)


class TestLookupClaudeCUConfig:
    """Test _lookup_claude_cu_config reads from allowed_models.json."""

    def test_sonnet_46_returns_config(self):
        tv, bf = _lookup_claude_cu_config("claude-sonnet-4-6")
        assert tv == "computer_20251124"
        assert bf == "computer-use-2025-11-24"

    def test_opus_46_returns_config(self):
        tv, bf = _lookup_claude_cu_config("claude-opus-4-6")
        assert tv == "computer_20251124"
        assert bf == "computer-use-2025-11-24"

    def test_unknown_model_returns_none(self):
        tv, bf = _lookup_claude_cu_config("unknown-model")
        assert tv is None and bf is None


class TestContextPruneConstant:
    """Verify pruning constant matches reference implementations."""

    def test_prune_keep_recent_is_3(self):
        """Should match Google reference MAX_RECENT_TURN_WITH_SCREENSHOTS and
        Anthropic reference only_n_most_recent_images defaults."""
        assert _CONTEXT_PRUNE_KEEP_RECENT == 3
