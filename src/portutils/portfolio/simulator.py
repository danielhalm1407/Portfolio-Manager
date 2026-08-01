"""
PORTFOLIO SIMULATOR — replay a price history against a set of rules and account the result.

This is the offline sibling of kts.py's ``_build_replay``: same shape (march bars → ask the
policy what to trade → synthesize fills → mark the book → record a state row), minus the
Tk main thread, the ``messagebox`` error dialogs and the live IBKR bar fetch. It exists so
a question like *"how do my realised and unrealised P&L split out if I cut the hedge sleeve
into a drawdown and rotate into high beta?"* can be answered without opening the GUI.

What it is NOT: a return-compounding backtester. It tracks fills and cost basis, so it
answers "what did I crystallise and what am I still carrying". For weight-based return
studies with slippage/funding models, use
``portutils.analysis.strategies.QuantileRiskControlStrategy`` instead — the two are
complementary and deliberately not merged.
"""

import pandas as pd

from .book import Book
from .execution import SimExecutionBackend
from .ledger import StateLedger


class PortfolioSimulator:
    def __init__(self, prices, rules, book=None, starting_capital=1_000_000.0,
                 ledger=None, slippage_k=0.0, min_trade_units=1e-12):
        # `prices` is a WIDE frame: one row per bar (DatetimeIndex), one column per symbol.
        # Everything downstream reads marks out of a row of this frame, so the simulator
        # never cares where the data came from (IBKR pull, cached parquet, synthetic).
        self.prices = prices
        # Rules are applied in order and their proposed deltas are merged, so a scripted
        # policy and a hand-written trade list can drive the same run.
        self.rules = list(rules)
        # One book for the whole run. base_equity is the starting capital the weights size
        # against and the base that equity()/portfolio_value are measured on top of.
        self.book = book if book is not None else Book(base_equity=starting_capital)
        self.ledger = ledger if ledger is not None else StateLedger()
        # Fills go through the same backend kts.py uses, so a simulated fill here is the
        # same object, booked the same way, as a simulated fill in the app.
        self.backend = SimExecutionBackend(self.book, slippage_k=slippage_k)
        # Ignore dust deltas. Float arithmetic on target-vs-current units produces 1e-15
        # residuals that would otherwise litter the ledger with meaningless trades.
        self.min_trade_units = float(min_trade_units)
        # Every fill executed, for the trade blotter the report prints.
        self.fills = []

    def run(self):
        # Reset rule state so a simulator instance can be re-run (e.g. across a parameter
        # sweep) without inheriting the previous run's peak equity or rotation flags.
        for rule in self.rules:
            rule.reset()

        for ts, row in self.prices.iterrows():
            # Marks for this bar as a plain dict — drop NaNs so a symbol with no quote today
            # is simply absent rather than poisoning the book's valuation with NaN.
            marks = {sym: float(px) for sym, px in row.items() if pd.notna(px)}

            # 1) Ask every rule what to trade, at a given timestamp, merging their proposals.
            #    Deltas are signed UNITS; rules have already done their own target→delta arithmetic.
            deltas = {}
            for rule in self.rules:
                for sym, qty in rule.propose(ts, marks, self.book).items():
                    deltas[sym] = deltas.get(sym, 0.0) + qty

            # 2) Execute. Sorted for determinism: two runs of the same scenario must produce
            #    byte-identical order ids, which matters when diffing two exports.
            #    Turnover is accumulated here, at the only place a fill actually happens, so
            #    it can never drift from the blotter.
            bar_turnover = 0.0
            bar_trades = 0
            for sym in sorted(deltas):
                qty = deltas[sym]
                if abs(qty) < self.min_trade_units:
                    continue
                px = marks.get(sym)
                if px is None:
                    # Never fill on a missing mark — that would invent a price.
                    continue
                fill = self.backend.execute(1 if qty > 0 else -1, abs(qty), px, ts, symbol=sym)
                self.fills.append(fill)
                # Measure turnover at the FILLED price (slippage included), not the mark —
                # what the book actually paid to trade, not what it hoped to.
                bar_turnover += abs(fill.qty * fill.price)
                bar_trades += 1

            # 3) Mark the book at this bar's prices (post-trade) → one wide state row.
            #    equity/drawdown are stamped as extras because they are portfolio-level
            #    context, not per-symbol accounting.
            equity = self.book.equity(marks)
            self.ledger.record_book(ts, self.book, marks, extra={
                "equity": equity,
                "gross_exposure": self.book.gross_exposure(marks),
                "n_trades": bar_trades,
                # Notional traded this bar and the same figure as a fraction of equity.
                # A daily-rebalanced book realises P&L continuously, but it pays the spread
                # ~250x a year to do it — quoting the realised figure without this alongside
                # would be quoting half the story.
                "turnover_notional": bar_turnover,
                "turnover_frac": (bar_turnover / equity) if equity else 0.0,
            })

        df = self.ledger.df
        # Drawdown is a whole-path statistic, so it is computed once at the end rather than
        # bar-by-bar: peak-to-date equity, and the % below it the book currently sits.
        if len(df):
            peak = df["equity"].cummax()
            df["equity_peak"] = peak
            df["drawdown"] = (peak - df["equity"]) / peak.replace(0, pd.NA)
            # Running total of notional traded — the cost-side counterpart to the running
            # realised P&L, so the two can be read off the same frame at the same bar.
            df["turnover_cum"] = df["turnover_notional"].astype(float).cumsum()
        return df

    def blotter(self):
        # The trade list this run produced, as a frame — the "what did it actually do"
        # companion to the per-bar state frame.
        return pd.DataFrame([{
            "ts": f.ts, "symbol": f.symbol, "side": f.side,
            "qty": f.qty, "price": f.price, "notional": f.side * f.qty * f.price,
        } for f in self.fills])
