"""
Unit tests for the §4b accounting rules now living in ``portutils.portfolio.book``.

These pin the four behaviours the engine must never get wrong — VWAP on grow, avg entry
untouched on a pure reduce, avg entry cleared on a full close, and the two-leg split on a
flip — plus short-side sign symmetry and per-symbol independence in a multi-asset ``Book``.
They are deliberately arithmetic-only (hand-computed expectations, no fixtures from a run)
so a regression shows up as a wrong NUMBER, not just a changed output.
"""

from datetime import datetime, timedelta

import pytest

from portutils.portfolio import Book, Fill, Position

T0 = datetime(2026, 1, 1, 9, 30)


def mk(side, qty, price, i=0, symbol=""):
    # Terse Fill factory: the tests care about side/qty/price only, so everything else
    # takes its default. `i` just spaces the timestamps so ordering is inspectable.
    return Fill(order_id=i + 1, ts=T0 + timedelta(minutes=i), side=side,
                qty=qty, price=price, symbol=symbol)


# ---------------------------------------------------------------------------
# GROW — volume-weighted average entry.
# ---------------------------------------------------------------------------
def test_grow_long_vwaps_entry():
    p = Position("X")
    assert p.apply_fill(mk(+1, 10, 100.0)) == 0.0      # opening books no realised P&L
    assert p.qty == 10 and p.avg_entry == 100.0
    # Add 10 more at 110 → VWAP = (100*10 + 110*10) / 20 = 105.
    assert p.apply_fill(mk(+1, 10, 110.0, 1)) == 0.0
    assert p.qty == 20
    assert p.avg_entry == pytest.approx(105.0)


def test_grow_short_vwaps_entry():
    p = Position("X")
    p.apply_fill(mk(-1, 10, 100.0))
    p.apply_fill(mk(-1, 30, 120.0, 1))
    # Short book: VWAP uses ABSOLUTE units, so (100*10 + 120*30)/40 = 115.
    assert p.qty == -40
    assert p.avg_entry == pytest.approx(115.0)


# ---------------------------------------------------------------------------
# REDUCE / CLOSE — realised = sign(pos)·(price − avg)·units, entry preserved.
# ---------------------------------------------------------------------------
def test_partial_reduce_keeps_avg_entry():
    p = Position("X")
    p.apply_fill(mk(+1, 20, 100.0))
    realised = p.apply_fill(mk(-1, 5, 130.0, 1))
    # +1 · (130 − 100) · 5 = +150 crystallised; the surviving 15 units keep entry 100.
    assert realised == pytest.approx(150.0)
    assert p.realised == pytest.approx(150.0)
    assert p.qty == 15
    assert p.avg_entry == pytest.approx(100.0)
    assert p.closed_this_bar == pytest.approx(5.0)


def test_full_close_zeroes_avg_entry():
    p = Position("X")
    p.apply_fill(mk(+1, 20, 100.0))
    p.apply_fill(mk(-1, 20, 90.0, 1))
    # Flat book: entry must reset to 0 or the next open would VWAP against a ghost.
    assert p.qty == 0
    assert p.avg_entry == 0.0
    assert p.realised == pytest.approx(-200.0)
    assert p.unrealised(500.0) == 0.0          # flat → no mark-to-market, whatever the price


def test_short_side_sign_symmetry():
    p = Position("X")
    p.apply_fill(mk(-1, 10, 100.0))            # short 10 @ 100
    realised = p.apply_fill(mk(+1, 10, 90.0, 1))   # buy back at 90 → short profits
    # sign(pos) = −1, so −1 · (90 − 100) · 10 = +100.
    assert realised == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# FLIP — close the old leg, open the remainder fresh at the fill price.
# ---------------------------------------------------------------------------
def test_flip_splits_into_close_then_open():
    p = Position("X")
    p.apply_fill(mk(+1, 10, 100.0))
    realised = p.apply_fill(mk(-1, 25, 120.0, 1))
    # Leg 1: close all 10 longs at 120 → +1·(120−100)·10 = +200.
    assert realised == pytest.approx(200.0)
    # Leg 2: the remaining 15 units open SHORT at the fill price, with a fresh entry.
    assert p.qty == -15
    assert p.avg_entry == pytest.approx(120.0)
    assert p.closed_this_bar == pytest.approx(10.0)   # only the closed leg counts


# ---------------------------------------------------------------------------
# MARKING.
# ---------------------------------------------------------------------------
def test_unrealised_signs_and_guards():
    p = Position("X")
    p.apply_fill(mk(+1, 10, 100.0))
    assert p.unrealised(110.0) == pytest.approx(100.0)     # long, price up → gain
    assert p.unrealised(None) == 0.0                       # no mark → 0, never NaN
    assert p.unrealised(float("nan")) == 0.0
    q = Position("Y")
    q.apply_fill(mk(-1, 10, 100.0))
    assert q.unrealised(110.0) == pytest.approx(-100.0)    # short, price up → loss


def test_snapshot_falls_back_to_entry_price_when_unmarked():
    p = Position("X")
    p.apply_fill(mk(+1, 10, 100.0))
    row = p.snapshot(None)
    # Unmarked bars must not poison the row: entry_cost == mark_value, unrealised 0.
    assert row["last_price"] == pytest.approx(100.0)
    assert row["entry_cost"] == pytest.approx(row["mark_value"])
    assert row["unrealised_pnl"] == 0.0


def test_restore_matches_replayed_state():
    replayed = Position("X")
    replayed.apply_fill(mk(+1, 10, 100.0))
    replayed.apply_fill(mk(-1, 4, 120.0, 1))
    # The replay scrubber rewinds by assignment rather than re-running fills; that
    # shortcut is only valid if it lands on exactly the same book.
    jumped = Position("X")
    jumped.restore(replayed.qty, replayed.avg_entry, replayed.realised)
    assert (jumped.qty, jumped.avg_entry, jumped.realised) == \
           (replayed.qty, replayed.avg_entry, replayed.realised)


# ---------------------------------------------------------------------------
# MULTI-SYMBOL BOOK — the capability the kts.py scalars could not express.
# ---------------------------------------------------------------------------
def test_book_keeps_symbols_independent_and_totals_agree():
    b = Book(base_equity=1_000_000.0)
    # Interleave two instruments to prove neither leaks into the other's average entry.
    b.apply_fill(mk(+1, 10, 100.0, 0, "HEDGE"))
    b.apply_fill(mk(+1, 5, 200.0, 1, "BETA"))
    b.apply_fill(mk(+1, 10, 120.0, 2, "HEDGE"))     # HEDGE VWAP → 110
    b.apply_fill(mk(-1, 5, 250.0, 3, "BETA"))       # BETA closes flat: +1·(250−200)·5 = +250

    assert b.position("HEDGE").avg_entry == pytest.approx(110.0)
    assert b.position("HEDGE").realised == 0.0
    assert b.position("BETA").qty == 0
    assert b.position("BETA").realised == pytest.approx(250.0)
    assert b.realised == pytest.approx(250.0)

    prices = {"HEDGE": 130.0, "BETA": 999.0}         # BETA is flat, its price is irrelevant
    assert b.unrealised(prices) == pytest.approx(400.0)   # (130−110)·20
    assert b.total_pnl(prices) == pytest.approx(650.0)
    assert b.equity(prices) == pytest.approx(1_000_650.0)

    snap = b.snapshot(prices)
    # TOTAL must be the sum of the legs — the whole point of the aggregate row.
    assert snap["TOTAL"]["total_pnl"] == pytest.approx(650.0)
    assert snap["TOTAL"]["mark_value"] == pytest.approx(20 * 130.0)
    assert snap["TOTAL"]["portfolio_value"] == pytest.approx(1_000_650.0)


def test_default_symbol_makes_book_behave_like_the_old_scalars():
    # The single-asset compatibility path kts.py relies on: unnamed fills and no-arg
    # lookups both land on default_symbol, so a Book is a drop-in for the three scalars.
    b = Book(default_symbol="BTC")
    b.apply_fill(mk(+1, 2, 50_000.0))               # Fill.symbol left empty
    assert b.symbols == ["BTC"]
    assert b.position().qty == 2
    assert b.unrealised(51_000.0) == pytest.approx(2_000.0)   # scalar mark, no mapping


def test_reset_bar_counters_clears_all_symbols():
    b = Book()
    b.apply_fill(mk(+1, 10, 100.0, 0, "A"))
    b.apply_fill(mk(-1, 4, 110.0, 1, "A"))
    assert b.position("A").closed_this_bar == pytest.approx(4.0)
    b.reset_bar_counters()
    # closed_units is a PER-BAR figure; leaking it across bars would double-count the
    # scatter markers on the position-breakdown plot.
    assert b.position("A").closed_this_bar == 0.0
