"""
NARROW LEDGER TESTS (13-01.1) — identity against StateLedger, and the defect it removes.

WHAT THESE TESTS ARE FOR
------------------------
``NarrowLedger`` is a SPEED fix, so the only thing that can go wrong is that it also changes a
number. Every test here is therefore either an identity check against ``StateLedger`` on the same
run, or a check on the frame's SHAPE — never on the strategy's behaviour, which belongs to
``test_option_monetisation.py``.

Two of them assert the DEFECT as well as the fix (``test_statelegder_column_count_grows``): a
test that only pinned ``NarrowLedger``'s constant width would still pass if the parent's growth
silently disappeared for some unrelated reason, and we would lose the evidence that the fix is
still doing something.

Everything runs on short synthetic paths — no parquet, no real IV, no TWS — so the suite stays
fast. Wall-clock is deliberately NOT asserted here: a timing assertion in CI is a flaky test. The
ms/bar table is measured in 13-01.1's verify step and recorded in its SUMMARY instead.
"""

import numpy as np
import pandas as pd
import pytest

from portutils.portfolio.book import Book
from portutils.portfolio.ledger import StateLedger
from portutils.portfolio.narrow_ledger import NarrowLedger
from portutils.portfolio.rules import BuyAndHoldRule
from portutils.portfolio.simulator import PortfolioSimulator
from portutils.strategies.rules.options import ProtectivePutRule

UNDERLYING = "SPY"


# ============================================================================
# FIXTURE — a deterministic spot path long enough to force several rolls.
# A 63-bar roll cadence over 260 bars is ~4 rolls, so ~4 dead legs accumulate:
# enough for the parent's column count to grow visibly, short enough to stay fast.
# ============================================================================

def _spot_path(n):
    # A gentle drift with a drawdown in the middle, so the put actually goes in the money and the
    # book books both a premium and a settlement. A pure uptrend would leave every leg expiring
    # worthless and the two ledgers would agree for an uninteresting reason.
    i = np.arange(n, dtype=float)
    drift = 100.0 * (1.0 + 0.0003 * i)
    dip = -18.0 * np.exp(-((i - n * 0.55) ** 2) / (2 * (n * 0.06) ** 2))
    idx = pd.bdate_range("2010-01-04", periods=n)
    return pd.Series(drift + dip, index=idx, name=UNDERLYING)


def _run(n, ledger):
    """One protective-put configuration over `n` bars, recorded by `ledger`.

    A FRESH Book every call. PortfolioSimulator.run() resets rules but NOT the book
    (`simulator.py:48-50`), so a shared book would carry the previous run's dead legs into the
    next one — which is exactly the accumulation these tests are about.
    """
    spot = _spot_path(n)
    prices = spot.to_frame(UNDERLYING)
    rules = [BuyAndHoldRule({UNDERLYING: 1.0}),
             ProtectivePutRule(underlying=UNDERLYING, floor=0.90, reset_bars=63)]
    sim = PortfolioSimulator(prices, rules, book=Book(base_equity=float(spot.iloc[0])),
                             starting_capital=float(spot.iloc[0]), ledger=ledger)
    state = sim.run()
    return state, sim


# ============================================================================
# AC-1 — the narrow ledger changes no number.
# ============================================================================

def test_equity_series_identical_to_state_ledger():
    # THE headline guarantee. If this fails, nothing else about the fix matters.
    wide, _ = _run(250, StateLedger())
    narrow, _ = _run(250, NarrowLedger())
    # Same bars, same order.
    assert list(narrow.index) == list(wide.index)
    # Equity is the series every downstream score slices, so 1e-12 rather than a looser tolerance:
    # these are the same float operations in the same order, not an approximation of them.
    np.testing.assert_allclose(narrow["equity"].to_numpy(),
                               wide["equity"].to_numpy(), rtol=0, atol=1e-12)


def test_total_fields_and_blotter_identical():
    wide, sim_wide = _run(250, StateLedger())
    narrow, sim_narrow = _run(250, NarrowLedger())

    # Every TOTAL_* column the narrow frame carries must agree bar for bar with the parent's.
    # The parent may carry more (it does not — TOTAL is TOTAL) but never fewer of these.
    for col in ["TOTAL_entry_cost", "TOTAL_mark_value", "TOTAL_unrealised_pnl",
                "TOTAL_realised_pnl", "TOTAL_total_pnl", "TOTAL_closed_units",
                "TOTAL_portfolio_value"]:
        assert col in wide.columns, f"{col} missing from the wide frame"
        np.testing.assert_allclose(narrow[col].to_numpy(), wide[col].to_numpy(),
                                   rtol=0, atol=1e-12, err_msg=col)

    # The fills themselves are produced by the backend and the book, neither of which this fix
    # touches — but asserting it here is what rules out "the ledger changed what got traded".
    bw, bn = sim_wide.blotter(), sim_narrow.blotter()
    assert len(bw) == len(bn) and len(bw) > 0
    assert list(bw["symbol"]) == list(bn["symbol"])
    np.testing.assert_allclose(bn["price"].to_numpy(), bw["price"].to_numpy(),
                               rtol=0, atol=1e-12)
    np.testing.assert_allclose(bn["qty"].to_numpy(), bw["qty"].to_numpy(),
                               rtol=0, atol=1e-12)


def test_extras_stamped_by_the_simulator_are_preserved():
    # The simulator stamps portfolio-level context as `extra` (`simulator.py:104-113`). A narrow
    # ledger that dropped those would be narrow in the wrong place — they are per-BAR, not
    # per-SYMBOL, so they cost nothing and everything downstream reads them.
    narrow, _ = _run(150, NarrowLedger())
    for col in ["equity", "gross_exposure", "n_trades", "turnover_notional", "turnover_frac"]:
        assert col in narrow.columns, f"{col} was dropped"


# ============================================================================
# AC-2 — constant width, and the defect shown rather than asserted.
# ============================================================================

@pytest.mark.parametrize("n", [125, 250, 400])
def test_narrow_column_count_is_constant(n):
    # The fix, stated as a shape property: however many legs the run strikes, the row does not
    # get wider. Checked against the 125-bar run as the reference width.
    ref, _ = _run(125, NarrowLedger())
    frame, _ = _run(n, NarrowLedger())
    assert len(frame.columns) == len(ref.columns), (
        f"{n} bars produced {len(frame.columns)} columns against {len(ref.columns)} at 125")


def test_state_ledger_column_count_grows():
    # THE DEFECT, asserted. Without this the suite would still pass if the parent stopped growing
    # for some unrelated reason, and we would silently lose the evidence that NarrowLedger is
    # still buying anything. Strictly increasing is the claim: more bars means more dead legs.
    widths = [len(_run(n, StateLedger())[0].columns) for n in (125, 250, 400)]
    assert widths[0] < widths[1] < widths[2], (
        f"expected StateLedger's row to widen with bars, got {widths}")
    # And the narrow ledger must be dramatically narrower at the longest of them, or the fix is
    # doing nothing worth the extra class.
    narrow_width = len(_run(400, NarrowLedger())[0].columns)
    assert narrow_width < widths[-1] / 2


def test_no_fragmentation_warning():
    # StateLedger._put_row widens the frame one column at a time (`ledger.py:88-90`), which is
    # what pandas' "DataFrame is highly fragmented" PerformanceWarning points at. A constant-width
    # row can never trigger it, at any run length.
    #
    # Only the NARROW side is asserted. The parent's warning fires on pandas' own internal
    # block-count threshold, which a 400-bar / ~6-roll test run does not reach — it needs the
    # ~82 rolls of the real 5,201-bar window. Asserting the parent warns HERE would be asserting
    # a pandas implementation detail at a size that does not exercise it; the growth itself is
    # already pinned by test_state_ledger_column_count_grows, which is the real claim.
    import warnings
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        _run(400, NarrowLedger())
    assert not [w for w in recorded if issubclass(w.category, pd.errors.PerformanceWarning)]


# ============================================================================
# THE df CONTRACT — the single easiest thing to break with a lazy property.
# ============================================================================

def test_df_returns_the_same_object_and_caller_columns_survive():
    # PortfolioSimulator.run() reads ledger.df ONCE and then writes equity_peak / drawdown /
    # turnover_cum onto it (`simulator.py:116-127`). A property that rebuilt the frame per access
    # would hand back a fresh object and those three columns would vanish without a trace.
    state, sim = _run(120, NarrowLedger())
    assert sim.ledger.df is sim.ledger.df, "df must be cached, not rebuilt per access"
    assert state is sim.ledger.df, "run() must have been writing onto the ledger's own frame"
    for col in ["equity_peak", "drawdown", "turnover_cum"]:
        assert col in sim.ledger.df.columns, f"{col} did not survive on the ledger's frame"


def test_re_marking_a_bar_overwrites_rather_than_appends():
    # StateLedger's documented contract: one row per timestamp, a re-mark of the same bar
    # OVERWRITES. kts.py depends on this (it re-marks after a manual trade), and a subclass that
    # appended instead would double every such bar.
    led = NarrowLedger()
    book = Book(default_symbol=UNDERLYING, base_equity=1000.0)
    ts = pd.Timestamp("2020-01-02")
    led.record_book(ts, book, {UNDERLYING: 100.0}, extra={"equity": 1000.0})
    led.record_book(ts, book, {UNDERLYING: 101.0}, extra={"equity": 1001.0})
    assert len(led.df) == 1
    assert led.df.loc[ts, "equity"] == 1001.0


def test_reset_clears_rows_and_the_cache():
    led = NarrowLedger()
    book = Book(default_symbol=UNDERLYING, base_equity=1000.0)
    led.record_book(pd.Timestamp("2020-01-02"), book, {UNDERLYING: 100.0})
    assert len(led.df) == 1
    led.reset()
    # Both halves matter: a reset that emptied the frame but kept _rows would silently resurrect
    # the previous run's bars on the next record.
    assert len(led.df) == 0
    assert led._rows == {}


def test_closed_units_is_a_within_bar_figure():
    # record_book must clear the per-bar counters immediately after writing, exactly as the
    # parent does — otherwise closed_units becomes cumulative and every consumer misreads it.
    led = NarrowLedger()
    book = Book(default_symbol=UNDERLYING, base_equity=0.0)
    from portutils.portfolio.fills import Fill
    book.apply_fill(Fill(order_id=1, side=1, qty=10.0, price=100.0, ts="t0", symbol=UNDERLYING))
    book.apply_fill(Fill(order_id=2, side=-1, qty=4.0, price=105.0, ts="t1", symbol=UNDERLYING))
    row = led.record_book(pd.Timestamp("2020-01-02"), book, {UNDERLYING: 105.0})
    assert row["TOTAL_closed_units"] == pytest.approx(4.0)
    row2 = led.record_book(pd.Timestamp("2020-01-03"), book, {UNDERLYING: 106.0})
    assert row2["TOTAL_closed_units"] == pytest.approx(0.0)


def test_single_asset_record_path_still_works():
    # `record()` is inherited untouched, and it routes through the overridden _put_row. It is out
    # of this plan's scope to narrow, but it must not be silently BROKEN by the lazy frame: a
    # subclass that only overrode record_book would leave record() mutating a cached frame that
    # _rows knew nothing about.
    led = NarrowLedger()
    book = Book(default_symbol=UNDERLYING, base_equity=1000.0)
    led.record(pd.Timestamp("2020-01-02"), book, 100.0)
    led.record(pd.Timestamp("2020-01-03"), book, 101.0)
    assert len(led.df) == 2
    assert "position" in led.df.columns
