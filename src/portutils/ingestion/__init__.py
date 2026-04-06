"""Data ingestion — fetch functions for APIs, scrapers, and feeds."""

from .reuters import Article, ArticleStub, fetch_article, fetch_section_headline

__all__ = ["Article", "ArticleStub", "fetch_article", "fetch_section_headline"]
