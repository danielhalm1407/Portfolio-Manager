"""
VOL — the synthetic implied-volatility surface used to price the option overlay.

WHY THIS MODULE EXISTS
----------------------
Until this file, the surface existed only as an inline code block inside a planning document
(``.paul/phases/12-option-overlay/12-01-PLAN.md``). Every consumer would therefore have retyped
it, and a retyped model is a different model wearing the same numbers. It is now one importable
function with the constants exposed as arguments.

The function is ported from ``crash.py:2579`` ``synthetic_iv_surface``. The porting decision
(12-01, 2026-08-26) is that it goes across AS WRITTEN and is recalibrated later against whatever
Phase 11 produces — not re-derived here.

WHY THE SURFACE LIVES IN ``instruments/`` AND NOT INSIDE THE OPTION LEG
----------------------------------------------------------------------
12-01 is explicit: "the surface itself is a pipeline concern, passed in; the leg never fetches
it". An ``OptionLeg`` receives a vol that has ALREADY been evaluated at that leg's strike — the
rule calls a ``vol_fn(strike, tau) -> float`` handed in by the pipeline. So the surface is a
sibling of the pricer, not a member of it, and 16-02's ``options.py`` can be written without
knowing which surface it will be fed.

WHY THE VOL MUST BE A FUNCTION OF STRIKE AND NOT A SCALAR
---------------------------------------------------------
A flat vol per bar destroys ``PutSpreadRule`` outright. That structure is only cheaper than a
naked put BECAUSE the sold lower strike carries HIGHER implied vol than the bought upper strike.
Price both legs off one number and the spread's entire economic rationale disappears — the
structure looks like a strictly worse put. This is recorded as a 12-01 decision (2026-08-26).

V1 VERSUS V2 — WHICH REFERENCE SPOT TO MEASURE MONEYNESS AGAINST
----------------------------------------------------------------
There are two independently dynamic terms, and the decision of 2026-08-29 is to ship them
STATIC first and to enable them together, never term B alone:

    Term A, the vol LEVEL      v1: ``base`` constant at 0.16
                               v2: ``base`` driven bar by bar off a VIX / realised-vol series
    Term B, the moneyness REF  v1: ``spot_ref`` pinned to the PERIOD-START spot
                               v2: ``spot_ref`` = the current bar's spot

The reason v1 pins ``spot_ref`` is counter-intuitive and worth stating here rather than leaving
in the plan: skew is a function of ``log(K/S)``, so holding K fixed and dropping S slides the leg
DOWN the smirk — a strike the market has actually fallen to is no longer a tail strike. Measured
on this function at ``K=90, t=0.125, r=0.02``, spot ``S=88`` gives iv 0.176 / price 3.206 on the
current-spot reference against 0.210 / 3.612 frozen: 11% CHEAPER in exactly the crash state
Phase 13 exists to measure. Term A is what actually lifts a put in a selloff. Cell 8 of
``research/option_overlay_probe.py`` plots both references side by side.

v1 also carries NO Phase 11 dependency — ``base = 0.16`` needs no vol history at all — which is
why the option instruments and rules are buildable before 11-01 lands.
"""

import numpy as np


# ============================================================================
# SYNTHETIC IV SURFACE — implied vol as a function of strike and tenor.
# Pure: no I/O, no state, no market data. Called per leg, per bar, by whatever
# pipeline is running the overlay; vectorises over `strike` and `tau` because
# numpy is the only thing it touches.
# ============================================================================

def synthetic_iv_surface(
    strike,                 # absolute strike price K (scalar or array), same units as spot_ref
    tau,                    # time to expiry in YEARS (scalar or array); 63 trading days = 0.25
    spot_ref,               # the spot moneyness is measured against — see the v1/v2 note above
    *,
    base=0.16,              # ATM vol LEVEL <- v2 replaces this default with the Phase 11 series.
                            # An ARGUMENT with the crash.py value as its default, never an inline
                            # literal, so v2 needs no edit to this function — only a caller change
    term_coef=0.06,         # strength of the term structure in sqrt(tau)
    skew_coef=-0.24,        # slope of the smirk in log-moneyness; negative => lower K, higher IV
    smile_coef=0.30,        # convexity in log-moneyness; keeps the smirk surface-like at the wings
    iv_floor=0.10,          # clip floor — no leg is ever priced off an implausibly cheap vol
    iv_cap=0.85,            # clip cap — and none off a vol the market has never actually printed
):
    """Return implied vol for ``strike`` at tenor ``tau``, measured against ``spot_ref``.

    Every constant is a keyword argument with the ``crash.py`` value as its default, because
    12-01 requires that ``base`` in particular is "an ARGUMENT with a default of 0.16, never an
    inline literal". v1 takes the default; v2 passes a bar-by-bar level in without this
    function changing at all.

    Parameters
    ----------
    strike, tau, spot_ref : float or array-like
        Broadcast against each other by numpy, so a strike ladder can be priced across a date
        index in one call.

    Returns
    -------
    float or ndarray
        Implied vol as a decimal (0.16 = 16%), clipped to ``[iv_floor, iv_cap]``.
    """
    # Equity-like: lower strikes carry higher IV (put skew / smirk),
    # longer expiries slightly more IV, mild convexity keeps it surface-like.
    log_moneyness = np.log(np.asarray(strike, dtype=float) / np.asarray(spot_ref, dtype=float))
    # Term structure: sqrt(tau) so the curve is steep at the front and flattens out, matching how
    # a real term structure behaves. The maximum() guards log/sqrt against an expiring leg where
    # tau has run to exactly zero.
    term = term_coef * np.sqrt(np.maximum(tau, 1e-6))
    # THE skew term: K < S  =>  higher IV. This is the term the put spread's economics live on.
    skew = skew_coef * log_moneyness
    # Convexity — without it the wings extrapolate linearly and deep strikes price absurdly.
    smile = smile_coef * log_moneyness ** 2
    # Clip last, so no combination of extreme strike and extreme tenor can hand the pricer a
    # negative or a 400% vol. The bounds are the source's own.
    return np.clip(base + term + skew + smile, iv_floor, iv_cap)
