from __future__ import annotations

import pytest

from backend.infra.mcp_fetch import _encode_stdio, fetch_url_for_model, public_http_urls
from backend.providers.planner import create_web_execution_brief


def test_public_http_urls_skips_private_and_keeps_order():
    text = (
        "see https://example.com/docs and http://127.0.0.1/secret "
        "also https://example.com/docs again https://docs.python.org/3/"
    )
    assert public_http_urls(text) == [
        "https://example.com/docs",
        "https://docs.python.org/3/",
    ]


def test_public_http_urls_rejects_localhost_and_non_http():
    assert public_http_urls("ftp://example.com/a http://localhost/x https://internal") == []


@pytest.mark.asyncio
async def test_fetch_url_for_model_rejects_private_urls():
    assert await fetch_url_for_model("http://127.0.0.1/secret") == (
        "Error: only public http(s) URLs can be fetched."
    )


def test_mcp_stdio_is_newline_json_not_content_length():
    frame = _encode_stdio({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert frame.endswith(b"\n")
    assert b"Content-Length" not in frame
    assert frame.startswith(b'{"jsonrpc":"2.0"')


@pytest.mark.asyncio
async def test_planner_fetches_task_urls_and_does_not_use_native_search():
    seen: list[list[str]] = []

    async def fake_fetch(urls: list[str]) -> list[dict[str, str]]:
        seen.append(urls)
        return [{"url": urls[0], "text": "Chrome help"}]

    class Client:
        _model = "gpt-5.6-luna"
        _client = type("Sdk", (), {"responses": object()})()
        prompts: list[str] = []

        async def _create_response(self, *, on_log, **kwargs):
            assert "tools" not in kwargs
            self.prompts.append(kwargs["input"])
            return type("R", (), {"output_text": "Launch Chrome from the menu."})()

    client = Client()
    brief = await create_web_execution_brief(
        provider="openai",
        task="Read https://www.google.com/chrome/ then open Chrome",
        client=client,
        fetch_pages_fn=fake_fetch,
    )
    assert brief == "Launch Chrome from the menu."
    assert seen == [["https://www.google.com/chrome/"]]
    assert any("Fetched pages" in prompt for prompt in client.prompts)
    assert any("Chrome help" in prompt for prompt in client.prompts)
