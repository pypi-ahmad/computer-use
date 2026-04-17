"""Public entrypoint for the OpenAI Computer Use client.

Re-exports :class:`OpenAICUClient` and the OpenAI-specific Responses
API helpers from the consolidated engine module. Keeping a
per-provider import path here lets a future content split into
``backend/engine/_openai.py`` be an internal-only change.
"""

from __future__ import annotations

from backend.engine import (
    OpenAICUClient,
    _extract_openai_output_text,
    _build_openai_computer_call_output,
    _sanitize_openai_response_item_for_replay,
)

__all__ = [
    "OpenAICUClient",
    "_extract_openai_output_text",
    "_build_openai_computer_call_output",
    "_sanitize_openai_response_item_for_replay",
]
