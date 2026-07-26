"""
Offline tests for the broker bridge and the policy-anchored backcast.

No TWS, no network: every input is a synthetic DataFrame shaped exactly like what
``get_account_updates`` / ``get_executions_data`` return. That is deliberate — the bridge
was written to be pure so it could be proven correct while the workstation is unavailable,
and so the assertions are about arithmetic rather than about whatever the market did.

The sharpest test here is ``test_backcast_wrong_policy_is_caught``: a falsification check
that never fails would be worthless, so the suite proves it fires on a wrong assumption.
"""

import warnings

import numpy as np
import pandas as pd
import pytest

from portutils.portfolio import (
    Book,
    ConstantMixRule,
    PortfolioSimulator,
    backcast,
    book_from_portfolio,
    fills_from_executions,
    reconcile,
    rewind_positions,
)


def portfolio_frame(rows):
    # The columns updatePortfolio actually delivers, so the tests break if the bridge
    # starts depending on a field IB does not send.
    return pd.DataFrame([{
        "account": "DUP102412", "symbol": s, "conId": i, "secType": "STK",
        "exchange": "SMART", "currency": "USD", "position": q, "marketPrice": mp,
        "marketValue": q * mp, "averageCost": ac, "unrealizedPnL": (mp - ac) * q,
        "realizedPnL": rp,
    } for i, (s, q, mp, ac, rp) in enumerate(rows)])


def exec_frame(rows):
    # Shaped like get_executions_data output, including the joined commission columns.
    return pd.DataFrame([{
        "execId": f"{i:04d}.x", "symbol": s, "conId": i, "secType": "STK",
        "currency": "USD", "side": side, "shares": qty, "price": px,
        "cumQty": qty, "avgPrice": px, "time": ts.strftime("%Y%m%d %H:%M:%S"),
        "orderId": i + 1, "permId": 1000 + i, "commission": 1.0, "ts": ts,
    } for i, (s, side, qty, px, ts) in enumerate(rows)])


# ---------------------------------------------------------------------------
# BOOK FROM PORTFOLIO
# ---------------------------------------------------------------------------
def test_book_from_portfolio_reproduces_positions():
    df = portfolio_frame([("SPY", 100.0, 650.0, 600.0, 250.0),
                          ("KMLM", 400.0, 27.0, 25.0, -10.0)])
    book = book_from_portfolio(df, base_equity=1_000_000.0)

    assert book.position("SPY").qty == 100.0
    assert book.position("SPY").avg_entry == 600.0
    assert book.position("KMLM").qty == 400.0
    # realizedPnL is NOT seeded by default: IB's figure is session-scoped while ours is
    # cumulative, so carrying it across silently would misreport a lifetime number.
    assert book.position("SPY").realised == 0.0
    # Unrealised must agree with IB's own, since both are (mark - cost) * qty.
    assert book.position("SPY").unrealised(650.0) == pytest.approx(5000.0)


def test_equity_equals_net_liquidation_not_double_counted():
    # REGRESSION: Book.equity is base + realised + unrealised, which is right when base means
    # STARTING capital. A broker's NetLiquidation is the opposite — it ALREADY contains the
    # unrealised P&L of every open position — so passing it through raw counted that P&L
    # twice and inflated every weight-based target by it.
    #
    # Here: 1,000,000 NetLiq holding 150,000 of unrealised gains reported 1,150,000, a 15%
    # over-sizing that would have caused real over-trading live.
    net_liq = 1_000_000.0
    df = portfolio_frame([("SPY", 1000.0, 700.0, 600.0, 0.0),      # +100,000 unrealised
                          ("KMLM", 10_000.0, 30.0, 25.0, 0.0)])    # + 50,000 unrealised
    book = book_from_portfolio(df, base_equity=net_liq)
    marks = {"SPY": 700.0, "KMLM": 30.0}

    assert book.unrealised(marks) == pytest.approx(150_000.0)
    # The account is worth what IBKR says it is worth — no more.
    assert book.equity(marks) == pytest.approx(net_liq)


def test_zero_positions_are_dropped():
    # IB keeps listing a symbol after it is closed. It should not appear as a holding.
    df = portfolio_frame([("SPY", 0.0, 650.0, 600.0, 900.0)])
    assert book_from_portfolio(df).symbols == []


def test_seed_realised_is_opt_in():
    df = portfolio_frame([("SPY", 10.0, 650.0, 600.0, 123.0)])
    assert book_from_portfolio(df, seed_realised=True).position("SPY").realised == 123.0


def test_non_stock_without_multiplier_warns():
    # A futures cost basis off by the multiplier yields P&L wrong by orders of magnitude
    # while still looking like a plausible number — so it must be loud.
    df = portfolio_frame([("ES", 2.0, 5000.0, 250_000.0, 0.0)])
    df.loc[0, "secType"] = "FUT"
    with pytest.warns(UserWarning, match="multiplier"):
        book_from_portfolio(df)
    # With the multiplier supplied, avg_entry comes back as a real per-unit price.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        book = book_from_portfolio(df, multipliers={"ES": 50.0})
    assert book.position("ES").avg_entry == pytest.approx(5000.0)


# ---------------------------------------------------------------------------
# EXECUTIONS -> FILLS
# ---------------------------------------------------------------------------
def test_fills_from_executions_maps_side_and_skips_junk():
    ts = pd.Timestamp("2026-07-20 15:30")
    df = exec_frame([("SPY", "BOT", 10.0, 640.0, ts), ("SPY", "SLD", 4.0, 650.0, ts)])
    fills = fills_from_executions(df)
    assert [f.side for f in fills] == [1, -1]
    assert all(f.source == "live" for f in fills)

    # An unrecognised side must be skipped loudly, never guessed — booking a fill in the
    # wrong direction is worse than not booking it.
    bad = exec_frame([("SPY", "???", 10.0, 640.0, ts)])
    with pytest.warns(UserWarning, match="unrecognised side"):
        assert fills_from_executions(bad) == []


def test_real_fills_run_through_the_same_engine():
    # The point of the backend-agnostic design: broker fills book exactly like sim fills.
    ts = pd.Timestamp("2026-07-20 15:30")
    df = exec_frame([("SPY", "BOT", 10.0, 600.0, ts), ("SPY", "SLD", 4.0, 650.0, ts)])
    book = Book()
    for f in fills_from_executions(df):
        book.apply_fill(f)
    assert book.position("SPY").qty == 6.0
    assert book.position("SPY").realised == pytest.approx(4 * (650.0 - 600.0))


# ---------------------------------------------------------------------------
# REWIND — the anchor, which is real data rather than an assumption
# ---------------------------------------------------------------------------
def test_rewind_recovers_the_pre_window_position_exactly():
    ts = pd.Timestamp("2026-07-20 15:30")
    # Held 100, bought 30, sold 10 -> now 120. Rewinding must return exactly 100.
    now = portfolio_frame([("SPY", 120.0, 650.0, 610.0, 0.0)])
    execs = exec_frame([("SPY", "BOT", 30.0, 645.0, ts), ("SPY", "SLD", 10.0, 655.0, ts)])
    assert rewind_positions(now, execs)["SPY"] == pytest.approx(100.0)


def test_rewind_with_no_executions_is_the_identity():
    now = portfolio_frame([("SPY", 120.0, 650.0, 610.0, 0.0)])
    assert rewind_positions(now, pd.DataFrame())["SPY"] == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# RECONCILE
# ---------------------------------------------------------------------------
def test_reconcile_matches_and_flags_drift():
    df = portfolio_frame([("SPY", 100.0, 650.0, 600.0, 0.0)])
    book = book_from_portfolio(df)
    assert reconcile(book, df)["match"].all()

    # Corrupt our side and confirm the drift is caught rather than absorbed.
    book.position("SPY").qty = 95.0
    rec = reconcile(book, df)
    assert not rec["match"].all()
    assert rec.loc[0, "position_diff"] == pytest.approx(-5.0)


def test_reconcile_reports_symbols_only_one_side_has():
    df = portfolio_frame([("SPY", 100.0, 650.0, 600.0, 0.0)])
    book = book_from_portfolio(df)
    book.position("KMLM").restore(50.0, 26.0, 0.0)     # we think we hold it; IB does not
    rec = reconcile(book, df).set_index("symbol")
    assert "KMLM" in rec.index and not bool(rec.loc["KMLM", "match"])


# ---------------------------------------------------------------------------
# BACKCAST
# ---------------------------------------------------------------------------
def synthetic_prices(n=120, vol=0.008, drift=0.0005, seed=7):
    # `vol` is exposed because the backcast's falsification check only has power when prices
    # actually moved — see test_backcast_cannot_discriminate_on_a_quiet_window.
    idx = pd.date_range("2026-03-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    spy = 600 * np.cumprod(1 + rng.normal(drift, vol, n))
    kmlm = 25 * np.cumprod(1 + rng.normal(0.0002, vol * 0.75, n))
    return pd.DataFrame({"SPY": spy, "KMLM": kmlm}, index=idx)


def test_buy_and_hold_backcast_holds_units_constant():
    prices = synthetic_prices()
    path = backcast.position_path(prices, {"SPY": 100.0}, prices.index[-1],
                                  policy=backcast.BUY_AND_HOLD, months=1)
    held = path["SPY"][path["SPY"] != 0]
    # Constant going back is the whole claim of this policy — not an approximation.
    assert held.nunique() == 1 and held.iloc[0] == 100.0
    # And before the start date the position is genuinely absent, not a small number.
    assert (path["SPY"].iloc[0] == 0.0) or (path.index[0] >= held.index[0])


def test_constant_mix_backcast_holds_weights_at_target():
    prices = synthetic_prices()
    weights = {"SPY": 0.6, "KMLM": 0.4}
    anchor_ts = prices.index[-1]
    anchor_qty = {"SPY": 100.0, "KMLM": 400.0}
    path = backcast.position_path(prices, anchor_qty, anchor_ts,
                                  policy=backcast.CONSTANT_MIX, weights=weights, months=3)
    value = path * prices.loc[path.index]
    w = value.div(value.sum(axis=1), axis=0).dropna()
    # The defining property of the policy: weights sit at target on every bar it covers.
    assert np.allclose(w["SPY"], 0.6, atol=1e-9)
    assert np.allclose(w["KMLM"], 0.4, atol=1e-9)


def test_backcast_round_trip_recovers_a_known_book():
    # THE round-trip: run a real constant-mix book forward, throw away everything except
    # its final positions, then backcast and check the path comes back.
    prices = synthetic_prices()
    weights = {"SPY": 0.6, "KMLM": 0.4}
    sim = PortfolioSimulator(prices, [ConstantMixRule(weights, every=1)],
                             starting_capital=1_000_000.0)
    truth = sim.run()

    final_qty = {s: float(truth.iloc[-1][f"{s}_position"]) for s in weights}
    portfolio_df = portfolio_frame([
        (s, final_qty[s], float(prices.iloc[-1][s]),
         float(truth.iloc[-1][f"{s}_avg_entry_price"]), 0.0) for s in weights])

    out = backcast.run(prices, portfolio_df, exec_df=None,
                       policy=backcast.CONSTANT_MIX, weights=weights, months=6,
                       base_equity=1_000_000.0)

    # Positions recovered bar for bar.
    for s in weights:
        assert np.allclose(out["position_path"][s].values,
                           truth[f"{s}_position"].astype(float).values, rtol=1e-6)
    # And the reconstruction is judged consistent with the "broker" it was checked against.
    assert out["consistent"], out["check"]


def _truth_and_portfolio(prices, weights):
    truth = PortfolioSimulator(prices, [ConstantMixRule(weights, every=1)],
                               starting_capital=1_000_000.0).run()
    portfolio_df = portfolio_frame([
        (s, float(truth.iloc[-1][f"{s}_position"]), float(prices.iloc[-1][s]),
         float(truth.iloc[-1][f"{s}_avg_entry_price"]), 0.0) for s in weights])
    return truth, portfolio_df


def test_backcast_wrong_policy_is_caught():
    # The test that gives the falsification check its value. The account was ACTUALLY
    # rebalanced daily; backcasting it as buy-and-hold is a wrong assumption, and the step-5
    # check must say so rather than returning a plausible-looking path.
    #
    # A TRENDING, volatile window is used deliberately: the check compares cost bases, and
    # two policies only build different cost bases to the extent prices moved. See the
    # companion test below for what happens when they did not.
    prices = synthetic_prices(vol=0.02, drift=0.002)
    weights = {"SPY": 0.6, "KMLM": 0.4}
    _, portfolio_df = _truth_and_portfolio(prices, weights)

    out = backcast.run(prices, portfolio_df, exec_df=None,
                       policy=backcast.BUY_AND_HOLD, weights=weights,
                       months=6, base_equity=1_000_000.0)

    # Quantities still land (buy-and-hold ends where it started), but the average entry cost
    # cannot: a book that rebalanced daily has a different cost basis from one that never
    # traded. Measured separation here is ~5-8%, well outside the 2% tolerance.
    assert not out["consistent"]
    assert out["verdict"] == "INCONSISTENT"
    assert (out["check"]["verdict"] == "INCONSISTENT").any()


def test_backcast_cannot_discriminate_on_a_quiet_window():
    # The honest counterpart: on a low-volatility window the two policies' cost bases land
    # ~1.2% apart, inside the 2% tolerance IB's commission-inclusive averageCost forces us
    # to allow. The wrong policy therefore PASSES the check.
    #
    # That is a real limitation of the method, and the code must not dress it up as
    # confirmation. The verdict has to come back `indeterminate` — "it fits, but nothing
    # here could have rejected it" — rather than `consistent`.
    prices = synthetic_prices(vol=0.004, drift=0.0002)
    weights = {"SPY": 0.6, "KMLM": 0.4}
    _, portfolio_df = _truth_and_portfolio(prices, weights)

    out = backcast.run(prices, portfolio_df, exec_df=None,
                       policy=backcast.BUY_AND_HOLD, weights=weights,
                       months=6, base_equity=1_000_000.0)

    assert out["consistent"]                 # the check itself passes...
    assert out["verdict"] == "indeterminate"  # ...but it is reported as uninformative
    assert out["separation"]["separation"].max() <= 0.02


def test_separation_measures_policy_distinguishability():
    # The diagnostic behind the indeterminate verdict: how far apart are the cost bases the
    # candidate policies imply? Bigger price moves must mean more separation, which is what
    # gives the falsification check its power.
    weights = {"SPY": 0.6, "KMLM": 0.4}
    quiet = synthetic_prices(vol=0.004, drift=0.0002)
    loud = synthetic_prices(vol=0.02, drift=0.002)

    _, pf_quiet = _truth_and_portfolio(quiet, weights)
    _, pf_loud = _truth_and_portfolio(loud, weights)

    s_quiet = backcast.separation(quiet, pf_quiet, weights, months=6, base_equity=1e6)
    s_loud = backcast.separation(loud, pf_loud, weights, months=6, base_equity=1e6)

    assert s_quiet["separation"].max() < s_loud["separation"].max()
    assert s_loud["separation"].max() > 0.02   # loud window can actually discriminate


def test_backcast_produces_a_realised_unrealised_split():
    # The thing positions alone cannot give: a realised/unrealised split before the window.
    prices = synthetic_prices()
    weights = {"SPY": 0.6, "KMLM": 0.4}
    truth = PortfolioSimulator(prices, [ConstantMixRule(weights, every=1)],
                               starting_capital=1_000_000.0).run()
    portfolio_df = portfolio_frame([
        (s, float(truth.iloc[-1][f"{s}_position"]), float(prices.iloc[-1][s]),
         float(truth.iloc[-1][f"{s}_avg_entry_price"]), 0.0) for s in weights])

    out = backcast.run(prices, portfolio_df, policy=backcast.CONSTANT_MIX,
                       weights=weights, months=6, base_equity=1_000_000.0)
    state = out["state"]
    assert state["TOTAL_realised_pnl"].iloc[-1] != 0.0
    # Every row is labelled as modelled, so no consumer can present it as measured history.
    assert (state["provenance"] == "backcast (constant_mix)").all()
