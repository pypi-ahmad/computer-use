"""Build the self-contained Zero to Hero handbook website."""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "zero-to-hero-study-handbook.html"


@dataclass(frozen=True)
class SourceDoc:
    key: str
    path: str
    title: str
    group: str
    audiences: tuple[str, ...]
    summary: str
    historical: bool = False


SOURCES = (
    SourceDoc(
        "overview",
        "README.md",
        "Repository overview",
        "Start here",
        ("all",),
        "Purpose, supported routes, quick start, and verification status.",
    ),
    SourceDoc(
        "business",
        "docs/business-guide.md",
        "Business guide",
        "Start here",
        ("business",),
        "Capabilities, pilot design, controls, measures, and go/no-go questions.",
    ),
    SourceDoc(
        "user-manual",
        "USAGE.md",
        "Complete user manual",
        "Operate",
        ("user", "technical"),
        "Installation, dashboard operation, credentials, safety, APIs, and troubleshooting.",
    ),
    SourceDoc(
        "prompt-guide",
        "docs/computer-use-prompt-guide.md",
        "Prompt-writing guide",
        "Operate",
        ("user", "technical"),
        "Write tasks that are bounded, observable, and easier to verify.",
    ),
    SourceDoc(
        "deployment",
        "docs/deployment.md",
        "Deployment guide",
        "Operate",
        ("user", "technical", "business"),
        "Local operation, public binding, persistence, backup, and rollback preparation.",
    ),
    SourceDoc(
        "handbook",
        "docs/zero-to-hero-study-handbook.md",
        "Zero to Hero technical handbook",
        "Understand",
        ("technical", "user"),
        "Computer Use theory, architecture, execution flows, and study exercises.",
    ),
    SourceDoc(
        "technical",
        "TECHNICAL.md",
        "Technical architecture",
        "Understand",
        ("technical",),
        "Current runtime boundaries, public contracts, authentication, and quality gates.",
    ),
    SourceDoc(
        "architecture",
        "docs/codebase/ARCHITECTURE.md",
        "Architecture map",
        "Understand",
        ("technical", "business"),
        "Verified layers, runtime flow, patterns, and architectural risks.",
    ),
    SourceDoc(
        "structure",
        "docs/codebase/STRUCTURE.md",
        "Repository structure",
        "Understand",
        ("technical",),
        "Entry points, module boundaries, directories, and naming organization.",
    ),
    SourceDoc(
        "stack",
        "docs/codebase/STACK.md",
        "Technology stack",
        "Understand",
        ("technical", "business"),
        "Runtime, dependencies, infrastructure, toolchain, and configuration.",
    ),
    SourceDoc(
        "integrations",
        "docs/codebase/INTEGRATIONS.md",
        "Integrations",
        "Understand",
        ("technical", "business"),
        "Provider APIs, local stores, credentials, reliability, and observability.",
    ),
    SourceDoc(
        "conventions",
        "docs/codebase/CONVENTIONS.md",
        "Engineering conventions",
        "Understand",
        ("technical",),
        "Naming, formatting, contracts, errors, logging, and testing discipline.",
    ),
    SourceDoc(
        "security",
        "SECURITY.md",
        "Security policy",
        "Trust and verify",
        ("all",),
        "Supported versions and responsible vulnerability reporting.",
    ),
    SourceDoc(
        "testing",
        "docs/codebase/TESTING.md",
        "Testing and quality",
        "Trust and verify",
        ("technical", "business"),
        "Test layers, commands, isolation strategy, and known coverage gaps.",
    ),
    SourceDoc(
        "concerns",
        "docs/codebase/CONCERNS.md",
        "Known concerns",
        "Trust and verify",
        ("technical", "business"),
        "Prioritized risks, privacy watchpoints, and change discipline.",
    ),
    SourceDoc(
        "migration",
        "docs/migration-v2.md",
        "v2 migration",
        "Evidence and history",
        ("technical",),
        "Contract and operational migration guidance.",
        True,
    ),
    SourceDoc(
        "rollback",
        "docs/rollback-v2.md",
        "v2 rollback",
        "Evidence and history",
        ("technical", "business"),
        "Rollback triggers, procedure, and verification.",
        True,
    ),
    SourceDoc(
        "release",
        "docs/release-notes-v3.1.0.md",
        "v3.1.0 release notes",
        "Evidence and history",
        ("all",),
        "Current patch scope, dependency audit, and remaining CI gates.",
    ),
    SourceDoc(
        "release-v302",
        "docs/release-notes-v3.0.2.md",
        "v3.0.2 release notes",
        "Evidence and history",
        ("all",),
        "Historical v3.0.2 CI and sandbox notes.",
        True,
    ),
    SourceDoc(
        "release-v301",
        "docs/release-notes-v3.0.1.md",
        "v3.0.1 release notes",
        "Evidence and history",
        ("all",),
        "Historical v3.0.1 Windows launcher fixes.",
        True,
    ),
    SourceDoc(
        "release-v3",
        "docs/release-notes-v3.0.0.md",
        "v3.0.0 release notes",
        "Evidence and history",
        ("all",),
        "Historical v3.0.0 release scope.",
        True,
    ),
    SourceDoc(
        "release-v2",
        "docs/release-notes-v2.0.0.md",
        "v2.0.0 release notes",
        "Evidence and history",
        ("all",),
        "Historical v2 release scope.",
        True,
    ),
    SourceDoc(
        "research",
        "docs/research-audit-2026-07-23.md",
        "Provider research audit",
        "Evidence and history",
        ("technical", "business"),
        "Dated evidence behind the supported provider catalog.",
        True,
    ),
    SourceDoc(
        "gemini-evaluation",
        "docs/gemini-successor-evaluation.md",
        "Gemini successor checklist",
        "Evidence and history",
        ("technical", "business"),
        "Acceptance criteria for future Gemini route changes.",
        True,
    ),
)

TRACKS = {
    "user": ("overview", "user-manual", "prompt-guide", "deployment"),
    "technical": ("handbook", "technical", "architecture", "structure", "testing"),
    "business": ("business", "security", "concerns", "deployment"),
}


def find_pandoc() -> str:
    found = shutil.which("pandoc")
    if found:
        return found
    if os.name == "nt":
        candidate = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Pandoc" / "pandoc.exe"
        )
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("Pandoc is required. Install it and ensure 'pandoc' is on PATH.")


def source_paths() -> dict[Path, SourceDoc]:
    return {(ROOT / source.path).resolve(): source for source in SOURCES}


def validate_sources() -> None:
    missing = [source.path for source in SOURCES if not (ROOT / source.path).is_file()]
    if missing:
        raise RuntimeError("Missing handbook sources: " + ", ".join(missing))
    keys = [source.key for source in SOURCES]
    if len(keys) != len(set(keys)):
        raise RuntimeError("Handbook source keys must be unique")
    unknown = sorted({key for route in TRACKS.values() for key in route} - set(keys))
    if unknown:
        raise RuntimeError("Track references unknown source keys: " + ", ".join(unknown))


def rewrite_links(fragment: str, source: SourceDoc, included: dict[Path, SourceDoc]) -> str:
    source_path = ROOT / source.path

    def replace(match: re.Match[str]) -> str:
        href = html.unescape(match.group(1))
        if href.startswith(("#", "http://", "https://", "mailto:")):
            return match.group(0)
        target_text, separator, anchor = href.partition("#")
        target = (source_path.parent / target_text).resolve()
        included_source = included.get(target)
        if included_source:
            destination = (
                f"#{included_source.key}-{anchor}"
                if separator and anchor
                else f"#doc-{included_source.key}"
            )
            return f'href="{destination}"'
        try:
            relative = os.path.relpath(target, DEFAULT_OUTPUT.parent).replace("\\", "/")
        except ValueError:
            return match.group(0)
        destination = relative + (f"#{anchor}" if separator else "")
        return f'href="{html.escape(destination, quote=True)}"'

    return re.sub(r'href="([^"]+)"', replace, fragment)


def convert_source(source: SourceDoc, pandoc: str, included: dict[Path, SourceDoc]) -> str:
    result = subprocess.run(
        [
            pandoc,
            "-f",
            "gfm",
            "-t",
            "html5",
            "--section-divs",
            f"--id-prefix={source.key}-",
            str(ROOT / source.path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return rewrite_links(result.stdout, source, included)


def content_fingerprint() -> str:
    digest = hashlib.sha256()
    for source in SOURCES:
        digest.update(source.path.encode())
        digest.update((ROOT / source.path).read_bytes())
    return digest.hexdigest()[:12]


def render_navigation() -> str:
    groups: dict[str, list[SourceDoc]] = {}
    for source in SOURCES:
        groups.setdefault(source.group, []).append(source)
    rendered: list[str] = []
    for group, sources in groups.items():
        rendered.append(f'<section class="nav-group"><h2>{html.escape(group)}</h2>')
        for source in sources:
            audiences = " ".join(source.audiences)
            rendered.append(
                f'<a class="nav-link" href="#doc-{source.key}" data-audiences="{audiences}">'
                f"<span>{html.escape(source.title)}</span><small>{html.escape(source.summary)}</small></a>"
            )
        rendered.append("</section>")
    return "".join(rendered)


def render_track_routes() -> str:
    by_key = {source.key: source for source in SOURCES}
    routes: list[str] = []
    for track, keys in TRACKS.items():
        steps = "".join(
            f'<a href="#doc-{key}"><span>{index}</span>{html.escape(by_key[key].title)}</a>'
            for index, key in enumerate(keys, 1)
        )
        routes.append(f'<div class="track-route" data-track-route="{track}">{steps}</div>')
    return "".join(routes)


def render_articles(pandoc: str) -> str:
    included = source_paths()
    rendered: list[str] = []
    for source in SOURCES:
        fragment = convert_source(source, pandoc, included)
        audiences = " ".join(source.audiences)
        label = f'<p class="source-label">{html.escape(source.group)} · Source: {html.escape(source.path)}</p>'
        navigation = '<footer class="lesson-nav"><button type="button" data-previous>Previous</button><button type="button" data-next>Next</button></footer>'
        if source.historical:
            rendered.append(
                f'<details class="manual-entry evidence-entry" id="doc-{source.key}" data-audiences="{audiences}">'
                f"<summary><span>{html.escape(source.title)}</span><small>{html.escape(source.summary)}</small></summary>"
                f'<div class="entry-body">{label}{fragment}{navigation}</div></details>'
            )
        else:
            rendered.append(
                f'<article class="manual-entry" id="doc-{source.key}" data-audiences="{audiences}">'
                f"{label}{fragment}{navigation}</article>"
            )
    return "".join(rendered)


def build_site(pandoc: str | None = None) -> str:
    validate_sources()
    pandoc = pandoc or find_pandoc()
    return (
        TEMPLATE.replace("__NAVIGATION__", render_navigation())
        .replace("__TRACK_ROUTES__", render_track_routes())
        .replace("__ARTICLES__", render_articles(pandoc))
        .replace("__FINGERPRINT__", content_fingerprint())
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if the committed handbook is stale"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        rendered = build_site()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print(f"Handbook is stale: run {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"Handbook is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Built {len(SOURCES)} source documents -> {args.output}")
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="A complete operator, technical, and business handbook for Computer Use Workbench.">
<title>Computer Use Workbench · Zero to Hero</title>
<style>
:root {
  color-scheme: light;
  --canvas: #f3f5f4;
  --paper: #ffffff;
  --panel: #e4e9e8;
  --ink: #17212b;
  --muted: #52616d;
  --line: #c8d1d0;
  --signal: #0b6173;
  --signal-soft: #d8eaed;
  --copper: #b65c2a;
  --code: #111b23;
  --code-ink: #e8f0ee;
  --shadow: 0 18px 50px rgb(23 33 43 / 10%);
}
[data-theme="dark"] {
  color-scheme: dark;
  --canvas: #0e161d;
  --paper: #131e26;
  --panel: #1b2a33;
  --ink: #e7ecea;
  --muted: #9fb0b7;
  --line: #344650;
  --signal: #54b5c5;
  --signal-soft: #173944;
  --copper: #e08a54;
  --code: #091016;
  --code-ink: #edf4f2;
  --shadow: 0 18px 50px rgb(0 0 0 / 28%);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 5.5rem; }
body {
  margin: 0;
  background: var(--canvas);
  color: var(--ink);
  font: 16px/1.7 "Segoe UI Variable", Aptos, "Segoe UI", system-ui, sans-serif;
}
button, input { font: inherit; }
button, a { -webkit-tap-highlight-color: transparent; }
a { color: var(--signal); text-underline-offset: .18em; }
a:hover { text-decoration-thickness: 2px; }
:focus-visible { outline: 3px solid var(--copper); outline-offset: 3px; }
.skip-link { position: fixed; left: 1rem; top: -5rem; z-index: 100; padding: .65rem 1rem; background: var(--ink); color: var(--paper); }
.skip-link:focus { top: 1rem; }
.progress { position: fixed; inset: 0 0 auto; z-index: 80; height: 3px; background: transparent; }
.progress span { display: block; width: 0; height: 100%; background: var(--copper); }
.topbar {
  position: sticky; top: 0; z-index: 60; display: grid; grid-template-columns: auto 1fr auto;
  align-items: center; gap: 1rem; min-height: 4.4rem; padding: .65rem 1rem;
  border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--canvas) 92%, transparent); backdrop-filter: blur(14px);
}
.brand { display: flex; align-items: center; gap: .75rem; color: var(--ink); text-decoration: none; }
.brand-mark { display: grid; place-items: center; width: 2.4rem; aspect-ratio: 1; border: 1px solid var(--signal); color: var(--signal); font: 700 .75rem/1 "Cascadia Mono", Consolas, monospace; }
.brand strong { display: block; font: 650 1rem/1.05 Bahnschrift, "Arial Narrow", sans-serif; letter-spacing: .03em; text-transform: uppercase; }
.brand small { color: var(--muted); font-size: .72rem; }
.track-switcher { justify-self: center; display: flex; gap: .25rem; padding: .25rem; border: 1px solid var(--line); background: var(--paper); }
.track-switcher button, .utility-button, .menu-button, .lesson-nav button {
  border: 0; background: transparent; color: var(--muted); min-height: 2.5rem; padding: .45rem .8rem; cursor: pointer;
}
.track-switcher button[aria-pressed="true"] { background: var(--signal); color: #fff; }
.top-actions { display: flex; gap: .25rem; }
.utility-button, .menu-button { border: 1px solid var(--line); color: var(--ink); background: var(--paper); }
.menu-button { display: none; }
.shell { display: grid; grid-template-columns: minmax(14rem, 18rem) minmax(0, 54rem) minmax(12rem, 15rem); gap: clamp(1rem, 2.5vw, 2.5rem); max-width: 100rem; margin: 0 auto; padding: 2rem clamp(1rem, 3vw, 3rem) 6rem; }
.sidebar, .page-toc { position: sticky; top: 6.3rem; align-self: start; max-height: calc(100vh - 7.4rem); overflow: auto; scrollbar-width: thin; }
.sidebar-header { margin-bottom: 1rem; }
.sidebar-tracks { display: none; margin-bottom: 1rem; }
.eyebrow, .source-label { margin: 0 0 .65rem; color: var(--copper); font: 700 .72rem/1.3 "Cascadia Mono", Consolas, monospace; letter-spacing: .08em; text-transform: uppercase; }
.sidebar-header p:last-child { color: var(--muted); font-size: .84rem; }
.nav-group { padding: 1rem 0; border-top: 1px solid var(--line); }
.nav-group h2, .page-toc h2 { margin: 0 0 .6rem; color: var(--muted); font: 700 .72rem/1.3 "Cascadia Mono", Consolas, monospace; letter-spacing: .08em; text-transform: uppercase; }
.nav-link { display: block; margin: .15rem 0; padding: .55rem .65rem; border-left: 2px solid transparent; color: var(--ink); text-decoration: none; }
.nav-link span { display: block; font-size: .88rem; font-weight: 650; }
.nav-link small { display: none; color: var(--muted); font-size: .75rem; line-height: 1.35; }
.nav-link:hover, .nav-link[aria-current="page"] { border-left-color: var(--signal); background: var(--signal-soft); }
.nav-link[aria-current="page"] small { display: block; margin-top: .25rem; }
.nav-link[hidden] { display: none; }
.content { min-width: 0; }
.hero { position: relative; overflow: hidden; margin-bottom: 2rem; padding: clamp(1.5rem, 4vw, 3.4rem); border: 1px solid var(--line); background: var(--paper); box-shadow: var(--shadow); }
.hero::after { content: "CUA"; position: absolute; right: -1.2rem; top: -2.1rem; color: var(--panel); font: 800 clamp(7rem, 18vw, 13rem)/1 Bahnschrift, "Arial Narrow", sans-serif; letter-spacing: -.08em; pointer-events: none; }
.hero > * { position: relative; z-index: 1; }
.hero h1 { max-width: 14ch; margin: .2rem 0 1rem; font: 650 clamp(2.7rem, 7vw, 5.6rem)/.9 Bahnschrift, "Arial Narrow", sans-serif; letter-spacing: -.035em; text-transform: uppercase; }
.hero-lede { max-width: 62ch; color: var(--muted); font-size: 1.08rem; }
.route-status { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1.2rem 0; }
.route-status span { padding: .35rem .55rem; border: 1px solid var(--line); background: var(--canvas); font: 650 .75rem/1.2 "Cascadia Mono", Consolas, monospace; }
.route-status span::before { content: ""; display: inline-block; width: .5rem; height: .5rem; margin-right: .45rem; border-radius: 50%; background: var(--signal); }
.track-route { display: none; grid-template-columns: repeat(4, 1fr); margin-top: 1.5rem; border-block: 1px solid var(--line); }
.track-route.active { display: grid; }
.track-route a { display: flex; gap: .55rem; align-items: center; min-height: 4.5rem; padding: .75rem; border-right: 1px solid var(--line); color: var(--ink); text-decoration: none; font-size: .82rem; font-weight: 650; }
.track-route a:last-child { border-right: 0; }
.track-route a:hover { background: var(--signal-soft); }
.track-route span { color: var(--copper); font: 700 .72rem "Cascadia Mono", Consolas, monospace; }
.trace-panel { margin: 2rem 0; padding: 1.25rem; border: 1px solid var(--line); background: var(--ink); color: #eef5f3; }
.trace-panel header { display: flex; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
.trace-panel h2 { margin: 0; font: 650 1.2rem Bahnschrift, sans-serif; text-transform: uppercase; letter-spacing: .04em; }
.trace-panel p { margin: 0; color: #b6c6ca; font-size: .82rem; }
.trace-rail { display: grid; grid-template-columns: repeat(6, 1fr); }
.trace-rail a { position: relative; padding: 2.1rem .45rem .5rem; color: #eef5f3; text-align: center; text-decoration: none; font: 650 .75rem "Cascadia Mono", Consolas, monospace; }
.trace-rail a::before { content: ""; position: absolute; left: 50%; top: .65rem; width: .7rem; height: .7rem; border: 2px solid #eef5f3; background: var(--ink); transform: translateX(-50%) rotate(45deg); z-index: 2; }
.trace-rail a::after { content: ""; position: absolute; left: 50%; right: -50%; top: 1rem; height: 1px; background: #55717a; }
.trace-rail a:last-child::after { display: none; }
.trace-rail a:hover::before { background: var(--copper); }
.manual-entry { display: block; margin: 0 0 2rem; padding: clamp(1.25rem, 4vw, 3rem); border: 1px solid var(--line); background: var(--paper); box-shadow: 0 8px 30px rgb(23 33 43 / 5%); }
.manual-entry h1, .manual-entry h2, .manual-entry h3 { scroll-margin-top: 6rem; color: var(--ink); }
.manual-entry h1 { margin: .2rem 0 1.25rem; font: 650 clamp(2rem, 5vw, 3.6rem)/1 Bahnschrift, "Arial Narrow", sans-serif; letter-spacing: -.025em; text-transform: uppercase; }
.manual-entry h2 { margin: 2.8rem 0 .75rem; padding-top: .8rem; border-top: 1px solid var(--line); font: 650 1.75rem/1.1 Bahnschrift, sans-serif; }
.manual-entry h3 { margin: 2rem 0 .55rem; font-size: 1.12rem; }
.manual-entry p, .manual-entry li { max-width: 76ch; }
.manual-entry blockquote { margin: 1.4rem 0; padding: .8rem 1rem; border-left: 4px solid var(--copper); background: var(--panel); }
.manual-entry table { display: block; width: 100%; overflow-x: auto; border-collapse: collapse; font-size: .88rem; }
.manual-entry th, .manual-entry td { min-width: 8rem; padding: .65rem .8rem; border: 1px solid var(--line); text-align: left; vertical-align: top; }
.manual-entry th { background: var(--panel); }
.manual-entry code { padding: .12em .35em; border-radius: 3px; background: var(--panel); font: .88em/1.5 "Cascadia Mono", Consolas, monospace; }
.manual-entry pre { position: relative; max-width: 100%; overflow: auto; padding: 1.2rem; background: var(--code); color: var(--code-ink); border-left: 3px solid var(--signal); }
.manual-entry pre code { padding: 0; background: transparent; color: inherit; }
.copy-code { position: absolute; right: .5rem; top: .5rem; border: 1px solid #526873; background: #17252e; color: #eef5f3; padding: .3rem .55rem; cursor: pointer; font-size: .72rem; }
.lesson-nav { display: flex; justify-content: space-between; margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line); }
.lesson-nav button { border: 1px solid var(--line); background: var(--canvas); color: var(--ink); }
.lesson-nav button:disabled { visibility: hidden; }
.evidence-entry { padding: 0; }
.evidence-entry > summary { display: grid; gap: .25rem; padding: 1.1rem 1.3rem; cursor: pointer; list-style: none; }
.evidence-entry > summary::-webkit-details-marker { display: none; }
.evidence-entry > summary span { font-weight: 750; }
.evidence-entry > summary small { color: var(--muted); }
.evidence-entry > summary::after { content: "+"; grid-column: 2; grid-row: 1 / 3; align-self: center; color: var(--copper); font-size: 1.5rem; }
.evidence-entry[open] > summary::after { content: "-"; }
.entry-body { padding: 0 clamp(1.25rem, 4vw, 3rem) clamp(1.25rem, 4vw, 3rem); border-top: 1px solid var(--line); }
.page-toc { padding-left: 1rem; border-left: 1px solid var(--line); }
.page-toc a { display: block; padding: .35rem 0; color: var(--muted); font-size: .78rem; line-height: 1.35; text-decoration: none; }
.page-toc a[data-level="3"] { padding-left: .75rem; }
.page-toc a:hover { color: var(--signal); }
.build-note { margin-top: 2rem; color: var(--muted); font: .7rem/1.5 "Cascadia Mono", Consolas, monospace; }
dialog { width: min(42rem, calc(100% - 2rem)); max-height: min(42rem, calc(100vh - 2rem)); padding: 0; border: 1px solid var(--line); background: var(--paper); color: var(--ink); box-shadow: var(--shadow); }
dialog::backdrop { background: rgb(5 12 17 / 70%); backdrop-filter: blur(4px); }
.search-head { display: grid; grid-template-columns: 1fr auto; gap: .75rem; padding: 1rem; border-bottom: 1px solid var(--line); }
.search-head input { width: 100%; min-height: 2.8rem; padding: .6rem .8rem; border: 1px solid var(--line); background: var(--canvas); color: var(--ink); }
.search-head button { border: 1px solid var(--line); background: var(--paper); color: var(--ink); cursor: pointer; }
.search-results { max-height: 31rem; overflow: auto; padding: .5rem; }
.search-result { display: block; width: 100%; padding: .75rem; border: 0; border-bottom: 1px solid var(--line); background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
.search-result strong, .search-result small { display: block; }
.search-result small { margin-top: .2rem; color: var(--muted); }
.search-result:hover { background: var(--signal-soft); }
.search-empty { padding: 2rem; color: var(--muted); text-align: center; }
@media (max-width: 1180px) {
  .shell { grid-template-columns: minmax(14rem, 17rem) minmax(0, 1fr); }
  .page-toc { display: none; }
}
@media (max-width: 780px) {
  html { scroll-padding-top: 4.8rem; }
  .topbar { grid-template-columns: auto 1fr auto; }
  .brand small, .track-switcher { display: none; }
  .sidebar-tracks { display: flex; }
  .menu-button { display: inline-block; }
  .utility-button span { display: none; }
  .shell { display: block; padding: 1rem 1rem 5rem; }
  .sidebar { position: fixed; inset: 4.4rem auto 0 0; z-index: 55; width: min(22rem, 88vw); max-height: none; padding: 1rem; border-right: 1px solid var(--line); background: var(--canvas); transform: translateX(-105%); transition: transform .2s ease; }
  body.nav-open .sidebar { transform: translateX(0); box-shadow: var(--shadow); }
  .hero h1 { font-size: clamp(2.7rem, 15vw, 4.4rem); }
  .track-route.active { grid-template-columns: 1fr 1fr; }
  .track-route a:nth-child(2) { border-right: 0; }
  .trace-panel header { display: block; }
  .trace-rail { grid-template-columns: repeat(3, 1fr); row-gap: 1rem; }
  .trace-rail a:nth-child(3)::after { display: none; }
  .manual-entry { padding: 1.15rem; }
  .evidence-entry { padding: 0; }
  .entry-body { padding: 0 1.15rem 1.15rem; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
@media print {
  .topbar, .sidebar, .page-toc, .progress, .trace-panel, .track-route, .lesson-nav, .copy-code, dialog { display: none !important; }
  body, .shell { display: block; background: #fff; color: #000; }
  .shell { max-width: none; padding: 0; }
  .hero, .manual-entry { break-inside: avoid; box-shadow: none; border-color: #bbb; }
  .manual-entry { display: block !important; }
  a { color: #000; text-decoration: none; }
  a[href^="http"]::after { content: " (" attr(href) ")"; font-size: .8em; }
}
</style>
</head>
<body>
<a class="skip-link" href="#main-content">Skip to handbook</a>
<div class="progress" aria-hidden="true"><span></span></div>
<header class="topbar">
  <a class="brand" href="#top"><span class="brand-mark">CU</span><span><strong>Computer Use</strong><small>Zero to Hero field guide</small></span></a>
  <div class="track-switcher" aria-label="Reading track">
    <button type="button" data-track="user" aria-pressed="true">GitHub user</button>
    <button type="button" data-track="technical" aria-pressed="false">Technical</button>
    <button type="button" data-track="business" aria-pressed="false">Business</button>
  </div>
  <div class="top-actions">
    <button class="menu-button" type="button" aria-label="Open chapters" aria-expanded="false">Menu</button>
    <button class="utility-button" type="button" data-search aria-label="Search handbook">⌕ <span>Search</span></button>
    <button class="utility-button" type="button" data-theme-toggle aria-label="Toggle color theme">◐ <span>Theme</span></button>
    <button class="utility-button" type="button" data-print aria-label="Print handbook">↧ <span>Print</span></button>
  </div>
</header>
<div class="shell" id="top">
  <nav class="sidebar" aria-label="Handbook chapters">
    <div class="sidebar-header"><p class="eyebrow">Select your route</p><p>Navigation adapts to your role. Search always covers the complete handbook.</p></div>
    <div class="track-switcher sidebar-tracks" aria-label="Mobile reading track">
      <button type="button" data-track="user" aria-pressed="true">GitHub user</button>
      <button type="button" data-track="technical" aria-pressed="false">Technical</button>
      <button type="button" data-track="business" aria-pressed="false">Business</button>
    </div>
    __NAVIGATION__
    <p class="build-note">Offline build · source fingerprint __FINGERPRINT__</p>
  </nav>
  <main class="content" id="main-content">
    <section class="hero" aria-labelledby="hero-title">
      <p class="eyebrow">Local AI operator workbench · Complete manual</p>
      <h1 id="hero-title">From first run to full system understanding.</h1>
      <p class="hero-lede">Learn to operate, evaluate, and extend a provider-native Computer Use agent. Choose a reading track, then follow the verified source all the way from a task prompt to an audited sandbox action.</p>
      <div class="route-status" aria-label="Supported model routes"><span>GPT-5.6 Luna</span><span>GPT-5.6 Terra</span><span>Claude Sonnet 5</span><span>Gemini 3.7 Flash</span><span>Gemini 3.5 Flash-Lite</span></div>
      __TRACK_ROUTES__
    </section>
    <section class="trace-panel" aria-labelledby="trace-title">
      <header><h2 id="trace-title">Follow one run</h2><p>The handbook's live index: select a stage to inspect its contract and implementation.</p></header>
      <div class="trace-rail"><a href="#doc-user-manual">Task</a><a href="#doc-technical">API</a><a href="#doc-architecture">Route</a><a href="#doc-handbook">Provider</a><a href="#doc-architecture">Sandbox</a><a href="#doc-testing">Audit</a></div>
    </section>
    __ARTICLES__
  </main>
  <aside class="page-toc" aria-label="On this page"><h2>On this page</h2><div data-page-toc></div></aside>
</div>
<dialog data-search-dialog aria-label="Search handbook">
  <div class="search-head"><input type="search" aria-label="Search handbook" placeholder="Search concepts, commands, files…" autocomplete="off"><button type="button" data-search-close aria-label="Close search">Close</button></div>
  <div class="search-results" aria-live="polite"><p class="search-empty">Type at least two characters.</p></div>
</dialog>
<script>
(() => {
  const root = document.documentElement;
  const body = document.body;
  const entries = [...document.querySelectorAll('.manual-entry')];
  const navLinks = [...document.querySelectorAll('.nav-link')];
  const trackButtons = [...document.querySelectorAll('[data-track]')];
  const routes = [...document.querySelectorAll('[data-track-route]')];
  const toc = document.querySelector('[data-page-toc]');
  const progress = document.querySelector('.progress span');
  const menu = document.querySelector('.menu-button');
  const searchDialog = document.querySelector('[data-search-dialog]');
  const searchInput = searchDialog.querySelector('input');
  const searchResults = searchDialog.querySelector('.search-results');
  const storage = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch {} }
  };

  function openAncestors(element) {
    let details = element && element.closest('details');
    while (details) { details.open = true; details = details.parentElement && details.parentElement.closest('details'); }
  }

  function goTo(id) {
    const target = document.getElementById(id);
    if (!target) return;
    openAncestors(target);
    searchDialog.close();
    body.classList.remove('nav-open');
    menu.setAttribute('aria-expanded', 'false');
    target.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  }

  function setTrack(track) {
    trackButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.track === track)));
    routes.forEach(route => route.classList.toggle('active', route.dataset.trackRoute === track));
    navLinks.forEach(link => {
      const audiences = link.dataset.audiences.split(' ');
      link.hidden = !(audiences.includes('all') || audiences.includes(track));
    });
    storage.set('cua-handbook:track', track);
    updateLessonButtons();
  }

  function setCurrent(entry) {
    if (!entry) return;
    navLinks.forEach(link => link.toggleAttribute('aria-current', link.getAttribute('href') === `#${entry.id}`));
    const headings = [...entry.querySelectorAll('h2[id], h3[id]')];
    toc.replaceChildren(...headings.slice(0, 28).map(heading => {
      const link = document.createElement('a');
      link.href = `#${heading.id}`;
      link.textContent = heading.textContent;
      link.dataset.level = heading.tagName.slice(1);
      return link;
    }));
  }

  function visibleLinks() { return navLinks.filter(link => !link.hidden); }
  function updateLessonButtons() {
    const links = visibleLinks();
    entries.forEach(entry => {
      const index = links.findIndex(link => link.getAttribute('href') === `#${entry.id}`);
      const previous = entry.querySelector('[data-previous]');
      const next = entry.querySelector('[data-next]');
      previous.disabled = index <= 0;
      next.disabled = index < 0 || index >= links.length - 1;
      previous.onclick = () => index > 0 && goTo(links[index - 1].hash.slice(1));
      next.onclick = () => index >= 0 && index < links.length - 1 && goTo(links[index + 1].hash.slice(1));
    });
  }

  trackButtons.forEach(button => button.addEventListener('click', () => setTrack(button.dataset.track)));
  setTrack(storage.get('cua-handbook:track') || 'user');

  const savedTheme = storage.get('cua-handbook:theme');
  root.dataset.theme = savedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.querySelector('[data-theme-toggle]').addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    storage.set('cua-handbook:theme', root.dataset.theme);
  });
  document.querySelector('[data-print]').addEventListener('click', () => window.print());

  menu.addEventListener('click', () => {
    const open = body.classList.toggle('nav-open');
    menu.setAttribute('aria-expanded', String(open));
  });
  navLinks.forEach(link => link.addEventListener('click', () => {
    const id = link.hash.slice(1);
    openAncestors(document.getElementById(id));
    body.classList.remove('nav-open');
    menu.setAttribute('aria-expanded', 'false');
  }));

  entries.forEach(entry => entry.querySelectorAll('pre').forEach(pre => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'copy-code';
    button.textContent = 'Copy';
    button.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(pre.querySelector('code')?.textContent || pre.textContent); button.textContent = 'Copied'; }
      catch { button.textContent = 'Select text'; }
      setTimeout(() => { button.textContent = 'Copy'; }, 1400);
    });
    pre.append(button);
  }));

  function openSearch() { searchDialog.showModal(); searchInput.focus(); }
  document.querySelector('[data-search]').addEventListener('click', openSearch);
  document.querySelector('[data-search-close]').addEventListener('click', () => searchDialog.close());
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openSearch(); }
    if (event.key === 'Escape' && searchDialog.open) searchDialog.close();
  });

  const searchTargets = entries.flatMap(entry => {
    const source = navLinks.find(link => link.hash === `#${entry.id}`)?.querySelector('span')?.textContent || 'Handbook';
    const headings = [...entry.querySelectorAll('h1[id], h2[id], h3[id]')];
    return headings.map(heading => ({ id: heading.id, title: heading.textContent.trim(), source, text: heading.parentElement.textContent.toLowerCase() }));
  });
  searchInput.addEventListener('input', () => {
    const query = searchInput.value.trim().toLowerCase();
    searchResults.replaceChildren();
    if (query.length < 2) { searchResults.innerHTML = '<p class="search-empty">Type at least two characters.</p>'; return; }
    const matches = searchTargets.filter(item => item.title.toLowerCase().includes(query) || item.text.includes(query)).slice(0, 30);
    if (!matches.length) { searchResults.innerHTML = '<p class="search-empty">No matching chapter. Try a model, command, API, or file name.</p>'; return; }
    matches.forEach(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'search-result';
      const title = document.createElement('strong'); title.textContent = item.title;
      const source = document.createElement('small'); source.textContent = item.source;
      button.append(title, source);
      button.addEventListener('click', () => goTo(item.id));
      searchResults.append(button);
    });
  });

  const observer = new IntersectionObserver(records => {
    const visible = records.filter(record => record.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (visible) setCurrent(visible.target);
  }, { rootMargin: '-15% 0px -70%', threshold: [0, .05, .2] });
  entries.forEach(entry => observer.observe(entry));
  setCurrent(entries[0]);

  addEventListener('scroll', () => {
    const total = document.documentElement.scrollHeight - innerHeight;
    progress.style.width = `${total > 0 ? Math.min(100, scrollY / total * 100) : 0}%`;
  }, { passive: true });
  addEventListener('beforeprint', () => document.querySelectorAll('details').forEach(details => { details.dataset.printOpen = String(details.open); details.open = true; }));
  addEventListener('afterprint', () => document.querySelectorAll('details').forEach(details => { details.open = details.dataset.printOpen === 'true'; }));
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
