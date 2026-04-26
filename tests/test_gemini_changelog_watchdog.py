from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "gemini_changelog_watchdog.py"
_SPEC = importlib.util.spec_from_file_location("gemini_changelog_watchdog", _SCRIPT_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
_WATCHDOG = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_WATCHDOG)


def test_find_shutdown_announcement_ignores_unrelated_deprecations():
    lines = [
        "April 27, 2026",
        "Released `gemini-3-flash-preview`, our current combined-tool model.",
        "Deprecation announcement: The `gemini-robotics-er-1.5-preview` model will be shut down on April 30, 2026.",
    ]

    assert _WATCHDOG.find_shutdown_announcement(lines) is None


def test_find_shutdown_announcement_matches_single_line_notice():
    lines = [
        "April 27, 2026",
        "Deprecation announcement: The `gemini-3-flash-preview` model will be shut down on May 30, 2026.",
    ]

    assert _WATCHDOG.find_shutdown_announcement(lines) == (
        "April 27, 2026\n"
        "Deprecation announcement: The `gemini-3-flash-preview` model will be shut down on May 30, 2026."
    )


def test_find_shutdown_announcement_matches_multi_model_block():
    lines = [
        "April 27, 2026",
        "Deprecation announcement: The following models will be shut down on May 30, 2026:",
        "`gemini-2.0-flash`",
        "`gemini-3-flash-preview`",
        "Use `gemini-3.1-flash-preview` instead.",
        "Released `gemini-embedding-3`, our latest embedding model.",
    ]

    assert _WATCHDOG.find_shutdown_announcement(lines) == (
        "April 27, 2026\n"
        "Deprecation announcement: The following models will be shut down on May 30, 2026:\n"
        "`gemini-2.0-flash`\n"
        "`gemini-3-flash-preview`\n"
        "Use `gemini-3.1-flash-preview` instead."
    )


def test_build_failure_message_quotes_announcement_and_links_follow_up():
    announcement = (
        "April 27, 2026\n"
        "Deprecation announcement: The `gemini-3-flash-preview` model will be shut down on May 30, 2026."
    )

    message = _WATCHDOG.build_failure_message(announcement)

    assert announcement in message
    assert _WATCHDOG.MODELS_URL in message
    assert _WATCHDOG.SUCCESSOR_CHECKLIST_PATH in message
    assert "Gemini combined-tool allowlist needs updating" in message