"""Opus 4.7-specific follow-ups: zoom action handler, opt-in prompt
caching, and CU-prompt audit.

Covers:
  * ``zoom`` action handler crops the screenshot to the model's region
    and rejects inverted rectangles.
  * Claude adapter adds ``cache_control: ephemeral`` to the
    ``computer_20251124`` tool definition only when
    ``CUA_CLAUDE_CACHING=1`` is set.
  * The CU system prompt for Opus 4.7 has self-verification scaffolding
    stripped; Sonnet 4.6 keeps the scaffolded prompt as a negative
    control.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pytest

from backend.engine import CUActionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes(width: int, height: int) -> bytes:
    """Build a real PNG whose header advertises width x height."""
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# zoom action handler
# ---------------------------------------------------------------------------


class TestZoomActionHandler:
    @pytest.mark.asyncio
    async def test_zoom_action_crops_region(self):
        """Claude adapter's zoom dispatch must call the executor with the
        validated region and surface the cropped PNG back to the caller."""
        pytest.importorskip("PIL")
        from backend.engine import ClaudeCUClient

        captured_args: dict = {}

        class FakeExecutor:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                captured_args["name"] = name
                captured_args["args"] = args
                # Simulate agent_service: return a PNG whose dimensions
                # match (x2-x1) x (y2-y1).
                region = args["region"]
                w = region[2] - region[0]
                h = region[3] - region[1]
                return CUActionResult(
                    name="zoom",
                    extra={"image_bytes": _png_bytes(w, h)},
                )

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        result = await client._execute_claude_action(
            {"action": "zoom", "region": [100, 100, 300, 200]},
            FakeExecutor(),
        )

        assert result.success is True
        assert result.name == "zoom"
        assert captured_args["name"] == "zoom"
        assert captured_args["args"]["region"] == [100, 100, 300, 200]

        # The returned PNG must match the requested region dimensions
        # (200 x 100), NOT the full display (1440 x 900).
        from PIL import Image
        img = Image.open(BytesIO(result.extra["image_bytes"]))
        assert img.size == (200, 100)

    @pytest.mark.asyncio
    async def test_zoom_action_rejects_inverted_region(self):
        """An inverted region must fail-fast without calling the
        executor — no crash, no full-screen fallback."""
        from backend.engine import ClaudeCUClient

        executor_called = False

        class FakeExecutor:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                nonlocal executor_called
                executor_called = True
                return CUActionResult(name=name)

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        result = await client._execute_claude_action(
            {"action": "zoom", "region": [100, 100, 50, 50]},
            FakeExecutor(),
        )

        assert result.success is False
        assert result.error is not None
        assert "inverted" in result.error or "empty" in result.error
        assert executor_called is False, (
            "executor must not be called for an invalid region"
        )

    @pytest.mark.asyncio
    async def test_zoom_action_rejects_wrong_shape(self):
        from backend.engine import ClaudeCUClient

        class FakeExecutor:
            screen_width = 1440
            screen_height = 900

            async def execute(self, name, args):
                raise AssertionError("executor must not be called")

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        # Missing region
        result = await client._execute_claude_action(
            {"action": "zoom"}, FakeExecutor(),
        )
        assert result.success is False
        # Wrong length
        result = await client._execute_claude_action(
            {"action": "zoom", "region": [0, 0, 10]}, FakeExecutor(),
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# Prompt caching env flag
# ---------------------------------------------------------------------------


class TestClaudeCachingEnvFlag:
    def test_claude_caching_env_flag_adds_cache_control(self, monkeypatch):
        from backend.engine import ClaudeCUClient

        monkeypatch.setenv("CUA_CLAUDE_CACHING", "1")
        # Reset the one-shot log flag so the test is order-independent.
        ClaudeCUClient._caching_logged = False

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        tools = client._build_tools(1440, 900)
        assert len(tools) == 1
        assert tools[0]["type"] == "computer_20251124"
        assert tools[0].get("cache_control") == {"type": "ephemeral"}

    def test_claude_caching_disabled_by_default(self, monkeypatch):
        from backend.engine import ClaudeCUClient

        monkeypatch.delenv("CUA_CLAUDE_CACHING", raising=False)

        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")

        tools = client._build_tools(1440, 900)
        assert len(tools) == 1
        assert "cache_control" not in tools[0]

    def test_claude_caching_off_for_unknown_env_value(self, monkeypatch):
        """Any value other than exactly '1' keeps caching off."""
        from backend.engine import ClaudeCUClient

        for bad in ("0", "true", "yes", "", "on"):
            monkeypatch.setenv("CUA_CLAUDE_CACHING", bad)
            with patch("anthropic.AsyncAnthropic"):
                client = ClaudeCUClient(api_key="k", model="claude-opus-4-7")
            tools = client._build_tools(1440, 900)
            assert "cache_control" not in tools[0], (
                f"cache_control unexpectedly set for CUA_CLAUDE_CACHING={bad!r}"
            )


# ---------------------------------------------------------------------------
# Opus 4.7 prompt audit
# ---------------------------------------------------------------------------


# Scaffolding phrases that Opus 4.7's more-literal interpreter reads as
# explicit instructions and that adaptive thinking already subsumes.
_SCAFFOLDING_PHRASES = (
    "step by step",
    "double-check",
    "verify before returning",
)


class TestPromptAudit:
    def test_opus_47_prompts_stripped_of_scaffolding(self):
        from backend.agent.prompts import get_system_prompt

        prompt = get_system_prompt(
            "computer_use", "desktop",
            provider="anthropic", model="claude-opus-4-7",
        )
        lowered = prompt.lower()
        for phrase in _SCAFFOLDING_PHRASES:
            assert phrase not in lowered, (
                f"Opus 4.7 prompt unexpectedly contains {phrase!r}"
            )

    def test_sonnet_46_prompt_keeps_scaffolding(self):
        """Negative control: non-4.7 Claude models still benefit from
        the scaffolding and must retain it."""
        from backend.agent.prompts import get_system_prompt

        prompt = get_system_prompt(
            "computer_use", "desktop",
            provider="anthropic", model="claude-sonnet-4-6",
        )
        lowered = prompt.lower()
        scaffolding_present = any(p in lowered for p in _SCAFFOLDING_PHRASES)
        assert scaffolding_present, (
            "Sonnet 4.6 prompt should retain 4.6-era scaffolding — "
            f"none of {_SCAFFOLDING_PHRASES} found"
        )

    def test_opus_46_gets_scaffolded_prompt(self):
        """Opus 4.6 (computer_20251124 tool but older reasoning) still
        benefits from scaffolding — only 4.7 is more literal."""
        from backend.agent.prompts import get_system_prompt

        prompt = get_system_prompt(
            "computer_use", "desktop",
            provider="anthropic", model="claude-opus-4-6",
        )
        assert any(p in prompt.lower() for p in _SCAFFOLDING_PHRASES)

    def test_anthropic_without_model_defaults_to_scaffolded(self):
        """Callers that don't pass a model id must get the conservative
        scaffolded prompt."""
        from backend.agent.prompts import get_system_prompt

        prompt = get_system_prompt(
            "computer_use", "desktop", provider="anthropic",
        )
        assert any(p in prompt.lower() for p in _SCAFFOLDING_PHRASES)
