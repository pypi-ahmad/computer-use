"""OpenAI Computer Use provider loop.

The public ``run`` function owns the documented provider loop shape:
``run(task, *, tools, files, on_event, on_safety, executor)``.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from backend.executor import (
    DEFAULT_SCREEN_HEIGHT,
    DEFAULT_SCREEN_WIDTH,
    DesktopExecutor,
)
from backend.engine import DEFAULT_TURN_LIMIT
from backend.engine.openai import OpenAICUClient
from backend.providers._common import (
    EventCallback,
    ProviderTools,
    SafetyCallback,
    normalize_tools,
    stream_client_run_loop,
)


async def run(
    task: str,
    *,
    tools: ProviderTools | Mapping[str, Any] | None = None,
    files: Sequence[str] | None = None,
    on_event: EventCallback | None = None,
    on_safety: SafetyCallback | None = None,
    executor: Any | None = None,
    **options: Any,
):
    """Run OpenAI's native Computer Use loop with optional web/files."""
    provider_tools = normalize_tools(tools)
    file_ids = list(files or [])

    client = options.get("client")
    if client is None:
        api_key = options.get("api_key")
        if not api_key:
            raise ValueError("OpenAI provider run requires api_key when no client is supplied.")
        client = OpenAICUClient(
            api_key=api_key,
            model=options.get("model") or "gpt-5.5",
            system_prompt=options.get("system_prompt"),
            reasoning_effort=options.get("reasoning_effort"),
            use_builtin_search=provider_tools.web_search,
            search_max_uses=provider_tools.search_max_uses,
            search_allowed_domains=provider_tools.search_allowed_domains,
            search_blocked_domains=provider_tools.search_blocked_domains,
            attached_file_ids=file_ids,
        )

    created_executor = executor is None
    if executor is None:
        executor = DesktopExecutor(
            screen_width=int(options.get("screen_width") or DEFAULT_SCREEN_WIDTH),
            screen_height=int(options.get("screen_height") or DEFAULT_SCREEN_HEIGHT),
            normalize_coords=False,
            agent_service_url=options.get("agent_service_url") or "http://127.0.0.1:9222",
            container_name=options.get("container_name") or "cua-environment",
        )

    async for event in stream_client_run_loop(
        task,
        client=client,
        executor=executor,
        turn_limit=int(options.get("turn_limit") or DEFAULT_TURN_LIMIT),
        on_event=on_event,
        on_safety=on_safety,
        close_executor=created_executor,
    ):
        yield event
