from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

from scripts import build_handbook_site


class HandbookParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.fragment_links: list[str] = []
        self.remote_assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if identifier := attributes.get("id"):
            self.ids.append(identifier)
        if (href := attributes.get("href")) and href.startswith("#"):
            self.fragment_links.append(href[1:])
        asset = attributes.get("src")
        if asset and asset.startswith(("http://", "https://")):
            self.remote_assets.append(asset)


def test_source_manifest_covers_required_codebase_documents() -> None:
    build_handbook_site.validate_sources()
    codebase_sources = {
        Path(source.path).name
        for source in build_handbook_site.SOURCES
        if source.path.startswith("docs/codebase/")
    }
    assert codebase_sources == {
        "ARCHITECTURE.md",
        "CONCERNS.md",
        "CONVENTIONS.md",
        "INTEGRATIONS.md",
        "STACK.md",
        "STRUCTURE.md",
        "TESTING.md",
    }


def test_rewrite_links_targets_included_documents() -> None:
    overview = next(source for source in build_handbook_site.SOURCES if source.key == "overview")
    fragment = '<a href="USAGE.md#daily-operation">Manual</a><a href="https://example.com">Web</a>'
    rewritten = build_handbook_site.rewrite_links(
        fragment, overview, build_handbook_site.source_paths()
    )
    assert 'href="#user-manual-daily-operation"' in rewritten
    assert 'href="https://example.com"' in rewritten


def test_generated_handbook_is_offline_and_internally_linked() -> None:
    content = build_handbook_site.DEFAULT_OUTPUT.read_text(encoding="utf-8")
    parser = HandbookParser()
    parser.feed(content)

    assert content.startswith("<!doctype html>")
    assert 'data-track="user"' in content
    assert 'data-track="technical"' in content
    assert 'data-track="business"' in content
    assert all(
        model in content for model in ("GPT-5.6 Luna", "Claude Sonnet 5", "Gemini 3.6 Flash")
    )
    assert "<script src=" not in content
    assert '<link rel="stylesheet"' not in content
    assert "url(http" not in content
    assert not parser.remote_assets
    assert len(parser.ids) == len(set(parser.ids))
    assert set(parser.fragment_links) <= set(parser.ids)


def test_generated_handbook_is_current() -> None:
    try:
        build_handbook_site.find_pandoc()
    except RuntimeError:
        pytest.skip("Pandoc is not installed")
    assert build_handbook_site.main(["--check"]) == 0
