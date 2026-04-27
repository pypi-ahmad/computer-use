"""Provider-owned native Computer Use run loops."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.providers._common import ProviderEvent, ProviderTools


def runner_for(provider: str) -> Callable[..., Any]:
    """Return the provider module's ``run`` coroutine generator."""
    key = str(provider or "").strip().lower()
    if key in {"google", "gemini"}:
        from backend.providers.gemini import run
        return run
    if key in {"anthropic", "claude"}:
        from backend.providers.anthropic import run
        return run
    if key == "openai":
        from backend.providers.openai import run
        return run
    raise ValueError(f"Unsupported CU provider: {provider}")


async def run_client(
    provider: str,
    task: str,
    *,
    client: Any,
    files: list[str],
    executor: Any,
    turn_limit: int,
    on_safety: Callable[[str], Any] | None,
    on_turn: Callable[[Any], Any] | None,
    on_log: Callable[[str, str], Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Run an already-constructed provider client through its provider module."""
    tools = ProviderTools(
        web_search=bool(getattr(client, "_use_builtin_search", False)),
        search_allowed_domains=getattr(client, "_search_allowed_domains", None),
        search_blocked_domains=getattr(client, "_search_blocked_domains", None),
        allowed_callers=getattr(client, "_allowed_callers", None),
    )
    final_text = ""
    completion_payload: dict[str, Any] = {}
    async for event in runner_for(provider)(
        task,
        tools=tools,
        files=files,
        on_event=None,
        on_safety=on_safety,
        executor=executor,
        turn_limit=turn_limit,
        client=client,
    ):
        if event.type == "turn" and on_turn:
            on_turn(event.data)
        elif event.type == "log" and on_log:
            data = event.data or {}
            on_log(data.get("level", "info"), data.get("message", ""))
        elif event.type == "final":
            data = event.data or {}
            final_text = str(data.get("text") or "")
            completion_payload = data.get("completion_payload") or {}
    return final_text, completion_payload


__all__ = ["ProviderEvent", "ProviderTools", "run_client", "runner_for"]
