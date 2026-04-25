"""Tests for the Computer Use Engine facade and configuration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.engine import (
    ComputerUseEngine,
    CUActionResult,
    Environment,
    OpenAICUClient,
    Provider,
    RunCompleted,
    _lookup_claude_cu_config,
    _CONTEXT_PRUNE_KEEP_RECENT,
    _build_openai_computer_call_output,
    _sanitize_openai_response_item_for_replay,
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

    def test_openai_provider_creates_openai_client(self):
        with patch("openai.OpenAI"):
            engine = ComputerUseEngine(
                provider=Provider.OPENAI,
                api_key="test-key",
                model="gpt-5.4",
            )
        assert engine.provider == Provider.OPENAI

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

    def test_default_openai_model(self):
        with patch("openai.OpenAI"):
            engine = ComputerUseEngine(
                provider=Provider.OPENAI,
                api_key="test-key",
            )
        assert engine._client._model == "gpt-5.4"

    def test_browser_env_is_rejected(self):
        with patch("anthropic.Anthropic"):
            engine = ComputerUseEngine(
                provider=Provider.CLAUDE,
                api_key="test-key",
                environment=Environment.BROWSER,
            )
        with pytest.raises(ValueError, match="Browser mode is no longer supported"):
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

    def test_opus_47_returns_config(self):
        tv, bf = _lookup_claude_cu_config("claude-opus-4-7")
        assert tv == "computer_20251124"
        assert bf == "computer-use-2025-11-24"

    def test_removed_opus_46_returns_none(self):
        tv, bf = _lookup_claude_cu_config("claude-opus-4-6")
        assert tv is None and bf is None

    def test_unknown_model_returns_none(self):
        tv, bf = _lookup_claude_cu_config("unknown-model")
        assert tv is None and bf is None


class TestContextPruneConstant:
    """Verify pruning constant matches reference implementations."""

    def test_prune_keep_recent_is_3(self):
        """Should match Google reference MAX_RECENT_TURN_WITH_SCREENSHOTS and
        Anthropic reference only_n_most_recent_images defaults."""
        assert _CONTEXT_PRUNE_KEEP_RECENT == 3


class TestOpenAIHelpers:
    """Verify OpenAI computer-call follow-up payloads."""

    def test_build_openai_computer_call_output(self):
        payload = _build_openai_computer_call_output(
            "call_123",
            "ZmFrZS1pbWFnZQ==",
            acknowledged_safety_checks=[{"id": "safety_1", "message": "approved"}],
        )
        assert payload["type"] == "computer_call_output"
        assert payload["call_id"] == "call_123"
        assert payload["output"]["type"] == "computer_screenshot"
        assert payload["output"]["detail"] == "original"
        assert payload["acknowledged_safety_checks"][0]["id"] == "safety_1"

    def test_sanitize_openai_replay_computer_call_strips_output_only_safety_fields(self):
        sanitized = _sanitize_openai_response_item_for_replay(
            SimpleNamespace(
                type="computer_call",
                id="comp_1",
                call_id="call_123",
                status="completed",
                pending_safety_checks=[
                    SimpleNamespace(id="safe_1", code="confirm", message="Confirm action"),
                ],
                actions=[
                    SimpleNamespace(type="click", x=44, y=55),
                ],
            )
        )

        assert sanitized == {
            "type": "computer_call",
            "call_id": "call_123",
            "actions": [{"type": "click", "x": 44, "y": 55}],
        }

    def test_sanitize_openai_replay_message_preserves_phase_and_strips_annotations(self):
        sanitized = _sanitize_openai_response_item_for_replay(
            SimpleNamespace(
                type="message",
                role="assistant",
                phase="commentary",
                status="in_progress",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text="Working on it",
                        annotations=[{"type": "file_citation"}],
                        logprobs=[{"token": "Working"}],
                    )
                ],
            )
        )

        assert sanitized == {
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Working on it"}],
        }


class TestOpenAIRuntimePath:
    """Verify the OpenAI runtime loop sends the expected follow-up payloads."""

    def test_run_loop_replays_sanitized_context_and_sends_computer_call_output(self):
        screenshot_bytes = b"a" * 128

        class FakeExecutor:
            def __init__(self):
                self.calls: list[tuple[str, dict]] = []

            async def capture_screenshot(self):
                return screenshot_bytes

            async def execute(self, action, payload):
                self.calls.append((action, payload))
                return CUActionResult(name=action, extra=payload)

        first_response = SimpleNamespace(
            id="resp_1",
            error=None,
            output_text="",
            output=[
                SimpleNamespace(
                    type="computer_call",
                    call_id="call_123",
                    pending_safety_checks=[
                        SimpleNamespace(id="safe_1", code="confirm", message="Confirm action")
                    ],
                    actions=[
                        SimpleNamespace(type="click", x=44, y=55),
                        SimpleNamespace(type="keypress", keys=["CTRL", "L"]),
                    ],
                )
            ],
        )
        second_response = SimpleNamespace(
            id="resp_2",
            error=None,
            output_text="Task complete",
            output=[],
        )

        with patch("openai.AsyncOpenAI") as mock_openai:
            responses_create = AsyncMock(side_effect=[first_response, second_response])
            mock_openai.return_value.responses.create = responses_create
            client = OpenAICUClient(api_key="test-key", model="gpt-5.4")

        executor = FakeExecutor()
        final_text = asyncio.run(
            client.run_loop(
                "Open the page and stop",
                executor,
                on_safety=lambda _: True,
            )
        )

        assert final_text == "Task complete"
        assert executor.calls == [
            ("click_at", {"x": 44, "y": 55}),
            ("key_combination", {"keys": "Control+L"}),
        ]
        assert responses_create.call_count == 2

        first_request = responses_create.call_args_list[0].kwargs
        assert first_request["model"] == "gpt-5.4"
        assert first_request["tools"] == [{"type": "computer"}]
        assert first_request["include"] == ["reasoning.encrypted_content"]
        assert first_request["input"][0]["role"] == "user"
        assert first_request["input"][0]["content"][0] == {"type": "input_text", "text": "Open the page and stop"}
        assert first_request["input"][0]["content"][1]["type"] == "input_image"
        assert first_request["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
        assert first_request["store"] is False

        second_request = responses_create.call_args_list[1].kwargs
        assert "previous_response_id" not in second_request
        assert second_request["include"] == ["reasoning.encrypted_content"]
        assert second_request["store"] is False
        assert len(second_request["input"]) == 2
        replayed_call = second_request["input"][0]
        assert replayed_call == {
            "type": "computer_call",
            "call_id": "call_123",
            "actions": [
                {"type": "click", "x": 44, "y": 55},
                {"type": "keypress", "keys": ["CTRL", "L"]},
            ],
        }
        tool_output = second_request["input"][1]
        assert tool_output["type"] == "computer_call_output"
        assert tool_output["call_id"] == "call_123"
        assert tool_output["output"]["type"] == "computer_screenshot"
        assert tool_output["output"]["detail"] == "original"
        assert tool_output["output"]["image_url"].startswith("data:image/png;base64,")
        assert tool_output["acknowledged_safety_checks"] == [{
            "id": "safe_1",
            "code": "confirm",
            "message": "Confirm action",
        }]


class TestIterTurnsDispatch:
    """Provider-specific native iterators should be used when available."""

    def test_gemini_provider_uses_native_iter_turns(self):
        async def _go():
            with patch("google.genai.Client"):
                engine = ComputerUseEngine(
                    provider=Provider.GEMINI,
                    api_key="test-key",
                    model="gemini-3.1-pro-preview",
                )

            async def fake_iter_turns(*args, **kwargs):
                yield RunCompleted(final_text="done")

            engine._client.iter_turns = fake_iter_turns  # type: ignore[method-assign]
            engine._client.run_loop = AsyncMock(side_effect=AssertionError("run_loop should not be used"))

            events = []
            async for event in engine.iter_turns("noop"):
                events.append(event)

            assert len(events) == 1
            assert isinstance(events[0], RunCompleted)
            assert events[0].final_text == "done"

        asyncio.run(_go())


class TestOpenAIReasoningEffort:
    """Regression guards for the April 2026 OpenAI reasoning-effort enum.

    The canonical values are ``{minimal, low, medium, high}``. The CU
    floor is ``high``. Legacy aliases (``none``, ``xhigh``) are mapped
    on input so the live API never sees a value it would 400 on.
    """

    def test_default_effort_is_high(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(api_key="test-key", model="gpt-5.4")
        assert client._reasoning_effort == "high", (
            "CU default must be 'high' — the April 2026 OpenAI CU guide "
            "lists high as the floor for agentic work."
        )

    def test_legacy_none_maps_to_minimal(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(
                api_key="test-key", model="gpt-5.4", reasoning_effort="none",
            )
        assert client._reasoning_effort == "minimal", (
            "Legacy 'none' must be mapped to canonical 'minimal' — "
            "the live OpenAI API rejects 'none' with HTTP 400."
        )

    def test_legacy_xhigh_maps_to_high(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(
                api_key="test-key", model="gpt-5.4", reasoning_effort="xhigh",
            )
        assert client._reasoning_effort == "high"

    def test_minimal_is_accepted(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(
                api_key="test-key", model="gpt-5.4", reasoning_effort="minimal",
            )
        assert client._reasoning_effort == "minimal"

    def test_unknown_coerces_to_high(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(
                api_key="test-key", model="gpt-5.4", reasoning_effort="garbage",
            )
        assert client._reasoning_effort == "high", (
            "Unknown values must coerce to the CU floor, not silently "
            "demote to 'low' like the pre-April-2026 behaviour did."
        )
