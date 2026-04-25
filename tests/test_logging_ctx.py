"""Regression tests for ``backend/logging_ctx.py``.

Covers the two public surfaces introduced by SC1:

* ``configure_logging(fmt="json")`` emits single-line JSON log records
  with the documented field set (``ts``, ``level``, ``logger``,
  ``msg``, ``session_id``).
* The ``session_id`` context variable flows into log records even when
  the record is emitted from a library module (via the
  ``SessionIdFilter`` installed on the root logger).
"""

from __future__ import annotations

import io
import json
import logging
from typing import Iterator

import pytest

from backend.logging_ctx import (
    JsonFormatter,
    SessionIdFilter,
    configure_logging,
    session_context,
)


@pytest.fixture
def root_with_stream() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """A throw-away Logger with a StringIO handler.

    The real root logger is shared across pytest invocations — reusing
    it and then reconfiguring would bleed formatters into sibling
    tests. Build a private Logger with its own handler for each test.
    """
    logger = logging.getLogger("test_logging_ctx._root")
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logger.addHandler(handler)
    logger.propagate = False
    yield logger, buf
    for h in list(logger.handlers):
        logger.removeHandler(h)


class TestJsonFormatter:
    """The formatter must emit valid JSON with the documented fields."""

    def test_fields(self, root_with_stream: tuple[logging.Logger, io.StringIO]) -> None:
        logger, buf = root_with_stream
        for h in logger.handlers:
            h.setFormatter(JsonFormatter())
            h.addFilter(SessionIdFilter())
        with session_context("abcdef01"):
            logger.info("hello")
        line = buf.getvalue().strip()
        assert line, "formatter emitted nothing"
        data = json.loads(line)
        assert set(data) >= {"ts", "level", "logger", "msg", "session_id"}
        assert data["level"] == "INFO"
        assert data["msg"] == "hello"
        assert data["session_id"] == "abcdef01"
        # Timestamp ends with 'Z' for UTC per the formatter contract.
        assert data["ts"].endswith("Z")

    def test_no_session_renders_dash(
        self, root_with_stream: tuple[logging.Logger, io.StringIO],
    ) -> None:
        """When no session is bound, the filter surfaces ``"-"`` so the
        JSON record always has a stable ``session_id`` field."""
        logger, buf = root_with_stream
        for h in logger.handlers:
            h.setFormatter(JsonFormatter())
            h.addFilter(SessionIdFilter())
        logger.info("no-session")
        data = json.loads(buf.getvalue().strip())
        assert data["session_id"] == "-"

    def test_exception_emits_exc_type_and_traceback(
        self, root_with_stream: tuple[logging.Logger, io.StringIO],
    ) -> None:
        """``logger.exception(...)`` must include ``exc_type`` and a
        stringified traceback for downstream collectors to index on."""
        logger, buf = root_with_stream
        for h in logger.handlers:
            h.setFormatter(JsonFormatter())
            h.addFilter(SessionIdFilter())
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("caught")
        data = json.loads(buf.getvalue().strip())
        assert data["exc_type"] == "ValueError"
        assert "ValueError: boom" in data["exc"]


class TestConfigureLogging:
    """``configure_logging`` respects env vars + explicit kwargs and
    is idempotent across calls."""

    def test_json_format_via_kwarg(self) -> None:
        """Explicit fmt='json' installs the JSON formatter on the root
        logger's handlers."""
        buf = io.StringIO()
        root = logging.getLogger()
        # Reset handlers to a private StringIO handler so the assertion
        # works regardless of whether pytest captured stderr.
        orig_handlers = list(root.handlers)
        for h in orig_handlers:
            root.removeHandler(h)
        root.addHandler(logging.StreamHandler(buf))
        try:
            configure_logging(level="INFO", fmt="json")
            logger = logging.getLogger("test_logging_ctx.cfg_json")
            with session_context("feedface"):
                logger.info("configured")
            line = buf.getvalue().strip().splitlines()[-1]
            data = json.loads(line)
            assert data["session_id"] == "feedface"
            assert data["msg"] == "configured"
        finally:
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in orig_handlers:
                root.addHandler(h)

    def test_unknown_format_falls_back_to_console(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unrecognised LOG_FORMAT must not crash startup."""
        monkeypatch.setenv("LOG_FORMAT", "yaml-please")
        # Should not raise.
        configure_logging()

    def test_idempotent(self) -> None:
        """Two calls back-to-back are safe (no duplicate filters)."""
        configure_logging(fmt="console")
        configure_logging(fmt="console")
        # A handler must not have more than one SessionIdFilter.
        root = logging.getLogger()
        for handler in root.handlers:
            filters = [f for f in handler.filters if isinstance(f, SessionIdFilter)]
            assert len(filters) <= 1


class TestPrintAudit:
    """SC1 constraint: no `print()` in backend/ outside __main__ blocks.

    Enforced as a source scan rather than a runtime check. The 22
    `print()` sites we *do* have are all inside CLI output functions
    (``_print_table`` in ``certifier.py``, ``_cli`` in ``tracing.py``)
    that are reachable only via ``python -m backend.<module>``.
    """

    def test_no_print_outside_cli_entrypoints(self) -> None:
        import re
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        allowed_cli_modules = {
            # CLI output modules — ``print`` is the interface, logging
            # would be wrong here (operators want raw stdout).
            "backend/certifier.py",
            "backend/tracing.py",
        }
        # Word-boundary match so substrings like ``_fingerprint(`` do
        # not false-positive on ``print(``. Also skip comment lines.
        print_re = re.compile(r"(?<![A-Za-z0-9_])print\s*\(")
        offenders: list[str] = []
        for py in (repo_root / "backend").rglob("*.py"):
            rel = py.relative_to(repo_root).as_posix()
            if rel in allowed_cli_modules:
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if print_re.search(stripped) and "# noqa: T201" not in stripped:
                    offenders.append(f"{rel}:{i}: {stripped}")
        assert not offenders, (
            "`print()` outside CLI entrypoints breaks the structured-"
            "logging contract. Convert to logger.* or mark the line "
            "with ``# noqa: T201`` with justification.\n  "
            + "\n  ".join(offenders)
        )
