"""
PRICING — closed-form option values consumed by the overlay rules.

WHY THIS MODULE EXISTS SEPARATELY FROM ``vol.py``
-------------------------------------------------
The surface answers "what vol"; this answers "what is it worth at that vol". They are split
because they are swapped independently: a real IBKR chain would replace ``vol.py`` and leave the
pricer untouched, and an American / binomial pricer would replace this and leave the surface
untouched. 16-02's ``OptionLeg.price()`` is expected to be a thin wrapper over
``black_scholes_put`` rather than a reimplementation of it.

EVERYTHING IS PRICED EUROPEAN, AND THE FLOOR IS THE *DISCOUNTED* INTRINSIC
--------------------------------------------------------------------------
Decision of 2026-08-26 (12-01): price European, floor at the DISCOUNTED intrinsic
``K*e^-rT - S``, and apply NO clamp. Black-Scholes satisfies that floor automatically via
put-call parity — ``put - (K*e^-rT - S) = call >= 0`` — so a clamp is not merely unnecessary, it
is actively wrong: clamping to the UNDISCOUNTED exercise value ``K - S`` overprices deep-ITM
puts (50.00 for an option worth 38.06 in 12-01's own worked example) and therefore inflates hedge
value in exactly the drawdown states Phase 13 is built to measure. ``crash.py:1853`` already uses
the discounted form in its degenerate branch.

THE AMERICAN APPROXIMATION, STATED PLAINLY
------------------------------------------
SPX options are European, so for SPX this is exact. For ETF options (SPY) it is an
APPROXIMATION: a deep-ITM American put carries early-exercise value that European pricing does
not, so this understates such a put. The understatement is accepted — it is conservative for the
v0.4 claim, since it makes the hedge look cheaper in payoff terms, never dearer. Anywhere SPY is
priced through this function, that fact belongs in a comment at the call site.

NO SCIPY
--------
``scipy`` is not a declared dependency of this package (it is only present transitively, via
scikit-learn), so the normal CDF is built here from ``math.erf``, which is exact to double
precision and in the standard library. ``np.vectorize`` is a loop, not a speed-up — that is fine
at probe scale (a ladder of strikes over 250 bars) and is the wrong choice only if this is ever
put on a hot path, at which point ``scipy.special.ndtr`` becomes the swap.
"""

import math

import numpy as np


# Elementwise error function. numpy has no ufunc for erf, and the alternatives (a rational
# approximation, or a scipy dependency) are respectively less accurate and heavier than the
# problem warrants — see the module docstring.
_ERF = np.vectorize(math.erf, otypes=[float])


def _norm_cdf(x):
    # Standard normal CDF from the error function: Phi(x) = 0.5 * (1 + erf(x / sqrt(2))).
    return 0.5 * (1.0 + _ERF(np.asarray(x, dtype=float) / math.sqrt(2.0)))


# ============================================================================
# EUROPEAN PUT — Black-Scholes-Merton, continuous dividend yield.
# Pure and vectorised: `spot`, `strike`, `tau` and `vol` broadcast against each
# other, so one call prices a whole strike ladder across a whole date index.
# ============================================================================

def black_scholes_put(
    spot,               # underlying spot S (scalar or array)
    strike,             # strike K (scalar or array)
    tau,                # time to expiry in YEARS; <= 0 is handled as an expired leg
    vol,                # implied vol as a decimal — evaluated AT THIS STRIKE, see vol.py
    rate=0.02,          # continuously-compounded risk-free rate; 12-01's worked examples use 0.02
    div_yield=0.0,      # continuous dividend yield q; SPY's ~1.2% matters over a 63-day roll
):
    """Return the European put value.

    Parameters
    ----------
    spot, strike, tau, vol : float or array-like
        Broadcast together by numpy.
    rate, div_yield : float
        Continuously compounded, as decimals.

    Returns
    -------
    float or ndarray
        The put price. Never below ``K*e^-rT - S*e^-qT``, and never below zero.

    Notes
    -----
    The degenerate branch (``tau <= 0`` or ``vol <= 0``) returns the DISCOUNTED intrinsic rather
    than ``max(K - S, 0)``, so the limit as ``tau -> 0`` is continuous with the main branch.
    """
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    tau = np.asarray(tau, dtype=float)
    vol = np.asarray(vol, dtype=float)

    # Both legs of the payoff, discounted once and reused by both branches below. Carrying the
    # dividend yield on the spot term is what makes this Merton rather than plain Black-Scholes;
    # with q = 0 the two coincide.
    disc_strike = strike * np.exp(-rate * tau)
    disc_spot = spot * np.exp(-div_yield * tau)

    # The degenerate cases: an expired leg, or a leg whose vol has been clipped to nothing. Both
    # make sigma*sqrt(tau) zero, which would divide by zero in d1. Detected up front so the main
    # branch can be computed on a SANITISED denominator rather than guarded afterwards.
    degenerate = (tau <= 0.0) | (vol <= 0.0)

    # Sanitised diffusion term. Where the leg is degenerate this value is never used — it exists
    # only so the vectorised d1/d2 below produce no warnings and no NaNs to clean up.
    sigma_sqrt_tau = np.where(degenerate, 1.0, vol * np.sqrt(np.maximum(tau, 0.0)))

    # Standard BSM d1/d2 on the discounted forward. Written as log(disc_spot / disc_strike) so
    # the rate and dividend carry terms are already inside the logarithm and cannot be dropped.
    d1 = (np.log(disc_spot / disc_strike) + 0.5 * vol ** 2 * tau) / sigma_sqrt_tau
    d2 = d1 - sigma_sqrt_tau

    # The put: K*e^-rT * N(-d2) - S*e^-qT * N(-d1). No clamp — put-call parity already guarantees
    # this sits above the discounted intrinsic, and clamping to the undiscounted exercise value
    # would overprice deep-ITM puts (see the module docstring).
    priced = disc_strike * _norm_cdf(-d2) - disc_spot * _norm_cdf(-d1)

    # Discounted intrinsic, floored at zero: the value of the degenerate leg, and the quantity the
    # main branch is guaranteed never to fall below.
    intrinsic = np.maximum(disc_strike - disc_spot, 0.0)

    result = np.where(degenerate, intrinsic, priced)
    # Return a plain float when every input was scalar, so call sites doing arithmetic on the
    # result are not silently handed a 0-d array.
    return float(result) if result.ndim == 0 else result
