"""Tests for context pruning logic (both Gemini and Claude)."""

from __future__ import annotations

import pytest

from backend.engine import _prune_claude_context


# ── Claude context pruning ────────────────────────────────────────────────────

class TestClaudeContextPruning:
    """Verify _prune_claude_context replaces old screenshots with placeholders."""

    @staticmethod
    def _make_messages(n_tool_results: int) -> list[dict]:
        """Build a realistic Claude message list with n tool_result pairs."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Do something"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "INITIAL_SS"}},
                ],
            }
        ]
        for i in range(n_tool_results):
            # Assistant turn with tool_use
            messages.append({
                "role": "assistant",
                "content": [{"type": "tool_use", "id": f"tu_{i}", "input": {"action": "click"}}],
            })
            # User turn with tool_result
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"tu_{i}",
                        "content": [
                            {"type": "text", "text": "ok"},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": f"SS_{i}"}},
                        ],
                    }
                ],
            })
        return messages

    def test_no_pruning_when_short(self):
        """Messages shorter than keep_recent should not be pruned."""
        msgs = self._make_messages(2)
        original_len = len(msgs)
        _prune_claude_context(msgs, 10)
        assert len(msgs) == original_len
        # Images should still be intact
        assert msgs[0]["content"][1]["type"] == "image"

    def test_pruning_replaces_old_screenshots(self):
        """Old tool_result images should become [screenshot omitted]."""
        msgs = self._make_messages(10)
        _prune_claude_context(msgs, 3)

        # First message (goal + initial screenshot) should be untouched
        assert msgs[0]["content"][1]["type"] == "image"

        # Old messages (index 1 .. len-3) should have screenshots replaced
        old_msgs = msgs[1:-3]
        for msg in old_msgs:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    inner = part.get("content", [])
                    for item in inner:
                        if isinstance(item, dict) and item.get("type") == "image":
                            # Should NOT have any remaining images in old turns
                            pytest.fail(f"Found unpruned image in old turn: {msg}")

    def test_recent_messages_preserved(self):
        """The most recent keep_recent messages should retain screenshots."""
        msgs = self._make_messages(10)
        _prune_claude_context(msgs, 3)

        # Last 3 messages should still have image data
        recent = msgs[-3:]
        has_image = False
        for msg in recent:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        for inner in part.get("content", []):
                            if isinstance(inner, dict) and inner.get("type") == "image":
                                has_image = True
        assert has_image, "Recent messages should still have screenshot images"

    def test_initial_screenshot_never_pruned(self):
        """The first user message always keeps its screenshot."""
        msgs = self._make_messages(20)
        _prune_claude_context(msgs, 3)
        first_content = msgs[0]["content"]
        assert first_content[1]["type"] == "image"
        assert first_content[1]["source"]["data"] == "INITIAL_SS"
