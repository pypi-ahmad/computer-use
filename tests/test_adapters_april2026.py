"""Adapter-level tests for the April 2026 CU upgrade.

Covers:
  * Claude: tool-version branching (``computer_20251124`` vs
    ``computer_20250124``), adaptive thinking, 1:1 coordinates for
    Opus 4.7 / Sonnet 4.6, legacy scale-factor path for pre-4.5
    models.
  * OpenAI: ``gpt-5.4`` default with ``reasoning.effort == "high"``
    floor, ZDR-safe replay (no ``previous_response_id``), and
    ``include=["reasoning.encrypted_content"]``.
  * Gemini: ``require_confirmation`` routing via the shared
    ``on_safety`` interrupt.

All provider SDKs are mocked; no network calls.
"""

from __future__ import annotations

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
        "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
    ])
    def test_new_tool_version_models(self, model):
        from backend.engine import ClaudeCUClient
        with patch("anthropic.AsyncAnthropic"):
            c = ClaudeCUClient(api_key="k", model=model)
        assert c._tool_version == "computer_20251124"
        assert c._beta_flag == "computer-use-2025-11-24"

    def test_legacy_tool_version_for_pre_45(self):
        from backend.engine import ClaudeCUClient
        with patch("anthropic.AsyncAnthropic"):
            c = ClaudeCUClient(api_key="k", model="claude-3-5-sonnet-20241022")
        assert c._tool_version == "computer_20250124"
        assert c._beta_flag == "computer-use-2025-01-24"


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
        "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
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
    """CU floor is ``high``; construction without explicit effort → high."""

    def test_default_reasoning_effort_is_high(self):
        from backend.engine import OpenAICUClient
        with patch("openai.AsyncOpenAI"):
            c = OpenAICUClient(api_key="k", model="gpt-5.4")
        assert c._reasoning_effort == "high"

    def test_invalid_effort_falls_back_to_high(self):
        from backend.engine import OpenAICUClient
        with patch("openai.AsyncOpenAI"):
            c = OpenAICUClient(api_key="k", model="gpt-5.4", reasoning_effort="bogus")
        assert c._reasoning_effort == "high"

    def test_facade_default_passes_high(self):
        from backend.engine import ComputerUseEngine, Provider
        with patch("openai.AsyncOpenAI"):
            eng = ComputerUseEngine(provider=Provider.OPENAI, api_key="k", model="gpt-5.4")
        assert eng._client._reasoning_effort == "high"


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
            client = OpenAICUClient(api_key="k", model="gpt-5.4")

        await client.run_loop("noop", FakeExecutor(), turn_limit=1)

        assert captured_requests, "OpenAI responses.create was never called"
        req = captured_requests[0]
        assert req["include"] == ["reasoning.encrypted_content"]
        assert req["reasoning"] == {"effort": "high"}
        assert req["store"] is False
        assert "previous_response_id" not in req


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
