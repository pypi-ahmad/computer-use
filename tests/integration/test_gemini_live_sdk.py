"""Live SDK integration checks for Gemini computer-only execution mode.

This test intentionally uses the real ``google-genai`` types and a mock
``httpx`` transport. It verifies that the pinned SDK accepts the
Computer Use config emitted by ``GeminiCUClient`` even when product-level
web search is enabled. Search itself runs in a separate planning phase.
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


def test_gemini_computer_use_config_is_accepted_by_live_sdk() -> None:
    """Exercise the real google-genai schema for the CU-only config.

    The current adapter emits:
    - ``Tool(computer_use=ComputerUse(...))``

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
                "google-genai==1.67.0 rejected the Gemini Computer Use config emitted "
                f"by GeminiCUClient: {exc}"
            )

        assert isinstance(config, types.GenerateContentConfig)
        assert len(config.tools or []) == 1
        assert any(getattr(tool, "computer_use", None) is not None for tool in config.tools)

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
    assert not any("googleSearch" in tool for tool in tools)
