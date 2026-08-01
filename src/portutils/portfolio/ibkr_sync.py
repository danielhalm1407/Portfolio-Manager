"""
BROKER -> BOOK BRIDGE — turn what IBKR reports into the accounting engine's own objects.

The engine in this package was built so that a fill is a fill regardless of where it came
from (plan §7.3: "the accounting engine is backend-agnostic; only the fill source differs").
This module is where that pays off: IBKR's portfolio rows become a ``Book``, IBKR's
executions become ``Fill``s, and both then flow through exactly the same ``apply_fill`` that
the simulated studies use. One set of accounting rules, two sources of truth to compare.

Everything here is PURE — no network, no IBApp, no side effects. The caller fetches the
DataFrames (``get_account_updates`` / ``get_executions_data``) and passes them in, which is
what makes this testable offline while TWS is unavailable.

═══════════════════════════════════════════════════════════════════════════════
TWO ACCOUNTING MISMATCHES THAT MUST NOT BE PAPERED OVER
═══════════════════════════════════════════════════════════════════════════════

1. ``averageCost`` IS NOT ALWAYS A PER-UNIT PRICE.
   IB reports average cost per share INCLUDING commissions for stocks, but per CONTRACT
   (i.e. multiplied by the contract multiplier) for futures and options. Our
   ``Position.avg_entry`` is always a plain per-unit price comparable to a quote. For STK
   the two agree to within the commission component; for anything with a multiplier they
   differ by exactly that multiplier and must be divided down first. ``book_from_portfolio``
   takes a ``multipliers`` mapping for this and, when it sees a non-STK row without one,
   WARNS rather than silently booking a wrong cost basis.

2. ``realizedPnL`` FROM ``updatePortfolio`` IS TYPICALLY SESSION-SCOPED.
   IB's per-position realised figure generally covers the current session, whereas
   ``Position.realised`` is cumulative since the position was opened. Seeding one from the
   other is therefore NOT a like-for-like transfer. ``book_from_portfolio`` defaults to
   seeding realised at 0.0 and keeping IBKR's number alongside for reporting, so nobody
   reads a day's realised P&L as a lifetime figure. Pass ``seed_realised=True`` only when
   you know what the number's scope is.
"""

import warnings

import numpy as np
import pandas as pd

from .book import Book
from .fills import Fill

# IB reports the buy/sell direction as these strings on an execution.
_SIDE_MAP = {"BOT": 1, "BUY": 1, "SLD": -1, "SELL": -1}


def book_from_portfolio(portfolio_df, base_equity=0.0, multipliers=None,
                        seed_realised=False, default_symbol=""):
    """Build a ``Book`` mirroring the account's CURRENT positions.

    Maps each ``updatePortfolio`` row onto ``Position.restore(qty, avg_entry, realised)``:
    ``position`` -> qty, ``averageCost`` -> avg_entry (divided by any multiplier),
    ``realizedPnL`` -> realised only when ``seed_realised`` is set (see the header note on
    its scope).

    ═══════════════════════════════════════════════════════════════════════════
    ``base_equity`` IS THE ACCOUNT'S NET LIQUIDATION, AND IT IS BACK-SOLVED
    ═══════════════════════════════════════════════════════════════════════════
    ``Book.equity(prices)`` is defined as ``base_equity + realised + unrealised``, which is
    correct when ``base_equity`` means STARTING capital and P&L accrues on top — the
    simulator's case. A broker snapshot is the opposite situation: NetLiquidation is the
    CURRENT equity and **already contains the unrealised P&L** of every open position.
    Passing it straight through would count that P&L twice.

    Concretely: a 1,000,000 account holding 150,000 of unrealised gains would report
    ``equity() == 1,150,000``, and any weight-based sizing off it would over-trade by 15%.

    So ``base_equity`` here is taken to mean "the account's NetLiquidation", and the book's
    internal base is back-solved as ``net_liq - unrealised - realised`` using the marks in
    the frame, such that ``book.equity(marks) == net_liq`` exactly. Callers pass what IBKR
    reports and get back a book that agrees with the broker.

    Parameters
    ----------
    portfolio_df : pd.DataFrame
        Output of ``ibkr_requests.get_account_updates`` — needs `symbol`, `position`,
        `averageCost`; `marketPrice`, `secType` and `realizedPnL` are used when present.
    base_equity : float
        Account NetLiquidation (see the box above — it is back-solved, not stored raw).
    multipliers : dict[str, float] | None
        Contract multiplier per symbol, for the non-STK case described in the header.
    seed_realised : bool
        Carry IBKR's `realizedPnL` into the book. Off by default because that figure is
        usually session-scoped while ours is cumulative.
    """
    multipliers = multipliers or {}
    book = Book(default_symbol=default_symbol, base_equity=float(base_equity))
    # Broker marks, collected as we go and used for the base-equity back-solve at the end.
    marks = {}
    if portfolio_df is None or len(portfolio_df) == 0:
        return book

    for _, row in portfolio_df.iterrows():
        symbol = row.get("symbol")
        qty = float(row.get("position", 0.0) or 0.0)
        # A zero-quantity row is a position IB still lists but we no longer hold. Skipping
        # it keeps `book.symbols` to what is actually owned.
        if symbol is None or qty == 0:
            continue

        avg_cost = float(row.get("averageCost", 0.0) or 0.0)
        sec_type = str(row.get("secType", "STK") or "STK")
        mult = float(multipliers.get(symbol, 1.0))
        if mult == 1.0 and sec_type not in ("STK", "CASH", "CRYPTO"):
            # Loud, because a futures cost basis off by the multiplier produces P&L that is
            # wrong by orders of magnitude while still looking like a plausible number.
            warnings.warn(
                f"{symbol} is {sec_type} but no multiplier was supplied; averageCost is "
                f"per-contract for non-stock instruments, so avg_entry may be wrong by the "
                f"contract multiplier.",
                stacklevel=2,
            )
        avg_entry = avg_cost / mult if mult else avg_cost

        realised = float(row.get("realizedPnL", 0.0) or 0.0) if seed_realised else 0.0
        book.position(symbol).restore(qty, avg_entry, realised)
        # Keep the broker's own mark so the base-equity back-solve below can value the book
        # exactly as IBKR did, rather than needing a separate price feed.
        mp = row.get("marketPrice")
        if mp is not None and np.isfinite(float(mp or np.nan)):
            marks[symbol] = float(mp)

    # Back-solve the base so that equity(marks) == the NetLiquidation that was passed in.
    # Without this the unrealised P&L already inside NetLiq is added a second time (see the
    # box in this function's docstring) and every weight-based target is inflated by it.
    if marks:
        book.base_equity = float(base_equity) - book.unrealised(marks) - book.realised

    return book


def fills_from_executions(exec_df, default_ts=None):
    """Convert an executions DataFrame into ``Fill`` objects.

    This is what lets REAL broker fills run through the same ``apply_fill`` as simulated
    ones. Rows whose side is unrecognised are skipped with a warning rather than guessed
    at — booking a fill in the wrong direction is worse than not booking it.
    """
    fills = []
    if exec_df is None or len(exec_df) == 0:
        return fills

    for _, row in exec_df.iterrows():
        side = _SIDE_MAP.get(str(row.get("side", "")).upper())
        if side is None:
            warnings.warn(f"skipping execution with unrecognised side {row.get('side')!r}",
                          stacklevel=2)
            continue
        qty = abs(float(row.get("shares", 0.0) or 0.0))
        price = float(row.get("price", 0.0) or 0.0)
        if qty == 0 or price <= 0:
            continue
        # Prefer the parsed `ts` get_executions_data adds; fall back to the raw string.
        ts = row.get("ts", None)
        if ts is None or (isinstance(ts, float) and np.isnan(ts)) or pd.isna(ts):
            ts = row.get("time", default_ts)
        fills.append(Fill(
            order_id=int(row.get("orderId", 0) or 0),
            ts=ts, side=side, qty=qty, price=price,
            perm_id=row.get("permId"),
            # source="live" marks provenance, so a frame holding both replayed and real
            # fills stays honest about which is which.
            source="live",
            symbol=str(row.get("symbol", "")),
        ))
    return fills


def rewind_positions(portfolio_df, exec_df, multipliers=None):
    """Positions as they stood BEFORE the executions window — the backcast anchor.

    Takes today's positions and subtracts each fill, giving the exact position at the start
    of whatever window ``get_executions_data`` returned. This end of any reconstruction is
    REAL DATA, not an assumption, which is what makes the policy backcast in ``backcast.py``
    anchored rather than free-floating.

    Only quantities are rewound. Average entry cost is deliberately NOT reversed: undoing a
    VWAP requires knowing the cost of the units that were added, which is exactly the
    information a position snapshot does not carry. Callers that need a pre-window cost
    basis get it from the forward replay in ``backcast.py``, not from here.

    Returns
    -------
    dict[str, float] — symbol -> quantity held at the start of the window.
    """
    multipliers = multipliers or {}
    qty = {}
    if portfolio_df is not None and len(portfolio_df):
        for _, row in portfolio_df.iterrows():
            symbol = row.get("symbol")
            if symbol is not None:
                qty[symbol] = qty.get(symbol, 0.0) + float(row.get("position", 0.0) or 0.0)

    for fill in fills_from_executions(exec_df):
        # Reverse the fill: a BUY added units going forward, so it is subtracted going back.
        qty[fill.symbol] = qty.get(fill.symbol, 0.0) - fill.side * fill.qty

    return qty


def reconcile(book, portfolio_df, tol=1e-6, multipliers=None):
    """Compare our book against what IBKR reports, per symbol.

    This is the "✓ matches IBKR / ⚠ drift" check ``orders/plan.md`` §7.3 specified and never
    got. It exists so a divergence is SEEN rather than quietly carried: if our position or
    cost basis has drifted from the broker's, every P&L figure downstream is suspect and the
    right response is to stop and find out why.

    Returns a DataFrame with our figures, IBKR's, the differences, and a `match` flag.
    Symbols present on either side appear, so a position we hold and IBKR does not (or vice
    versa) shows up as a row rather than vanishing from the comparison.
    """
    multipliers = multipliers or {}
    ib = {}
    if portfolio_df is not None and len(portfolio_df):
        for _, row in portfolio_df.iterrows():
            symbol = row.get("symbol")
            if symbol is None:
                continue
            mult = float(multipliers.get(symbol, 1.0)) or 1.0
            ib[symbol] = {
                "ib_position": float(row.get("position", 0.0) or 0.0),
                "ib_avg_entry": float(row.get("averageCost", 0.0) or 0.0) / mult,
                "ib_realised": float(row.get("realizedPnL", 0.0) or 0.0),
                "ib_unrealised": float(row.get("unrealizedPnL", 0.0) or 0.0),
                "ib_market_price": float(row.get("marketPrice", 0.0) or 0.0),
            }

    rows = []
    for symbol in sorted(set(book.symbols) | set(ib)):
        pos = book.position(symbol)
        side = ib.get(symbol, {})
        ib_qty = side.get("ib_position", 0.0)
        ib_avg = side.get("ib_avg_entry", 0.0)
        mark = side.get("ib_market_price", 0.0)
        rows.append({
            "symbol": symbol,
            "our_position": pos.qty,
            "ib_position": ib_qty,
            "position_diff": pos.qty - ib_qty,
            "our_avg_entry": pos.avg_entry,
            "ib_avg_entry": ib_avg,
            "avg_entry_diff": pos.avg_entry - ib_avg,
            "our_realised": pos.realised,
            "ib_realised": side.get("ib_realised", np.nan),
            "our_unrealised": pos.unrealised(mark) if mark else np.nan,
            "ib_unrealised": side.get("ib_unrealised", np.nan),
            # Quantity is the hard check: it is an integer-ish fact both sides must agree
            # on. Average entry is compared on a relative tolerance because IB folds
            # commissions into its figure, so an exact match is not expected.
            "match": abs(pos.qty - ib_qty) < tol,
            "avg_entry_close": (abs(pos.avg_entry - ib_avg) <= max(tol, 0.01 * abs(ib_avg))
                                if ib_avg else abs(pos.avg_entry) < tol),
        })
    return pd.DataFrame(rows)
