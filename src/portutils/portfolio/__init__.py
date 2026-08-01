"""Portfolio allocation, rebalancing, and risk functions.

Also home to the position/P&L accounting engine extracted from ``orders/kts.py``
(plan §4b / §7.3) — fills, average-cost books, and realised/unrealised P&L — so the
same rules serve the live trading GUI, a replay, and any offline scenario study.
Pure/importable: nothing here touches Tk, IBKR or the network.
"""

from . import backcast
from .book import Book, Position
from .execution import SimExecutionBackend
from .ibkr_sync import (
    book_from_portfolio,
    fills_from_executions,
    reconcile,
    rewind_positions,
)
from .fills import Fill, Order, _iso
from .ledger import StateLedger
from .rules import (
    BuyAndHoldRule,
    ConstantMixRule,
    DrawdownRotationRule,
    RebalanceRule,
    TradeListRule,
    weights_to_units,
)
from .simulator import PortfolioSimulator

__all__ = [
    "Book", "Position",
    "Fill", "Order", "_iso",
    "SimExecutionBackend",
    "StateLedger",
    "RebalanceRule", "BuyAndHoldRule", "ConstantMixRule", "DrawdownRotationRule",
    "TradeListRule",
    "weights_to_units",
    "PortfolioSimulator",
    # broker bridge + reconstruction
    "book_from_portfolio", "fills_from_executions", "reconcile", "rewind_positions",
    "backcast",
]
