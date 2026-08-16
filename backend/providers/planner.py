"""Web-search planning phase for Web Search ON runs.

The planner is separate from the Computer Use executor. When the user enables
Provider web search planning, this module fetches public pages through the
Fetch MCP server (``uvx mcp-server-fetch``) and asks the selected provider
for a compact execution brief. The Computer Use loop then runs with only the
computer tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from backend.infra.mcp_fetch import fetch_pages, public_http_urls

LogCallback = Callable[[str, str], None]
FetchPages = Callable[[list[str]], Awaitable[list[dict[str, str]]]]


_URL_PROMPT = """You are preparing sources for a Computer Use execution brief.

Reply with at most 3 public https URLs, one per line, that would help interpret
this desktop task (docs, official download pages, current UI labels).
No commentary. If no URL would help, reply with NONE.

User task:
{task}
"""

_PLANNER_PROMPT = """You are preparing an execution brief for a Computer Use model.

The next phase will control a Linux desktop through screenshots, mouse, and
keyboard. Use the fetched web pages below only when they help interpret the
user's request, the application name, operating-system behavior, or current
public web facts. Do not perform the desktop task yourself.

Return a concise brief with exactly these sections:
- Interpreted task
- Environment assumptions
- Step-by-step execution brief
- Verification condition
- Pitfalls

User task:
{task}

Fetched pages (MCP fetch):
{pages}
"""


def build_planned_computer_use_task(task: str, brief: str) -> str:
    """Combine the original task and planner brief for the CU-only phase."""
    brief = (brief or "").strip()
    if not brief:
        return task
    return (
        "Complete the original user task using the computer tool only.\n\n"
        f"Original user task:\n{task}\n\n"
        "Execution brief from the MCP fetch planning/search phase:\n"
        f"{brief}\n\n"
        "Do not use web search in this phase. Use screenshots and computer "
        "actions to complete the task. Stop only when the verification "
        "condition is true."
    )


async def create_web_execution_brief(
    *,
    provider: str,
    task: str,
    client: Any,
    on_log: LogCallback | None = None,
    fetch_pages_fn: FetchPages | None = None,
) -> str | None:
    """Create an execution brief using MCP fetch, not provider-native search."""
    fetch = fetch_pages_fn or fetch_pages
    urls = public_http_urls(task)
    if not urls:
        listed = await _complete_text(
            provider=provider,
            client=client,
            prompt=_URL_PROMPT.format(task=task),
            on_log=on_log,
        )
        urls = public_http_urls(listed or "")
    if on_log and urls:
        on_log("info", f"MCP fetch planner: fetching {len(urls)} URL(s)")
    pages = await fetch(urls) if urls else []
    if on_log:
        on_log(
            "info",
            "MCP fetch planner: building execution brief before Computer Use"
            + (f" ({len(pages)} page(s))" if pages else " (no pages fetched)"),
        )
    return await _complete_text(
        provider=provider,
        client=client,
        prompt=_PLANNER_PROMPT.format(task=task, pages=_format_pages(pages)),
        on_log=on_log,
    )


def _format_pages(pages: list[dict[str, str]]) -> str:
    if not pages:
        return "(none)"
    blocks: list[str] = []
    for page in pages:
        text = (page.get("text") or "").strip() or "(empty)"
        blocks.append(f"URL: {page.get('url', '')}\n{text}")
    return "\n\n".join(blocks)


async def _complete_text(
    *,
    provider: str,
    client: Any,
    prompt: str,
    on_log: LogCallback | None,
) -> str | None:
    provider_key = str(provider or "").strip().lower()
    if provider_key == "openai":
        return await _openai_text(client=client, prompt=prompt, on_log=on_log)
    if provider_key in {"google", "gemini"}:
        return await _gemini_text(client=client, prompt=prompt, on_log=on_log)
    if provider_key in {"anthropic", "claude"}:
        return await _anthropic_text(client=client, prompt=prompt)
    return None


async def _openai_text(*, client: Any, prompt: str, on_log: LogCallback | None) -> str | None:
    sdk_client = getattr(client, "_client", None)
    if sdk_client is None or not hasattr(sdk_client, "responses"):
        return None
    request: dict[str, Any] = {
        "model": getattr(client, "_model", "gpt-5.6-luna"),
        "input": prompt,
        "store": False,
        "truncation": "auto",
    }
    if str(request["model"]).startswith("gpt-5"):
        request["reasoning"] = {"effort": "low"}
    create_response = getattr(client, "_create_response", None)
    if create_response is not None:
        response = await create_response(on_log=on_log, **request)
    else:
        response = await sdk_client.responses.create(**request)
    return _extract_response_text(response)


async def _gemini_text(*, client: Any, prompt: str, on_log: LogCallback | None) -> str | None:
    create_interaction = getattr(client, "_create_interaction", None)
    if create_interaction is None:
        return None
    _ = on_log
    interaction = await create_interaction([{"type": "text", "text": prompt}])
    raw_text = (
        interaction.get("output_text")
        if isinstance(interaction, dict)
        else getattr(interaction, "output_text", "")
    )
    text = str(raw_text or "").strip()
    return text or None


async def _anthropic_text(*, client: Any, prompt: str) -> str | None:
    sdk_client = getattr(client, "_client", None)
    if sdk_client is None or not hasattr(sdk_client, "beta"):
        return None
    response = await sdk_client.beta.messages.create(
        model=getattr(client, "_model", "claude-sonnet-5"),
        max_tokens=2048,
        system=(
            "You create concise execution briefs for a separate Computer Use "
            "agent. Do not perform desktop actions."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_anthropic_text(response)


def _extract_response_text(response: Any) -> str | None:
    text = getattr(response, "output_text", None)
    if text:
        return str(text).strip()
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content_part in getattr(item, "content", []) or []:
            value = getattr(content_part, "text", None)
            if value:
                parts.append(str(value).strip())
    return "\n\n".join(part for part in parts if part) or None


def _extract_anthropic_text(response: Any) -> str | None:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        value = getattr(block, "text", None)
        if value:
            parts.append(str(value).strip())
    return "\n\n".join(part for part in parts if part) or None
