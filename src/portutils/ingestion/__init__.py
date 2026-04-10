"""Data ingestion — fetch functions for APIs, scrapers, and feeds."""

from .reuters import Article, ArticleStub, fetch_article, fetch_section_headline
from .ibkr_requests import get_equity_data, get_account_data

__all__ = [
    "Article", "ArticleStub", "fetch_article", "fetch_section_headline",
    "get_equity_data", "get_account_data",
]
