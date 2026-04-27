# === merged from tests/test_prompts.py ===
"""Tests for system prompt generation and provider separation."""

from __future__ import annotations

from backend.agent.executor_prompt import build_executor_system_prompt
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

    def test_executor_prompt_strips_planning_guidance_and_appends_active_subgoal(self):
        prompt = build_executor_system_prompt(
            provider="openai",
            model="gpt-5.5",
            active_plan={"summary": "Open settings and enable Wi-Fi.", "active_subgoal": "Open Settings"},
            subgoals=[
                {"title": "Open Settings", "status": "active"},
                {"title": "Enable Wi-Fi", "status": "pending"},
            ],
            completion_criteria=["Settings is open", "Wi-Fi toggle is on"],
        )
        lowered = prompt.lower()
        assert "planning alone" not in lowered
        assert "active subgoal: open settings" in lowered
        assert "do not re-plan" in lowered

    def test_executor_prompt_includes_memory_briefs(self):
        prompt = build_executor_system_prompt(
            provider="openai",
            model="gpt-5.5",
            active_plan={"summary": "Open billing.", "active_subgoal": "Open Billing"},
            subgoals=[{"title": "Open Billing", "status": "active"}],
            completion_criteria=["Billing is open"],
            evidence=[{"kind": "evidence_summary", "summary": "Check the billing dashboard first."}],
            memory_context={
                "prior_workflows": [{"workflow_summary": "Use the Billing menu before opening invoices."}],
                "ui_patterns": [{"pattern": "Invoices live under the top Billing tab."}],
                "operator_preferences": [{"preferred_model": "gpt-5.5", "risk_level": "low", "notes": ["Check dashboards before login"]}],
            },
        )
        lowered = prompt.lower()
        assert "working memory" in lowered
        assert "long-term memory" in lowered
        assert "billing menu" in lowered

    def test_fallback_for_unknown_engine(self):
        """Unknown engine should still return a prompt (with warning)."""
        prompt = get_system_prompt("unknown_engine", "browser", provider="google")
        assert len(prompt) > 100  # Should return a valid prompt


class TestPromptValidation:
    """Test prompt/schema drift detection."""

    def test_validate_prompt_actions_returns_list(self):
        result = validate_prompt_actions()
        assert isinstance(result, list)

