from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.capability_probe import build_capability_probe_subgraph
from backend.agent.graph import NodeBundle, build_agent_graph, get_runtime, init_runtime, shutdown_runtime


@pytest.fixture
def tmp_sqlite_path():
    with tempfile.TemporaryDirectory() as d:
        yield str(Path(d) / "sessions.sqlite")


def _make_bundle(**overrides):
    async def _health():
        return True

    defaults = dict(check_health=_health)
    defaults.update(overrides)
    return NodeBundle(**defaults)


def _base_state(provider: str, model: str, *, use_builtin_search: bool = False, reasoning_effort: str | None = None, allowed_callers=None) -> dict:
    return {
        "session_id": f"cap-{provider}",
        "task": "probe capabilities",
        "goal": "probe capabilities",
        "max_steps": 2,
        "provider": provider,
        "model": model,
        "planner_model": model,
        "api_key": "test-api-key",
        "system_instruction": "system",
        "screen_width": 1440,
        "screen_height": 900,
        "container_name": "cua-environment",
        "agent_service_url": "http://127.0.0.1:9222",
        "reasoning_effort": reasoning_effort,
        "use_builtin_search": use_builtin_search,
        "search_max_uses": None,
        "search_allowed_domains": None,
        "search_blocked_domains": None,
        "allowed_callers": allowed_callers,
        "attached_files": [],
        "active_plan": {"summary": "Already planned", "steps": ["Step 1"]},
        "provider_capabilities": None,
        "session_data": {
            "session_id": f"cap-{provider}",
            "task": "probe capabilities",
            "status": "running",
            "model": model,
            "engine": "computer_use",
            "steps": [],
            "max_steps": 2,
            "created_at": "2026-04-27T00:00:00+00:00",
            "final_text": None,
            "gemini_grounding": None,
        },
    }


@pytest.mark.parametrize(
    ("provider", "model", "use_builtin_search", "allowed_callers", "expected"),
    [
        (
            "openai",
            "gpt-5.5",
            False,
            None,
            {
                "computer_use": True,
                "web_search": False,
                "reasoning_effort_default": "medium",
                "tool_combination_supported": False,
                "search_filtering_supported": True,
                "allowed_callers_supported": False,
                "tool_version": None,
                "beta_flag": None,
            },
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            True,
            ["direct"],
            {
                "computer_use": True,
                "web_search": True,
                "web_search_version": "web_search_20260209",
                "search_filtering_supported": True,
                "allowed_callers_supported": True,
                "tool_version": "computer_20251124",
                "beta_flag": "computer-use-2025-11-24",
            },
        ),
        (
            "google",
            "gemini-3-flash-preview",
            True,
            None,
            {
                "computer_use": True,
                "web_search": True,
                "tool_combination_supported": True,
                "search_filtering_supported": False,
                "allowed_callers_supported": False,
                "tool_version": None,
                "beta_flag": None,
            },
        ),
    ],
)
def test_probe_populates_capabilities_for_each_provider_model_pair(provider, model, use_builtin_search, allowed_callers, expected):
    async def _go():
        graph = build_capability_probe_subgraph()
        fake_client = SimpleNamespace(
            _use_builtin_search=use_builtin_search,
            _allowed_callers=list(allowed_callers) if allowed_callers is not None else None,
            _tool_version="computer_20251124" if provider == "anthropic" else None,
            _beta_flag="computer-use-2025-11-24" if provider == "anthropic" else None,
            _ensure_anthropic_web_search_enabled=AsyncMock(),
        )
        fake_engine = SimpleNamespace(_client=fake_client)
        with patch("backend.agent.capability_probe._build_engine", return_value=fake_engine), patch(
            "backend.agent.capability_probe._get_gemini_builtin_search_sdk_error",
            return_value=None,
        ):
            result = await graph.ainvoke(_base_state(provider, model, use_builtin_search=use_builtin_search, allowed_callers=allowed_callers))
        assert result["route"] == "planner"
        assert result["provider_capabilities"]["verified"] is True
        for key, value in expected.items():
            assert result["provider_capabilities"][key] == value
        if provider == "anthropic":
            fake_client._ensure_anthropic_web_search_enabled.assert_awaited_once()

    asyncio.run(_go())


def test_probe_fails_fast_on_unsatisfiable_combinations():
    async def _go():
        graph = build_capability_probe_subgraph()
        with patch(
            "backend.agent.capability_probe._build_engine",
            side_effect=ValueError("OpenAI web_search is not supported with gpt-5 models at minimal reasoning."),
        ):
            result = await graph.ainvoke(
                _base_state("openai", "gpt-5.5", use_builtin_search=True, reasoning_effort="minimal")
            )
        assert result["route"] == "completed"
        assert result["status"] == "error"
        assert "tools-web-search" in result["error"]

    asyncio.run(_go())


def test_cached_probe_results_round_trip_through_checkpointer(tmp_sqlite_path):
    async def _go():
        try:
            await init_runtime(tmp_sqlite_path)
            graph = build_agent_graph(_make_bundle())
            state = _base_state("openai", "gpt-5.5")
            fake_client = SimpleNamespace(
                _use_builtin_search=False,
                _allowed_callers=None,
                _tool_version=None,
                _beta_flag=None,
            )
            fake_engine = SimpleNamespace(_client=fake_client)
            with patch("backend.agent.capability_probe._build_engine", return_value=fake_engine), patch(
                "backend.agent.graph.advance_provider_turn",
                return_value={
                    "route": "completed",
                    "status": "completed",
                    "final_text": "done",
                    "session_data": {**state["session_data"], "status": "completed", "final_text": "done"},
                },
            ):
                await graph.ainvoke(state, config={"configurable": {"thread_id": "capability-round-trip"}})

            tup = await get_runtime().checkpointer.aget_tuple({"configurable": {"thread_id": "capability-round-trip"}})
            values = tup.checkpoint.get("channel_values") or {}
            assert values["provider_capabilities"]["verified"] is True
            assert values["provider_capabilities"]["provider"] == "openai"
            assert values["provider_capabilities"]["reasoning_effort_default"] == "medium"
        finally:
            await shutdown_runtime()

    asyncio.run(_go())