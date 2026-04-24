"""Regression test for the ``backend.certifier`` CLI default schema path.

The previous default resolved ``engine_capabilities.json`` at the repo
root (``parent.parent``) while the file actually lives next to
``backend/engine_capabilities.py``, so ``python -m backend.certifier``
raised ``FileNotFoundError`` on every clean checkout unless an explicit
``--schema`` argument was passed. This test locks in the fix.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the certifier CLI out-of-process so ``-m`` path resolution
    is exercised exactly as a real user would hit it."""
    return subprocess.run(
        [sys.executable, "-m", "backend.certifier", *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )


class TestCertifierCli:
    """Covers the default-path bug fix and the ``--schema`` override."""

    def test_default_path_resolves_without_filenotfound(self):
        """``python -m backend.certifier --json`` must NOT raise
        ``FileNotFoundError`` on a clean checkout. We only assert the
        schema loaded (exit code 0 = all healthy, 1 = schema loaded but
        env checks failed on this host) — any FileNotFoundError surfaces
        as exit code 2 with the traceback on stderr."""
        result = _run_cli("--json")
        assert "FileNotFoundError" not in result.stderr, (
            f"Default schema path still broken.\nstderr:\n{result.stderr}"
        )
        # Exit 0 (healthy) or 1 (unhealthy but schema-loaded) are both
        # acceptable — the point of the fix is that we got past the
        # load step. Exit 2+ means argparse/import/other crash.
        assert result.returncode in (0, 1), (
            f"Unexpected exit={result.returncode}\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    def test_explicit_schema_override_still_errors_on_missing_file(self):
        """``--schema`` must still override the default. Passing a path
        that doesn't exist must produce a non-zero exit and a clear
        error (not silently fall back to the default)."""
        result = _run_cli("--schema", "/nonexistent-schema-for-test.json")
        assert result.returncode != 0
        # The error message must name the missing path so operators
        # know they typo'd rather than hit an unrelated bug.
        combined = result.stdout + result.stderr
        assert "nonexistent-schema-for-test.json" in combined, (
            f"Error message did not reference the bad path.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
