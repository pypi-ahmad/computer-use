"""Build a static, multipage htmx site from every tracked .md file in the repo.

Regeneratable, source-of-truth-free build step (like build_release.py):
reads the current .md files via `git ls-files`, converts each with pandoc,
and writes a self-contained site to docs-site/dist/ (gitignored, matches
the existing dist/ ignore rule). Nothing here is committed as output.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs-site" / "dist"
PAGES = OUT / "pages"
HTMX_URL = "https://unpkg.com/htmx.org@2/dist/htmx.min.js"


def slug_for(md_path: str) -> str:
    return md_path.removesuffix(".md").replace("/", "__")


def title_for(md_path: str, html_fragment: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html_fragment, re.DOTALL)
    if match:
        return re.sub(r"<[^>]+>", "", match.group(1)).strip()
    return md_path


def rewrite_md_links(fragment: str, slug_by_path: dict[str, str]) -> str:
    """Point in-doc links to other tracked .md files at their generated page."""

    def replace(match: re.Match[str]) -> str:
        href = match.group(1)
        path, _, anchor = href.partition("#")
        path = path.lstrip("./")
        if path in slug_by_path:
            target = f"pages/{slug_by_path[path]}.html"
            return f'href="{target}{"#" + anchor if anchor else ""}"'
        return match.group(0)

    return re.sub(r'href="([^"]+\.md(?:#[^"]*)?)"', replace, fragment)


def main() -> int:
    md_files = sorted(
        subprocess.run(
            ["git", "ls-files", "*.md"], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.splitlines()
    )
    if not md_files:
        print("No tracked .md files found.")
        return 1

    if OUT.exists():
        shutil.rmtree(OUT)
    PAGES.mkdir(parents=True)

    slug_by_path = {path: slug_for(path) for path in md_files}
    pages: list[tuple[str, str, str]] = []  # (path, slug, title)

    for path in md_files:
        result = subprocess.run(
            ["pandoc", "-f", "gfm", "-t", "html5", str(ROOT / path)],
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        fragment = rewrite_md_links(result.stdout, slug_by_path)
        slug = slug_by_path[path]
        title = title_for(path, fragment)
        (PAGES / f"{slug}.html").write_text(fragment, encoding="utf-8")
        pages.append((path, slug, title))

    try:
        urllib.request.urlretrieve(HTMX_URL, OUT / "htmx.min.js")
    except Exception as exc:
        print(f"warning: could not fetch htmx.min.js ({exc}); site nav will not work offline")

    groups: dict[str, list[tuple[str, str, str]]] = {}
    for path, slug, title in pages:
        group = path.rsplit("/", 1)[0] if "/" in path else "(root)"
        groups.setdefault(group, []).append((path, slug, title))

    nav_html = []
    for group in sorted(groups, key=lambda g: (g != "(root)", g)):
        nav_html.append(f'<div class="nav-group"><h3>{html.escape(group)}</h3><ul>')
        for path, slug, title in sorted(groups[group], key=lambda item: item[2].lower()):
            nav_html.append(
                f'<li><a href="pages/{slug}.html" hx-get="pages/{slug}.html" '
                f'hx-target="#content" hx-push-url="true" '
                f'title="{html.escape(path)}">{html.escape(title)}</a></li>'
            )
        nav_html.append("</ul></div>")

    default_slug = slug_by_path.get("README.md", pages[0][1])
    (OUT / "index.html").write_text(
        INDEX_TEMPLATE.format(nav="".join(nav_html), default_slug=default_slug),
        encoding="utf-8",
    )

    print(f"Built {len(pages)} pages -> {OUT}")
    print("Serve it (htmx's fetches need http://, not file://):")
    print(f"  cd {OUT} && python -m http.server 8420")
    print("  then open http://127.0.0.1:8420/")
    return 0


INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>computer-use docs</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="htmx.min.js"></script>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; font: 15px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
  background: #10151c; color: #e6edf3; display: grid;
  grid-template-columns: 280px 1fr; min-height: 100vh;
}}
.sidebar {{
  border-right: 1px solid #2b3744; padding: 20px 16px; overflow-y: auto;
  height: 100vh; position: sticky; top: 0; background: #0d1218;
}}
.sidebar h1 {{ font-size: 15px; letter-spacing: .04em; margin: 0 0 18px; color: #3aa7ff; }}
.nav-group h3 {{
  font-size: 10px; text-transform: uppercase; letter-spacing: .1em;
  color: #6b7684; margin: 18px 0 6px;
}}
.nav-group ul {{ list-style: none; margin: 0; padding: 0; }}
.nav-group a {{
  display: block; padding: 6px 8px; border-radius: 4px; color: #c4ccd4;
  text-decoration: none; font-size: 13px; border-left: 2px solid transparent;
}}
.nav-group a:hover {{ background: #182430; color: #fff; }}
.nav-group a.active {{ color: #3aa7ff; border-left-color: #3aa7ff; background: #182430; }}
main {{ padding: 40px clamp(20px, 5vw, 72px); max-width: 900px; }}
#content h1 {{ font-size: 30px; border-bottom: 1px solid #2b3744; padding-bottom: 12px; }}
#content h2 {{ font-size: 22px; margin-top: 40px; }}
#content h3 {{ font-size: 17px; color: #cfd8e0; }}
#content a {{ color: #3aa7ff; }}
#content code {{ background: #1a232c; padding: 2px 5px; border-radius: 3px; font-size: 90%; }}
#content pre {{ background: #0d1218; border: 1px solid #2b3744; padding: 14px; overflow-x: auto; border-radius: 6px; }}
#content pre code {{ background: none; padding: 0; }}
#content table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }}
#content th, #content td {{ border: 1px solid #2b3744; padding: 7px 10px; text-align: left; }}
#content th {{ background: #182430; }}
#content blockquote {{ border-left: 3px solid #3aa7ff; margin: 0; padding: 4px 16px; color: #a6b2bd; }}
@media (max-width: 800px) {{ body {{ grid-template-columns: 1fr; }} .sidebar {{ position: static; height: auto; }} }}
</style>
</head>
<body>
<nav class="sidebar">
  <h1>computer-use / docs</h1>
  {nav}
</nav>
<main>
  <div id="content" hx-get="pages/{default_slug}.html" hx-trigger="load"></div>
</main>
<script>
function markActive(href) {{
  document.querySelectorAll('.sidebar a').forEach(function (a) {{
    a.classList.toggle('active', a.getAttribute('href') === href);
  }});
}}
document.body.addEventListener('click', function (e) {{
  var a = e.target.closest('.sidebar a');
  if (a) markActive(a.getAttribute('href'));
}});
markActive('pages/{default_slug}.html');
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
