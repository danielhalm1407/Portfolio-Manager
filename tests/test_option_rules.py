"""
Offline tests for the option rules and the simulator's synthetic-marks hook (plan 16-03).

Every path here is synthetic and built in the test, so the expected economics can be stated by
hand: a flat path (pure carry), a path that falls 12% into a roll (the collar should redeploy),
one that rises 3% (the collar should carry) and one that rises 40% (the short call is breached
and the collar must fund). No mocks — the real pricer, the real Book, the real simulator.
"""

import numpy as np
import pandas as pd
import pytest

from portutils.portfolio.ledger import StateLedger
from portutils.portfolio.rules import BuyAndHoldRule
from portutils.portfolio.simulator import PortfolioSimulator
from portutils.strategies.rules.options import (
    BudgetMatchedPutRule, PortfolioBudgetPutRule, ProtectivePutRule, PutSpreadRule,
    RollingCollarRule,
)


N_BARS = 200


def _path(to_roll, after=None, start=100.0, n=N_BARS):
    # Bar 0 and bar 1 sit at `start` (the hedge opens on bar 1, the first bar SPY is held).
    # Spot then moves linearly to `to_roll` at bar 64 — the first roll bar — and is flat after,
    # unless `after` is given.
    px = np.full(n, float(start))
    px[1:65] = np.linspace(start, to_roll, 64)
    px[65:] = to_roll if after is None else after
    return pd.Series(px, index=pd.bdate_range("2025-01-02", periods=n), name="SPY")


class _MarkAuditLedger(StateLedger):
    # Records, on every bar, which OPEN positions had no mark — the failure the hook exists to
    # prevent (the Book would value them at zero unrealised, silently).
    def __init__(self):
        super().__init__()
        self.unmarked = []

    def record_book(self, ts, book, prices, extra=None):
        for sym in book.symbols:
            if abs(book.position(sym).qty) > 1e-12 and sym not in prices:
                self.unmarked.append((ts, sym))
        return super().record_book(ts, book, prices, extra=extra)


def _run(path, rule):
    ledger = _MarkAuditLedger()
    sim = PortfolioSimulator(path.to_frame(), [BuyAndHoldRule({"SPY": 1.0}), rule],
                             starting_capital=float(path.iloc[0]), ledger=ledger)
    df = sim.run()
    return sim, df, rule, ledger


# ---- AC-1 (the hook itself) ------------------------------------------------------------------

def test_default_rules_supply_no_synthetic_marks():
    assert BuyAndHoldRule({"SPY": 1.0}).synthetic_marks(None, {"SPY": 1.0}, None) == {}


def test_colliding_synthetic_mark_raises():
    class Bad(BuyAndHoldRule):
        def synthetic_marks(self, ts, prices, book):
            return {"SPY": 1.0}
    path = _path(100.0)
    with pytest.raises(ValueError):
        PortfolioSimulator(path.to_frame(), [Bad({"SPY": 1.0})], starting_capital=100.0).run()


# ---- AC-2: legs fill, mark and settle through the Book ---------------------------------------

def test_roll_cadence_and_every_open_leg_is_marked():
    sim, df, rule, ledger = _run(_path(100.0), ProtectivePutRule())
    opens = sorted({e["bar"] for e in rule.events if e["action"] == "open"})
    # First open on bar 1 (first bar SPY is held), then every 63 bars.
    assert opens == [1, 64, 127, 190]
    assert ledger.unmarked == []
    # Every open is a real fill in the blotter, one put per SPY unit held.
    blot = sim.blotter()
    puts = blot[blot["symbol"].str.contains(" P ")]
    bought = puts[puts["side"] > 0]["qty"].tolist()
    assert len(bought) == 4 and bought == pytest.approx([1.0] * 4)


def test_expiring_leg_closes_at_intrinsic():
    # Falls to 80 by the roll: the 0.90 put (K=90) must settle at exactly 90 - 80 = 10.
    _, _, rule, _ = _run(_path(80.0), ProtectivePutRule())
    close = [e for e in rule.events if e["action"] == "close"][0]
    assert close["bar"] == 64
    assert close["strike"] == pytest.approx(90.0)
    assert close["price"] == pytest.approx(10.0)


def test_equity_identity():
    # Equity = start + SPY P&L + option P&L, with option P&L rebuilt from the rule's own events
    # log and the final marks. Ties the Book's accounting to the rule's record, to 1e-9.
    path = _path(88.0, after=95.0)
    sim, df, rule, _ = _run(path, PutSpreadRule())
    spy_pnl = sim.book.position("SPY").qty * path.iloc[-1] - sum(
        f.side * f.qty * f.price for f in sim.fills if f.symbol == "SPY")
    realised = sum(abs(e["qty"]) * e["pnl_per_unit"] for e in rule.events if e["action"] == "close")
    final_marks = rule.synthetic_marks(None, {"SPY": path.iloc[-1]}, sim.book)
    open_pnl = sum(ent["qty"] * (final_marks[ent["leg"].symbol] - ent["premium"])
                   for ent in rule._legs)
    expected = path.iloc[0] + spy_pnl + realised + open_pnl
    assert df["equity"].iloc[-1] == pytest.approx(expected, abs=1e-9)


# ---- AC-3: structure economics ----------------------------------------------------------------

def test_put_spread_legs_and_validation():
    _, _, rule, _ = _run(_path(100.0), PutSpreadRule(floor=0.90, spread_width=0.80))
    first = [e for e in rule.events if e["action"] == "open" and e["bar"] == 1]
    by_strike = {round(e["strike"]): e["qty"] for e in first}
    assert by_strike == {90: 1.0, 80: -1.0}
    with pytest.raises(ValueError):
        PutSpreadRule(floor=0.90, spread_width=0.90)
    with pytest.raises(ValueError):
        PutSpreadRule(floor=0.80, spread_width=0.90)


def test_collar_redeploys_after_a_bad_period():
    sim, df, rule, _ = _run(_path(88.0), RollingCollarRule())
    ev = [e for e in rule.events if e["reason"] == "collar redeploy"]
    assert len(ev) == 1 and ev[0]["bar"] == 64 and ev[0]["qty"] > 0
    # Growth-leg units strictly greater after the roll than before it.
    held_after = sim.book.position("SPY").qty
    assert held_after == pytest.approx(1.0 + ev[0]["qty"])
    assert held_after > 1.0


def test_collar_carries_after_a_good_period():
    sim, _, rule, _ = _run(_path(103.0), RollingCollarRule())
    first_roll = [e for e in rule.events if e["bar"] == 64 and e["symbol"] == "SPY"]
    assert [e["reason"] for e in first_roll] == ["collar carry"]
    assert sim.book.position("SPY").qty == pytest.approx(1.0)


def test_collar_funds_a_breached_cap_capped_at_99_9_percent():
    sim, _, rule, _ = _run(_path(140.0), RollingCollarRule())
    ev = [e for e in rule.events if e["reason"] == "collar funding"]
    assert len(ev) >= 1 and ev[0]["bar"] == 64
    # Short 1.28 call at K=128, spot 140 -> owes 12 per unit -> sells 12/140 units.
    assert ev[0]["net_cash"] == pytest.approx(-12.0)
    assert -ev[0]["qty"] == pytest.approx(12.0 / 140.0)
    assert -ev[0]["qty"] <= 0.999 * 1.0


def test_collar_validation():
    with pytest.raises(ValueError):
        RollingCollarRule(floor=0.90, cap=0.85)


# ---- 13-02.1: premium-budget sizing -----------------------------------------------------------

def _opens(rule):
    return [e for e in rule.events if e["action"] == "open"]


def test_budget_matched_spends_the_budget_strikes_premium_at_every_strike():
    # AC-2: contracts x premium per contract == the budget strike's premium per unit held, to
    # 1e-9, whatever the strike. Checked on every open across a falling path (three rolls).
    for floor in (0.80, 0.875, 0.95):
        _, _, rule, ledger = _run(_path(85.0), BudgetMatchedPutRule(floor=floor,
                                                                     budget_moneyness=0.95))
        assert ledger.unmarked == []
        for e in _opens(rule):
            spent_per_unit = e["contracts_per_unit"] * e["premium"]
            assert spent_per_unit == pytest.approx(e["budget_premium"], abs=1e-9)


def test_budget_matched_at_the_budget_strike_equals_the_plain_rule():
    # At floor == budget_moneyness the scale is exactly 1, so the run must be identical to
    # ProtectivePutRule — the subclass changes sizing and nothing else.
    _, df_a, _, _ = _run(_path(85.0), BudgetMatchedPutRule(floor=0.95, budget_moneyness=0.95))
    _, df_b, _, _ = _run(_path(85.0), ProtectivePutRule(floor=0.95))
    np.testing.assert_allclose(df_a["equity"].to_numpy(), df_b["equity"].to_numpy(), atol=1e-9)


def test_budget_matched_buys_more_contracts_deeper_out_of_the_money():
    _, _, deep, _ = _run(_path(100.0), BudgetMatchedPutRule(floor=0.80))
    _, _, near, _ = _run(_path(100.0), BudgetMatchedPutRule(floor=0.95))
    assert _opens(deep)[0]["contracts_per_unit"] > 1.0
    assert _opens(near)[0]["contracts_per_unit"] == pytest.approx(1.0)


def test_portfolio_budget_spends_its_annual_share_pro_rata():
    # 5% a year over a 63-bar period is 1.25% of equity per open; contracts x premium == spend.
    _, _, rule, ledger = _run(_path(100.0), PortfolioBudgetPutRule(floor=0.90, budget_pct=0.05))
    assert ledger.unmarked == []
    first = _opens(rule)[0]
    assert first["contracts"] * first["premium"] == pytest.approx(first["budget_spend"], rel=1e-9)
    assert first["budget_spend"] == pytest.approx(0.05 * 63 / 252 * 100.0, rel=1e-6)
