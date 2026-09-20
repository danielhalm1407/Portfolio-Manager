"""
NARROW STATE LEDGER (13-01.1) — a constant-width recorder for long option simulations.

WHY THIS EXISTS
---------------
``StateLedger.record_book`` writes ONE COLUMN PER SYMBOL PER FIELD. That is the right shape for
a three-ticker rebalance study and the wrong shape for a rolling option overlay, because the
option universe GROWS WITHOUT BOUND while the book never forgets a leg:

  * ``Book.position()`` (``book.py:195-202``) auto-creates a ``Position`` on demand and nothing
    ever removes one, so a put that expired in 2009 is still a position in 2026;
  * ``Book.snapshot()`` (``book.py:264-268``) iterates EVERY position the book has ever held and
    builds a nine-key dict for each;
  * ``StateLedger.record_book`` (``ledger.py:127-139``) flattens all of those into one wide row;
  * ``StateLedger._put_row`` (``ledger.py:88-90``) then widens the frame with
    ``self.df[k] = np.nan`` once per newly-seen symbol, COPYING the frame each time — which is
    what the ``DataFrame is highly fragmented`` PerformanceWarning is pointing at.

A 63-bar roll over a 5,201-bar window is ~82 rolls, so ~82 dead legs times ~8 columns accumulate
and the per-bar cost grows linearly with the bar number: O(bars**2 / 63) overall.

THE MEASUREMENT THAT MOTIVATED IT
---------------------------------
One ``ProtectivePutRule`` configuration over leading slices of the real window
(``.paul/phases/13-walk-forward-validation/13-01-IMPLEMENTATION-CONTEXT.md`` section 5):

    bars    runtime   ms/bar    hedged columns
     250      1.50s     5.99         --
     500      5.39s    10.78         96
   1,000     17.37s    17.37        168
   2,000     57.26s    28.63        312

ms/bar DOUBLES as bars double. The isolating run in the same section settles the attribution: an
UNHEDGED configuration — one symbol, no new columns ever — is flat at ~2.7 ms/bar across those
same slices. The cost is the RECORDER, not the rules, not the vol surface and not the fill model
(section 7 measured fills as negligible: ~164 of them over twenty years, O(1) dispatch each).

WHAT THIS LEDGER RECORDS, AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------------------
It records the book-level ``TOTAL_*`` aggregates and whatever ``extra`` the caller stamps on —
nothing per symbol. There is no ``"SPY 2009-03-20 P450_position"`` column and there never will be,
however many legs the run strikes. The row width is therefore fixed by the caller's schema rather
than by the traded universe, which is the entire point.

**CONSEQUENCE, AND THE OBLIGATION IT PLACES ON EVERY CALLER: this ledger CANNOT EXPLAIN ITS OWN
EQUITY PATH.** A caller that uses it must persist, alongside the equity series:

  * ``PortfolioSimulator.blotter()`` — every fill as ts / symbol / side / qty / price / notional,
    which is what an option lifecycle (open, roll, monetise) is reconstructed from; and
  * the rule's own per-bar history — for ``OptionOverlayRule`` that is ``rule.history``, carrying
    ``state``, ``drawdown``, ``multiple`` and ``iv`` per bar.

Both are already cheap and already exist. Dropping them and keeping only the narrow frame would
leave a run that is fast and unexplainable, which is worse than a slow run.

WHY A SUBCLASS AND NOT AN EDIT TO ``StateLedger``
-------------------------------------------------
``PortfolioSimulator.__init__`` already accepts ``ledger=`` (``simulator.py:26``, defaulted at
``:37``), so the whole intervention is a subclass handed in at construction. ``Book``, ``Fill``,
``PortfolioSimulator`` and ``StateLedger`` are untouched — they are on 13-01's DO NOT CHANGE list
and they did not need to move. Every existing caller (``rebalance_study``,
``drawdown_rotation_sim``, ``overlay_runs``) passes no ``ledger=`` and therefore keeps the wide
``StateLedger`` verbatim: their output is unchanged BY CONSTRUCTION rather than by a hashing
argument, because their code path does not change at all.
"""

import numpy as np
import pandas as pd

# The book's own mark-resolution rule. Imported rather than reimplemented because marks arrive in
# three shapes (mapping / bare scalar / None) and a second, subtly different resolution here would
# break bar-for-bar identity against StateLedger on exactly the paths that are hardest to test.
from .book import _resolve_price
from .ledger import StateLedger


class NarrowLedger(StateLedger):
    """A ``StateLedger`` that records book-level totals only, at constant row width.

    Drop-in wherever a ``StateLedger`` goes: ``PortfolioSimulator(prices, rules,
    ledger=NarrowLedger())``. Same ``df`` contract, same ``index_name``, same one-row-per-bar
    semantics — only the columns differ.
    """

    def __init__(self, columns=None, index_name="ts"):
        # These two MUST exist before super().__init__() runs, because the parent's constructor
        # ends with `self.df = self._empty()` and `df` is a property on this class — its setter
        # touches _frame, and _put_row (also overridden) touches _rows.
        self._rows = {}
        self._frame = None
        super().__init__(columns=columns, index_name=index_name)

    # ---- the frame, built once rather than grown per bar ------------------------

    @property
    def df(self):
        # Materialise on first access and CACHE. The caching is not an optimisation, it is a
        # correctness requirement: PortfolioSimulator.run() reads `self.ledger.df` once after the
        # bar loop and then WRITES equity_peak / drawdown / turnover_cum onto it
        # (`simulator.py:116-127`). A property that rebuilt the frame on every access would hand
        # back a fresh object and those three columns would silently vanish.
        if self._frame is None:
            self._frame = self._materialise()
        return self._frame

    @df.setter
    def df(self, value):
        # StateLedger.__init__ and StateLedger.reset() both do `self.df = self._empty()`. Accept
        # that as "here is the materialised frame" — for an empty ledger it is exactly right, and
        # honouring the parent's assignment is what keeps this a drop-in subclass.
        self._frame = value

    def _materialise(self):
        # Rows are held as {ts: row} and turned into a frame ONCE. This is the second half of the
        # speed fix: the parent assigns `self.df.loc[ts] = row` per bar, which is a full row
        # write into a live frame on every single bar. Here pandas sees the whole thing at once.
        if not self._rows:
            return self._empty()
        # Honour any pre-declared column ORDER, then append keys the schema did not anticipate —
        # the same precedence StateLedger._put_row uses, so a caller that declared columns gets
        # the same layout from either ledger.
        cols = list(self._columns) if self._columns else []
        for row in self._rows.values():
            cols += [k for k in row if k not in cols]
        frame = pd.DataFrame.from_dict(self._rows, orient="index")
        # from_dict orders columns by first appearance; reindex to the precedence above. Missing
        # keys become NaN, which is the same thing the parent's frame-widening produces.
        frame = frame.reindex(columns=cols)
        frame.index.name = self.index_name
        return frame

    # ---- recording ---------------------------------------------------------------

    def _put_row(self, ts, row):
        # One row per timestamp, exactly as the parent documents: a bar that is re-marked
        # OVERWRITES its row rather than appending a second one. A dict keyed by ts gives that
        # for free and also preserves insertion order, so the frame comes out in bar order by
        # construction. Overriding here (rather than only record_book) means the inherited
        # single-asset `record()` path keeps working and stays consistent with `df`.
        self._rows[ts] = dict(row)
        # Any cached frame is now stale. Rebuilt lazily on the next `df` access.
        self._frame = None

    def reset(self):
        # Drop every recorded row but keep the declared schema — same contract as the parent.
        # The accumulated rows must go too, or a re-run would inherit the previous run's bars
        # through _rows while `df` looked empty.
        self._rows = {}
        self._frame = None
        super().reset()

    def record_book(self, ts, book, prices, extra=None):
        # ============================================================================
        # THE HOT PATH. Same signature and same contract as StateLedger.record_book —
        # one wide-ish row per bar, `extra` merged last so it wins on a key clash, and
        # the per-bar closed-units counters cleared immediately afterwards. The single
        # difference is that book.snapshot() is NEVER called, because that call is the
        # O(dead legs) term this class exists to remove.
        # ============================================================================

        # ONE pass over the positions, computing every TOTAL field the parent's snapshot would
        # have produced. The parent builds a nine-key dict per symbol and then sums across them;
        # this accumulates the same sums directly, so ~82 dead legs cost ~82 float operations
        # instead of ~82 dict constructions plus a 700-column row assignment.
        entry_cost = 0.0
        mark_value = 0.0
        unrealised = 0.0
        closed_units = 0.0
        # Iterating book._positions directly is the same access Book.snapshot and Book.unrealised
        # use internally; going through the public position() would repeat the dict lookup per
        # symbol per bar for no benefit, on the one loop this class exists to keep cheap.
        for sym, pos in book._positions.items():
            # The RAW resolved mark — may be None on a warm-up bar or for a symbol with no quote
            # today. Position.unrealised already treats that (and a zero qty) as 0.0, which is
            # exactly what Book.unrealised does, so unrealised matches the parent bar for bar.
            px = _resolve_price(sym, prices)
            unrealised += pos.unrealised(px)
            # mark_value uses the ENTRY-PRICE FALLBACK instead, mirroring Position.snapshot
            # (`book.py:139-143`): a stale-but-real valuation beats a NaN that would poison the
            # whole row. Without this the two ledgers would disagree on mark_value alone.
            # np.isfinite, not a hand-rolled NaN test, because that is literally the predicate
            # Position.snapshot uses — a different one here would diverge on some edge case.
            if px is None or not np.isfinite(px):
                px = pos.avg_entry or 0.0
            entry_cost += pos.avg_entry * pos.qty
            mark_value += px * pos.qty
            # A within-bar figure, not a cumulative one — which is why reset_bar_counters() fires
            # immediately after this row is written, exactly as in the parent.
            closed_units += pos.closed_this_bar

        realised = book.realised
        row = {
            "TOTAL_entry_cost": entry_cost,
            "TOTAL_mark_value": mark_value,
            "TOTAL_unrealised_pnl": unrealised,
            "TOTAL_realised_pnl": realised,
            "TOTAL_total_pnl": realised + unrealised,
            "TOTAL_closed_units": closed_units,
            "TOTAL_portfolio_value": book.base_equity + realised + unrealised,
        }
        if extra:
            row.update(extra)
        self._put_row(ts, row)
        # Same per-bar reset as every other recording path: closed_units is a within-bar figure
        # and the ledger clearing it after writing is part of StateLedger's documented behaviour,
        # not an implementation detail of it.
        book.reset_bar_counters()
        return row
