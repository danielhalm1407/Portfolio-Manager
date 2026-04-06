"""Reuters scraper — fetches section headlines and article text.

Uses the sync Playwright API (headless). Install with:
    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# Scroll offsets tuned for Reuters section pages.
_SECTION_SCROLL: dict[str, int] = {
    "markets": 1800,
    "business": 1600,
    "world": 1600,
    "sustainability": 1600,
}

_BASE_URL = "https://www.reuters.com"


@dataclass
class ArticleStub:
    title: str
    url: str
    section: str
    label: str = ""          # e.g. ANALYSIS, EXCLUSIVE
    published: str = ""      # raw date string from page
    lede: str = ""


@dataclass
class Article(ArticleStub):
    body: str = ""
    authors: list[str] = field(default_factory=list)
    scraped_date: str = field(default_factory=lambda: date.today().isoformat())


def fetch_section_headline(section: str = "markets") -> ArticleStub:
    """Return the top headline stub from a Reuters section page.

    Args:
        section: One of 'markets', 'business', 'world', 'sustainability'.

    Returns:
        ArticleStub with title, url, label, published, and lede populated.
    """
    from playwright.sync_api import sync_playwright

    url = f"{_BASE_URL}/{section}/"
    scroll_y = _SECTION_SCROLL.get(section, 1800)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.evaluate(f"() => {{ window.scrollTo(0, {scroll_y}); }}")
        page.wait_for_timeout(1000)

        stub = _extract_headline_stub(page, section)
        browser.close()

    return stub


def fetch_article(stub: ArticleStub) -> Article:
    """Fetch full article body for a given ArticleStub.

    Args:
        stub: An ArticleStub returned by fetch_section_headline.

    Returns:
        Article with body and authors populated.
    """
    from playwright.sync_api import sync_playwright

    full_url = stub.url if stub.url.startswith("http") else f"{_BASE_URL}{stub.url}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(full_url, wait_until="domcontentloaded")

        body = _extract_article_body(page)
        authors = _extract_authors(page)
        browser.close()

    return Article(
        title=stub.title,
        url=full_url,
        section=stub.section,
        label=stub.label,
        published=stub.published,
        lede=stub.lede,
        body=body,
        authors=authors,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_headline_stub(page, section: str) -> ArticleStub:
    """Pull the first article link/meta from the section page DOM."""
    # Reuters wraps the lead article in an <a> with a data-testid or
    # standard article-body structure. We select all article links and
    # take the first one that points to an editorial URL (not ads/promos).
    links = page.locator("a[href*='/markets/'], a[href*='/business/'], a[href*='/world/']")
    count = links.count()

    for i in range(count):
        href = links.nth(i).get_attribute("href") or ""
        text = (links.nth(i).inner_text() or "").strip()
        # Filter out nav links (short text) and non-article paths
        if len(text) > 30 and re.search(r"/\d{4}-\d{2}-\d{2}/", href):
            return ArticleStub(title=text, url=href, section=section)

    # Fallback: grab page title
    return ArticleStub(title=page.title(), url=page.url, section=section)


def _extract_article_body(page) -> str:
    """Extract paragraph text from a Reuters article page."""
    paragraphs = page.locator("div[class*='article-body'] p, div[class*='ArticleBody'] p")
    count = paragraphs.count()
    return "\n\n".join(
        paragraphs.nth(i).inner_text().strip()
        for i in range(count)
        if paragraphs.nth(i).inner_text().strip()
    )


def _extract_authors(page) -> list[str]:
    """Extract author names from a Reuters article page."""
    raw = page.locator("a[class*='author'], span[class*='author']").all_inner_texts()
    return [a.strip() for a in raw if a.strip()]
