"""Scrape market commentary from Reuters and write to data/raw/commentary/.

Usage:
    python -m src.pipelines.scrape_commentary
    python -m src.pipelines.scrape_commentary --section markets --full

Output:
    data/raw/commentary/YYYY-MM-DD_reuters_<section>_headline.md
    data/raw/commentary/YYYY-MM-DD_reuters_<section>_full.md  (with --full)
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from portutils.ingestion.reuters import ArticleStub, fetch_article, fetch_section_headline
from portutils.utils.config import PROJECT_ROOT

OUTPUT_DIR = PROJECT_ROOT / "data" / "raw" / "commentary"


def _stub_to_markdown(stub: ArticleStub) -> str:
    lines = [
        f"# {stub.title}",
        "",
        f"- **Source:** Reuters / {stub.section.title()}",
        f"- **Label:** {stub.label or 'n/a'}",
        f"- **Published:** {stub.published or 'n/a'}",
        f"- **URL:** {stub.url}",
        "",
        stub.lede or "_No lede extracted._",
    ]
    return "\n".join(lines)


def _article_to_markdown(article) -> str:
    authors = ", ".join(article.authors) if article.authors else "n/a"
    lines = [
        f"# {article.title}",
        "",
        f"- **Source:** Reuters / {article.section.title()}",
        f"- **Label:** {article.label or 'n/a'}",
        f"- **Authors:** {authors}",
        f"- **Published:** {article.published or 'n/a'}",
        f"- **Scraped:** {article.scraped_date}",
        f"- **URL:** {article.url}",
        "",
        "## Body",
        "",
        article.body or "_No body text extracted._",
    ]
    return "\n".join(lines)


def run(section: str = "markets", full: bool = False) -> Path:
    today = date.today().isoformat()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching Reuters {section} headline...")
    stub = fetch_section_headline(section)
    print(f"  -> {stub.title}")

    if full:
        print("Fetching full article body...")
        article = fetch_article(stub)
        filename = f"{today}_reuters_{section}_full.md"
        content = _article_to_markdown(article)
    else:
        filename = f"{today}_reuters_{section}_headline.md"
        content = _stub_to_markdown(stub)

    out_path = OUTPUT_DIR / filename
    out_path.write_text(content, encoding="utf-8")
    print(f"Written: {out_path.relative_to(PROJECT_ROOT)}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Scrape Reuters section headline.")
    parser.add_argument("--section", default="markets",
                        choices=["markets", "business", "world", "sustainability"],
                        help="Reuters section to scrape (default: markets)")
    parser.add_argument("--full", action="store_true",
                        help="Fetch full article body, not just the headline stub")
    args = parser.parse_args()
    run(section=args.section, full=args.full)


if __name__ == "__main__":
    main()
