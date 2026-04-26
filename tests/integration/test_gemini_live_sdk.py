"""Live SDK integration checks for Gemini combined tool mode.

This test intentionally uses the real ``google-genai`` types and a mock
``httpx`` transport. It verifies that the pinned SDK accepts the exact
combined-mode config shape emitted by ``GeminiCUClient`` when
``use_builtin_search=True``. If the SDK rejects that config, that is a
wrapper bug and this test must fail loudly.
"""

from __future__ import annotations

import json

import httpx
import pytest
from google import genai
from google.genai import types

from backend.engine import Environment, GeminiCUClient


pytestmark = pytest.mark.integration


_MOCK_RESPONSE = {
    "candidates": [
        {
            "content": {
                "parts": [{"text": "Hello from mock!"}],
                "role": "model",
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 1,
        "candidatesTokenCount": 1,
        "totalTokenCount": 2,
    },
}


def test_gemini_combined_mode_config_is_accepted_by_live_sdk() -> None:
    """Exercise the real google-genai schema for the combined-mode config.

    The current adapter emits:
    - ``Tool(computer_use=ComputerUse(...))``
    - ``Tool(google_search=GoogleSearch())``
    - ``ToolConfig(FunctionCallingConfig(mode=VALIDATED))``
    - ``include_server_side_tool_invocations=True``

    No real API request is made: the SDK client uses ``httpx.MockTransport``.
    """
    captured: dict[str, object] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_MOCK_RESPONSE)

    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as httpx_client:
        adapter = GeminiCUClient(
            api_key="test-key",
            model="gemini-3-flash-preview",
            environment=Environment.DESKTOP,
            use_builtin_search=True,
        )
        adapter._client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(httpxClient=httpx_client),
        )

        try:
            config = adapter._build_config()
        except Exception as exc:  # pragma: no cover - exercised in the current failing SDK state
            pytest.fail(
                "google-genai==1.67.0 rejected the Gemini combined-mode config emitted "
                f"by GeminiCUClient: {exc}"
            )

        assert isinstance(config, types.GenerateContentConfig)
        assert len(config.tools or []) == 2
        assert any(getattr(tool, "computer_use", None) is not None for tool in config.tools)
        assert any(getattr(tool, "google_search", None) is not None for tool in config.tools)
        assert config.include_server_side_tool_invocations is True
        assert config.tool_config is not None
        assert config.tool_config.function_calling_config is not None
        assert (
            config.tool_config.function_calling_config.mode
            == types.FunctionCallingConfigMode.VALIDATED
        )

        response = adapter._client.models.generate_content(
            model=adapter._model,
            contents="test prompt",
            config=config,
        )

    assert response.text == "Hello from mock!"
    assert captured["method"] == "POST"
    body = captured["body"]
    assert isinstance(body, dict)
    tools = body.get("tools")
    assert isinstance(tools, list)
    assert any("computerUse" in tool for tool in tools)
    assert any("googleSearch" in tool for tool in tools)