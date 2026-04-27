# === merged from tests/test_computer_use_engine.py ===
"""Tests for the Computer Use Engine facade and configuration."""

from __future__ import annotations

import asyncio
import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from backend.engine import (
    ComputerUseEngine,
    CUActionResult,
    DesktopExecutor,
    Environment,
    OpenAICUClient,
    Provider,
    RunCompleted,
    _lookup_claude_cu_config,
    _CONTEXT_PRUNE_KEEP_RECENT,
    _build_openai_computer_call_output,
    _sanitize_openai_response_item_for_replay,
)


def _png_bytes(size: tuple[int, int] = (32, 32)) -> bytes:
    image = Image.new("RGB", size, color=(255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class TestComputerUseEngine:
    """Test the unified ComputerUseEngine facade."""

    def test_claude_passes_allowed_callers_to_client(self):
        with patch("backend.engine.ClaudeCUClient") as mock_client:
            ComputerUseEngine(
                provider=Provider.CLAUDE,
                api_key="test-key",
                model="claude-sonnet-4-6",
                use_builtin_search=True,
                allowed_callers=["direct"],
            )

        assert mock_client.call_args.kwargs["allowed_callers"] == ["direct"]

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
        assert engine._client._model == "gpt-5.5"

    def test_unified_surface_always_uses_desktop_executor(self):
        """Desktop and Browser are unified \u2014 every session uses the\n        xdotool DesktopExecutor regardless of the legacy ``environment``\n        argument; the model decides whether to drive desktop apps or\n        Chromium itself."""
        from backend.engine import DesktopExecutor

        with patch("anthropic.AsyncAnthropic"):
            engine_browser = ComputerUseEngine(
                provider=Provider.CLAUDE,
                api_key="test-key",
                environment=Environment.BROWSER,
            )
        with patch("anthropic.AsyncAnthropic"):
            engine_desktop = ComputerUseEngine(
                provider=Provider.CLAUDE,
                api_key="test-key",
                environment=Environment.DESKTOP,
            )

        assert isinstance(engine_browser._build_executor(page=None), DesktopExecutor)
        assert isinstance(engine_desktop._build_executor(page=None), DesktopExecutor)

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


class TestDesktopExecutorIdempotency:
    @pytest.mark.asyncio
    async def test_action_id_uses_deterministic_substeps(self, monkeypatch):
        captured_payloads: list[dict[str, object]] = []

        class _FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"success": True, "message": "ok"}

        class _FakeClient:
            async def post(self, _url, *, json, headers):
                captured_payloads.append(dict(json))
                assert isinstance(headers, dict)
                return _FakeResponse()

        executor = DesktopExecutor(screen_width=1440, screen_height=900, normalize_coords=False)
        monkeypatch.setattr("backend.engine._app_config.ui_settle_delay", 0.0)
        monkeypatch.setattr(executor, "_get_client", AsyncMock(return_value=_FakeClient()))

        result = await executor.execute("triple_click", {"x": 44, "y": 55, "action_id": "replay-123"})

        assert result.success is True
        assert [payload["action_id"] for payload in captured_payloads] == [
            "replay-123:0",
            "replay-123:1",
        ]
        assert [payload["action"] for payload in captured_payloads] == [
            "double_click",
            "click",
        ]


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

    def test_prepare_openai_screenshot_downscales_above_doc_threshold(self):
        from backend.engine.openai import (
            _OPENAI_ORIGINAL_MAX_PIXELS,
            _prepare_openai_computer_screenshot,
        )

        screenshot_bytes = _png_bytes((5500, 2000))

        prepared_bytes, scale = _prepare_openai_computer_screenshot(screenshot_bytes)

        assert scale == pytest.approx((_OPENAI_ORIGINAL_MAX_PIXELS / (5500 * 2000)) ** 0.5)
        assert scale < 1.0
        with Image.open(io.BytesIO(prepared_bytes)) as downscaled:
            assert downscaled.size == (5307, 1930)

    def test_run_loop_downscales_and_reverse_remaps_click_coordinates(self):
        screenshot_bytes = _png_bytes((5120, 5120))

        class FakeExecutor:
            def __init__(self):
                self.calls: list[tuple[str, dict]] = []

            async def capture_screenshot(self):
                return screenshot_bytes

            async def execute(self, action, payload):
                self.calls.append((action, payload))
                return CUActionResult(name=action, extra=payload)

        first_response = SimpleNamespace(
            id="resp_scaled_1",
            error=None,
            output_text="",
            output=[
                SimpleNamespace(
                    type="computer_call",
                    call_id="call_scaled",
                    actions=[
                        SimpleNamespace(type="click", x=3125, y=3125),
                    ],
                )
            ],
        )
        second_response = SimpleNamespace(
            id="resp_scaled_2",
            error=None,
            output_text="done",
            output=[],
        )

        with patch("openai.AsyncOpenAI") as mock_openai:
            responses_create = AsyncMock(side_effect=[first_response, second_response])
            mock_openai.return_value.responses.create = responses_create
            client = OpenAICUClient(api_key="test-key", model="gpt-5.5")

        executor = FakeExecutor()
        final_text = asyncio.run(client.run_loop("Click the far corner", executor))

        assert final_text == "done"
        assert client._current_screenshot_scale == pytest.approx(0.625)
        assert executor.calls == [("click_at", {"x": 5000, "y": 5000})]

        first_request = responses_create.call_args_list[0].kwargs
        image_item = first_request["input"][0]["content"][1]
        encoded_png = image_item["image_url"].split(",", 1)[1]
        with Image.open(io.BytesIO(base64.standard_b64decode(encoded_png))) as downscaled:
            assert downscaled.size == (3200, 3200)

    @pytest.mark.parametrize("phase", ["commentary", "final_answer"])
    def test_run_loop_replays_assistant_message_phase_verbatim(self, phase):
        screenshot_bytes = _png_bytes()

        class FakeExecutor:
            async def capture_screenshot(self):
                return screenshot_bytes

            async def execute(self, action, payload):
                return CUActionResult(name=action, extra=payload)

        first_response = SimpleNamespace(
            id="resp_phase_1",
            error=None,
            output_text="",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    phase=phase,
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text=f"{phase} status",
                            annotations=[{"type": "url_citation", "url": "https://example.com"}],
                        )
                    ],
                ),
                SimpleNamespace(
                    type="computer_call",
                    call_id="call_phase",
                    actions=[SimpleNamespace(type="click", x=10, y=20)],
                ),
            ],
        )
        second_response = SimpleNamespace(
            id="resp_phase_2",
            error=None,
            output_text="done",
            output=[],
        )

        with patch("openai.AsyncOpenAI") as mock_openai:
            responses_create = AsyncMock(side_effect=[first_response, second_response])
            mock_openai.return_value.responses.create = responses_create
            client = OpenAICUClient(api_key="test-key", model="gpt-5.5")

        asyncio.run(client.run_loop("Work through the task", FakeExecutor()))

        second_request = responses_create.call_args_list[1].kwargs
        replayed_message = second_request["input"][0]
        assert replayed_message == {
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "content": [{"type": "output_text", "text": f"{phase} status"}],
        }

    def test_run_loop_appends_openai_sources_to_final_text(self):
        screenshot_bytes = b"a" * 128

        class FakeExecutor:
            async def capture_screenshot(self):
                return screenshot_bytes

            async def execute(self, action, payload):
                return CUActionResult(name=action, extra=payload)

        response = SimpleNamespace(
            id="resp_1",
            error=None,
            output_text="",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text="It is sunny today.",
                            annotations=[
                                {
                                    "type": "url_citation",
                                    "url": "https://weather.example.com",
                                    "title": "Weather Example",
                                }
                            ],
                        )
                    ],
                )
            ],
        )

        with patch("openai.AsyncOpenAI") as mock_openai:
            mock_openai.return_value.responses.create = AsyncMock(return_value=response)
            client = OpenAICUClient(api_key="test-key", model="gpt-5.4")

        final_text = asyncio.run(client.run_loop("weather", FakeExecutor(), turn_limit=1))
        assert "It is sunny today." in final_text
        assert "Sources:" in final_text
        assert "https://weather.example.com" in final_text

    def test_run_loop_search_only_turn_nudges_until_computer_action(self):
        screenshot_bytes = b"b" * 128

        class FakeExecutor:
            def __init__(self):
                self.calls: list[tuple[str, dict]] = []

            async def capture_screenshot(self):
                return screenshot_bytes

            async def execute(self, action, payload):
                self.calls.append((action, payload))
                return CUActionResult(name=action, extra=payload)

        first_response = SimpleNamespace(
            id="resp_search",
            error=None,
            output_text="I found instructions for creating folders.",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text="I found instructions for creating folders.",
                        )
                    ],
                )
            ],
        )
        second_response = SimpleNamespace(
            id="resp_act",
            error=None,
            output_text="",
            output=[
                SimpleNamespace(
                    type="computer_call",
                    call_id="call_after_search",
                    actions=[SimpleNamespace(type="double_click", x=120, y=220)],
                )
            ],
        )
        third_response = SimpleNamespace(
            id="resp_done",
            error=None,
            output_text="Done",
            output=[],
        )

        with patch("openai.AsyncOpenAI") as mock_openai:
            responses_create = AsyncMock(side_effect=[first_response, second_response, third_response])
            mock_openai.return_value.responses.create = responses_create
            client = OpenAICUClient(
                api_key="test-key",
                model="gpt-5.5",
                use_builtin_search=True,
            )

        executor = FakeExecutor()
        final_text = asyncio.run(client.run_loop("Learn how to do it, then create the folder", executor))

        assert final_text == "Done"
        assert executor.calls == [("double_click", {"x": 120, "y": 220})]
        assert responses_create.call_count == 3

        second_request = responses_create.call_args_list[1].kwargs
        follow_up = second_request["input"][-1]
        assert follow_up["role"] == "user"
        assert "not complete until you perform the requested action with the computer tool" in follow_up["content"][0]["text"]
        assert follow_up["content"][1]["type"] == "input_image"


class TestIterTurnsDispatch:
    """Provider-specific native iterators should be used when available."""

    def test_gemini_provider_uses_native_iter_turns(self):
        async def _go():
            with patch("google.genai.Client"):
                engine = ComputerUseEngine(
                    provider=Provider.GEMINI,
                    api_key="test-key",
                    model="gemini-3-flash-preview",
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


class TestSearchEnabledRequiresComputerAction:
    def test_claude_iter_turns_nudges_after_retrieval_only_turn(self):
        async def _go():
            screenshot_bytes = _png_bytes()

            class FakeExecutor:
                screen_width = 1440
                screen_height = 900

                def __init__(self):
                    self.calls: list[tuple[str, dict]] = []

                async def capture_screenshot(self):
                    return screenshot_bytes

                async def execute(self, action, payload):
                    self.calls.append((action, payload))
                    return CUActionResult(name=action, extra=payload)

                def get_current_url(self):
                    return None

            first_response = SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="I found instructions online.")],
            )
            second_response = SimpleNamespace(
                stop_reason="tool_use",
                content=[SimpleNamespace(type="tool_use", id="tu_1", input={"action": "double_click", "coordinate": [120, 220]})],
            )
            third_response = SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="Done")],
            )

            with patch("anthropic.AsyncAnthropic") as mock_client:
                create = AsyncMock(side_effect=[first_response, second_response, third_response])
                mock_client.return_value.messages.create = AsyncMock(
                    return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])
                )
                mock_client.return_value.beta.messages.create = create
                from backend.engine import ClaudeCUClient
                client = ClaudeCUClient(
                    api_key="k",
                    model="claude-sonnet-4-6",
                    use_builtin_search=True,
                )

            executor = FakeExecutor()
            events = []
            async for event in client.iter_turns("Learn first, then create the Projects folder", executor):
                events.append(event)

            assert executor.calls == [("double_click", {"x": 120, "y": 220})]
            assert isinstance(events[-1], RunCompleted)
            assert events[-1].final_text == "Done"

            second_request = create.call_args_list[1].kwargs
            assert any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and message["content"]
                and isinstance(message["content"][0], dict)
                and "not complete until you perform the requested action with the computer tool" in message["content"][0].get("text", "")
                for message in second_request["messages"]
            )

        asyncio.run(_go())

    def test_gemini_iter_turns_nudges_after_retrieval_only_turn(self):
        async def _go():
            screenshot_bytes = _png_bytes()

            class FakeExecutor:
                screen_width = 1440
                screen_height = 900

                def __init__(self):
                    self.calls: list[tuple[str, dict]] = []

                async def capture_screenshot(self):
                    return screenshot_bytes

                async def execute(self, action, payload):
                    self.calls.append((action, payload))
                    return CUActionResult(name=action, extra=payload)

                def get_current_url(self):
                    return None

            first_response = SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[SimpleNamespace(function_call=None, text="I found instructions online.")]
                        )
                    )
                ]
            )
            second_response = SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[SimpleNamespace(function_call=SimpleNamespace(name="double_click", args={"x": 120, "y": 220}), text=None)]
                        )
                    )
                ]
            )
            third_response = SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[SimpleNamespace(function_call=None, text="Done")]
                        )
                    )
                ]
            )

            with patch("google.genai.Client"), \
                 patch("backend.engine._get_gemini_builtin_search_sdk_error", return_value=None), \
                 patch("backend.engine.gemini._get_gemini_builtin_search_sdk_error", return_value=None):
                from backend.engine import GeminiCUClient
                client = GeminiCUClient(api_key="k", use_builtin_search=True)
                client._build_config = lambda: SimpleNamespace()
                client._client.aio.models.generate_content = AsyncMock(
                    side_effect=[first_response, second_response, third_response]
                )

                executor = FakeExecutor()
                events = []
                async for event in client.iter_turns("Learn first, then create the Projects folder", executor):
                    events.append(event)

                assert executor.calls == [("double_click", {"x": 120, "y": 220})]
                assert isinstance(events[-1], RunCompleted)
                assert events[-1].final_text == "Done"

                second_call = client._client.aio.models.generate_content.call_args_list[1].kwargs
                assert any(
                    hasattr(content, "parts")
                    and content.parts
                    and getattr(content.parts[0], "text", "")
                    and "not complete until you perform the requested action with the computer_use tool" in content.parts[0].text
                    for content in second_call["contents"]
                )

        asyncio.run(_go())


class TestOpenAIReasoningEffort:
    """Regression guards for the April 2026 OpenAI reasoning-effort enum.

    GPT-5.5 defaults to ``medium`` and accepts ``xhigh`` directly.
    ``none`` remains a legacy alias for ``minimal``.
    """

    def test_default_effort_is_medium(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(api_key="test-key", model="gpt-5.5")
        assert client._reasoning_effort == "medium"

    def test_gpt54_default_effort_maps_none_to_minimal(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(api_key="test-key", model="gpt-5.4")
        assert client._reasoning_effort == "minimal"

    def test_legacy_none_maps_to_minimal(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(
                api_key="test-key", model="gpt-5.5", reasoning_effort="none",
            )
        assert client._reasoning_effort == "minimal", (
            "Legacy 'none' must be mapped to canonical 'minimal' — "
            "the adapter keeps it only for wire compatibility."
        )

    def test_xhigh_is_accepted(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(
                api_key="test-key", model="gpt-5.5", reasoning_effort="xhigh",
            )
        assert client._reasoning_effort == "xhigh"

    def test_minimal_is_accepted(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(
                api_key="test-key", model="gpt-5.5", reasoning_effort="minimal",
            )
        assert client._reasoning_effort == "minimal"

    def test_unknown_coerces_to_medium(self):
        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(
                api_key="test-key", model="gpt-5.5", reasoning_effort="garbage",
            )
        assert client._reasoning_effort == "medium"

# === merged from tests/test_coordinate_scaling.py ===
"""Tests for coordinate scaling and denormalization."""


import math
import pytest

from backend.engine import (
    GEMINI_NORMALIZED_MAX,
    _CLAUDE_MAX_LONG_EDGE,
    _CLAUDE_MAX_PIXELS,
    denormalize_x,
    denormalize_y,
    get_claude_scale_factor,
    resize_screenshot_for_claude,
)


# ── Gemini denormalization ────────────────────────────────────────────────────

class TestDenormalize:
    """Gemini normalised coords → pixel conversion."""

    def test_zero_maps_to_zero(self):
        assert denormalize_x(0, 1440) == 0
        assert denormalize_y(0, 900) == 0

    def test_max_maps_to_screen_edge(self):
        # 999 / 1000 * 1440 → ~1438
        assert denormalize_x(999, 1440) > 1400
        assert denormalize_y(999, 900) > 890

    def test_mid_point(self):
        x = denormalize_x(500, 1440)
        y = denormalize_y(500, 900)
        assert 690 <= x <= 730  # ~ 720
        assert 440 <= y <= 460  # ~ 450

    def test_custom_screen_size(self):
        assert denormalize_x(500, 1920) == int(500 / GEMINI_NORMALIZED_MAX * 1920)
        assert denormalize_y(500, 1080) == int(500 / GEMINI_NORMALIZED_MAX * 1080)


# ── Claude screenshot scaling ─────────────────────────────────────────────────

class TestClaudeScaleFactor:
    """Scaling factor computation per Anthropic docs."""

    def test_small_screen_no_scaling(self):
        """Screens that fit within both thresholds → scale = 1.0."""
        assert get_claude_scale_factor(800, 600) == 1.0

    def test_1440x900_requires_scaling(self):
        """Default 1440×900 (1.296M pixels) exceeds pixel threshold."""
        scale = get_claude_scale_factor(1440, 900)
        assert scale < 1.0
        # Verify resulting pixels are within limits
        new_w, new_h = int(1440 * scale), int(900 * scale)
        assert new_w * new_h <= _CLAUDE_MAX_PIXELS * 1.01  # small rounding tolerance
        assert max(new_w, new_h) <= _CLAUDE_MAX_LONG_EDGE

    def test_4k_requires_scaling(self):
        """3840×2160 exceeds both edge and pixel limits."""
        scale = get_claude_scale_factor(3840, 2160)
        assert scale < 0.5

    def test_exactly_at_limits(self):
        """Screen exactly at max long edge and pixel limit."""
        w = _CLAUDE_MAX_LONG_EDGE
        h = _CLAUDE_MAX_PIXELS // _CLAUDE_MAX_LONG_EDGE
        scale = get_claude_scale_factor(w, h)
        assert scale <= 1.0

    def test_scale_formula(self):
        """Verify the scale factor matches the documented formula."""
        w, h = 1440, 900
        expected = min(
            1.0,
            _CLAUDE_MAX_LONG_EDGE / max(w, h),
            math.sqrt(_CLAUDE_MAX_PIXELS / (w * h)),
        )
        assert get_claude_scale_factor(w, h) == pytest.approx(expected)

    def test_modern_tool_version_uses_high_res_budget_even_without_model_match(self):
        scale = get_claude_scale_factor(
            1920,
            1200,
            "custom-claude-build",
            tool_version="computer_20251124",
        )
        assert scale == 1.0


class TestResizeScreenshot:
    """Screenshot resize via Pillow."""

    def _make_png(self, w: int, h: int) -> bytes:
        """Create a minimal PNG of given size using Pillow."""
        from PIL import Image
        import io
        img = Image.new("RGB", (w, h), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_no_resize_when_scale_gte_1(self):
        png = self._make_png(800, 600)
        result, rw, rh = resize_screenshot_for_claude(png, 1.0)
        assert rw == 800 and rh == 600

    def test_resize_scales_down(self):
        png = self._make_png(1440, 900)
        scale = get_claude_scale_factor(1440, 900)
        result, rw, rh = resize_screenshot_for_claude(png, scale)
        assert rw == int(1440 * scale)
        assert rh == int(900 * scale)
        assert len(result) > 0

    def test_resize_produces_valid_png(self):
        png = self._make_png(1440, 900)
        scale = get_claude_scale_factor(1440, 900)
        result, rw, rh = resize_screenshot_for_claude(png, scale)
        # PNG header: 8-byte signature
        assert result[:4] == b'\x89PNG'

# === merged from tests/test_context_pruning.py ===
"""Tests for context pruning logic (both Gemini and Claude)."""


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

# === merged from tests/test_adapters_april2026.py ===
"""Adapter-level tests for the April 2026 CU upgrade.

Covers:
  * Claude: tool-version branching (``computer_20251124`` vs
    ``computer_20250124``), adaptive thinking, 1:1 coordinates for
    Opus 4.7 / Sonnet 4.6, legacy scale-factor path for pre-4.5
    models.
    * OpenAI: ``gpt-5.5`` default with ``reasoning.effort == "medium"``,
        ZDR-safe replay (no ``previous_response_id``), and
    ``include=["reasoning.encrypted_content"]``.
  * Gemini: ``require_confirmation`` routing via the shared
    ``on_safety`` interrupt.

All provider SDKs are mocked; no network calls.
"""


from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.engine import (
    CUActionResult,
    SafetyDecision,
    _CLAUDE_HIGH_RES_MODELS,
    get_claude_scale_factor,
)


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


class TestClaudeToolVersioning:
    """Tool version + beta header must track model class."""

    @pytest.mark.parametrize("model", [
        "claude-opus-4-7", "claude-sonnet-4-6",
    ])
    def test_new_tool_version_models(self, model):
        from backend.engine import ClaudeCUClient
        with patch("anthropic.AsyncAnthropic"):
            c = ClaudeCUClient(api_key="k", model=model)
        assert c._tool_version == "computer_20251124"
        assert c._beta_flag == "computer-use-2025-11-24"

    def test_unregistered_model_raises_explicit_registry_error(self):
        from backend.engine import ClaudeCUClient
        with patch("anthropic.AsyncAnthropic"):
            with pytest.raises(ValueError, match="not in registry"):
                ClaudeCUClient(api_key="k", model="claude-haiku-5-0-future")


class TestClaudeCoordinateSpace:
    """``computer_20251124`` models are 1:1 at typical resolutions."""

    @pytest.mark.parametrize("model", list(_CLAUDE_HIGH_RES_MODELS))
    def test_no_downscale_at_1920x1200_for_new_tool_models(self, model):
        # 1920 long edge < 2576 long edge and 2,304,000 px < 3.75 MP.
        assert get_claude_scale_factor(1920, 1200, model) == 1.0

    def test_downscale_kicks_in_for_legacy_models(self):
        # Legacy path: 1920x1200 exceeds both 1568 long-edge and
        # 1.15 MP budgets; the pixel budget is the binding constraint.
        scale = get_claude_scale_factor(1920, 1200, "claude-3-5-sonnet-20241022")
        assert scale < 1.0
        # sqrt(1_150_000 / (1920*1200)) ≈ 0.7065
        assert scale == pytest.approx(0.7065, rel=1e-3)


class TestClaudeThinkingMode:
    """``computer_20251124`` rejects ``budget_tokens``; must send adaptive."""

    @pytest.mark.parametrize("model", [
        "claude-opus-4-7", "claude-sonnet-4-6",
    ])
    @pytest.mark.asyncio
    async def test_adaptive_thinking_for_new_tool_version(self, model):
        from backend.engine import ClaudeCUClient

        captured: dict = {}

        class FakeResponse:
            stop_reason = "end_turn"
            content: list = []

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return FakeResponse()

        class FakeMessages:
            create = staticmethod(fake_create)

        class FakeBeta:
            messages = FakeMessages()

        class FakeClient:
            beta = FakeBeta()

        class FakeExecutor:
            screen_width = 1280
            screen_height = 800

            async def capture_screenshot(self):
                # Minimal valid PNG IHDR: sig + IHDR for 1280x800.
                return (
                    b"\x89PNG\r\n\x1a\n"
                    + b"\x00" * 8
                    + (1280).to_bytes(4, "big")
                    + (800).to_bytes(4, "big")
                    + b"\x00" * 100
                )

            async def execute(self, name, args):
                return CUActionResult(name=name)

            def get_current_url(self):
                return ""

        with patch("anthropic.AsyncAnthropic") as AA:
            AA.return_value = FakeClient()
            client = ClaudeCUClient(api_key="k", model=model)

            # Drive one turn of the generator to capture the request.
            gen = client.iter_turns("noop", FakeExecutor(), turn_limit=1)
            async for _ in gen:
                pass

        assert captured.get("thinking") == {"type": "adaptive"}
        # Sampling params must not be sent for computer_20251124.
        assert "temperature" not in captured
        assert "top_p" not in captured
        assert "top_k" not in captured
        assert captured.get("betas") == ["computer-use-2025-11-24"]


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class TestOpenAIReasoningEffort:
    """GPT-5.5 defaults to ``medium`` and accepts ``xhigh``."""

    def test_default_reasoning_effort_is_medium(self):
        from backend.engine import OpenAICUClient
        with patch("openai.AsyncOpenAI"):
            c = OpenAICUClient(api_key="k", model="gpt-5.5")
        assert c._reasoning_effort == "medium"

    def test_gpt54_default_reasoning_effort_maps_none_to_minimal(self):
        from backend.engine import OpenAICUClient
        with patch("openai.AsyncOpenAI"):
            c = OpenAICUClient(api_key="k", model="gpt-5.4")
        assert c._reasoning_effort == "minimal"

    def test_invalid_effort_falls_back_to_medium(self):
        from backend.engine import OpenAICUClient
        with patch("openai.AsyncOpenAI"):
            c = OpenAICUClient(api_key="k", model="gpt-5.5", reasoning_effort="bogus")
        assert c._reasoning_effort == "medium"

    def test_xhigh_is_preserved(self):
        from backend.engine import OpenAICUClient
        with patch("openai.AsyncOpenAI"):
            c = OpenAICUClient(api_key="k", model="gpt-5.5", reasoning_effort="xhigh")
        assert c._reasoning_effort == "xhigh"

    def test_facade_default_passes_medium(self):
        from backend.engine import ComputerUseEngine, Provider
        with patch("openai.AsyncOpenAI"):
            eng = ComputerUseEngine(provider=Provider.OPENAI, api_key="k", model="gpt-5.5")
        assert eng._client._reasoning_effort == "medium"

    def test_facade_gpt54_default_maps_none_to_minimal(self):
        from backend.engine import ComputerUseEngine, Provider
        with patch("openai.AsyncOpenAI"):
            eng = ComputerUseEngine(provider=Provider.OPENAI, api_key="k", model="gpt-5.4")
        assert eng._client._reasoning_effort == "minimal"


class TestOpenAIZDRReplay:
    """Run-loop must carry ``reasoning.encrypted_content`` and must never
    attempt ``previous_response_id`` stashing."""

    @pytest.mark.asyncio
    async def test_request_includes_encrypted_reasoning_and_no_previous_id(self):
        from backend.engine import OpenAICUClient

        captured_requests: list[dict] = []

        async def fake_create(**kwargs):
            captured_requests.append(kwargs)
            # Return a terminal no-tool-calls response immediately.
            return SimpleNamespace(
                output=[],
                output_text="done",
                error=None,
            )

        class FakeResponses:
            create = staticmethod(fake_create)

        class FakeClient:
            responses = FakeResponses()

        class FakeExecutor:
            screen_width = 1280
            screen_height = 800

            async def capture_screenshot(self):
                return b"x" * 512

            async def execute(self, name, args):
                return CUActionResult(name=name)

            def get_current_url(self):
                return ""

        with patch("openai.AsyncOpenAI") as AA:
            AA.return_value = FakeClient()
            client = OpenAICUClient(api_key="k", model="gpt-5.5")

        await client.run_loop("noop", FakeExecutor(), turn_limit=1)

        assert captured_requests, "OpenAI responses.create was never called"
        req = captured_requests[0]
        assert req["include"] == ["reasoning.encrypted_content"]
        assert req["reasoning"] == {"effort": "medium"}
        assert req["store"] is False
        assert "previous_response_id" not in req

    @pytest.mark.asyncio
    async def test_search_enabled_requests_web_search_sources(self):
        from backend.engine import OpenAICUClient

        captured_requests: list[dict] = []

        async def fake_create(**kwargs):
            captured_requests.append(kwargs)
            return SimpleNamespace(
                output=[],
                output_text="done",
                error=None,
            )

        class FakeResponses:
            create = staticmethod(fake_create)

        class FakeClient:
            responses = FakeResponses()

        class FakeExecutor:
            screen_width = 1280
            screen_height = 800

            async def capture_screenshot(self):
                return b"x" * 512

            async def execute(self, name, args):
                return CUActionResult(name=name)

            def get_current_url(self):
                return ""

        with patch("openai.AsyncOpenAI") as AA:
            AA.return_value = FakeClient()
            client = OpenAICUClient(
                api_key="k",
                model="gpt-5.5",
                use_builtin_search=True,
            )

        await client.run_loop("noop", FakeExecutor(), turn_limit=1)

        req = captured_requests[0]
        assert req["include"] == [
            "reasoning.encrypted_content",
            "web_search_call.action.sources",
        ]


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


class TestGeminiRequireConfirmation:
    """Safety decision ``require_confirmation`` routes through ``on_safety``."""

    @pytest.mark.parametrize("model", [
        "gemini-3-flash-preview",
    ])
    @pytest.mark.asyncio
    async def test_denied_confirmation_terminates(self, model):
        from backend.engine import GeminiCUClient, Environment

        # Build the client with the SDK mocked at import time.
        with patch("google.genai.Client"):
            client = GeminiCUClient(
                api_key="k",
                model=model,
                environment=Environment.DESKTOP,
            )

        # Assemble a fake candidate that emits a function call with a
        # ``safety_decision: require_confirmation`` arg.
        fc = SimpleNamespace(
            name="click_at",
            args={"x": 10, "y": 20, "safety_decision": {
                "decision": "require_confirmation",
                "explanation": "Confirm destructive click",
            }},
        )
        part = SimpleNamespace(function_call=fc, text=None, inline_data=None, function_response=None)
        cand = SimpleNamespace(content=SimpleNamespace(parts=[part], role="model"))
        resp = SimpleNamespace(candidates=[cand])

        client._generate = AsyncMock(return_value=resp)  # type: ignore[method-assign]
        # Skip context pruning (expects real SDK types).
        import backend.engine.gemini as gem
        with patch.object(gem, "_prune_gemini_context", lambda *a, **k: None):
            class FakeExecutor:
                screen_width = 1280
                screen_height = 800

                async def capture_screenshot(self):
                    return b"x" * 512

                async def execute(self, name, args):
                    return CUActionResult(name=name)

                def get_current_url(self):
                    return ""

            explanations: list[str] = []

            def deny_safety(msg: str) -> bool:
                explanations.append(msg)
                return False

            final = await client.run_loop(
                "noop", FakeExecutor(),
                turn_limit=1, on_safety=deny_safety,
            )

        assert explanations == ["Confirm destructive click"]
        assert "terminated" in final.lower() and "safety" in final.lower()

    @pytest.mark.parametrize("model", [
        "gemini-3-flash-preview",
    ])
    @pytest.mark.asyncio
    async def test_approved_confirmation_stamps_acknowledgement(self, model):
        from backend.engine import GeminiCUClient, Environment

        with patch("google.genai.Client"):
            client = GeminiCUClient(
                api_key="k",
                model=model,
                environment=Environment.DESKTOP,
            )

        fc = SimpleNamespace(
            name="click_at",
            args={"x": 10, "y": 20, "safety_decision": {
                "decision": "require_confirmation",
                "explanation": "Confirm click",
            }},
        )
        part = SimpleNamespace(function_call=fc, text=None, inline_data=None, function_response=None)
        cand = SimpleNamespace(content=SimpleNamespace(parts=[part], role="model"))

        # First turn: function call. Second turn: no-op to terminate.
        final_part = SimpleNamespace(function_call=None, text="ok", inline_data=None, function_response=None)
        final_cand = SimpleNamespace(content=SimpleNamespace(parts=[final_part], role="model"))

        responses = iter([
            SimpleNamespace(candidates=[cand]),
            SimpleNamespace(candidates=[final_cand]),
        ])

        async def fake_gen(**kwargs):
            return next(responses)

        client._generate = fake_gen  # type: ignore[method-assign]

        import backend.engine.gemini as gem
        with patch.object(gem, "_prune_gemini_context", lambda *a, **k: None):

            class FakeExecutor:
                screen_width = 1280
                screen_height = 800
                executed: list = []

                async def capture_screenshot(self):
                    return b"x" * 512

                async def execute(self, name, args):
                    self.executed.append((name, dict(args)))
                    return CUActionResult(name=name, extra=dict(args))

                def get_current_url(self):
                    return ""

            executor = FakeExecutor()

            final = await client.run_loop(
                "noop", executor,
                turn_limit=3, on_safety=lambda _msg: True,
            )

        # safety_decision must be stripped before reaching executor.
        assert executor.executed
        name, args = executor.executed[0]
        assert name == "click_at"
        assert "safety_decision" not in args
        # Final text from the terminal turn.
        assert "ok" in final

    @pytest.mark.parametrize("model", [
        "gemini-3-flash-preview",
    ])
    @pytest.mark.asyncio
    async def test_iter_turns_emits_safetyrequired_and_resumes(self, model):
        from backend.engine import (
            GeminiCUClient,
            Environment,
            ModelTurnStarted,
            RunCompleted,
            SafetyRequired,
            ToolBatchCompleted,
        )

        with patch("google.genai.Client"):
            client = GeminiCUClient(
                api_key="k",
                model=model,
                environment=Environment.DESKTOP,
            )

        gated_fc = SimpleNamespace(
            name="click_at",
            args={"x": 10, "y": 20, "safety_decision": {
                "decision": "require_confirmation",
                "explanation": "Confirm click",
            }},
        )
        gated_part = SimpleNamespace(
            function_call=gated_fc, text=None, inline_data=None, function_response=None,
        )
        final_part = SimpleNamespace(
            function_call=None, text="ok", inline_data=None, function_response=None,
        )
        responses = iter([
            SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[gated_part], role="model"))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[final_part], role="model"))],
            ),
        ])

        async def fake_gen(**kwargs):
            return next(responses)

        client._generate = fake_gen  # type: ignore[method-assign]

        import backend.engine.gemini as gem
        with patch.object(gem, "_prune_gemini_context", lambda *a, **k: None):

            class FakeExecutor:
                screen_width = 1280
                screen_height = 800

                async def capture_screenshot(self):
                    return b"x" * 512

                async def execute(self, name, args):
                    return CUActionResult(name=name, extra=dict(args))

                def get_current_url(self):
                    return ""

            agen = client.iter_turns("noop", FakeExecutor(), turn_limit=3)
            first = await agen.__anext__()
            assert isinstance(first, ModelTurnStarted)
            assert first.pending_tool_uses == 1

            gated = await agen.__anext__()
            assert isinstance(gated, SafetyRequired)
            assert gated.explanation == "Confirm click"

            resumed = await agen.asend(True)
            assert isinstance(resumed, ToolBatchCompleted)
            assert resumed.results[0].safety_decision == SafetyDecision.REQUIRE_CONFIRMATION

            final = await agen.__anext__()
            assert isinstance(final, RunCompleted)
            assert final.final_text == "ok"

# === merged from tests/test_websearch_tools.py ===
"""Hermetic adapter tests for the official provider-native web-search tools.

Asserts that each provider's ``_build_tools`` / ``_build_config`` emits
the exact tool shape documented by the provider as of April 2026 when
``use_builtin_search`` is enabled, and is a no-op otherwise.

Reference shapes:
    * OpenAI Responses API:  ``{"type": "web_search"}``
    * Anthropic Messages:    ``{"type": "web_search_20250305", "name": "web_search", "max_uses": N}``
    * Gemini GenerateContent: ``Tool(google_search=GoogleSearch())`` plus
      ``include_server_side_tool_invocations=True`` on the config.

All provider SDKs are mocked; no network calls are made.
"""


from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class TestOpenAIWebSearch:
    """OpenAI Responses API web_search tool."""

    def _make(self, **kwargs):
        from backend.engine import OpenAICUClient
        with patch("openai.AsyncOpenAI"):
            return OpenAICUClient(api_key="k", model=kwargs.pop("model", "gpt-5.5"), **kwargs)

    def test_disabled_emits_only_computer_tool(self):
        client = self._make(use_builtin_search=False)
        tools = client._build_tools(1440, 900)
        assert tools == [{"type": "computer"}]

    def test_enabled_appends_web_search_tool(self):
        client = self._make(use_builtin_search=True)
        tools = client._build_tools(1440, 900)
        assert {"type": "computer"} in tools
        assert {"type": "web_search"} in tools

    def test_enabled_with_domain_filters(self):
        client = self._make(
            use_builtin_search=True,
            search_allowed_domains=["example.com"],
            search_blocked_domains=["bad.test"],
        )
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("type") == "web_search")
        assert ws["filters"]["allowed_domains"] == ["example.com"]
        assert ws["filters"]["blocked_domains"] == ["bad.test"]

    def test_enabled_with_file_search_keeps_all_tools(self):
        client = self._make(use_builtin_search=True)
        client._vector_store_id = "vs_test"
        tools = client._build_tools(1440, 900)
        assert {"type": "computer"} in tools
        assert {"type": "web_search"} in tools
        assert {
            "type": "file_search",
            "vector_store_ids": ["vs_test"],
        } in tools

    def test_file_search_without_web_search_keeps_computer_tool(self):
        client = self._make(use_builtin_search=False)
        client._vector_store_id = "vs_test"
        tools = client._build_tools(1440, 900)
        assert tools == [
            {"type": "computer"},
            {"type": "file_search", "vector_store_ids": ["vs_test"]},
        ]

    def test_unsupported_openai_model_with_search_raises_explicit_error(self):
        client = self._make(model="gpt-experimental-xyz", use_builtin_search=True)
        with pytest.raises(ValueError, match="not a supported OpenAI computer-use model"):
            client._build_tools(1440, 900)

    def test_unregistered_openai_ga_model_is_rejected_for_computer_use(self):
        blocked_model = "gpt-5.5" + "-pro"
        with pytest.raises(ValueError, match="not in the computer-use registry"):
            self._make(model=blocked_model)

    def test_minimal_reasoning_with_search_raises_explicit_error(self):
        with pytest.raises(ValueError, match="minimal reasoning"):
            self._make(
                model="gpt-5.5",
                use_builtin_search=True,
                reasoning_effort="minimal",
            )


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class TestClaudeWebSearch:
    """Anthropic web_search_20250305 server tool."""

    @pytest.fixture(autouse=True)
    def _clear_web_search_probe_cache(self, monkeypatch):
        import backend.engine.claude as claude_mod

        claude_mod._ANTHROPIC_WEB_SEARCH_PROBE_CACHE.clear()
        claude_mod._ANTHROPIC_WEB_SEARCH_PROBE_LOCKS.clear()
        monkeypatch.delenv("CUA_ANTHROPIC_WEB_SEARCH_ENABLED", raising=False)

    def _make(self, **kwargs):
        from backend.engine import ClaudeCUClient
        with patch("anthropic.AsyncAnthropic"):
            return ClaudeCUClient(api_key="k", model=kwargs.pop("model", "claude-sonnet-4-6"), **kwargs)

    def test_disabled_emits_only_computer_tool(self):
        client = self._make(use_builtin_search=False)
        tools = client._build_tools(1440, 900)
        assert len(tools) == 1
        assert tools[0]["name"] == "computer"

    def test_enabled_appends_web_search_tool(self):
        client = self._make(use_builtin_search=True)
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["type"] == "web_search_20250305"
        assert ws["max_uses"] == 5  # default
        assert "allowed_callers" not in ws

    def test_enabled_with_files_keeps_computer_and_web_search_tools(self):
        client = self._make(use_builtin_search=True, attached_file_ids=["f_doc"])
        tools = client._build_tools(1440, 900)
        assert tools[0]["name"] == "computer"
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["type"] == "web_search_20250305"

    def test_allowed_callers_direct_serializes_into_dynamic_web_search_tool(self):
        client = self._make(use_builtin_search=True, allowed_callers=["direct"])
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["type"] == "web_search_20260209"
        assert ws["allowed_callers"] == ["direct"]

    def test_unknown_allowed_callers_value_emits_warning(self, caplog):
        with caplog.at_level("WARNING", logger="backend.engine.claude"):
            client = self._make(use_builtin_search=True, allowed_callers=["partner-proxy"])

        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["allowed_callers"] == ["partner-proxy"]
        assert any("allowed_callers contains undocumented values" in rec.message for rec in caplog.records)

    def test_enabled_respects_max_uses_and_allowed_domains(self):
        client = self._make(
            use_builtin_search=True,
            search_max_uses=10,
            search_allowed_domains=["docs.python.org"],
        )
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["max_uses"] == 10
        assert ws["allowed_domains"] == ["docs.python.org"]
        assert "blocked_domains" not in ws

    def test_blocked_domains_used_when_no_allowlist(self):
        client = self._make(
            use_builtin_search=True,
            search_blocked_domains=["bad.test"],
        )
        tools = client._build_tools(1440, 900)
        ws = next(t for t in tools if t.get("name") == "web_search")
        assert ws["blocked_domains"] == ["bad.test"]
        assert "allowed_domains" not in ws

    def test_both_domain_lists_raise_explicit_error(self):
        with pytest.raises(ValueError, match="either search_allowed_domains or search_blocked_domains"):
            self._make(
                use_builtin_search=True,
                search_allowed_domains=["docs.python.org"],
                search_blocked_domains=["bad.test"],
            )

    @pytest.mark.asyncio
    async def test_probe_success_marks_api_key_as_enabled(self):
        import backend.engine.claude as claude_mod
        from backend.engine import ClaudeCUClient

        mock_sdk_client = SimpleNamespace(
            messages=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))
            )
        )

        with patch("anthropic.AsyncAnthropic", return_value=mock_sdk_client):
            client = ClaudeCUClient(
                api_key="k",
                model="claude-sonnet-4-6",
                use_builtin_search=True,
            )

        await client._ensure_anthropic_web_search_enabled()

        cache_key = claude_mod._anthropic_web_search_cache_key("k")
        assert mock_sdk_client.messages.create.await_count == 1
        assert claude_mod._ANTHROPIC_WEB_SEARCH_PROBE_CACHE[cache_key][0] is True

    @pytest.mark.asyncio
    async def test_probe_failure_raises_actionable_console_error(self):
        from backend.engine import ClaudeCUClient

        mock_sdk_client = SimpleNamespace(
            messages=SimpleNamespace(
                create=AsyncMock(
                    side_effect=RuntimeError(
                        "Web search is not enabled for this organization. "
                        "Ask an admin to enable web search in Claude Console."
                    )
                )
            )
        )

        with patch("anthropic.AsyncAnthropic", return_value=mock_sdk_client):
            client = ClaudeCUClient(
                api_key="k",
                model="claude-sonnet-4-6",
                use_builtin_search=True,
            )

        with pytest.raises(ValueError, match="platform\\.claude\\.com/settings/privacy"):
            await client._ensure_anthropic_web_search_enabled()

        assert mock_sdk_client.messages.create.await_count == 1

    @pytest.mark.asyncio
    async def test_probe_success_is_cached_per_api_key(self):
        from backend.engine import ClaudeCUClient

        first_sdk_client = SimpleNamespace(
            messages=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))
            )
        )
        second_sdk_client = SimpleNamespace(
            messages=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))
            )
        )

        with patch("anthropic.AsyncAnthropic", side_effect=[first_sdk_client, second_sdk_client]):
            first = ClaudeCUClient(api_key="shared-key", model="claude-sonnet-4-6", use_builtin_search=True)
            second = ClaudeCUClient(api_key="shared-key", model="claude-sonnet-4-6", use_builtin_search=True)

        await first._ensure_anthropic_web_search_enabled()
        await second._ensure_anthropic_web_search_enabled()

        assert first_sdk_client.messages.create.await_count == 1
        assert second_sdk_client.messages.create.await_count == 0

    @pytest.mark.asyncio
    async def test_probe_cache_rechecks_after_ttl_expiry(self, monkeypatch):
        import backend.engine.claude as claude_mod
        from backend.engine import ClaudeCUClient

        now = [1000.0]
        monkeypatch.setattr(claude_mod.time, "monotonic", lambda: now[0])

        first_sdk_client = SimpleNamespace(
            messages=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))
            )
        )
        second_sdk_client = SimpleNamespace(
            messages=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))
            )
        )

        with patch("anthropic.AsyncAnthropic", side_effect=[first_sdk_client, second_sdk_client]):
            first = ClaudeCUClient(api_key="shared-key", model="claude-sonnet-4-6", use_builtin_search=True)
            second = ClaudeCUClient(api_key="shared-key", model="claude-sonnet-4-6", use_builtin_search=True)

        await first._ensure_anthropic_web_search_enabled()
        now[0] += claude_mod._ANTHROPIC_WEB_SEARCH_PROBE_TTL_SECONDS + 1
        await second._ensure_anthropic_web_search_enabled()

        assert first_sdk_client.messages.create.await_count == 1
        assert second_sdk_client.messages.create.await_count == 1

    @pytest.mark.asyncio
    async def test_env_override_skips_probe(self, monkeypatch):
        from backend.engine import ClaudeCUClient

        monkeypatch.setenv("CUA_ANTHROPIC_WEB_SEARCH_ENABLED", "1")
        mock_sdk_client = SimpleNamespace(
            messages=SimpleNamespace(create=AsyncMock())
        )

        with patch("anthropic.AsyncAnthropic", return_value=mock_sdk_client):
            client = ClaudeCUClient(
                api_key="k",
                model="claude-sonnet-4-6",
                use_builtin_search=True,
            )

        await client._ensure_anthropic_web_search_enabled()

        assert mock_sdk_client.messages.create.await_count == 0


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


class TestGeminiGoogleSearch:
    """Gemini google_search grounding tool."""

    def _make(self, **kwargs):
        from backend.engine import GeminiCUClient
        with patch("google.genai.Client"):
            return GeminiCUClient(api_key="k", **kwargs)

    def test_disabled_emits_only_computer_use_tool(self):
        client = self._make(use_builtin_search=False)
        config = client._build_config()
        # Exactly one tool: computer_use
        assert len(config.tools) == 1
        assert config.tools[0].computer_use is not None

    def test_attached_files_are_rejected_for_gemini_computer_use(self):
        with pytest.raises(ValueError, match="Gemini File Search cannot be combined with Computer Use"):
            self._make(attached_file_ids=["f_doc"])

    def test_enabled_appends_google_search_tool(self):
        try:
            client = self._make(use_builtin_search=True)
            config = client._build_config()
        except ValueError as exc:
            assert "include_server_side_tool_invocations" in str(exc)
            return
        # Two tools, one of which has google_search set
        assert len(config.tools) == 2
        has_search = any(getattr(t, "google_search", None) is not None for t in config.tools)
        assert has_search, "google_search tool not present"

    def test_enabled_sets_include_server_side_tool_invocations(self):
        try:
            client = self._make(use_builtin_search=True)
            config = client._build_config()
        except ValueError as exc:
            assert "include_server_side_tool_invocations" in str(exc)
            return
        # The flag is required for combined tool execution per Gemini docs.
        # If the SDK supports the field, it must be True.
        if hasattr(config, "include_server_side_tool_invocations"):
            assert config.include_server_side_tool_invocations is True

    def test_enabled_pins_validated_function_calling_mode(self):
        client = self._make(use_builtin_search=False)

        class _FakeComputerUse:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class _FakeTool:
            def __init__(self, **kwargs):
                self.computer_use = kwargs.get("computer_use")
                self.google_search = kwargs.get("google_search")

        class _FakeThinkingConfig:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        class _FakeFunctionCallingConfig:
            def __init__(self, **kwargs):
                self.mode = kwargs.get("mode")

        class _FakeToolConfig:
            def __init__(self, **kwargs):
                self.function_calling_config = kwargs.get("function_calling_config")

        class _FakeGenerateContentConfig:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

            def model_dump(self, mode="json", exclude_none=True):
                return {
                    "include_server_side_tool_invocations": getattr(
                        self, "include_server_side_tool_invocations", None,
                    ),
                    "tool_config": {
                        "function_calling_config": {
                            "mode": self.tool_config.function_calling_config.mode,
                        },
                    },
                }

        fake_types = SimpleNamespace(
            Tool=_FakeTool,
            ComputerUse=_FakeComputerUse,
            ThinkingConfig=_FakeThinkingConfig,
            ToolConfig=_FakeToolConfig,
            FunctionCallingConfig=_FakeFunctionCallingConfig,
            FunctionCallingConfigMode=SimpleNamespace(VALIDATED="VALIDATED"),
            GoogleSearch=lambda: SimpleNamespace(),
            Environment=SimpleNamespace(
                ENVIRONMENT_BROWSER="browser",
                ENVIRONMENT_DESKTOP="desktop",
            ),
            GenerateContentConfig=_FakeGenerateContentConfig,
        )

        client._types = fake_types
        client._genai = SimpleNamespace(types=fake_types)
        client._use_builtin_search = True

        with patch("backend.engine.gemini._get_gemini_builtin_search_sdk_error", return_value=None):
            config = client._build_config()

        body = config.model_dump(mode="json", exclude_none=True)
        assert body["include_server_side_tool_invocations"] is True
        assert body["tool_config"]["function_calling_config"]["mode"] == "VALIDATED"

    def test_non_gemini3_combo_raises_explicit_error(self):
        with pytest.raises(ValueError, match="Gemini 3"):
            self._make(
                model="gemini-2.5-flash",
                use_builtin_search=True,
            )

    def test_search_options_raise_for_google_search(self):
        with pytest.raises(ValueError, match="does not support search_max_uses or domain filters"):
            self._make(
                use_builtin_search=True,
                search_max_uses=3,
            )

    def test_grounding_payload_keeps_rendered_content_out_of_footer(self):
        async def _go():
            screenshot_bytes = _png_bytes()

            class FakeExecutor:
                screen_width = 1440
                screen_height = 900

                async def capture_screenshot(self):
                    return screenshot_bytes

                async def execute(self, action, payload):
                    return CUActionResult(name=action, extra=payload)

                def get_current_url(self):
                    return None

            grounded_response = SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        grounding_metadata=SimpleNamespace(
                            search_entry_point=SimpleNamespace(
                                rendered_content="<style>body{font-family:sans-serif;}</style><div>Search Suggestions</div>",
                            ),
                            grounding_chunks=[
                                SimpleNamespace(
                                    web=SimpleNamespace(
                                        uri="https://example.com/source",
                                        title="Example Source",
                                    )
                                )
                            ],
                            grounding_supports=[
                                SimpleNamespace(
                                    segment=SimpleNamespace(
                                        start_index=0,
                                        end_index=15,
                                        text="Grounded answer",
                                    ),
                                    grounding_chunk_indices=[0],
                                )
                            ],
                            web_search_queries=["grounded answer query"],
                        ),
                        content=SimpleNamespace(
                            parts=[SimpleNamespace(function_call=None, text="Grounded answer")]
                        ),
                    )
                ]
            )

            client = self._make()
            client._client.aio.models.generate_content = AsyncMock(return_value=grounded_response)

            executor = FakeExecutor()
            events = []
            async for event in client.iter_turns("Explain the grounded answer", executor):
                events.append(event)

            assert isinstance(events[-1], RunCompleted)
            assert events[-1].final_text == "Grounded answer"
            assert "Sources:" not in events[-1].final_text
            assert "Search Suggestions" not in events[-1].final_text

            payload = client._last_completion_payload
            assert payload is not None
            grounding = payload.get("gemini_grounding")
            assert grounding is not None
            assert grounding["renderedContent"]
            assert grounding["groundingChunks"][0]["web"] == {
                "uri": "https://example.com/source",
                "title": "Example Source",
            }
            assert grounding["groundingSupports"][0]["groundingChunkIndices"] == [0]
            assert grounding["webSearchQueries"] == ["grounded answer query"]

        asyncio.run(_go())

