from __future__ import annotations

import html
import os
import re
import sys
import time
from html.parser import HTMLParser
from typing import Iterable
from urllib.request import Request, urlopen

TARGET_MODEL = "gemini-3-flash-preview"
CHANGELOG_URL = "https://ai.google.dev/gemini-api/docs/changelog"
MODELS_URL = "https://ai.google.dev/gemini-api/docs/models"
SUCCESSOR_CHECKLIST_PATH = "docs/gemini-successor-evaluation.md"

_FETCH_ATTEMPTS = 3
_FETCH_TIMEOUT_SECONDS = 30
_DATE_HEADING_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}$"
)
_MODEL_CODE_RE = re.compile(r"^`?[a-z0-9][a-z0-9.\-]+`?$")
_NEW_ITEM_PREFIXES = (
    "Released ",
    "Launched ",
    "Introduced ",
    "Changed ",
    "Updated ",
    "Rolled out ",
    "Added ",
    "Expanded ",
    "Increased ",
    "Model updates:",
    "API updates:",
    "SDK updates:",
    "AI Studio updates:",
)


class _VisibleTextParser(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "ol",
        "p",
        "section",
        "table",
        "tr",
        "ul",
    }
    _SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth == 0 and tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth == 0 and tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def fetch_changelog_html(url: str = CHANGELOG_URL) -> str:
    request = Request(
        url,
        headers={"User-Agent": "computer-use-gemini-watchdog/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(1, _FETCH_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - network failure path
            last_error = exc
            if attempt == _FETCH_ATTEMPTS:
                break
            time.sleep(attempt)
    assert last_error is not None
    raise last_error


def html_to_lines(raw_html: str) -> list[str]:
    parser = _VisibleTextParser()
    parser.feed(raw_html)
    parser.close()

    lines: list[str] = []
    previous: str | None = None
    for raw_line in parser.get_text().splitlines():
        line = re.sub(r"\s+", " ", html.unescape(raw_line)).strip()
        if not line or line == previous:
            continue
        lines.append(line)
        previous = line
    return lines


def _is_date_heading(line: str) -> bool:
    return bool(_DATE_HEADING_RE.match(line))


def _is_shutdown_cue(line: str) -> bool:
    lower = line.lower()
    return (
        "deprecation announcement" in lower
        or "will be shut down" in lower
        or "has been shut down" in lower
        or "are now shut down" in lower
        or "are shut down" in lower
        or "is shut down" in lower
        or "deprecated and has been shut down" in lower
    )


def _is_model_code_line(line: str) -> bool:
    return bool(_MODEL_CODE_RE.match(line))


def _starts_new_item(line: str) -> bool:
    if not line or _is_date_heading(line):
        return True
    if _is_shutdown_cue(line) or _is_model_code_line(line):
        return False
    if line.startswith(("Use ", "Migrate ", "See ", "Read ")):
        return False
    return line.startswith(_NEW_ITEM_PREFIXES)


def _extract_announcement_block(section_lines: list[str], start_index: int) -> list[str]:
    block = [section_lines[start_index]]
    for line in section_lines[start_index + 1 :]:
        if _starts_new_item(line):
            break
        block.append(line)
    return block


def _find_shutdown_in_section(
    section_date: str,
    section_lines: list[str],
    model: str,
) -> str | None:
    for index, line in enumerate(section_lines):
        if not _is_shutdown_cue(line):
            continue
        block = _extract_announcement_block(section_lines, index)
        if not any(model in block_line for block_line in block):
            continue
        return "\n".join([section_date, *block])
    return None


def find_shutdown_announcement(
    lines: Iterable[str],
    model: str = TARGET_MODEL,
) -> str | None:
    current_date: str | None = None
    section_lines: list[str] = []

    for line in lines:
        if _is_date_heading(line):
            if current_date is not None:
                match = _find_shutdown_in_section(current_date, section_lines, model)
                if match is not None:
                    return match
            current_date = line
            section_lines = []
            continue
        if current_date is not None:
            section_lines.append(line)

    if current_date is not None:
        return _find_shutdown_in_section(current_date, section_lines, model)
    return None


def build_failure_message(
    announcement_text: str,
    model: str = TARGET_MODEL,
) -> str:
    return (
        f"Gemini changelog watchdog fired for {model}.\n\n"
        "Exact changelog shutdown announcement:\n"
        f"{announcement_text}\n\n"
        f"Review successor candidates at: {MODELS_URL}\n"
        f"Follow the successor evaluation checklist: {SUCCESSOR_CHECKLIST_PATH}\n"
        "The Gemini model allowlist needs updating before a replacement model is added."
    )


def _write_summary(message: str) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("## Gemini changelog watchdog\n\n")
        handle.write("```text\n")
        handle.write(message)
        handle.write("\n```\n")


def main() -> int:
    try:
        raw_html = fetch_changelog_html()
    except Exception as exc:  # pragma: no cover - network failure path
        print(
            "Gemini changelog watchdog could not fetch the changelog: "
            f"{exc}",
            file=sys.stderr,
        )
        return 2

    announcement = find_shutdown_announcement(html_to_lines(raw_html))
    if announcement is None:
        print(
            "Gemini changelog watchdog: no shutdown/deprecation announcement "
            f"found for {TARGET_MODEL} in {CHANGELOG_URL}."
        )
        return 0

    message = build_failure_message(announcement)
    _write_summary(message)
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
