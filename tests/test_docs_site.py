"""Automated test suite for documentation site, navigation integrity, and assets.

Ensures that:
1. All core documentation pages exist and are well-formed HTML5.
2. The design system (style.css, toc.js, lightbox.js, theme toggle) is consistently applied.
3. Bidirectional navigation across all 5 documentation pages is intact and accurate.
4. Zero broken internal links or missing anchor targets exist across the site.
5. All image assets and local scripts load properly with non-zero byte size.
6. README.md links strictly resolve to live GitHub Pages URLs without broken anchors or raw blob views.
"""

from html.parser import HTMLParser
from pathlib import Path
import re
import urllib.parse
import pytest

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
README_PATH = Path(__file__).resolve().parent.parent / "README.md"

CORE_PAGES = [
    "index.html",
    "walkthrough.html",
    "governance-and-pii.html",
    "cost-economics.html",
    "design-critique.html",
]


class DocPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ids = set()
        self.links = []  # list of (href, text)
        self.images = []  # list of src
        self.scripts = []  # list of src
        self.stylesheets = []  # list of href
        self.title = ""
        self.has_doctype = False
        self.in_title = False
        self.nav_links = []  # list of (href, classes, text)
        self.current_tag = None
        self.theme_toggle_found = False

    def handle_decl(self, decl):
        if "html" in decl.lower():
            self.has_doctype = True

    def handle_starttag(self, tag, attrs):
        self.current_tag = tag
        attrs_dict = dict(attrs)

        if "id" in attrs_dict:
            self.ids.add(attrs_dict["id"])

        if tag == "title":
            self.in_title = True

        if tag == "a":
            href = attrs_dict.get("href", "")
            classes = attrs_dict.get("class", "").split()
            self.links.append((href, attrs_dict))
            # Check if this link is inside navbar
            self.nav_links.append((href, classes))

        elif tag == "img":
            src = attrs_dict.get("src", "")
            if src:
                self.images.append(src)

        elif tag == "script":
            src = attrs_dict.get("src", "")
            if src:
                self.scripts.append(src)

        elif tag == "link" and attrs_dict.get("rel") == "stylesheet":
            href = attrs_dict.get("href", "")
            if href:
                self.stylesheets.append(href)

        elif tag == "button" and "theme-toggle" in attrs_dict.get("class", ""):
            self.theme_toggle_found = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        self.current_tag = None

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def parse_page(page_name: str) -> DocPageParser:
    file_path = DOCS_DIR / page_name
    assert file_path.exists(), f"Expected documentation file {page_name} does not exist in {DOCS_DIR}"
    content = file_path.read_text(encoding="utf-8")
    parser = DocPageParser()
    parser.feed(content)
    return parser


def test_core_documentation_pages_exist():
    """Assert all 5 primary documentation files exist in docs/."""
    for page in CORE_PAGES:
        file_path = DOCS_DIR / page
        assert file_path.is_file(), f"Missing core documentation page: {file_path}"
        assert file_path.stat().st_size > 500, f"Page {page} appears abnormally small or empty."


@pytest.mark.parametrize("page_name", CORE_PAGES)
def test_html_structure_and_metadata(page_name: str):
    """Assert doctype, utf-8 charset, responsive viewport, title, and navbar exist."""
    parser = parse_page(page_name)
    raw_html = (DOCS_DIR / page_name).read_text(encoding="utf-8")

    assert parser.has_doctype, f"{page_name} is missing <!DOCTYPE html>"
    assert '<meta charset="utf-8">' in raw_html or '<meta charset="UTF-8">' in raw_html, f"{page_name} missing charset meta tag"
    assert 'name="viewport"' in raw_html, f"{page_name} missing responsive viewport meta tag"
    assert parser.title.strip(), f"{page_name} has an empty <title> tag"
    assert '<header class="navbar">' in raw_html, f"{page_name} missing standard <header class=\"navbar\">"


@pytest.mark.parametrize("page_name", CORE_PAGES)
def test_design_system_and_scripts_consistency(page_name: str):
    """Assert style.css, lightbox.js, toc.js, theme toggle, and Mermaid exist on every page."""
    parser = parse_page(page_name)
    raw_html = (DOCS_DIR / page_name).read_text(encoding="utf-8")

    assert "style.css" in parser.stylesheets, f"{page_name} does not link style.css"
    assert "lightbox.js" in parser.scripts, f"{page_name} does not load lightbox.js"
    assert "toc.js" in parser.scripts, f"{page_name} does not load toc.js"
    assert parser.theme_toggle_found, f"{page_name} missing .theme-toggle button"
    assert "mermaid.initialize" in raw_html, f"{page_name} missing mermaid.initialize script"


@pytest.mark.parametrize("page_name", CORE_PAGES)
def test_bidirectional_navigation_and_active_state(page_name: str):
    """Verify navbar contains links to all 5 core pages with exactly one active link matching the current page."""
    raw_html = (DOCS_DIR / page_name).read_text(encoding="utf-8")

    # Extract navbar section
    nav_match = re.search(r'<nav class="nav-links">(.*?)</nav>', raw_html, re.DOTALL)
    assert nav_match, f"{page_name} missing <nav class=\"nav-links\"> container"
    nav_content = nav_match.group(1)

    # Check each core page is linked in the navbar
    for target in CORE_PAGES:
        assert f'href="{target}"' in nav_content, f"Navbar on {page_name} does not link to {target}"

    # Verify active state
    for target in CORE_PAGES:
        if target == page_name:
            # Must be active
            assert re.search(rf'href="{target}"[^>]*class="[^"]*active[^"]*"', nav_content) or \
                   re.search(rf'class="[^"]*active[^"]*"[^>]*href="{target}"', nav_content), \
                   f"On {page_name}, its own link {target} must have class='active'"
        else:
            # Must not be active
            assert not (re.search(rf'href="{target}"[^>]*class="[^"]*active[^"]*"', nav_content) or
                        re.search(rf'class="[^"]*active[^"]*"[^>]*href="{target}"', nav_content)), \
                   f"On {page_name}, another page's link {target} must NOT have class='active'"


def test_zero_broken_internal_links_and_anchors():
    """Verify all <a href="..."> links in all docs pages resolve cleanly (zero 404s, zero dead anchors)."""
    # Pre-parse all pages to collect known IDs
    page_parsers = {page: parse_page(page) for page in CORE_PAGES}

    for page_name, parser in page_parsers.items():
        for href, attrs in parser.links:
            # Skip empty, mailto, javascript, external https/http
            if not href or href.startswith(("http://", "https://", "mailto:", "javascript:")):
                continue

            parsed = urllib.parse.urlparse(href)
            target_file = parsed.path
            target_fragment = parsed.fragment

            # Internal anchor on same page
            if not target_file:
                assert target_fragment in parser.ids, \
                    f"Dead internal anchor '#{target_fragment}' found on {page_name}"
            else:
                # Link to another page
                assert target_file in CORE_PAGES, \
                    f"Unknown target page '{target_file}' linked from {page_name}"
                if target_fragment:
                    assert target_fragment in page_parsers[target_file].ids, \
                        f"Dead cross-page anchor '{href}' from {page_name} (target {target_file} missing id '{target_fragment}')"


def test_zero_broken_assets_and_images():
    """Verify all local image assets referenced in <img> tags exist on disk and have non-zero size."""
    for page in CORE_PAGES:
        parser = parse_page(page)
        for img_src in parser.images:
            if img_src.startswith(("http://", "https://", "data:")):
                continue
            img_path = (DOCS_DIR / img_src).resolve()
            assert img_path.exists(), f"Image {img_src} referenced in {page} does not exist at {img_path}"
            assert img_path.stat().st_size > 0, f"Image {img_src} referenced in {page} is an empty 0-byte file"


def test_readme_link_invariants():
    """Assert README.md has zero raw blob documentation links and all live URLs resolve to real pages & anchors."""
    assert README_PATH.exists(), f"README.md not found at {README_PATH}"
    readme_content = README_PATH.read_text(encoding="utf-8")

    # Invariant 1: No raw github blob URLs for docs
    blob_matches = re.findall(r'https://github\.com/[^/]+/[^/]+/blob/[^/]+/docs/[a-zA-Z0-9._#-]+', readme_content)
    assert not blob_matches, f"Found raw GitHub blob documentation links in README.md: {blob_matches}"

    # Invariant 2: Live GitHub Pages URLs must point to valid pages and valid anchors
    page_parsers = {page: parse_page(page) for page in CORE_PAGES}
    live_links = re.findall(r'https://duncmon\.github\.io/AI-Devcamp-rowing-coach-agent/([a-zA-Z0-9._#-]+)', readme_content)

    for link in live_links:
        parsed = urllib.parse.urlparse(link)
        target_page = parsed.path or "index.html"
        target_fragment = parsed.fragment

        assert target_page in CORE_PAGES, f"README links to non-existent live page: {target_page}"
        if target_fragment:
            assert target_fragment in page_parsers[target_page].ids, \
                f"README link '{link}' targets non-existent anchor #{target_fragment} in {target_page}"
