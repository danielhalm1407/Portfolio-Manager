"""
Offline tests for the option overlay's two pure functions.

No data, no network, no chain: these pin ``synthetic_iv_surface`` and ``black_scholes_put``
against the numbers and the properties that ``.paul/phases/12-option-overlay/12-01-PLAN.md``
argues from. The plan makes several decisions — price European, floor at the DISCOUNTED
intrinsic with no clamp, make vol a function of STRIKE and not a scalar — and each of those is
only worth anything if the code actually behaves that way.

The most important assertions here are the two the plan's economics rest on:

* the put is MONOTONIC IN STRIKE, and swapping two strikes swaps their prices. Everything the
  overlay does — ladders, spreads, collars — assumes a higher strike is worth more.
* a PUT SPREAD IS CHEAPER THAN THE NAKED PUT IT IS BUILT FROM, and cheaper by more than a flat
  vol would make it. That is the entire reason PutSpreadRule exists, and it is a property of
  the SKEW: price both legs off one vol and the structure loses its rationale.

The anchor numbers come from 12-01's own text, so a mis-ported constant fails here rather than
being discovered three plans later in a backtest.
"""

import pathlib
import sys

import numpy as np
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from portutils.strategies.instruments.pricing import black_scholes_put      # noqa: E402
from portutils.strategies.instruments.vol import synthetic_iv_surface       # noqa: E402


# ----------------------------------------------------------------------------
# The port is exact — 12-01's four quoted anchors.
# ----------------------------------------------------------------------------

def test_iv_matches_the_plans_frozen_reference_anchor():
    # 12-01: K=90, t=0.125, spot_ref=100 (the period-start spot) gives iv 0.210.
    assert synthetic_iv_surface(90, 0.125, 100) == pytest.approx(0.2098, abs=5e-5)


def test_iv_matches_the_plans_current_spot_anchor():
    # Same strike and tenor, but moneyness measured against the CURRENT spot of 88: iv 0.176.
    # The two together are the measurement the 2026-08-29 v1/v2 decision was made on.
    assert synthetic_iv_surface(90, 0.125, 88) == pytest.approx(0.1760, abs=5e-5)


def test_put_price_matches_the_plans_anchors_under_both_references():
    # S=88, K=90, t=0.125, r=0.02 priced off each of the two vols above: 3.612 frozen against
    # 3.206 current-spot. The 11% gap IS the v1/v2 decision, so it is pinned, not just observed.
    frozen = black_scholes_put(88, 90, 0.125, synthetic_iv_surface(90, 0.125, 100), rate=0.02)
    current = black_scholes_put(88, 90, 0.125, synthetic_iv_surface(90, 0.125, 88), rate=0.02)
    assert frozen == pytest.approx(3.612, abs=5e-4)
    assert current == pytest.approx(3.206, abs=5e-4)
    # And the SIGN, stated separately: the current-spot reference is the cheaper of the two in a
    # selloff. A future recalibration may move the magnitudes; if it flips this sign, the
    # decision it justified is void.
    assert current < frozen


# ----------------------------------------------------------------------------
# Monotonicity in strike — swap the strikes, swap the prices.
# ----------------------------------------------------------------------------

def test_a_higher_strike_put_is_worth_more():
    # Same spot, tenor and vol: the only difference is the strike. A put paying (K - S) must be
    # worth more the higher K is, or nothing built on top of it means anything.
    lower = black_scholes_put(100, 90, 0.25, 0.20)
    higher = black_scholes_put(100, 110, 0.25, 0.20)
    assert higher > lower


def test_swapping_the_two_strikes_swaps_the_two_prices():
    # The explicit swap: price a pair, price the pair with the strikes exchanged, and the two
    # results must be each other's mirror. This catches an argument-order slip in the signature
    # (spot and strike transposed) that a single-strike test would sail straight past.
    k_low, k_high = 90.0, 110.0
    a = black_scholes_put(100, k_low, 0.25, 0.20)
    b = black_scholes_put(100, k_high, 0.25, 0.20)
    swapped_a = black_scholes_put(100, k_high, 0.25, 0.20)
    swapped_b = black_scholes_put(100, k_low, 0.25, 0.20)
    assert (a, b) == (swapped_b, swapped_a)
    assert a < b


def test_the_ladder_is_monotonic_across_every_rung():
    # The whole ladder at once, priced off the surface as the probe prices it — so this covers
    # the composition of the two functions, not just the pricer. Vol RISES as strike falls
    # (the smirk), which pushes the cheap strikes UP; monotonicity has to survive that.
    spot = 100.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0])
    ivs = synthetic_iv_surface(strikes, 0.25, spot)
    prices = black_scholes_put(spot, strikes, 0.25, ivs)
    assert np.all(np.diff(prices) > 0)


# ----------------------------------------------------------------------------
# The skew is what makes a put spread cheap — 12-01's central economic claim.
# ----------------------------------------------------------------------------

def test_the_written_leg_of_a_spread_carries_a_higher_vol_than_the_bought_leg():
    # PutSpreadRule buys the higher strike and writes one `spread_width` lower. The structure is
    # only cheaper than the naked put because that written leg carries MORE implied vol.
    spot = 100.0
    bought, written = 95.0, 76.0          # 0.95x spot, written 0.80x of that per 12-01's default
    assert synthetic_iv_surface(written, 0.25, spot) > synthetic_iv_surface(bought, 0.25, spot)


def test_a_put_spread_costs_less_than_the_naked_put():
    # The structure itself: long the higher strike, short the lower. Must be strictly cheaper
    # than the long leg alone, and strictly positive — a spread that costs nothing or pays you
    # to hold it is an arbitrage and means the pricer is wrong.
    spot, tau = 100.0, 0.25
    bought, written = 95.0, 76.0
    long_leg = black_scholes_put(spot, bought, tau, synthetic_iv_surface(bought, tau, spot))
    short_leg = black_scholes_put(spot, written, tau, synthetic_iv_surface(written, tau, spot))
    spread = long_leg - short_leg
    assert 0 < spread < long_leg


# ----------------------------------------------------------------------------
# The discounted intrinsic floor, and the decision NOT to clamp.
# ----------------------------------------------------------------------------

def test_the_put_never_breaches_its_discounted_intrinsic_floor():
    # Swept across a wide spot range and three strikes. Put-call parity guarantees this, which is
    # precisely why 12-01 rejected a clamp: the floor holds on its own, and clamping to the
    # UNDISCOUNTED exercise value would OVERPRICE deep-ITM puts and inflate hedge payoffs in the
    # drawdown states Phase 13 exists to measure.
    spots = np.linspace(40, 160, 61)
    strikes = np.array([[90.0], [100.0], [110.0]])
    tau, rate = 0.25, 0.02
    prices = black_scholes_put(spots, strikes, tau,
                               synthetic_iv_surface(strikes, tau, 100.0), rate=rate)
    floor = np.maximum(strikes * np.exp(-rate * tau) - spots, 0.0)
    assert np.all(prices >= floor - 1e-9)


def test_a_deep_itm_put_is_not_clamped_to_the_undiscounted_exercise_value():
    # 12-01's worked example in spirit: deep in the money, the European put sits just ABOVE
    # K*e^-rT - S and strictly BELOW K - S. A clamp would put it at the latter.
    spot, strike, tau, rate = 50.0, 100.0, 1.0, 0.02
    price = black_scholes_put(spot, strike, tau, 0.20, rate=rate)
    assert strike * np.exp(-rate * tau) - spot <= price < strike - spot


# ----------------------------------------------------------------------------
# Degenerate branches — an expired leg, and a vol clipped to nothing.
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("tau,vol", [(0.0, 0.20), (0.25, 0.0), (0.0, 0.0)])
def test_degenerate_legs_return_the_discounted_intrinsic(tau, vol):
    # Either input makes sigma*sqrt(tau) zero, which would divide by zero in d1. The branch must
    # return the DISCOUNTED intrinsic, not max(K - S, 0), so the limit as tau -> 0 is continuous
    # with the main branch rather than jumping.
    spot, strike, rate = 88.0, 90.0, 0.02
    expected = max(strike * np.exp(-rate * tau) - spot, 0.0)
    assert black_scholes_put(spot, strike, tau, vol, rate=rate) == pytest.approx(expected)


def test_an_expired_out_of_the_money_put_is_worthless_not_negative():
    assert black_scholes_put(120.0, 90.0, 0.0, 0.20) == 0.0


# ----------------------------------------------------------------------------
# Surface shape, and the vectorisation the probe relies on.
# ----------------------------------------------------------------------------

def test_the_smirk_slopes_the_right_way_and_the_term_structure_rises():
    # Lower strikes carry HIGHER vol (the skew term), and longer tenors carry more (the term
    # term). Both signs, asserted rather than eyeballed off the figure.
    assert synthetic_iv_surface(80, 0.25, 100) > synthetic_iv_surface(100, 0.25, 100)
    assert synthetic_iv_surface(100, 1.0, 100) > synthetic_iv_surface(100, 0.25, 100)


def test_the_surface_is_clipped_at_both_ends():
    # No combination of extreme strike and tenor may hand the pricer a negative or a 400% vol.
    ivs = synthetic_iv_surface(np.array([1.0, 20.0, 100.0, 500.0, 5000.0]), 5.0, 100.0)
    assert np.all((ivs >= 0.10) & (ivs <= 0.85))


def test_scalars_in_scalars_out_and_arrays_broadcast():
    # The probe prices a strike ladder across a date index in one call, so broadcasting has to
    # work; and call sites doing arithmetic on a scalar result must not be handed a 0-d array.
    assert isinstance(black_scholes_put(100, 95, 0.25, 0.2), float)
    spots = np.array([100.0, 95.0, 90.0])
    strikes = np.array([[80.0], [90.0], [100.0]])
    assert black_scholes_put(spots, strikes, 0.25, 0.2).shape == (3, 3)
