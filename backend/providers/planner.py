"""Provider-native planning phase for Web Search ON runs.

The planner is intentionally separate from the Computer Use executor. When
the user enables web search, providers first produce a compact execution
brief with their native search tool. The Computer Use loop then receives that
brief and runs with only the computer tool.
"""

from __future__ import annotations

from typing import Any, Callable


LogCallback = Callable[[str, str], None]


_PLANNER_PROMPT = """You are preparing an execution brief for a Computer Use model.

The next phase will control a Linux desktop through screenshots, mouse, and
keyboard. Use provider-native web search only when it helps interpret the
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
"""


def build_planned_computer_use_task(task: str, brief: str) -> str:
    """Combine the original task and planner brief for the CU-only phase."""
    brief = (brief or "").strip()
    if not brief:
        return task
    return (
        "Complete the original user task using the computer tool only.\n\n"
        f"Original user task:\n{task}\n\n"
        "Execution brief from the provider-native planning/search phase:\n"
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
) -> str | None:
    """Create an execution brief with the provider's native web-search tool."""
    provider_key = str(provider or "").strip().lower()
    if provider_key == "openai":
        return await _openai_web_plan(task=task, client=client, on_log=on_log)
    if provider_key in {"google", "gemini"}:
        return await _gemini_web_plan(task=task, client=client, on_log=on_log)
    if provider_key in {"anthropic", "claude"}:
        return await _anthropic_web_plan(task=task, client=client, on_log=on_log)
    return None


async def _openai_web_plan(
    *,
    task: str,
    client: Any,
    on_log: LogCallback | None,
) -> str | None:
    """Plan with OpenAI Responses API web_search, without computer."""
    sdk_client = getattr(client, "_client", None)
    if sdk_client is None or not hasattr(sdk_client, "responses"):
        return None

    if on_log:
        on_log("info", "OpenAI web planner: building execution brief before Computer Use")

    request: dict[str, Any] = {
        "model": getattr(client, "_model", "gpt-5.6-luna"),
        "tools": [{"type": "web_search"}],
        "input": _PLANNER_PROMPT.format(task=task),
        "store": False,
        "truncation": "auto",
        "parallel_tool_calls": False,
        "include": ["web_search_call.action.sources"],
    }
    # Keep planning latency bounded and avoid GPT-5 minimal+web_search issues.
    if str(request["model"]).startswith("gpt-5"):
        request["reasoning"] = {"effort": "low"}

    create_response = getattr(client, "_create_response", None)
    if create_response is not None:
        response = await create_response(on_log=on_log, **request)
    else:
        response = await sdk_client.responses.create(**request)
    return _extract_response_text(response)


async def _gemini_web_plan(
    *,
    task: str,
    client: Any,
    on_log: LogCallback | None,
) -> str | None:
    """Plan with Gemini Google Search grounding, without computer_use."""
    create_interaction = getattr(client, "_create_interaction", None)
    if create_interaction is None:
        return None

    if on_log:
        on_log("info", "Gemini Google Search planner: building execution brief before Computer Use")

    interaction = await create_interaction(
        [{"type": "text", "text": _PLANNER_PROMPT.format(task=task)}],
        tools=[{"type": "google_search"}],
    )
    raw_text = interaction.get("output_text") if isinstance(interaction, dict) else getattr(interaction, "output_text", "")
    text = str(raw_text or "").strip()
    return text or None


async def _anthropic_web_plan(
    *,
    task: str,
    client: Any,
    on_log: LogCallback | None,
) -> str | None:
    """Plan with Anthropic web_search_20250305, without computer."""
    sdk_client = getattr(client, "_client", None)
    if sdk_client is None or not hasattr(sdk_client, "beta"):
        return None

    ensure_search = getattr(client, "_ensure_anthropic_web_search_enabled", None)
    if ensure_search is not None:
        original_search = getattr(client, "_use_builtin_search", None)
        if original_search is not None:
            setattr(client, "_use_builtin_search", True)
        try:
            await ensure_search(on_log)
        finally:
            if original_search is not None:
                setattr(client, "_use_builtin_search", original_search)

    # D6: use the public protocol method (was a reach into a private one). The
    # tool it returns carries the model-appropriate version
    # (web_search_20260209 for the 4.6+ family), resolved inside the client.
    build_tool = getattr(client, "build_web_search_tool", None)
    if build_tool is None:
        return None

    if on_log:
        on_log("info", "Anthropic web planner: building execution brief before Computer Use")

    response = await sdk_client.beta.messages.create(
        model=getattr(client, "_model", "claude-sonnet-5"),
        max_tokens=2048,
        system=(
            "You create concise execution briefs for a separate Computer Use "
            "agent. Do not perform desktop actions."
        ),
        tools=[build_tool(max_uses=3)],
        messages=[
            {
                "role": "user",
                "content": _PLANNER_PROMPT.format(task=task),
            }
        ],
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


def _extract_gemini_text(response: Any) -> str | None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) or []
    text_parts = [str(getattr(part, "text", "")).strip() for part in parts if getattr(part, "text", None)]
    return "\n".join(part for part in text_parts if part) or None


def _extract_anthropic_text(response: Any) -> str | None:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        value = getattr(block, "text", None)
        if value:
            parts.append(str(value).strip())
    return "\n\n".join(part for part in parts if part) or None
