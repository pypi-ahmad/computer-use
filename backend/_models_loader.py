"""Shared loader for the canonical Computer Use model allowlist.

Moved out of ``backend.engine`` so both the engine and the HTTP server
can import it without duplicating the helper in two modules.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_allowed_models_json() -> list[dict]:
    """Load and return the ``models`` list from ``backend/allowed_models.json``.

    The JSON file is the single source of truth for provider/model ids
    and their CU capabilities.
    """
    fpath = Path(__file__).resolve().parent / "allowed_models.json"
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("models", [])
