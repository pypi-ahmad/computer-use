"""Tests for system prompt generation and provider separation."""

from __future__ import annotations

from backend.agent.prompts import (
    get_system_prompt,
    validate_prompt_actions,
)


class TestPromptSeparation:
    """Verify Gemini and Claude get distinct system prompts."""

    def test_gemini_prompt_mentions_normalized_coords(self):
        prompt = get_system_prompt("computer_use", "browser", provider="google")
        assert "normalized" in prompt.lower() or "0-999" in prompt

    def test_claude_prompt_mentions_pixel_coords(self):
        prompt = get_system_prompt("computer_use", "browser", provider="anthropic")
        assert "pixel" in prompt.lower()

    def test_different_prompts_for_providers(self):
        gemini = get_system_prompt("computer_use", "browser", provider="google")
        claude = get_system_prompt("computer_use", "browser", provider="anthropic")
        assert gemini != claude

    def test_openai_prompt_mentions_computer_tool(self):
        prompt = get_system_prompt("computer_use", "browser", provider="openai")
        assert "computer tool" in prompt.lower()
        assert "pixel" in prompt.lower()

    def test_viewport_dimensions_injected(self):
        prompt = get_system_prompt("computer_use", "browser", provider="google")
        # Should contain actual numbers, not placeholders
        assert "{viewport_width}" not in prompt
        assert "{viewport_height}" not in prompt

    def test_claude_prompt_no_gemini_actions(self):
        """Claude prompt should not mention Gemini-specific action names."""
        prompt = get_system_prompt("computer_use", "browser", provider="anthropic")
        gemini_actions = ["click_at", "type_text_at", "scroll_document", "hover_at"]
        for action in gemini_actions:
            assert action not in prompt, f"Claude prompt should not mention Gemini action: {action}"

    def test_gemini_prompt_has_action_instructions(self):
        prompt = get_system_prompt("computer_use", "browser", provider="google")
        assert "click" in prompt.lower()
        assert "scroll" in prompt.lower()

    def test_fallback_for_unknown_engine(self):
        """Unknown engine should still return a prompt (with warning)."""
        prompt = get_system_prompt("unknown_engine", "browser", provider="google")
        assert len(prompt) > 100  # Should return a valid prompt


class TestPromptValidation:
    """Test prompt/schema drift detection."""

    def test_validate_prompt_actions_returns_list(self):
        result = validate_prompt_actions()
        assert isinstance(result, list)
