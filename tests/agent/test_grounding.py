from __future__ import annotations

import asyncio
import copy
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.agent.graph import NodeBundle, build_agent_graph, init_runtime, shutdown_runtime
from backend.agent.grounding_subgraph import build_grounding_subgraph
from backend.agent.persisted_runtime import advance_provider_turn, collect_grounding_evidence
from backend.engine.grounding import _to_plain_dict


def _png_bytes() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (32, 32), color=(12, 34, 56))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


PNG_BYTES = _png_bytes()


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


def _base_state(provider: str = "openai", *, use_builtin_search: bool = True) -> dict[str, object]:
    model = {
        "openai": "gpt-5.5",
        "anthropic": "claude-sonnet-4-6",
        "google": "gemini-3-flash-preview",
    }[provider]
    return {
        "session_id": f"ground-{provider}",
        "task": "Find the official status page.",
        "goal": "Find the official status page.",
        "max_steps": 4,
        "provider": provider,
        "model": model,
        "planner_model": model,
        "api_key": "test-api-key",
        "system_instruction": "system",
        "screen_width": 1440,
        "screen_height": 900,
        "container_name": "cua-environment",
        "agent_service_url": "http://127.0.0.1:9222",
        "reasoning_effort": "medium" if provider == "openai" else None,
        "use_builtin_search": use_builtin_search,
        "search_max_uses": None,
        "search_allowed_domains": None,
        "search_blocked_domains": None,
        "allowed_callers": None,
        "attached_files": [],
        "provider_capabilities": {
            "computer_use": True,
            "web_search": use_builtin_search,
            "model_id": model,
            "beta_headers": [],
            "search_allowed_domains": [],
            "search_blocked_domains": [],
            "allowed_callers": None,
        },
        "subgoals": [{"title": "Find the official status page", "status": "active"}],
        "active_plan": {
            "summary": "Find the official status page before logging in.",
            "steps": ["Find the official status page", "Open the site"],
            "active_subgoal": "Find the official status page",
        },
        "evidence": [],
        "completion_criteria": ["The official status page is identified"],
        "session_data": {
            "session_id": f"ground-{provider}",
            "task": "Find the official status page.",
            "status": "running",
            "model": model,
            "engine": "computer_use",
            "steps": [],
            "max_steps": 4,
            "created_at": "2026-04-27T00:00:00+00:00",
            "final_text": None,
            "gemini_grounding": None,
        },
    }


def test_grounding_subgraph_skips_when_no_external_context_is_needed():
    async def _go():
        graph = build_grounding_subgraph()
        state = _base_state()
        state["goal"] = "Click Save in the current dialog."
        state["task"] = "Click Save in the current dialog."
        state["subgoals"] = [{"title": "Click Save", "status": "active"}]
        state["active_plan"] = {
            "summary": "Click the Save button in the current dialog.",
            "steps": ["Click Save"],
            "active_subgoal": "Click Save",
        }
        with patch(
            "backend.agent.grounding_subgraph.collect_grounding_evidence",
            side_effect=AssertionError("grounding should be skipped"),
        ):
            result = await graph.ainvoke(state)
        assert result["route"] == "model_turn"
        assert result["evidence"] == []

    asyncio.run(_go())


def test_grounding_compacts_evidence_when_bound_is_exceeded():
    async def _go():
        graph = build_grounding_subgraph(evidence_limit=2)
        state = _base_state()
        state["evidence"] = [
            {"kind": "note", "value": "Older note to compress"},
            {"kind": "note", "value": "Recent note to retain"},
        ]

        async def _fake_grounding(*args, **kwargs):
            del args, kwargs
            return {
                "kind": "grounding",
                "provider": "openai",
                "subgoal": "Find the official status page",
                "plan_summary": "Find the official status page before logging in.",
                "query": "find the official status page",
                "summary": "The official page is https://status.example.test",
                "facts": ["The official page is https://status.example.test"],
                "sources": [{"title": "Status", "url": "https://status.example.test"}],
                "confidence": "high",
                "raw_response": {"output": []},
            }

        async def _fake_memory_json(*args, **kwargs):
            del args, kwargs
            return {
                "summary": "Older note to compress remains relevant.",
                "key_points": ["Older note to compress"],
                "source_urls": [],
            }

        with patch(
            "backend.agent.grounding_subgraph.collect_grounding_evidence",
            side_effect=_fake_grounding,
        ), patch(
            "backend.agent.memory_layers.request_memory_json",
            side_effect=_fake_memory_json,
        ):
            result = await graph.ainvoke(state)

        assert result["route"] == "model_turn"
        assert result["evidence"][0]["kind"] == "evidence_summary"
        retained_values = [
            item.get("value")
            for item in result["evidence"]
            if isinstance(item, dict) and item.get("kind") == "note"
        ]
        assert "Older note to compress" not in retained_values
        assert "Recent note to retain" in retained_values
        assert result["evidence"][-1]["kind"] == "grounding"

    asyncio.run(_go())


def test_parent_graph_runs_grounding_before_executor(tmp_sqlite_path):
    async def _go():
        call_order: list[str] = []
        try:
            await init_runtime(tmp_sqlite_path)
            graph = build_agent_graph(_make_bundle())
            state = _base_state()

            async def _fake_grounding(*args, **kwargs):
                call_order.append("grounding")
                del args, kwargs
                return {
                    "kind": "grounding",
                    "provider": "openai",
                    "subgoal": "Find the official status page",
                    "plan_summary": "Find the official status page before logging in.",
                    "query": "find the official status page",
                    "summary": "The official page is https://status.example.test",
                    "facts": ["The official page is https://status.example.test"],
                    "sources": [{"title": "Status", "url": "https://status.example.test"}],
                    "confidence": "high",
                    "raw_response": {"output": []},
                }

            async def _fake_advance(execution_state, on_log=None):
                del on_log
                call_order.append("executor")
                assert execution_state["evidence"][0]["kind"] == "grounding"
                return {
                    "route": "completed",
                    "status": "completed",
                    "final_text": "done",
                    "session_data": {
                        **execution_state["session_data"],
                        "status": "completed",
                        "final_text": "done",
                    },
                }

            with patch(
                "backend.agent.grounding_subgraph.collect_grounding_evidence",
                side_effect=_fake_grounding,
            ), patch(
                "backend.agent.graph.advance_provider_turn",
                side_effect=_fake_advance,
            ):
                result = await graph.ainvoke(
                    state,
                    config={"configurable": {"thread_id": "grounding-before-executor"}},
                )
            assert result["status"] == "completed"
            assert call_order == ["grounding", "executor"]
        finally:
            await shutdown_runtime()

    asyncio.run(_go())


def test_openai_grounding_uses_search_only_tools_and_records_structured_evidence():
    async def _go():
        captured: dict[str, object] = {}

        class FakeOpenAIClient:
            _model = "gpt-5.5"
            _system_prompt = "ground"
            _reasoning_effort = "medium"

            def _build_tools(self, *_args, **_kwargs):
                return [{"type": "computer"}, {"type": "web_search"}]

            async def _create_response(self, on_log=None, **kwargs):
                del on_log
                captured["tools"] = copy.deepcopy(kwargs["tools"])
                return SimpleNamespace(
                    error=None,
                    output_text="The official status page is https://status.example.test.",
                    output=[
                        {
                            "type": "web_search_call",
                            "action": {
                                "sources": [
                                    {
                                        "title": "Status",
                                        "url": "https://status.example.test",
                                    }
                                ]
                            },
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "The official status page is https://status.example.test.",
                                }
                            ],
                        },
                    ],
                )

        with patch(
            "backend.agent.persisted_runtime._build_engine",
            return_value=SimpleNamespace(_client=FakeOpenAIClient()),
        ):
            entry = await collect_grounding_evidence(
                _base_state("openai"),
                subgoal="Find the official status page",
                plan_summary="Find the official status page before logging in.",
                query="find the official status page",
            )

        assert captured["tools"] == [{"type": "web_search"}]
        assert entry["kind"] == "grounding"
        assert entry["facts"] == ["The official status page is https://status.example.test."]
        assert entry["sources"] == [{"title": "Status", "url": "https://status.example.test"}]
        assert entry["confidence"] == "medium"

    asyncio.run(_go())


def test_gemini_grounding_uses_google_search_without_computer_tool():
    async def _go():
        captured: dict[str, object] = {}

        class FakePart:
            def __init__(self, text=None):
                self.text = text

        class FakeContent:
            def __init__(self, role: str, parts: list[object]):
                self.role = role
                self.parts = parts

        class FakeTypes:
            Part = FakePart
            Content = FakeContent

        class FakeGeminiClient:
            _types = FakeTypes
            _genai = SimpleNamespace(types=SimpleNamespace(GenerateContentConfig=lambda **kwargs: SimpleNamespace(**kwargs)))

            def _build_config(self):
                return SimpleNamespace(
                    tools=[
                        SimpleNamespace(computer_use={"environment": "desktop"}),
                        SimpleNamespace(google_search={}),
                    ],
                    thinking_config="thinking",
                    include_server_side_tool_invocations=True,
                    tool_config="validated",
                    system_instruction="ground",
                )

            async def _generate(self, *, contents, config):
                del contents
                captured["tools"] = copy.deepcopy(config.tools)
                return SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            content=SimpleNamespace(parts=[SimpleNamespace(text="Use https://status.example.test")]),
                            grounding_metadata={
                                "grounding_chunks": [
                                    {
                                        "web": {
                                            "uri": "https://status.example.test",
                                            "title": "Status",
                                        }
                                    }
                                ]
                            },
                        )
                    ]
                )

        with patch(
            "backend.agent.persisted_runtime._build_engine",
            return_value=SimpleNamespace(_client=FakeGeminiClient()),
        ):
            entry = await collect_grounding_evidence(
                _base_state("google"),
                subgoal="Find the official status page",
                plan_summary="Find the official status page before logging in.",
                query="find the official status page",
            )

        assert len(captured["tools"]) == 1
        assert _to_plain_dict(captured["tools"][0]).get("google_search") == {}
        assert entry["sources"] == [{"title": "Status", "url": "https://status.example.test"}]

    asyncio.run(_go())


def test_executor_openai_still_uses_computer_tools_after_grounding():
    async def _go():
        captured: dict[str, object] = {}

        class FakeExecutor:
            screen_width = 1440
            screen_height = 900

            async def capture_screenshot(self) -> bytes:
                return PNG_BYTES

            async def aclose(self):
                return None

        class FakeOpenAIClient:
            _model = "gpt-5.5"
            _system_prompt = "executor"
            _reasoning_effort = "medium"
            _vector_store_id = None
            _current_screenshot_scale = 1.0

            async def _ensure_vector_store(self, on_log=None):
                del on_log
                return None

            def _build_tools(self, *_args, **_kwargs):
                return [{"type": "computer"}, {"type": "web_search"}]

            async def _create_response(self, on_log=None, **kwargs):
                del on_log
                captured["tools"] = copy.deepcopy(kwargs["tools"])
                return SimpleNamespace(
                    error=None,
                    output_text="Continue with the page.",
                    output=[
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "Continue with the page."}
                            ],
                        }
                    ],
                )

        fake_engine = SimpleNamespace(
            _client=FakeOpenAIClient(),
            _build_executor=lambda: FakeExecutor(),
        )
        state = _base_state("openai")
        state["evidence"] = [
            {
                "kind": "grounding",
                "provider": "openai",
                "subgoal": "Find the official status page",
                "plan_summary": "Find the official status page before logging in.",
                "query": "find the official status page",
                "summary": "The official status page is https://status.example.test",
                "facts": ["The official status page is https://status.example.test"],
                "sources": [{"title": "Status", "url": "https://status.example.test"}],
                "confidence": "high",
                "raw_response": {"output": []},
            }
        ]

        with patch(
            "backend.agent.persisted_runtime._build_engine",
            return_value=fake_engine,
        ):
            delta = await advance_provider_turn(state)

        assert delta["route"] == "model_turn"
        assert captured["tools"][0]["type"] == "computer"
        assert {tool["type"] for tool in captured["tools"]} == {"computer", "web_search"}

    asyncio.run(_go())