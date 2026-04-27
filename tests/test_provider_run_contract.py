from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.providers import ProviderTools, run_client, runner_for
from backend.providers.gemini import run as gemini_run
from backend.providers.openai import run as openai_run


class FakeExecutor:
    async def aclose(self):
        return None


class FakeClient:
    _use_builtin_search = True
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
        tools=ProviderTools(web_search=True),
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
