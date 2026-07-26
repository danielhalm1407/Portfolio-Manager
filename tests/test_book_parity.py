"""
GOLDEN PARITY TEST — the extracted ``Book`` must reproduce kts.py's numbers exactly.

``orders/replay_orders.json`` and ``orders/replay_state.json`` are real exports from a
replay run of the ORIGINAL in-app accounting engine (``KalmanTradingApp.apply_fill`` and
friends). This test rebuilds the same run through ``portutils.portfolio.Book`` and asserts
the position, average entry, realised and unrealised match bar-for-bar.

That is the whole justification for the extraction being safe: if this passes, the move out
of kts.py changed nothing about what the app computes. It is the gate that had to go green
BEFORE kts.py was rewired to delegate here.

Skips (rather than fails) if the fixtures are absent, so a fresh clone without a replay
export still has a green suite.
"""

import json
import pathlib

import pytest

from portutils.portfolio import Book, Fill

ORDERS_JSON = pathlib.Path(__file__).resolve().parents[1] / "orders" / "replay_orders.json"
STATE_JSON = pathlib.Path(__file__).resolve().parents[1] / "orders" / "replay_state.json"

# The replay ran on a single instrument; every fill in the export belongs to it.
SYMBOL = "BTC"
# Float tolerance. The exports are JSON round-trips of float64, and the arithmetic is
# identical, so this is about serialization noise only — not a fudge factor.
TOL = 1e-9


def _load():
    if not (ORDERS_JSON.exists() and STATE_JSON.exists()):
        pytest.skip("replay fixtures not present (run a replay + export from kts.py first)")
    with open(ORDERS_JSON, encoding="utf-8") as f:
        orders = json.load(f)
    with open(STATE_JSON, encoding="utf-8") as f:
        state = json.load(f)
    return orders, state


def _fills_from_orders(orders):
    # Reconstruct the Fill stream from the ledger. Each sim order fills once, in the
    # "Filled" history event — that event carries qty/price/ts, and the order metadata
    # carries the BUY/SELL side. Sorted by (ts, order_id) so the replay order is exact:
    # order of application matters for VWAP, so getting this wrong would fail loudly.
    out = []
    for oid, o in orders.items():
        meta = o["metadata"]
        side = +1 if meta["action"] == "BUY" else -1
        for ev in o["history"]:
            rec = ev.get("fill")
            if rec is None:
                continue
            out.append((rec["ts"], int(oid), Fill(
                order_id=int(oid), ts=rec["ts"], side=side,
                qty=float(rec["qty"]), price=float(rec["price"]), symbol=SYMBOL,
            ), o))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def test_fill_by_fill_realised_matches_ledger():
    # Check 1 — per-ORDER: after applying each fill, cumulative realised and the
    # mark-to-fill unrealised must equal what the original engine stamped into the
    # order's `state` block at that moment.
    orders, _ = _load()
    book = Book(default_symbol=SYMBOL)
    for ts, oid, fill, order in _fills_from_orders(orders):
        realised_delta = book.apply_fill(fill)
        pos = book.position()
        expected_cum = float(order["state"]["realised_cum"])
        expected_unreal = float(order["state"]["unrealised"])
        assert pos.realised == pytest.approx(expected_cum, abs=TOL), f"order {oid} realised_cum"
        assert pos.unrealised(fill.price) == pytest.approx(expected_unreal, abs=TOL), \
            f"order {oid} unrealised"
        # The Submitted event stored the PRE-fill cumulative; realised_cum − that is the
        # delta this fill booked, which is exactly what apply_fill now returns.
        pre = float(order["history"][0]["pnl"]["realised_cum"])
        assert realised_delta == pytest.approx(expected_cum - pre, abs=TOL), f"order {oid} delta"


def test_bar_by_bar_state_matches_state_df():
    # Check 2 — per-BAR: march the whole replay timeline, applying any fills stamped at
    # that bar, then compare the marked book against the recorded state_df row. This
    # exercises grow/reduce/flip in their real sequence AND the marking path.
    orders, state = _load()
    fills_by_ts = {}
    for ts, oid, fill, _order in _fills_from_orders(orders):
        fills_by_ts.setdefault(ts, []).append(fill)

    book = Book(default_symbol=SYMBOL)
    checked = 0
    for row in state:
        # state_df timestamps carry milliseconds ("...T04:00:00.000"); the ledger's do
        # not. Normalize by trimming a trailing ".000" so the two indices join.
        ts = row["ts"]
        key = ts[:-4] if ts.endswith(".000") else ts
        # The replay executes the bar's trade FIRST, then records the row at bar close —
        # so fills must be applied before the comparison, matching _build_replay's order.
        for fill in fills_by_ts.get(key, []):
            book.apply_fill(fill)

        pos = book.position()
        price = row["last_price"]
        assert pos.qty == pytest.approx(row["position"], abs=TOL), f"position @ {ts}"
        assert pos.avg_entry == pytest.approx(row["avg_entry_price"], abs=TOL), f"avg_entry @ {ts}"
        assert pos.realised == pytest.approx(row["realised_pnl"], abs=TOL), f"realised @ {ts}"
        assert pos.unrealised(price) == pytest.approx(row["unrealised_pnl"], abs=TOL), \
            f"unrealised @ {ts}"
        # snapshot() is what the ledger will write, so verify the derived columns too.
        snap = pos.snapshot(price)
        assert snap["entry_cost"] == pytest.approx(row["entry_cost"], abs=TOL), f"entry_cost @ {ts}"
        assert snap["mark_value"] == pytest.approx(row["mark_value"], abs=TOL), f"mark_value @ {ts}"
        assert snap["total_pnl"] == pytest.approx(row["total_pnl"], abs=TOL), f"total_pnl @ {ts}"
        checked += 1

    # Guard against a silently empty fixture making this test vacuous.
    assert checked > 0
    # Every ledger fill must have been consumed by some bar — a leftover key would mean
    # the ts normalization above is wrong and the test never applied that trade.
    unmatched = set(fills_by_ts) - {(r["ts"][:-4] if r["ts"].endswith(".000") else r["ts"])
                                    for r in state}
    assert not unmatched, f"fills never applied: {sorted(unmatched)}"
