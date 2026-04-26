from __future__ import annotations
"""Follow-up fixes to the April-2026 wave: OpenAI ``gpt-5.4`` tool-shape
migration + registry correctness.

Covers:
  * Short-form ``{"type": "computer"}`` tool for ``gpt-5.4``.
  * Legacy long-form tool for the ``computer-use-preview`` model id.
  * ``actions[]`` array iteration with a single screenshot + single
    ``computer_call_output`` at the end.
  * ``computer_call_output.output.detail == "original"`` on every turn.
  * ``phase`` preserved on assistant-message replay (required for
    gpt-5.3-codex and beyond).
  * ``gpt-5.4-nano`` registered as NOT CU-capable (per the 2026-04-20
    OpenAI changelog entry).
"""


import base64
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.engine import (
    CUActionResult,
    _build_openai_computer_call_output,
    _sanitize_openai_response_item_for_replay,
)


# ---------------------------------------------------------------------------
# 1. Tool-shape selection
# ---------------------------------------------------------------------------


class TestToolShape:
    def test_gpt54_emits_short_form_computer_tool(self):
        from backend.engine import OpenAICUClient

        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(api_key="k", model="gpt-5.4")

        tools = client._build_tools(1440, 900)
        assert tools == [{"type": "computer"}]

    def test_gpt54_mini_also_uses_short_form(self):
        from backend.engine import OpenAICUClient

        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(api_key="k", model="gpt-5.4-mini")

        assert client._build_tools(1440, 900) == [{"type": "computer"}]

    def test_legacy_computer_use_preview_model_keeps_old_shape(self):
        from backend.engine import OpenAICUClient

        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(api_key="k", model="computer-use-preview")

        tools = client._build_tools(1440, 900)
        assert len(tools) == 1
        tool = tools[0]
        assert tool["type"] == "computer_use_preview"
        assert tool["display_width"] == 1440
        assert tool["display_height"] == 900
        assert tool["environment"] == "linux"

    def test_unknown_openai_model_warns_and_uses_short_form(self):
        from backend.engine import OpenAICUClient

        with patch("openai.AsyncOpenAI"):
            client = OpenAICUClient(api_key="k", model="gpt-experimental-xyz")

        logs: list[tuple[str, str]] = []
        tools = client._build_tools(1440, 900, on_log=lambda lvl, msg: logs.append((lvl, msg)))

        assert tools == [{"type": "computer"}]
        assert any(lvl == "warning" and "untested" in msg for lvl, msg in logs)


# ---------------------------------------------------------------------------
# 2. actions[] array iteration
# ---------------------------------------------------------------------------


class _FakeExecutor:
    """Fake executor that records every dispatched action."""

    screen_width = 1280
    screen_height = 800

    def __init__(self):
        self.screenshots_taken = 0
        self.actions_executed: list[str] = []

    async def capture_screenshot(self) -> bytes:
        self.screenshots_taken += 1
        # Large enough to clear the >=100 B empty-screenshot guard.
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 256

    async def click_at(self, *a, **kw):
        self.actions_executed.append("click")
        return {"status": "ok"}

    async def type_text_at(self, *a, **kw):
        self.actions_executed.append("type")
        return {"status": "ok"}

    async def wait_5_seconds(self, *a, **kw):
        self.actions_executed.append("wait")
        return {"status": "ok"}

    async def execute(self, name, args):
        self.actions_executed.append(name)
        return CUActionResult(name=name)

    def get_current_url(self) -> str:
        return ""


class TestActionsArray:
    @pytest.mark.asyncio
    async def test_gpt54_actions_array_iteration(self, monkeypatch):
        """A single computer_call with three actions should:

        * Dispatch all three actions in order.
        * Capture exactly one screenshot at the end of the batch.
        * Produce exactly one computer_call_output keyed to the call_id.
        """
        from backend.engine import OpenAICUClient

        executor = _FakeExecutor()

        # First response: one computer_call carrying three actions.
        # Second response: terminal, no computer_calls.
        first_call = SimpleNamespace(
            type="computer_call",
            call_id="call_abc123",
            pending_safety_checks=None,
            action=None,
            actions=[
                SimpleNamespace(type="click", x=100, y=200, button="left"),
                SimpleNamespace(type="type", text="hello"),
                SimpleNamespace(type="wait"),
            ],
        )
        first_response = SimpleNamespace(
            output=[first_call],
            output_text="",
            error=None,
        )
        terminal_response = SimpleNamespace(
            output=[],
            output_text="done",
            error=None,
        )
        responses = [first_response, terminal_response]

        dispatched: list[str] = []
        captured_inputs: list[list[dict]] = []

        async def fake_create(**kwargs):
            captured_inputs.append(kwargs["input"])
            return responses.pop(0)

        class FakeResponses:
            create = staticmethod(fake_create)

        class FakeClient:
            responses = FakeResponses()

        with patch("openai.AsyncOpenAI") as AA:
            AA.return_value = FakeClient()
            client = OpenAICUClient(api_key="k", model="gpt-5.4")

            # Stub per-action dispatch so we don't need a real executor.
            async def fake_exec(action, _executor):
                dispatched.append(getattr(action, "type", "?"))
                return CUActionResult(name=getattr(action, "type", "?"))

            monkeypatch.setattr(client, "_execute_openai_action", fake_exec)

            await client.run_loop("goal", executor, turn_limit=3)

        # All three actions dispatched in order.
        assert dispatched == ["click", "type", "wait"]

        # Exactly one post-batch screenshot (plus the initial one before
        # the first turn — so 2 total on the executor).
        assert executor.screenshots_taken == 2

        # The second turn's input must carry EXACTLY one
        # computer_call_output keyed to the original call_id.
        second_turn_input = captured_inputs[1]
        call_outputs = [
            item for item in second_turn_input
            if isinstance(item, dict) and item.get("type") == "computer_call_output"
        ]
        assert len(call_outputs) == 1
        assert call_outputs[0]["call_id"] == "call_abc123"

    @pytest.mark.asyncio
    async def test_single_action_fallback_still_works(self, monkeypatch):
        """If a computer_call has `.action` but no `.actions`, that single
        action should still be dispatched (legacy preview shape)."""
        from backend.engine import OpenAICUClient

        executor = _FakeExecutor()
        first_call = SimpleNamespace(
            type="computer_call",
            call_id="call_legacy",
            pending_safety_checks=None,
            action=SimpleNamespace(type="click", x=50, y=60, button="left"),
            actions=None,
        )
        first_response = SimpleNamespace(
            output=[first_call], output_text="", error=None,
        )
        terminal_response = SimpleNamespace(
            output=[], output_text="done", error=None,
        )
        responses = [first_response, terminal_response]
        dispatched: list[str] = []

        async def fake_create(**kwargs):
            return responses.pop(0)

        class FakeResponses:
            create = staticmethod(fake_create)

        class FakeClient:
            responses = FakeResponses()

        with patch("openai.AsyncOpenAI") as AA:
            AA.return_value = FakeClient()
            client = OpenAICUClient(api_key="k", model="gpt-5.4")

            async def fake_exec(action, _executor):
                dispatched.append(getattr(action, "type", "?"))
                return CUActionResult(name=getattr(action, "type", "?"))

            monkeypatch.setattr(client, "_execute_openai_action", fake_exec)
            await client.run_loop("goal", executor, turn_limit=3)

        assert dispatched == ["click"]


# ---------------------------------------------------------------------------
# 3. detail=original on computer_call_output
# ---------------------------------------------------------------------------


class TestScreenshotDetailOriginal:
    def test_build_openai_computer_call_output_sets_detail_original(self):
        fake_bytes = base64.standard_b64encode(b"\x89PNG" + b"\x00" * 16).decode()
        payload = _build_openai_computer_call_output("call_x", fake_bytes)

        assert payload["type"] == "computer_call_output"
        assert payload["call_id"] == "call_x"
        assert payload["output"]["type"] == "computer_screenshot"
        assert payload["output"]["detail"] == "original"
        assert payload["output"]["image_url"].startswith("data:image/png;base64,")

    def test_build_openai_computer_call_output_carries_safety_acks(self):
        payload = _build_openai_computer_call_output(
            "call_y",
            "b64",
            acknowledged_safety_checks=[{"id": "sc_1", "code": "x", "message": "y"}],
        )
        assert payload["acknowledged_safety_checks"] == [
            {"id": "sc_1", "code": "x", "message": "y"}
        ]
        # detail must still be "original" even with safety checks present.
        assert payload["output"]["detail"] == "original"

    def test_gpt54_screenshot_output_has_detail_original(self):
        """Spec-named guard (April 2026 followup): every
        ``computer_call_output`` emitted by the adapter's shared factory
        must carry ``output.detail == "original"``. OpenAI's CU guide is
        explicit that ``high`` / ``low`` image detail should never be
        used for computer-use tasks."""
        payload = _build_openai_computer_call_output(
            "call_detail_guard", "Zm9v",
        )
        assert payload["output"]["detail"] == "original"
        # And with safety acks — the detail must not regress on that path.
        payload_with_acks = _build_openai_computer_call_output(
            "call_detail_guard_2", "YmFy",
            acknowledged_safety_checks=[{"id": "sc", "code": "c", "message": "m"}],
        )
        assert payload_with_acks["output"]["detail"] == "original"


# ---------------------------------------------------------------------------
# 4. phase preserved on assistant-message replay
# ---------------------------------------------------------------------------


class TestPhasePreserved:
    def test_phase_commentary_survives_sanitizer(self):
        item = SimpleNamespace(
            type="message",
            role="assistant",
            content=[{"type": "output_text", "text": "thinking…"}],
            phase="commentary",
        )
        sanitized = _sanitize_openai_response_item_for_replay(item)
        assert sanitized["type"] == "message"
        assert sanitized.get("phase") == "commentary"

    def test_phase_final_answer_survives_sanitizer(self):
        item = SimpleNamespace(
            type="message",
            role="assistant",
            content=[{"type": "output_text", "text": "Done."}],
            phase="final_answer",
        )
        sanitized = _sanitize_openai_response_item_for_replay(item)
        assert sanitized.get("phase") == "final_answer"

    def test_message_without_phase_omits_key(self):
        item = SimpleNamespace(
            type="message",
            role="assistant",
            content=[{"type": "output_text", "text": "no phase here"}],
        )
        sanitized = _sanitize_openai_response_item_for_replay(item)
        # When the model omits phase, the sanitizer must not invent one.
        assert "phase" not in sanitized

    def test_phase_preserved_on_assistant_message_replay(self):
        """Spec-named guard (April 2026 followup): when an assistant
        message carries ``phase``, it must still carry ``phase`` after
        sanitization. Required for gpt-5.3-codex and beyond per the
        Responses API reference — dropping ``phase`` makes the model
        unable to distinguish intermediate commentary from the final
        answer on replay."""
        item = SimpleNamespace(
            type="message",
            role="assistant",
            status="in_progress",
            content=[
                SimpleNamespace(
                    type="output_text",
                    text="Working on it",
                    annotations=[{"type": "file_citation"}],
                    logprobs=[{"token": "W"}],
                ),
            ],
            phase="commentary",
        )
        sanitized = _sanitize_openai_response_item_for_replay(item)
        assert sanitized["phase"] == "commentary"
        # Output-only fields must still be stripped on the same pass —
        # this guard should never re-introduce them.
        assert "status" not in sanitized
        assert sanitized["content"][0].get("annotations") is None
        assert sanitized["content"][0].get("logprobs") is None


# ---------------------------------------------------------------------------
# 5. gpt-5.4-nano not CU-capable
# ---------------------------------------------------------------------------


class TestRegistryNanoFlag:
    def test_gpt54_nano_not_cu_capable(self):
        from backend.models.schemas import load_allowed_models_json

        models = {m["model_id"]: m for m in load_allowed_models_json()}
        assert "gpt-5.4-nano" in models, "gpt-5.4-nano must be present in the registry"
        assert models["gpt-5.4-nano"]["supports_computer_use"] is False, (
            "gpt-5.4-nano does not support Computer Use per the OpenAI 2026-04-20 changelog"
        )

    def test_gpt54_still_cu_capable(self):
        """Sanity — the main gpt-5.4 entry must remain CU-capable."""
        from backend.models.schemas import load_allowed_models_json

        models = {m["model_id"]: m for m in load_allowed_models_json()}
        assert models["gpt-5.4"]["supports_computer_use"] is True
