"""
Scenario-harness tests: the rules + simulator that answer the hedge-sleeve question.

Prices here are SYNTHETIC and deliberately shaped like the scenario under study — a flat
stretch, a sharp drawdown, then a recovery in which the high-beta leg rebounds harder than
the sleeve. Synthetic rather than cached market data because the assertions need to be about
mechanics (did the cut crystallise the loss? did the rotation stay self-financing? does
equity reconcile?), and those must not depend on what the market happened to do.
"""

import numpy as np
import pandas as pd
import pytest

from portutils.portfolio import (
    Book,
    BuyAndHoldRule,
    DrawdownRotationRule,
    PortfolioSimulator,
    TradeListRule,
)

CAPITAL = 1_000_000.0


def make_prices():
    # 60 daily bars: 10 flat, 20 down (BETA falls ~twice as hard as HEDGE), 30 recovering
    # (BETA rebounds harder — the whole premise of rotating into it for the recovery).
    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    hedge = np.concatenate([
        np.full(10, 100.0),
        np.linspace(100.0, 80.0, 20),
        np.linspace(80.0, 104.0, 30),
    ])
    beta = np.concatenate([
        np.full(10, 50.0),
        np.linspace(50.0, 30.0, 20),
        np.linspace(30.0, 62.0, 30),
    ])
    return pd.DataFrame({"HEDGE": hedge, "BETA": beta}, index=idx)


# ---------------------------------------------------------------------------
# BASELINE.
# ---------------------------------------------------------------------------
def test_buy_and_hold_baseline_reconciles():
    prices = make_prices()
    sim = PortfolioSimulator(prices, [BuyAndHoldRule({"HEDGE": 1.0})], starting_capital=CAPITAL)
    df = sim.run()

    # One opening trade and nothing else — a baseline that traded twice is not a baseline.
    assert len(sim.blotter()) == 1
    # Never sold anything, so realised must stay exactly zero across the whole path and
    # every penny of P&L must sit in unrealised.
    assert (df["HEDGE_realised_pnl"] == 0.0).all()
    last = df.iloc[-1]
    units = CAPITAL / 100.0
    assert last["HEDGE_position"] == pytest.approx(units)
    assert last["TOTAL_unrealised_pnl"] == pytest.approx(units * (104.0 - 100.0))
    # The core accounting identity: equity = starting capital + realised + unrealised.
    assert last["equity"] == pytest.approx(CAPITAL + last["TOTAL_total_pnl"])


# ---------------------------------------------------------------------------
# THE SCENARIO.
# ---------------------------------------------------------------------------
def test_drawdown_rotation_crystallises_the_cut_and_stays_self_financing():
    prices = make_prices()
    rule = DrawdownRotationRule(
        sleeve_weights={"HEDGE": 1.0},
        growth_weights={"BETA": 1.0},
        dd_trigger=0.10,       # cut once the book is 10% off its peak
        cut_frac=0.5,          # sell half the sleeve
        recovery_frac=0.5,     # rotate back once half the loss is clawed back
        max_rotations=1,
    )
    sim = PortfolioSimulator(prices, [rule], starting_capital=CAPITAL)
    df = sim.run()
    blotter = sim.blotter()

    events = [e[1] for e in rule.events]
    assert events == ["OPEN_SLEEVE", "ROTATE_TO_GROWTH", "ROTATE_TO_SLEEVE"]

    # --- the cut ---
    cut_ts = [e[0] for e in rule.events if e[1] == "ROTATE_TO_GROWTH"][0]
    cut_trades = blotter[blotter["ts"] == cut_ts]
    # Exactly two legs: sell half the sleeve, buy the growth basket.
    assert set(cut_trades["symbol"]) == {"HEDGE", "BETA"}
    sold = cut_trades[cut_trades["symbol"] == "HEDGE"].iloc[0]
    bought = cut_trades[cut_trades["symbol"] == "BETA"].iloc[0]
    assert sold["side"] == -1 and bought["side"] == +1
    # Self-financing: the proceeds of the sale fund the purchase exactly. If this drifts,
    # the book is quietly gaining or losing cash that no fill accounts for.
    assert abs(sold["notional"]) == pytest.approx(bought["notional"])
    # Half the units sold, so half remain.
    opening_units = CAPITAL / 100.0
    assert df.loc[cut_ts, "HEDGE_position"] == pytest.approx(opening_units / 2)

    # Selling into a drawdown CRYSTALLISES the loss: realised goes negative at the cut and
    # the sleeve's unrealised shrinks because half the open units are gone. This is the
    # realised/unrealised shift the whole study is about.
    assert df.loc[cut_ts, "HEDGE_realised_pnl"] < 0
    pre_cut = df.index[df.index.get_loc(cut_ts) - 1]
    assert abs(df.loc[cut_ts, "HEDGE_unrealised_pnl"]) < abs(df.loc[pre_cut, "HEDGE_unrealised_pnl"])
    # Crystallising does not change TOTAL P&L — it only relabels unrealised as realised.
    # The counterfactual is the untouched sleeve marked at this same bar's price: trading at
    # the mark can move the split, never the sum. (Comparing against the PREVIOUS bar would
    # be wrong: the price moved between bars, so its total legitimately differs.)
    cut_px = prices.loc[cut_ts, "HEDGE"]
    assert df.loc[cut_ts, "TOTAL_total_pnl"] == pytest.approx(
        opening_units * (cut_px - 100.0), rel=1e-9)

    # --- the rotate-back ---
    back_ts = [e[0] for e in rule.events if e[1] == "ROTATE_TO_SLEEVE"][0]
    back_trades = blotter[blotter["ts"] == back_ts]
    sold_b = back_trades[back_trades["symbol"] == "BETA"].iloc[0]
    bought_h = back_trades[back_trades["symbol"] == "HEDGE"].iloc[0]
    assert abs(sold_b["notional"]) == pytest.approx(bought_h["notional"])
    # The growth leg is fully closed, so its book must be flat with a zeroed entry price.
    assert df.loc[back_ts, "BETA_position"] == pytest.approx(0.0)
    assert df.loc[back_ts, "BETA_avg_entry_price"] == pytest.approx(0.0)

    # --- whole-path identity ---
    # equity must equal starting capital + realised + unrealised at EVERY bar, not just the
    # last one. This is the reconciliation that catches a rotation leaking value.
    assert np.allclose(df["equity"].astype(float),
                       CAPITAL + df["TOTAL_total_pnl"].astype(float))
    # Drawdown column is populated and peaked somewhere in the down leg.
    assert df["drawdown"].max() > 0.10


def test_rotation_beats_hold_when_beta_rebounds_harder():
    # The economic claim being tested: rotating into the harder-rebounding asset during the
    # drawdown ends richer than sitting in the sleeve. With these synthetic paths it must,
    # and if the accounting is wrong the sign of this comparison is the first thing to break.
    prices = make_prices()
    hold = PortfolioSimulator(prices, [BuyAndHoldRule({"HEDGE": 1.0})],
                              starting_capital=CAPITAL).run()
    rot = PortfolioSimulator(prices, [DrawdownRotationRule(
        {"HEDGE": 1.0}, {"BETA": 1.0}, dd_trigger=0.10, cut_frac=0.5,
        recovery_frac=0.5, max_rotations=1)], starting_capital=CAPITAL).run()
    assert rot.iloc[-1]["equity"] > hold.iloc[-1]["equity"]
    # And the two decompose differently: the rotation booked real realised P&L, the hold
    # booked none. Same ending question, very different tax/− and risk-taking profile.
    assert rot.iloc[-1]["TOTAL_realised_pnl"] != 0.0
    assert hold.iloc[-1]["TOTAL_realised_pnl"] == 0.0


# ---------------------------------------------------------------------------
# MANUAL TRADE LIST — the other half of the harness, and mixing the two.
# ---------------------------------------------------------------------------
def test_trade_list_rule_executes_dated_trades():
    prices = make_prices()
    trades = pd.DataFrame([
        {"ts": prices.index[0], "symbol": "HEDGE", "side": "BUY", "qty": 100},
        {"ts": prices.index[5], "symbol": "BETA", "side": +1, "qty": 200},
        {"ts": prices.index[40], "symbol": "HEDGE", "side": "SELL", "qty": 40},
    ])
    sim = PortfolioSimulator(prices, [TradeListRule(trades)], starting_capital=CAPITAL)
    df = sim.run()

    assert len(sim.blotter()) == 3
    assert df.iloc[-1]["HEDGE_position"] == pytest.approx(60.0)
    assert df.iloc[-1]["BETA_position"] == pytest.approx(200.0)
    # The sale on bar 40 happened at a price above the 100.0 entry, so realised is positive.
    sell_px = prices["HEDGE"].iloc[40]
    assert df.iloc[-1]["HEDGE_realised_pnl"] == pytest.approx(40 * (sell_px - 100.0))


def test_rules_compose_in_one_run():
    # A scripted policy and a manual overlay drive the same book — the "both" mode. The
    # manual trade must land on top of whatever the rule did, not replace it.
    prices = make_prices()
    manual = pd.DataFrame([{"ts": prices.index[3], "symbol": "BETA", "side": "BUY", "qty": 10}])
    sim = PortfolioSimulator(
        prices,
        [DrawdownRotationRule({"HEDGE": 1.0}, {"BETA": 1.0}, dd_trigger=0.10),
         TradeListRule(manual)],
        starting_capital=CAPITAL,
    )
    df = sim.run()
    # The manual BETA lot exists from bar 3, well before the rotation could have bought any.
    assert df.iloc[3]["BETA_position"] == pytest.approx(10.0)
    assert df.iloc[3]["HEDGE_position"] > 0
