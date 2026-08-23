"""
STATE LEDGER (plan §4b) — the per-bar record of what the book looked like.

kts.py's rule was "the dataframe is the source of truth; the widget at the top is just a
view of the latest row". That rule is worth keeping, but the old ``_record_state_row``
mixed two unrelated things in one method: the ACCOUNTING fields (position, entry cost,
realised/unrealised) and the STRATEGY context (the whole ForecastView, the binding risk
cap, the recommended weights).

The split here is: this module writes the accounting fields, and the caller passes
anything else it wants stamped on the row as ``extra``. kts.py hands over its
recommendation block; an offline scenario hands over nothing. Same frame shape, no
strategy knowledge leaking into the accounting layer.

WHAT THE CALLER HANDS OVER
--------------------------
``book`` is a ``portutils.portfolio.book.Book``: a dict of ``Position``s keyed by symbol
(signed ``qty``, VWAP ``avg_entry``, cumulative ``realised``, per-bar ``closed_this_bar``)
plus the ``base_equity`` the P&L is measured on top of. The ledger only ever READS it —
via ``Position.snapshot(price)`` / ``Book.snapshot(prices)`` — and then clears the per-bar
counters. It never applies a fill; ``apply_fill`` remains the sole mutator.

``price`` / ``prices`` are the MARK, not a fill price: the single-asset path takes one
scalar, the multi-asset path a ``{symbol: price}`` mapping. Marks are an input rather than
state on the book because unrealised P&L changes on every tick and is therefore never
stored as authoritative — it is recomputed at the moment of marking.

``extra`` is the STRATEGY / CONTEXT half of the row: any flat ``{column: value}`` dict the
caller wants stamped alongside the accounting fields, merged last so it wins on a key
clash. It is the "why" behind the bar. In practice:
  * kts.py (``_record_state_row``) stamps the whole recommendation block — ``r_hat``,
    ``q_low``/``q_high``, ``direction``, previous/target/capped/recommended weights,
    ``weight_threshold``, ``binding_cap``, ``intended``/``filled``/``unfilled`` qty,
    ``portfolio_value``/``portfolio_pnl``.
  * the scenario simulator stamps portfolio-level context — ``equity``,
    ``gross_exposure``, ``n_trades``, ``turnover_notional``/``turnover_frac``.
  * an offline scenario may stamp nothing at all; the frame shape is unchanged.
Keys the schema did not anticipate simply widen the frame (see ``_put_row``), so earlier
bars show NaN for a column that only starts existing mid-run.

WHEN A ROW IS WRITTEN
---------------------
Per BAR CLOSE, plus on-demand whenever the book actually moves — the union of the two,
not the lesser. kts.py calls it from the bar-close hook, from every replay bar, and again
immediately after a manual/auto trade so the panel reflects the click rather than waiting
for the next close. That is safe because ``_put_row`` keys on the bar timestamp: a re-mark
of the same bar OVERWRITES its row instead of appending a second one, so the frame stays
one-row-per-bar however many times a bar is marked. The one field that is genuinely
per-bar rather than cumulative — ``closed_units`` — is why ``reset_bar_counters()`` fires
right after the row is written.
"""

import numpy as np
import pandas as pd


class StateLedger:
    """A growing per-bar frame of book snapshots, indexed by bar timestamp."""

    def __init__(self, columns=None, index_name="ts"):
        # Pre-declaring columns keeps column ORDER stable and makes an empty ledger still
        # have the right shape for downstream plotting code that reads df["realised_pnl"]
        # before any bar has been marked. None → columns appear as rows are recorded.
        self._columns = list(columns) if columns is not None else None
        self.index_name = index_name
        self.df = self._empty()

    def _empty(self):
        df = pd.DataFrame(columns=self._columns) if self._columns else pd.DataFrame()
        df.index.name = self.index_name
        return df

    def _put_row(self, ts, row):
        # One row per timestamp: overwrite if the bar is re-marked, else append.
        if len(self.df) == 0:
            # FIRST row. Build the frame from the row itself rather than assigning into an
            # empty one: `.loc[ts] = dict` refuses outright on a frame with no columns, and
            # on an empty-but-typed frame it triggers pandas' deprecated all-NA concat path
            # (and infers object dtypes from it). Any pre-declared column order is honoured;
            # keys the schema did not anticipate are appended after it.
            cols = list(self.df.columns)
            cols += [k for k in row if k not in cols]
            self.df = pd.DataFrame([[row.get(c) for c in cols]], columns=cols, index=[ts])
            self.df.index.name = self.index_name
            return
        # A symbol first traded mid-run brings new "<SYM>_*" keys with it. Widen the frame
        # rather than let the row assignment fail: earlier bars correctly show NaN for a
        # leg that did not exist yet.
        for k in [k for k in row if k not in self.df.columns]:
            self.df[k] = np.nan
        self.df.loc[ts] = row

    def reset(self):
        # Drop every recorded row but keep the declared schema — used when a run restarts
        # so a fresh run does not inherit a stale ledger.
        self.df = self._empty()

    # ------------------------------------------------------------------
    # SINGLE-ASSET recording (the kts.py shape).
    # ------------------------------------------------------------------
    def accounting_row(self, book, price, symbol=None):
        # The accounting half of a state row for ONE symbol. Kept separate from record()
        # so a caller that assembles its own row (kts.py, which merges a large strategy
        # block in) can take just these fields without also writing to the frame.
        return book.position(symbol).snapshot(price)

    def record(self, ts, book, price, extra=None, symbol=None):
        # Append/refresh the row for bar timestamp `ts`, marked at `price`. One row per
        # bar: overwrite if the bar is re-marked (e.g. a mid-bar manual trade), else append.
        row = self.accounting_row(book, price, symbol)
        if extra:
            row.update(extra)
        self._put_row(ts, row)
        # Reset the per-bar closure accumulator now that it's been recorded.
        book.reset_bar_counters()
        return row

    # ------------------------------------------------------------------
    # MULTI-ASSET recording (the scenario-simulator shape).
    # ------------------------------------------------------------------
    def record_book(self, ts, book, prices, extra=None):
        # Flatten a whole multi-symbol book into ONE wide row: "<SYMBOL>_<field>" per leg
        # plus "TOTAL_<field>" for the aggregate. Wide (not long) because every plotting
        # and performance helper in portutils takes a wide frame of columns-as-series —
        # this row shape drops straight into PanelBuilder / PerformanceSummary.
        snap = book.snapshot(prices)
        row = {}
        for sym, fields in snap.items():
            for k, v in fields.items():
                row[f"{sym}_{k}"] = v
        if extra:
            row.update(extra)
        self._put_row(ts, row)
        # Same per-bar reset as the single-asset path: closed_units is a within-bar figure.
        book.reset_bar_counters()
        return row
