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
async def test_provider_run_keeps_task_and_mcp_fetch_flag_on_client():
    callback_events = []

    class FakeBrowseClient(FakeClient):
        _use_builtin_search = True

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
            assert goal == "Open Chrome"
            assert self._use_builtin_search is True
            on_log("info", "mcp-fetch-enabled")
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
        client=FakeBrowseClient(),
        turn_limit=2,
    ):
        yielded.append(event.type)

    assert yielded == ["log", "turn", "final"]
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
async def test_anthropic_planner_does_not_use_native_web_search():
    create = AsyncMock(
        return_value=SimpleNamespace(content=[SimpleNamespace(text="planner brief")])
    )

    class FakeAnthropicClient:
        _model = "claude-sonnet-5"
        _use_builtin_search = False
        probed = False

        def __init__(self):
            self._client = SimpleNamespace(
                beta=SimpleNamespace(messages=SimpleNamespace(create=create))
            )

        async def _ensure_anthropic_web_search_enabled(self, on_log):
            self.probed = True

    client = FakeAnthropicClient()
    brief = await create_web_execution_brief(
        provider="anthropic",
        task="Open Chrome",
        client=client,
        on_log=None,
        fetch_pages_fn=lambda urls: (_ for _ in ()).throw(AssertionError(urls)),
    )

    assert brief == "planner brief"
    assert client.probed is False
    assert create.await_count == 2
    for call in create.await_args_list:
        assert "tools" not in call.kwargs
