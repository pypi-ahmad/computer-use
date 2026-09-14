from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.engine import ComputerUseEngine, Environment, HostDesktopExecutor, Provider
from backend.models.schemas import StartTaskRequest
from backend.v2.api import SessionInput


def test_start_request_accepts_host_target() -> None:
    req = StartTaskRequest(
        task="Open notepad",
        provider="google",
        model="gemini-3.7-flash",
        execution_target="host",
    )
    assert req.execution_target == "host"


def test_v2_session_input_accepts_host_target() -> None:
    payload = SessionInput.model_validate(
        {"task": "Open notepad", "model": "gemini-3.7-flash", "executionTarget": "host"}
    )
    assert payload.execution_target == "host"


def test_v2_session_input_rejects_unknown_target() -> None:
    with pytest.raises(Exception):
        SessionInput.model_validate(
            {"task": "Open notepad", "model": "gemini-3.7-flash", "executionTarget": "local"}
        )


def test_host_engine_uses_host_executor_and_detected_screen() -> None:
    with (
        patch("backend.engine.detect_host_screen", return_value=(1920, 1080)),
        patch("anthropic.Anthropic"),
    ):
        engine = ComputerUseEngine(
            provider=Provider.CLAUDE,
            api_key="test-key",
            environment=Environment.DESKTOP,
            execution_target="host",
        )
    assert engine.screen_width == 1920
    assert engine.screen_height == 1080
    executor = engine._build_executor(page=None)
    assert isinstance(executor, HostDesktopExecutor)
    assert executor.screen_width == 1920


def test_docker_engine_still_uses_sandbox_executor() -> None:
    with patch("anthropic.Anthropic"):
        engine = ComputerUseEngine(
            provider=Provider.CLAUDE,
            api_key="test-key",
            execution_target="docker",
        )
    from backend.engine import DesktopExecutor

    executor = engine._build_executor(page=None)
    assert type(executor) is DesktopExecutor


@pytest.mark.asyncio
async def test_host_executor_dispatches_without_touching_os() -> None:
    with patch.object(HostDesktopExecutor, "_apply", return_value={"ok": True}) as apply:
        executor = HostDesktopExecutor(screen_width=1440, screen_height=900, normalize_coords=False)
        result = await executor.execute("click_at", {"x": 10, "y": 20})
    assert result.success
    assert apply.call_args.args[0]["action"] == "click"
