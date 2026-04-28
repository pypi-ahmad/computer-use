from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.providers import ProviderTools, run_client, runner_for
from backend.providers.gemini import run as gemini_run
from backend.providers.openai import run as openai_run
from backend.providers.planner import create_web_execution_brief


class FakeExecutor:
    async def aclose(self):
        return None


class FakeClient:
    _use_builtin_search = False
    _last_completion_payload = {"provider": "fake"}

    async def run_loop(
        self,
        *,
        goal,
        executor,
        turn_limit,
        on_safety,
        on_turn,
        on_log,
    ):
        assert goal == "task"
        assert turn_limit == 2
        assert executor is not None
        on_log("info", "started")
        on_turn(SimpleNamespace(turn=1, actions=[], screenshot_b64=None, model_text="thinking"))
        return "done"


@pytest.mark.asyncio
async def test_provider_run_streams_events_live_and_calls_on_event():
    callback_events = []
    yielded = []

    async for event in openai_run(
        "task",
        tools=ProviderTools(web_search=False),
        files=["file-openai"],
        on_event=callback_events.append,
        on_safety=None,
        executor=FakeExecutor(),
        client=FakeClient(),
        turn_limit=2,
    ):
        yielded.append(event.type)

    assert yielded == ["log", "turn", "final"]
    assert [event.type for event in callback_events] == yielded
    assert callback_events[-1].data["text"] == "done"
    assert callback_events[-1].data["completion_payload"] == {"provider": "fake"}


@pytest.mark.asyncio
async def test_provider_run_uses_web_planner_before_computer_only_loop():
    callback_events = []

    class FakePlannerClient(FakeClient):
        _use_builtin_search = True
        _client = SimpleNamespace(responses=SimpleNamespace())

        async def _create_response(self, *, on_log, **kwargs):
            on_log("info", "planner called")
            assert kwargs["tools"] == [{"type": "web_search"}]
            assert "Open Chrome" in kwargs["input"]
            return SimpleNamespace(output_text="Open the application menu, search for Chrome, then launch it.")

        async def run_loop(
            self,
            *,
            goal,
            executor,
            turn_limit,
            on_safety,
            on_turn,
            on_log,
        ):
            assert "Original user task:" in goal
            assert "Open Chrome" in goal
            assert "Execution brief" in goal
            assert self._use_builtin_search is False
            on_log("info", "computer-only")
            on_turn(SimpleNamespace(turn=1, actions=[], screenshot_b64=None, model_text="acting"))
            return "done"

    yielded = []
    async for event in openai_run(
        "Open Chrome",
        tools=ProviderTools(web_search=True),
        files=[],
        on_event=callback_events.append,
        on_safety=None,
        executor=FakeExecutor(),
        client=FakePlannerClient(),
        turn_limit=2,
    ):
        yielded.append(event.type)

    assert yielded == ["log", "log", "log", "log", "turn", "final"]
    assert [event.type for event in callback_events] == yielded
    assert callback_events[-1].data["text"] == "done"


@pytest.mark.asyncio
async def test_run_client_preserves_legacy_callbacks():
    turns = []
    logs = []

    final_text, payload = await run_client(
        "openai",
        "task",
        client=FakeClient(),
        files=["file-openai"],
        executor=FakeExecutor(),
        turn_limit=2,
        on_safety=None,
        on_turn=turns.append,
        on_log=lambda level, message: logs.append((level, message)),
    )

    assert final_text == "done"
    assert payload == {"provider": "fake"}
    assert len(turns) == 1
    assert logs == [("info", "started")]


@pytest.mark.asyncio
async def test_gemini_rejects_reference_files_with_computer_use():
    with pytest.raises(ValueError, match="Gemini File Search cannot be combined"):
        async for _event in gemini_run(
            "task",
            tools=ProviderTools(web_search=True),
            files=["gemini-file"],
            on_event=None,
            on_safety=None,
            executor=FakeExecutor(),
            client=FakeClient(),
            turn_limit=2,
        ):
            pass


def test_runner_for_provider_aliases():
    assert runner_for("openai") is openai_run
    assert runner_for("google").__module__ == "backend.providers.gemini"
    assert runner_for("anthropic").__module__ == "backend.providers.anthropic"


@pytest.mark.asyncio
async def test_anthropic_planner_probes_search_even_when_cu_client_is_computer_only():
    class FakeAnthropicClient:
        _model = "claude-sonnet-4-6"
        _use_builtin_search = False

        def __init__(self):
            self.probed = False
            self._client = SimpleNamespace(
                beta=SimpleNamespace(
                    messages=SimpleNamespace(
                        create=AsyncMock(
                            return_value=SimpleNamespace(
                                content=[SimpleNamespace(text="planner brief")]
                            )
                        )
                    )
                )
            )

        async def _ensure_anthropic_web_search_enabled(self, on_log):
            assert self._use_builtin_search is True
            self.probed = True

        def _build_web_search_tool(self, max_uses=None):
            return {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}

    client = FakeAnthropicClient()
    brief = await create_web_execution_brief(
        provider="anthropic",
        task="Open Chrome",
        client=client,
        on_log=None,
    )

    assert brief == "planner brief"
    assert client.probed is True
    assert client._use_builtin_search is False
