"""
Migration test — kts.py's accounting path still behaves identically after delegating to
``portutils.portfolio``.

``tests/test_book_parity.py`` proves the extracted Book reproduces the old numbers. This
file proves the REWIRING is correct too: that the sim_* property shims read and write the
book, that ``SimExecutionBackend`` → ``apply_fill`` → ``StateLedger`` still lands the same
row in ``state_df``, and that the §7.5 order ledger still gets built.

kts.py is a Tk script, so a real ``KalmanTradingApp`` cannot be constructed here. Instead
we build a HEADLESS instance with ``object.__new__`` and attach only the attributes the
accounting path touches — no Tk root, no IBKR connection. That is only possible because
the accounting path no longer depends on either, which is the point of the refactor.
"""

import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
KTS = REPO / "orders" / "kts.py"
ORDERS_JSON = REPO / "orders" / "replay_orders.json"
STATE_JSON = REPO / "orders" / "replay_state.json"

pytest.importorskip("matplotlib", reason="kts.py imports matplotlib at module scope")


@pytest.fixture(scope="module")
def kts():
    # Import kts.py by path (it lives in orders/, not on the package path). Safe to import:
    # everything runnable sits behind `if __name__ == "__main__"`. Tk is imported at module
    # scope but no window is created until main() runs.
    spec = importlib.util.spec_from_file_location("kts_under_test", KTS)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                       # pragma: no cover - environment guard
        pytest.skip(f"kts.py not importable in this environment: {exc}")
    return mod


class _FakeContract:
    # The order ledger reads symbol/secType/exchange/currency off the contract; that is the
    # only reason _register_sim_order needs the GUI at all.
    symbol, secType, exchange, currency, conId = "BTC", "CRYPTO", "PAXOS", "USD", 0


class _FakeIB:
    # _sim_base_equity prefers IBKR NetLiq; a plain float stands in for the account summary.
    account_value = 1_000_000.0


def _headless_app(kts):
    # Build the app object WITHOUT running __init__ (which builds the whole Tk UI), then
    # attach by hand exactly the attributes the accounting path reads. If this list ever
    # needs to grow, that is a signal the accounting path has re-acquired a GUI dependency.
    app = object.__new__(kts.KalmanTradingApp)
    app.book = kts.Book(default_symbol="")
    app.ledger = kts.StateLedger()
    app.exec_backend = kts.SimExecutionBackend(app.book, on_order=app._register_sim_order)
    app.orders = {}
    app._last_forecast_view = None                 # no recommendation drove these trades
    app.ib = _FakeIB()
    app.cash = 1_000_000.0
    app.contract = lambda: _FakeContract()
    return app


def test_property_shims_read_and_write_the_book(kts):
    app = _headless_app(kts)
    # Write through the shim, read off the book: they must be the same storage, not copies.
    app.sim_position = 3.0
    app.sim_avg_entry = 100.0
    app.sim_realised = 42.0
    assert app.book.position().qty == 3.0
    assert app.book.position().avg_entry == 100.0
    assert app.book.position().realised == 42.0
    # ...and the other direction (this is the path the replay scrubber's rewind relies on).
    app.book.position().qty = -1.5
    assert app.sim_position == -1.5
    assert app._sim_unrealised(110.0) == pytest.approx(-1.5 * (110.0 - 100.0))
    # state_df is a view onto the ledger's frame, not a second copy.
    assert app.state_df is app.ledger.df


def test_replay_through_the_migrated_path_matches_the_original_export(kts):
    # End-to-end: drive the SAME trades the original engine executed, through the migrated
    # exec_backend → apply_fill → ledger chain, and compare the resulting state_df rows to
    # the recorded export. This is the parity gate applied to the WIRING rather than the maths.
    if not (ORDERS_JSON.exists() and STATE_JSON.exists()):
        pytest.skip("replay fixtures not present")
    with open(ORDERS_JSON, encoding="utf-8") as f:
        orders = json.load(f)
    with open(STATE_JSON, encoding="utf-8") as f:
        state = json.load(f)

    # Ledger fills keyed by bar timestamp, as (side, qty, price) — the arguments the app
    # passes to exec_backend.execute at each bar of a replay.
    trades = {}
    for oid, o in orders.items():
        side = +1 if o["metadata"]["action"] == "BUY" else -1
        for ev in o["history"]:
            rec = ev.get("fill")
            if rec is not None:
                trades.setdefault(rec["ts"], []).append((int(oid), side, rec["qty"], rec["price"]))
    for v in trades.values():
        v.sort()

    app = _headless_app(kts)
    for row in state:
        ts = row["ts"]
        key = ts[:-4] if ts.endswith(".000") else ts
        # Trade first, then mark — the order _build_replay uses at each bar.
        for _oid, side, qty, price in trades.get(key, []):
            app.exec_backend.execute(side, qty, price, key)
        app._record_state_row(key, row["last_price"])

    df = app.state_df
    assert len(df) == len(state)
    for row in state:
        key = row["ts"][:-4] if row["ts"].endswith(".000") else row["ts"]
        got = df.loc[key]
        for col in ("position", "avg_entry_price", "entry_cost", "mark_value",
                    "unrealised_pnl", "realised_pnl", "total_pnl", "last_price"):
            assert got[col] == pytest.approx(row[col], abs=1e-9), f"{col} @ {key}"
        # portfolio_pnl is assembled by kts (base equity + book P&L), so it exercises the
        # strategy-side half of the row that the ledger does NOT write.
        assert got["portfolio_pnl"] == pytest.approx(row["portfolio_pnl"], abs=1e-9)

    # The §7.5 order ledger must still be populated by the on_order callback, one Order per
    # replay order, each serializing to the dummy_orders.json shape.
    assert len(app.orders) == len(orders)
    sample = app.orders[1].to_dict()
    assert sample["metadata"]["contract"]["symbol"] == "BTC"
    assert sample["state"]["lifecycle"] == "SIM_FILLED"
    assert sample["state"]["realised_cum"] == pytest.approx(
        float(orders["1"]["state"]["realised_cum"]), abs=1e-9)


def test_reset_accounting_clears_book_ledger_and_orders(kts):
    app = _headless_app(kts)
    app.exec_backend.execute(+1, 2.0, 100.0, "2026-01-01T00:00:00")
    app._record_state_row("2026-01-01T00:00:00", 105.0)
    assert app.sim_position == 2.0 and len(app.state_df) == 1 and app.orders

    app._reset_accounting()
    # book.reset() replaced four individual attribute assignments — verify it really does
    # clear every one of them, plus the frame and the order ledger.
    assert app.sim_position == 0.0
    assert app.sim_avg_entry == 0.0
    assert app.sim_realised == 0.0
    assert app._sim_closed_this_bar == 0.0
    assert len(app.state_df) == 0
    assert app.orders == {}
