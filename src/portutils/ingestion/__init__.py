"""Data ingestion — fetch functions for APIs, scrapers, and feeds."""

from .reuters import Article, ArticleStub, fetch_article, fetch_section_headline
from .ibkr_requests import (
    IBApp, OrderApp, get_equity_data, get_account_data,
    contract, contract_specs_from_portfolio, get_exchange_rates,
    wait_for_order_ack, cancel_order, cancel_all_orders,
)

__all__ = [
    "Article", "ArticleStub", "fetch_article", "fetch_section_headline",
    "IBApp", "OrderApp", "get_equity_data", "get_account_data",
    "contract", "contract_specs_from_portfolio", "get_exchange_rates",
    "wait_for_order_ack", "cancel_order", "cancel_all_orders",
]
