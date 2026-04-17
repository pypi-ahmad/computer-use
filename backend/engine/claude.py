"""Public entrypoint for the Claude Computer Use client.

Re-exports :class:`ClaudeCUClient` (and Claude-specific helpers:
context pruner, screenshot scaling, coordinate scaling factor) from
the consolidated engine module. Pinning imports here lets a future
content split into ``backend/engine/_claude.py`` happen without
touching the rest of the codebase.
"""

from __future__ import annotations

from backend.engine import (
    ClaudeCUClient,
    _prune_claude_context,
    get_claude_scale_factor,
    resize_screenshot_for_claude,
)

__all__ = [
    "ClaudeCUClient",
    "_prune_claude_context",
    "get_claude_scale_factor",
    "resize_screenshot_for_claude",
]
