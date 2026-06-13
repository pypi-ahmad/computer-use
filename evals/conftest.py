from __future__ import annotations
"""Pytest setup for the evals/ runtime-boundary checks.

Mirrors tests/conftest.py: set ``CUA_TEST_MODE=1`` before any ``backend.*``
import so the degraded-container-startup eval runs standalone (Starlette's
TestClient host header is rejected otherwise). Without this, the eval only
passed when collected alongside tests/ (which sets the flag) — see T4.
"""

import os

os.environ.setdefault("CUA_TEST_MODE", "1")
