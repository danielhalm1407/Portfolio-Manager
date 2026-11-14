"""
ATTRIBUTION — splitting a put's change in value into the drivers that moved it.

WHY THIS MODULE EXISTS
----------------------
Added 2026-11-13 after the 2026 window raised an obvious question: SPY fell 9% and implied vol
doubled, yet the deep out-of-the-money puts lost almost all their value. Both of those moves
should RAISE a put's price, so a reader needs to see how much each one added and what took it
away. Under v2 pricing a leg's value on any bar is a function of four inputs:

    spot            the underlying's price, which sets the payoff distance to the strike
    moneyness ref   the spot the smirk is measured against (v2: the same bar's spot). As spot
                    falls a fixed strike sits LESS far out of the money, so it slides UP the
                    smirk to a lower implied vol. Separated from spot because it pulls the other
                    way, and because it is a modelling choice (the 2026-08-29 v1/v2 decision),
                    not a market fact
    vol level       the bar's real ATM implied vol, the surface's ``base``
    time            years to expiry, which also enters the surface's term structure

WHY SHAPLEY AND NOT A SEQUENTIAL OR A GREEKS DECOMPOSITION
-----------------------------------------------------------
Option value is strongly non-linear in all four inputs, and the interactions are the story: a
vol rise is worth a lot with 60 days left and nothing with 5. A sequential "move spot, then vol,
then time" attribution gives a different answer for every ordering, and a first-order Greeks
expansion leaves a residual larger than the terms it explains for a deep put. The Shapley value
averages each input's marginal effect over every ordering of the four moves, so the four
contributions are order-free and sum EXACTLY to the change in value. Twenty-four orderings of a
closed-form price is cheap.

What it does not mean: "time" here is not linear theta. It is time's share of the interaction,
which for a deep put near expiry includes the spot and vol gains that time no longer allows to
count. The full-tenor counterfactual (same spot and vol, original time to expiry) is reported
beside it because it says the same thing in a form that needs no decomposition to read.

Pure: no IO, no simulator, no plotting.
"""

import itertools

import numpy as np
import pandas as pd

from .instruments.pricing import black_scholes_put
from .instruments.vol import synthetic_iv_surface

# The four inputs, in the order the output columns use. Names are the column names.
FACTORS = ("spot", "skew_slide", "vol_level", "time")

# Trading bars per year, the same convention as OptionLeg.tau (instruments/options.py).
BARS_PER_YEAR = 252.0


def put_value(spot, strike, tau, base, spot_ref, rate=0.02, div_yield=0.0, **vol_kwargs):
    """The v2 put price: implied vol read off the synthetic surface at this strike, then priced.

    The same two calls the rule makes in ``_vol_for`` and ``OptionLeg.price``, so an attribution
    of a run's leg reproduces that run's own marks rather than a near copy of them.
    """
    iv = float(synthetic_iv_surface(strike, tau, spot_ref, base=base, **vol_kwargs))
    return float(black_scholes_put(spot, strike, tau, iv, rate=rate, div_yield=div_yield)), iv


def shapley_put_attribution(start, end, strike, rate=0.02, div_yield=0.0, **vol_kwargs):
    """Split ``value(end) - value(start)`` into the four FACTORS. Returns a dict that sums exactly.

    ``start`` and ``end`` are dicts with keys ``spot``, ``base`` (ATM implied vol) and ``tau``
    (years to expiry). The moneyness reference moves with spot (v2), but as its own factor.
    """
    # Internal state names: the moneyness reference is carried separately from spot.
    s0 = {"spot": start["spot"], "skew_slide": start["spot"], "vol_level": start["base"],
          "time": start["tau"]}
    s1 = {"spot": end["spot"], "skew_slide": end["spot"], "vol_level": end["base"],
          "time": end["tau"]}

    def value(state):
        return put_value(state["spot"], strike, state["time"], state["vol_level"],
                         state["skew_slide"], rate=rate, div_yield=div_yield, **vol_kwargs)[0]

    contrib = dict.fromkeys(FACTORS, 0.0)
    orders = list(itertools.permutations(FACTORS))
    # For each ordering, move the factors from start to end one at a time and credit each move
    # with the change it causes. Averaging over every ordering is the Shapley value.
    for order in orders:
        state = dict(s0)
        prev = value(state)
        for factor in order:
            state[factor] = s1[factor]
            now = value(state)
            contrib[factor] += now - prev
            prev = now
    return {k: v / len(orders) for k, v in contrib.items()}


def attribute_leg(spot, iv, strike, open_ts, expiry_bar, rate=0.02, div_yield=0.0,
                  **vol_kwargs):
    """Per-bar attribution of one put leg from the bar it opened to the bar before expiry.

    Parameters
    ----------
    spot, iv : Series
        The window's closes and ATM implied vol, on the same index the simulator ran on.
    strike : float
        The leg's strike.
    open_ts : Timestamp
        The bar the leg was bought on (its blotter ``ts``).
    expiry_bar : int
        The leg's expiry as a POSITION in ``spot``'s index (the ``@b`` number in its symbol).

    Returns
    -------
    DataFrame indexed by date. ``multiple`` is value / entry price; the four FACTORS columns are
    each driver's contribution in the same units (multiples of the entry price), so
    ``1 + spot + skew_slide + vol_level + time == multiple`` on every row.
    ``multiple_full_tenor`` reprices the bar's spot and vol with the leg's ORIGINAL time to expiry.
    """
    t0 = spot.index.get_loc(open_ts)
    tau0 = (expiry_bar - t0) / BARS_PER_YEAR
    start = {"spot": float(spot.iloc[t0]), "base": float(iv.iloc[t0]), "tau": tau0}
    p0, _iv0 = put_value(start["spot"], strike, tau0, start["base"], start["spot"],
                         rate=rate, div_yield=div_yield, **vol_kwargs)
    rows = []
    # Stop the bar BEFORE expiry: on the expiry bar the leg settles at intrinsic and the rule
    # closes it, so the last bar with time value is the last one worth decomposing.
    for t in range(t0, min(expiry_bar, len(spot))):
        end = {"spot": float(spot.iloc[t]), "base": float(iv.iloc[t]),
               "tau": (expiry_bar - t) / BARS_PER_YEAR}
        value, strike_iv = put_value(end["spot"], strike, end["tau"], end["base"], end["spot"],
                                     rate=rate, div_yield=div_yield, **vol_kwargs)
        full, _ = put_value(end["spot"], strike, tau0, end["base"], end["spot"],
                            rate=rate, div_yield=div_yield, **vol_kwargs)
        parts = shapley_put_attribution(start, end, strike, rate=rate, div_yield=div_yield,
                                        **vol_kwargs)
        rows.append({"date": spot.index[t], "bars_left": expiry_bar - t, "spot_px": end["spot"],
                     "base_iv": end["base"], "strike_iv": strike_iv,
                     "multiple": value / p0,
                     **{k: v / p0 for k, v in parts.items()},
                     "multiple_full_tenor": full / p0})
    return pd.DataFrame(rows).set_index("date")
