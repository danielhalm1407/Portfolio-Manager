"""
Offline tests for the option instrument layer (plan 16-02).

Real arithmetic only — no mocks of the pricer. The numbers asserted here are either 12-01's own
worked examples (so the port stays tied to the economics spec) or invariants that a correct
European pricer must satisfy whatever the inputs (parity, finite-difference Greeks). The
invariant tests are the ones that would catch a mis-derived formula; the fixtures only catch a
changed one.
"""

import inspect
import math

import pytest

from portutils.strategies import schedule
from portutils.strategies.instruments.options import OptionLeg
from portutils.strategies.instruments.pricing import (
    black_scholes_call, black_scholes_delta, black_scholes_put,
)
from portutils.strategies.instruments.vol import synthetic_iv_surface
from portutils.strategies.schedule import RollCalendar


# ---- AC-1: the call pricer, parity and the discounted floor ----------------------------------

@pytest.mark.parametrize("q", [0.0, 0.012])
def test_put_call_parity_deep_itm(q):
    # 12-01's AC-1 worked example: S=100, K=150, T=2y, r=5%, vol=20%.
    S, K, T, r, vol = 100.0, 150.0, 2.0, 0.05, 0.20
    put = black_scholes_put(S, K, T, vol, rate=r, div_yield=q)
    call = black_scholes_call(S, K, T, vol, rate=r, div_yield=q)
    # Parity: put - call = K e^-rT - S e^-qT. This identity is WHY no clamp is needed.
    assert put - call == pytest.approx(K * math.exp(-r * T) - S * math.exp(-q * T), abs=1e-10)
    # The call is never below its own discounted floor, and never negative.
    assert call >= max(S * math.exp(-q * T) - K * math.exp(-r * T), 0.0)
    assert call >= 0.0


def test_deep_itm_put_matches_12_01_worked_example():
    # Prices BELOW exercise value (50.00) and ABOVE the discounted floor (35.7256), as a correct
    # European put must. A test asserting price >= K - S would fail a correct implementation.
    put = black_scholes_put(100.0, 150.0, 2.0, 0.20, rate=0.05)
    assert put == pytest.approx(38.0624, abs=1e-4)
    assert put < 150.0 - 100.0
    assert put >= 150.0 * math.exp(-0.05 * 2.0) - 100.0


def test_12_01_anchor_numbers_still_reproduce():
    # The four numbers 12-01 quotes from the source function; the probe verified them first.
    iv_frozen = float(synthetic_iv_surface(90, 0.125, 100))
    iv_current = float(synthetic_iv_surface(90, 0.125, 88))
    assert round(iv_frozen, 3) == 0.210
    assert round(iv_current, 3) == 0.176
    assert round(black_scholes_put(88, 90, 0.125, iv_frozen, rate=0.02), 3) == 3.612
    assert round(black_scholes_put(88, 90, 0.125, iv_current, rate=0.02), 3) == 3.206


def test_call_degenerate_branch_is_discounted_intrinsic():
    # Zero vol: the call is worth exactly its discounted forward intrinsic, floored at zero.
    assert black_scholes_call(110.0, 100.0, 0.5, 0.0, rate=0.02) == pytest.approx(
        110.0 - 100.0 * math.exp(-0.02 * 0.5))
    assert black_scholes_call(90.0, 100.0, 0.5, 0.0, rate=0.02) == 0.0


# ---- AC-2: OptionLeg wraps the pricer, and its Greeks agree with finite differences --------

SPOT, VOL, TAU = 100.0, 0.20, 0.25
STRIKES = (85.0, 100.0, 115.0)   # OTM put / ATM / ITM put (mirror for the call)


@pytest.mark.parametrize("right", ["P", "C"])
@pytest.mark.parametrize("strike", STRIKES)
def test_leg_price_is_a_thin_wrapper(right, strike):
    leg = OptionLeg("SPY", right, strike, open_bar=0, expiry_bar=63)
    pricer = black_scholes_put if right == "P" else black_scholes_call
    assert leg.price(SPOT, VOL, TAU) == pricer(SPOT, strike, TAU, VOL, rate=0.02, div_yield=0.0)
    assert leg.mark(SPOT, VOL, TAU) == leg.price(SPOT, VOL, TAU)


@pytest.mark.parametrize("right", ["P", "C"])
@pytest.mark.parametrize("strike", STRIKES)
def test_delta_matches_central_finite_difference(right, strike):
    leg = OptionLeg("SPY", right, strike, open_bar=0, expiry_bar=63, div_yield=0.012)
    h = 1e-3
    fd = (leg.price(SPOT + h, VOL, TAU) - leg.price(SPOT - h, VOL, TAU)) / (2 * h)
    assert leg.delta(SPOT, VOL, TAU) == pytest.approx(fd, abs=1e-4)
    # Bounds: put delta in [-1, 0], call delta in [0, 1].
    lo, hi = (-1.0, 0.0) if right == "P" else (0.0, 1.0)
    assert lo <= leg.delta(SPOT, VOL, TAU) <= hi


@pytest.mark.parametrize("right", ["P", "C"])
@pytest.mark.parametrize("strike", STRIKES)
def test_theta_is_the_one_bar_price_change(right, strike):
    leg = OptionLeg("SPY", right, strike, open_bar=0, expiry_bar=63)
    expected = leg.price(SPOT, VOL, TAU - 1 / 252) - leg.price(SPOT, VOL, TAU)
    assert leg.theta(SPOT, VOL, TAU) == pytest.approx(expected, abs=1e-3)
    # A long option with no dividend decays: theta is non-positive (the deep-ITM European put is
    # the textbook exception, so only assert it for calls and the non-ITM puts).
    if right == "C" or strike <= SPOT:
        assert leg.theta(SPOT, VOL, TAU) <= 0.0


def test_delta_step_at_expiry_and_bad_right():
    assert black_scholes_delta(90.0, 100.0, 0.0, 0.2, "P") == -1.0
    assert black_scholes_delta(110.0, 100.0, 0.0, 0.2, "P") == 0.0
    assert black_scholes_delta(110.0, 100.0, 0.0, 0.2, "C") == 1.0
    with pytest.raises(ValueError):
        black_scholes_delta(100.0, 100.0, 0.25, 0.2, "X")


def test_symbol_is_deterministic_and_unique():
    a = OptionLeg("SPY", "P", 625.94, open_bar=0, expiry_bar=63)
    assert a.symbol == "SPY P 625.94 @b63"
    # Same terms => same symbol (a new instance, not the same object).
    assert OptionLeg("SPY", "P", 625.94, open_bar=0, expiry_bar=63).symbol == a.symbol
    # Any one term changed => a different symbol.
    others = {
        OptionLeg("SPY", "C", 625.94, 0, 63).symbol,
        OptionLeg("SPY", "P", 626.94, 0, 63).symbol,
        OptionLeg("SPY", "P", 625.94, 0, 126).symbol,
        OptionLeg("QQQ", "P", 625.94, 0, 63).symbol,
    }
    assert a.symbol not in others and len(others) == 4


def test_leg_tau_intrinsic_and_validation():
    leg = OptionLeg("SPY", "P", 100.0, open_bar=10, expiry_bar=73)
    assert leg.tau(10) == pytest.approx(0.25)
    assert leg.tau(80) == 0.0                     # floored, never negative
    assert leg.is_expired(73) and not leg.is_expired(72)
    assert leg.intrinsic(90.0) == 10.0            # UNDISCOUNTED settlement value
    assert leg.intrinsic(110.0) == 0.0
    with pytest.raises(ValueError):
        OptionLeg("SPY", "X", 100.0, 0, 63)
    with pytest.raises(ValueError):
        OptionLeg("SPY", "P", 0.0, 0, 63)
    with pytest.raises(ValueError):
        OptionLeg("SPY", "P", 100.0, 63, 0)


# ---- AC-3: RollCalendar counts bars, never dates ---------------------------------------------

def test_roll_calendar_bars():
    cal = RollCalendar(reset_bars=63)
    assert cal.next_roll(0) == 63
    assert cal.is_roll_day(63, 0) is True
    assert cal.is_roll_day(62, 0) is False
    assert cal.year_fraction(63) == pytest.approx(0.25)
    with pytest.raises(ValueError):
        RollCalendar(reset_bars=0)


def test_schedule_has_no_date_arithmetic():
    # The bars-not-dates decision, enforced: no timedelta, no calendar/datetime import in CODE.
    code = "\n".join(line for line in inspect.getsource(schedule).splitlines()
                     if not line.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]               # drop the module docstring, which discusses them
    assert "timedelta" not in body
    assert "import datetime" not in body and "import calendar" not in body
