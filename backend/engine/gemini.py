"""Public entrypoint for the Gemini Computer Use client.

Re-exports :class:`GeminiCUClient` (and the Gemini-specific context
pruner) from the consolidated engine module. Lets callers pin their
imports to the per-provider surface so a future content split into
``backend/engine/_gemini.py`` becomes a no-op rename.
"""

from __future__ import annotations

from backend.engine import GeminiCUClient, _prune_gemini_context

__all__ = ["GeminiCUClient", "_prune_gemini_context"]
