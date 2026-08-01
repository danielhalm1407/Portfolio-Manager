"""
THE ACCOUNTING ENGINE (plan §4b / §7.3) — average-cost position + P&L book.

This module is the extraction of the rules that used to live as methods on the Tk
``KalmanTradingApp`` in ``orders/kts.py``. The logic is byte-for-byte the same; what
changed is WHERE the state lives:

    kts.py (before)                     here (after)
    ---------------                     ------------
    self.sim_position                   Position.qty
    self.sim_avg_entry                  Position.avg_entry
    self.sim_realised                   Position.realised
    self._sim_closed_this_bar           Position.closed_this_bar
    KalmanTradingApp.apply_fill         Position.apply_fill / Book.apply_fill
    KalmanTradingApp._sim_unrealised    Position.unrealised / Book.unrealised

Two things this buys us. First, importability: none of this touches Tk, IBKR or any
callback, so a script, a notebook or a test can build a book without a GUI. Second,
N symbols: ``Book`` holds one ``Position`` per instrument, which is what lets a
scenario hold a hedge sleeve AND a high-beta basket at once — impossible with the
three scalars kts.py used.

SCOPE / LIMITATIONS (be honest about these before trusting a number):
  * Average-cost (VWAP) accounting, NOT tax-lot (FIFO/LIFO/specific-ID) accounting.
    Realised P&L is measured against the running average entry of the open units.
  * No FX conversion — every symbol is assumed quoted in one accounting currency.
  * No contract multipliers — one unit of `qty` is one unit of `price`. Futures/options
    need a multiplier layered on top before these figures mean anything.
  * No commissions, financing, borrow or dividends. Slippage is the CALLER's job:
    pass the already-slipped fill price (see ``SimExecutionBackend``'s slippage hook,
    and ``portutils.analysis.strategies.calc_slippage`` for a modelled version).
"""

from collections.abc import Mapping

import numpy as np


def _resolve_price(symbol, prices, default=None):
    # Marks arrive in three shapes depending on the caller, and every P&L path needs to
    # cope with all of them: a Mapping {symbol: price} (multi-asset simulator), a bare
    # scalar (the single-symbol kts.py path, which never knew about symbols), or None
    # (no mark available yet — warm-up bars). Normalize to a float-or-None here so the
    # arithmetic below never has to branch again.
    if prices is None:
        return default
    if isinstance(prices, Mapping):
        return prices.get(symbol, default)
    return prices


class Position:
    """One symbol's book: signed units, VWAP entry of the OPEN units, cumulative realised."""

    def __init__(self, symbol="", qty=0.0, avg_entry=0.0, realised=0.0):
        # Which instrument this book belongs to. Purely a label at this level — all the
        # arithmetic is symbol-agnostic; `Book` is what routes fills by symbol.
        self.symbol = symbol
        # qty        — signed open units (long > 0, short < 0).
        # avg_entry  — VWAP of the CURRENTLY-OPEN units (0 when flat).
        # realised   — cumulative crystallised P&L from closed units.
        # Unrealised P&L is derived on the fly (mark price vs avg_entry), never stored
        # as authoritative — it changes every tick, so we recompute it when marking.
        self.qty = float(qty)
        self.avg_entry = float(avg_entry)
        self.realised = float(realised)
        # Units closed in the bar currently being marked — reset each bar, used by the
        # position-breakdown plot's "closed_units" scatter markers (§4c plot 2).
        self.closed_this_bar = 0.0

    # ------------------------------------------------------------------------
    # THE SOLE MUTATOR of position/P&L (plan §7.3 / §4b). Every fill — simulated,
    # replayed or (later) live from IBKR execDetails — funnels through here, which is
    # why the numbers can never diverge between backends.
    # ------------------------------------------------------------------------
    def apply_fill(self, fill):
        # Apply one Fill to the running book per the §4b rules. `fill.side` is +1 BUY /
        # -1 SELL; `fill.qty` is the absolute size. Updates qty, avg_entry and realised
        # in place and accumulates closed units for the current bar.
        # Returns the realised P&L this single fill crystallised (0.0 for a pure open) so
        # callers no longer need the before/after snapshot dance the old backend did.
        signed_qty = fill.side * fill.qty          # +long add or short reduce / -short add or long reduce
        pos = self.qty
        avg = self.avg_entry
        price = fill.price
        # Snapshot cumulative realised so we can report the delta this fill booked.
        realised_before = self.realised

        if pos == 0 or (pos > 0 and signed_qty > 0) or (pos < 0 and signed_qty < 0):
            # SAME DIRECTION (or opening from flat): position grows. New avg entry is the
            # volume-weighted average of the old open units and the new fill.
            new_pos = pos + signed_qty
            # if no previous position, then the vwap is just the fill price;
            if pos == 0:
                avg = price
            # if the fill is in the same direction as the existing position,
            # we calculate a new average entry price by taking a weighted average of the
            # existing position and the new fill. The weights are based on the absolute
            # number of units of the existing position and the new fill quantity.
            # This ensures that the average entry price accurately reflects the
            # combined position after the fill is applied.
            else:
                avg = (avg * abs(pos) + price * abs(signed_qty)) / abs(new_pos)
            self.qty = new_pos
            self.avg_entry = avg
        else:
            # OPPOSITE DIRECTION: the fill reduces (and maybe flips) the position.
            closing = min(abs(signed_qty), abs(pos))   # can't close more than existing position
            # Realised P&L on the closed units: sign(pos)·(price − avg)·units.
            self.realised += np.sign(pos) * (price - avg) * closing
            # Accumulate the closed units for this bar so the state row can report it, then reset at the next bar close.
            self.closed_this_bar += closing
            if abs(signed_qty) <= abs(pos):
                # Pure reduction (no flip): avg entry price of the surviving units is unchanged.
                # since signed_qty is negative, adding it to pos reduces the position size
                self.qty = pos + signed_qty
                if self.qty == 0:
                    self.avg_entry = 0.0
            else:
                # FLIP: close all old units (done above) then OPEN the remainder on the
                # opposite side at the fill price (new leg, fresh avg entry).
                remainder = abs(signed_qty) - abs(pos)
                self.qty = np.sign(signed_qty) * remainder
                self.avg_entry = price

        return self.realised - realised_before

    def unrealised(self, price):
        # Mark-to-market P&L on currently-open units at `price`. Zero when flat or when
        # no price is available. (last_price − avg_entry)·position carries the sign
        # correctly for both long and short books.
        if price is None or not np.isfinite(price) or self.qty == 0:
            return 0.0
        return (price - self.avg_entry) * self.qty

    def snapshot(self, price):
        # The accounting half of a state_df row (§4b). Key names are IDENTICAL to the
        # columns kts.py already writes, so every downstream plot and widget that reads
        # state_df keeps working without a rename.
        if price is None or not np.isfinite(price):
            # No usable mark: fall back to the entry price so entry_cost/mark_value agree
            # and unrealised comes out at 0 rather than NaN-poisoning the row.
            price = self.avg_entry or 0.0
        unreal = self.unrealised(price)
        return {
            "position": self.qty,
            "avg_entry_price": self.avg_entry,
            "entry_cost": self.avg_entry * self.qty,
            "mark_value": price * self.qty,
            "unrealised_pnl": unreal,
            "realised_pnl": self.realised,
            "total_pnl": self.realised + unreal,
            "last_price": price,
            "closed_units": self.closed_this_bar,
        }

    def restore(self, qty, avg_entry, realised):
        # Jump the book straight to a known state WITHOUT replaying fills. Used by the
        # replay scrubber, which stores every bar's book in state_df and rewinds by
        # assignment (replaying thousands of fills on every slider drag would be far too
        # slow for an interactive control).
        self.qty = float(qty)
        self.avg_entry = float(avg_entry)
        self.realised = float(realised)

    def reset(self):
        # Wipe the book back to flat. Called when the run/symbol changes so a fresh run
        # does not inherit a stale position or realised total.
        self.qty = 0.0
        self.avg_entry = 0.0
        self.realised = 0.0
        self.closed_this_bar = 0.0


class Book:
    """A set of ``Position``s keyed by symbol, plus the starting equity they trade against.

    ``default_symbol`` is the single-symbol escape hatch: fills with an empty
    ``Fill.symbol`` and lookups with no symbol argument resolve to it. That is what lets
    the one-instrument kts.py app treat a ``Book`` as if it were still three scalars.
    """

    def __init__(self, default_symbol="", base_equity=0.0):
        # symbol -> Position. Positions are created lazily on first touch so a caller
        # never has to declare its universe up front.
        self._positions = {}
        # The symbol used when a fill or a query does not name one (single-asset mode).
        self.default_symbol = default_symbol
        # Starting equity the book's P&L is measured on top of: IBKR NetLiq in the live
        # app, an explicit starting_capital in a scenario sim. Weight-based sizing needs
        # it, so it lives with the book rather than being passed around separately.
        self.base_equity = float(base_equity)

    # ---- position access -------------------------------------------------------
    def position(self, symbol=None):
        # Fetch (creating on demand) the Position for `symbol`, defaulting to
        # default_symbol. Auto-creation is deliberate: apply_fill on a brand-new ticker
        # must just work, and an empty Position is the correct "never traded" answer.
        sym = self.default_symbol if symbol is None or symbol == "" else symbol
        if sym not in self._positions:
            self._positions[sym] = Position(symbol=sym)
        return self._positions[sym]

    @property
    def symbols(self):
        # Traded universe in insertion order — stable enough to use as DataFrame columns.
        return list(self._positions.keys())

    # ---- mutation --------------------------------------------------------------
    def apply_fill(self, fill):
        # Route the fill to its symbol's Position and return the realised delta it booked.
        # This is the multi-asset generalisation of kts.py's single apply_fill: the rules
        # are untouched, only the dispatch is new.
        return self.position(getattr(fill, "symbol", "") or None).apply_fill(fill)

    def reset(self):
        # Drop every position entirely (not just flatten them) so a fresh run starts with
        # a clean universe — a symbol traded in the previous run should not linger as a
        # zero row in the next run's snapshot.
        self._positions = {}

    def reset_bar_counters(self):
        # Clear the per-bar closed-units accumulators across all symbols. Called by the
        # ledger right AFTER a row has been recorded, so each row reports only the units
        # closed within that bar.
        for p in self._positions.values():
            p.closed_this_bar = 0.0

    # ---- marking ---------------------------------------------------------------
    @property
    def realised(self):
        # Whole-book crystallised P&L. Mark-independent, hence a plain property.
        return sum(p.realised for p in self._positions.values())

    def unrealised(self, prices):
        # Whole-book mark-to-market on open units. `prices` may be a {symbol: price}
        # mapping (multi-asset) or a bare scalar (single-asset, kts.py style).
        return sum(p.unrealised(_resolve_price(sym, prices))
                   for sym, p in self._positions.items())

    def total_pnl(self, prices):
        # realised + unrealised — the figure the portfolio widget and the P&L plot show.
        return self.realised + self.unrealised(prices)

    def equity(self, prices):
        # Current account value: the starting equity plus everything the book has made or
        # lost. This is what weight-based sizing must divide by, NOT base_equity alone.
        return self.base_equity + self.total_pnl(prices)

    def gross_exposure(self, prices):
        # Sum of |mark value| across symbols — the denominator-free measure of how much
        # market the book is actually carrying (a long hedge sleeve against a long growth
        # basket nets out in net exposure but not here).
        total = 0.0
        for sym, p in self._positions.items():
            # Fall back to the entry price when no mark is available — a stale-but-real
            # valuation beats silently dropping the leg from the exposure total.
            px = _resolve_price(sym, prices, default=p.avg_entry)
            if px is None or not np.isfinite(px):
                px = p.avg_entry
            total += abs(p.qty * px)
        return total

    def snapshot(self, prices):
        # Per-symbol accounting rows PLUS a "TOTAL" aggregate, all keyed by symbol.
        # The per-symbol rows carry the same column names as kts.py's state_df; TOTAL
        # carries the book-level figures (avg_entry/last_price are meaningless across
        # instruments, so they are left out of it rather than faked).
        rows = {sym: p.snapshot(_resolve_price(sym, prices))
                for sym, p in self._positions.items()}
        unreal = self.unrealised(prices)
        rows["TOTAL"] = {
            "entry_cost": sum(r["entry_cost"] for r in rows.values()),
            "mark_value": sum(r["mark_value"] for r in rows.values()),
            "unrealised_pnl": unreal,
            "realised_pnl": self.realised,
            "total_pnl": self.realised + unreal,
            "closed_units": sum(r["closed_units"] for r in rows.values()),
            "portfolio_value": self.base_equity + self.realised + unreal,
        }
        return rows
