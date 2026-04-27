"""Gemini Computer Use provider loop.

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
from backend.engine import DEFAULT_TURN_LIMIT, Environment
from backend.engine.gemini import GeminiCUClient
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
    """Run Gemini's native Computer Use loop with optional Google Search."""
    provider_tools = normalize_tools(tools)
    file_ids = list(files or [])
    if file_ids:
        from backend.files import GEMINI_CU_FILE_REJECTION
        raise ValueError(GEMINI_CU_FILE_REJECTION)

    client = options.get("client")
    if client is None:
        api_key = options.get("api_key")
        if not api_key:
            raise ValueError("Gemini provider run requires api_key when no client is supplied.")
        client = GeminiCUClient(
            api_key=api_key,
            model=options.get("model") or "gemini-3-flash-preview",
            environment=options.get("environment") or Environment.DESKTOP,
            excluded_actions=options.get("excluded_actions"),
            system_instruction=options.get("system_prompt"),
            use_builtin_search=provider_tools.web_search,
            attached_file_ids=file_ids,
        )

    created_executor = executor is None
    if executor is None:
        executor = DesktopExecutor(
            screen_width=int(options.get("screen_width") or DEFAULT_SCREEN_WIDTH),
            screen_height=int(options.get("screen_height") or DEFAULT_SCREEN_HEIGHT),
            normalize_coords=True,
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
