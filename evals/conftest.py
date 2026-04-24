"""Shared fixtures for the eval harness.

Keeps evals fully offline:

* ``CUA_TEST_MODE`` and ``CUA_TRACE_DIR`` are pinned to a per-session
  temp directory so evals never touch the operator's real trace
  store.
* The :class:`backend.agent.graph.GraphRuntime` is initialised against
  a throwaway sqlite file and torn down on session teardown.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("CUA_TEST_MODE", "1")


@pytest.fixture(scope="session")
def _trace_tmp_root() -> Path:
    """Allocate a per-session temp dir for trace sidecars."""
    with tempfile.TemporaryDirectory(prefix="cua-eval-traces-") as d:
        root = Path(d)
        yield root


@pytest.fixture(autouse=True)
def _trace_dir(_trace_tmp_root, monkeypatch):
    """Redirect :func:`backend.tracing.trace_path` writes into the tmp root."""
    monkeypatch.setenv("CUA_TRACE_DIR", str(_trace_tmp_root))
    yield


@pytest.fixture
def sqlite_db(tmp_path):
    """Per-test LangGraph sqlite checkpoint path."""
    return str(tmp_path / "sessions.sqlite")
