"""Pytest fixtures and environment setup shared across the test suite.

Setting ``CUA_TEST_MODE=1`` here (before any ``backend.*`` import) lets
production code keep an opt-in flag for test-only allowances (e.g. the
``testserver`` Host header used by Starlette's TestClient) instead of
permanently widening security defaults.
"""

from __future__ import annotations

import os

os.environ.setdefault("CUA_TEST_MODE", "1")
