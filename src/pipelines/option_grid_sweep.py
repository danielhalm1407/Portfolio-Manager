"""
13-02 — THE PARAMETER GRID. ONE CODE PATH, CALLED MANY TIMES.

WHAT EXISTS IN THIS FILE TODAY, AND WHAT DOES NOT
--------------------------------------------------
This module currently holds the SWEEP RUNNER and the axis definitions, and nothing else. There is
no ``main()``, no 49-combination Cartesian product and no output writing yet — that is Task 2b,
and it is deliberately not written.

The reason is 13-02's own build order, confirmed with the user on 2026-09-26 and reaffirmed
2026-10-17: this plan is worked ONE MECHANISM AT A TIME, in the spirit of 13-01's ladder where
rungs 3a/3b/3c each changed exactly one thing and each was its own stop. The first two steps are
the moneyness axis in isolation (a blind roll at four strikes) and then the monetisation trigger
added on top. Both are driven from ``research/validation/option_moneyness_ladder.py``, a cell
script, and both call ``run_config`` below.

WHY THE RUNNER LIVES HERE AND NOT IN THAT CELL SCRIPT
-----------------------------------------------------
Task 2a's requirement is that the full grid is "the SAME code path called more times" — a grid
that works through a different code path from its own smoke test proves nothing about the smoke
test. So the runner is library-shaped from the first exploratory run rather than being lifted out
of a research script later, by which time the two would already have drifted.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No bid/ask, no slippage, no market impact: every roll, monetisation and reopen fills at the model
mark, which flatters every configuration and most of all the ones that trade most. Stated here
once and repeated beside every table, because a policy that trades more looks better than it is
under this assumption.

EVERYTHING THIS MODULE PRODUCES IS IN-SAMPLE. Every threshold is chosen looking at the same
windows it is scored on. That is the deliberate first stage, not a defect to apologise for —
13-03's CPCV is where it gets tested. Nothing here may be described as a validated result.
"""

import time

import pandas as pd

from portutils.portfolio.book import Book
from portutils.portfolio.narrow_ledger import NarrowLedger
from portutils.portfolio.rules import BuyAndHoldRule
from portutils.portfolio.simulator import PortfolioSimulator
from portutils.strategies import scoring
from portutils.strategies.rules.options import ProtectivePutRule

# The loaders and the shared carry constants live in 13-01's batch runner and the probe figure
# module. IMPORTED, never re-derived: a second copy of "which bars are the run window" is a
# second thing to get wrong, and it would be wrong silently.
from pipelines.option_monetisation_batch import RESET_BARS, build_series_frame, load_window
from pipelines.option_probe_figures import DIV_YIELD, RATE, UNDERLYING

# ============================================================================
# THE AXES.
#
# Both are module-level lists so the grid is readable without running anything.
# TENOR IS NOT AN AXIS: it stays fixed at RESET_BARS (63 bars, the quarterly
# cadence every prior plan used). A third axis multiplies the runtime by the
# length of its list, and this plan must first show that two axes are
# affordable — see 13-02's override table, item 2.
# ============================================================================

# THE EXPLORATORY LADDER — build-order step 1. Four strikes, wide apart, chosen to match the
# 2026-08-31 probe's own ladder so the first figure can be read against a result that already
# exists (0.80x LOSES 46.9% on a -9.1% fall under frozen vol and returns +317% under a vol-level
# response — the measurement that made the case for real IV in the first place). Wide spacing is
# the point at this step: four adjacent strikes two percent apart would be four nearly identical
# lines, and what step 1 has to establish is that the axis MOVES anything at all.
MONEYNESS_LADDER = (0.80, 0.85, 0.90, 0.95)

# THE GRID AXIS — the ladder's four strikes INTERLEAVED with the half-steps between them, plus
# at-the-money. Nine points from 0.80 to 1.00 in steps of 0.025.
#
# WHY IT BRACKETS THE LADDER RATHER THAN SITTING INSIDE IT (changed 2026-10-17, from the seven
# points 0.85-0.97 the plan first sized). The grid's job is a SURFACE, and a surface fitted over
# 0.85-0.97 cannot say whether the optimum is at the edge or beyond it. Including 0.80 and 1.00
# — the two ends the economics argue about, the near-worthless deep-OTM wing and the
# unaffordable ATM hedge — means an optimum found in the interior is an interior optimum rather
# than an artefact of where the axis was cut. The interleaving also makes every ladder strike a
# grid point, so the four stacked path figures are four COLUMNS of this grid and not a separate
# experiment.
#
# Nine points at ~2-6s per full-history run is well under a minute, which keeps the grid
# RE-RUNNABLE after the metric or the window set changes — the difference between a design
# surface and a one-shot result.
MONEYNESS_GRID = (0.800, 0.825, 0.850, 0.875, 0.900, 0.925, 0.950, 0.975, 1.000)

# THE MONETISATION-DRAWDOWN AXIS — Task 2b. Six depths plus ``None``, the blind roll, which is
# the reference row the whole monetisation question is asked against and therefore a member of
# the axis rather than a separate case.
MONETISE_DRAWDOWN_GRID = (None, 0.06, 0.08, 0.10, 0.12, 0.16, 0.20)

# The single illustrative depth build-order step 2 arms, so that step introduces ONE new thing.
# Same value 13-01's batch used, deliberately: step 2's figure is then readable against 13-01's
# own full-history run rather than against nothing.
LADDER_MONETISE_DRAWDOWN = 0.10

# The re-entry gate and the unhedged ceiling, held FIXED across every configuration here. They
# are 13-01 parameters, not 13-02 axes — this plan sweeps moneyness and monetisation drawdown and
# nothing else — but they cannot be left unset, because `max_flat_bars=None` waits forever and in
# 2008-09 that is an unhedged book straight through the drawdown.
LADDER_REENTRY_IV = 0.18
LADDER_MAX_FLAT_BARS = 126


# ============================================================================
# DATA — loaded once per process, because every configuration slices the same
# series and reloading the parquet per run would dominate a 1-2 second run.
# ============================================================================

_FULL = None


def load_full(reload=False):
    """``(spot, iv)`` over the usable window: SPY closes and REAL implied vol on their
    intersection, 5,201 bars, 2006-01-09 to 2026-09-18.

    Delegates to 13-01's ``load_window``, which prints the pre-2006 truncation loudly rather than
    absorbing it — v2 cannot price those thirteen years, which is a Phase 11 data limit and not a
    choice. Cached in a module global so a cell script can call this in every cell without paying
    for the parquet each time; ``reload=True`` forces a re-read.
    """
    global _FULL
    if _FULL is None or reload:
        _FULL = load_window()
    return _FULL


# ============================================================================
# THE RUNNER
# ============================================================================

def run_config(floor=0.90, monetise_drawdown=None, window=None, rule_cls=ProtectivePutRule,
               spot=None, iv=None, reentry_iv=LADDER_REENTRY_IV,
               max_flat_bars=LADDER_MAX_FLAT_BARS, quiet=False, **rule_kwargs):
    """One parameter combination over one window. THE single code path for every step of 13-02.

    Parameters
    ----------
    floor : float
        The moneyness axis — the put's strike as a fraction of the reference spot.
    monetise_drawdown : float or None
        The monetisation axis. ``None`` is the BLIND ROLL: hold every leg to expiry and re-strike
        every ``RESET_BARS``, which is what the rules did before 13-01 and the reference the
        monetisation question is asked against. When it is None the re-entry gate and
        ``max_flat_bars`` are not passed either — there is no monetisation, so there is no flat
        period for them to govern, and passing them would imply a policy that never runs.
    window : tuple or None
        ``(start, end)``, or None for the full usable history. Build-order steps 1-2 use the GFC
        window; step 3 runs the FULL history once per configuration and scores every indicative
        window by slicing the result, which is the property AC-2 exists to protect.
    spot, iv : Series or None
        The window's closes and real IV. None loads and slices ``load_full()``.

    Returns
    -------
    dict
        ``state`` (the narrow per-bar frame), ``series`` (the persisted artefact shape, via
        13-01's ``build_series_frame`` — ONE definition of what a per-bar artefact carries, so
        this plan's frames and 13-01's cannot differ), ``blotter``, ``history``, ``rule``,
        ``elapsed``, ``spot``, and the parameters themselves under ``params``.
    """
    # ------------------------------------------------------------------------
    # THE WINDOW. Sliced from the already-loaded full series rather than reloaded, so changing
    # one parameter in a live session costs a simulator run and nothing else.
    # ------------------------------------------------------------------------
    if spot is None or iv is None:
        spot_full, iv_full = load_full()
        start, end = window if window else (None, None)
        spot = spot_full.loc[start:end] if spot is None else spot
        iv = iv_full.loc[start:end] if iv is None else iv
    if len(spot) == 0:
        raise ValueError(f"empty window {window}")

    # ------------------------------------------------------------------------
    # THE RULE. v2 pricing always — real IV level AND current-spot moneyness, together, never
    # term B alone (the 2026-08-29 decision). `iv_series` is sliced to the SAME bars the
    # simulator will visit, because `_iv_now()` raises naming the bar if one is missing, and
    # that loud failure is the behaviour we want rather than a silent fallback.
    # ------------------------------------------------------------------------
    kw = dict(underlying=UNDERLYING, reset_bars=RESET_BARS, rate=RATE, div_yield=DIV_YIELD,
              vol_mode="v2", iv_series=iv, floor=floor)
    if monetise_drawdown is not None:
        # The policy arguments travel TOGETHER with the trigger, never separately: a re-entry
        # gate with no trigger to create a flat period is dead configuration that would still
        # show up in the run's parameter record and invite a reader to think it did something.
        kw.update(monetise_drawdown=monetise_drawdown, reentry_iv=reentry_iv,
                  max_flat_bars=max_flat_bars)
    kw.update(rule_kwargs)
    rule = rule_cls(**kw)

    # ------------------------------------------------------------------------
    # A FRESH Book AND A FRESH NarrowLedger EVERY CALL. `PortfolioSimulator.run()` resets rules
    # but NOT the book (simulator.py:48-50), so a reused book carries the previous
    # configuration's dead legs into this one — reinstating the exact quadratic cost 13-01.1
    # exists to remove, and compounding it across the sweep. This is the failure mode Task 2a's
    # four-corner check asserts against (ledger column count after config 4 == after config 1).
    #
    # `ledger=NarrowLedger()` is NOT optional: without it the simulator defaults to StateLedger
    # (simulator.py:37) and one full-history run costs ~6.5 min instead of ~1-2 s. That omission
    # is what had kept `option_monetisation_batch.py` from ever completing a run — see its
    # run_one comment and 13-01-SUMMARY.md.
    #
    # Starting capital = the window's first close, so every value reads in SPY PRICE UNITS and
    # the equity line is directly comparable to spot. Same convention as 16-03, 13-01 and the
    # ladder probe.
    # ------------------------------------------------------------------------
    sim = PortfolioSimulator(spot.to_frame(UNDERLYING),
                             [BuyAndHoldRule({UNDERLYING: 1.0}), rule],
                             book=Book(base_equity=float(spot.iloc[0])),
                             starting_capital=float(spot.iloc[0]),
                             ledger=NarrowLedger())
    t0 = time.perf_counter()
    state = sim.run()
    elapsed = time.perf_counter() - t0

    # The same loud guard 13-01's batch, the ladder probe and overlay_runs all use: legs proposed
    # but never filled means the synthetic-marks hook is not wired, and the hedged line would
    # silently equal the unhedged one — which looks like a finding and is a bug.
    if rule.events and not any(scoring.is_option_symbol(f.symbol, UNDERLYING) for f in sim.fills):
        raise RuntimeError(f"floor={floor}: option legs were proposed but never filled")

    blotter = sim.blotter()
    # The rule's per-bar read-outs, keyed by TIMESTAMP rather than bar number so they join
    # straight onto the state frame and onto the blotter.
    history = pd.DataFrame(rule.history.values())
    if len(history):
        history = history.set_index("ts").sort_index()

    if not quiet:
        label = f"floor={floor:.2f} dd={'blind' if monetise_drawdown is None else f'{monetise_drawdown:.0%}'}"
        print(f"{label:28s} | {len(spot):,} bars {spot.index.min().date()}->"
              f"{spot.index.max().date()} | {elapsed:5.2f}s "
              f"({1000 * elapsed / len(spot):.2f} ms/bar) | {len(state.columns)} cols | "
              f"{len(blotter)} fills | {len(rule.events)} events")

    return {
        "params": {"floor": floor, "monetise_drawdown": monetise_drawdown,
                   "reset_bars": RESET_BARS},
        "state": state,
        # The persisted artefact shape, built by 13-01's own function. AC-2 requires three
        # artefacts per combination — this frame, the blotter and the rule history — because
        # under NarrowLedger the equity series alone can no longer explain itself.
        "series": build_series_frame(state, rule, spot),
        "blotter": blotter,
        "history": history,
        "rule": rule,
        "elapsed": elapsed,
        "spot": spot,
    }


def slice_result(res, start, end):
    """One run's artefacts cut to a window, for a figure that reads a slice of a full-history run.

    Both halves have to be cut the SAME way or the markers drift off the line: the per-bar frame
    by index, the blotter by its ``ts`` column. Returned as a fresh dict rather than mutated in
    place, because the full-history originals are reused by every other window.
    """
    series = res["series"].loc[start:end]
    blot = res.get("blotter")
    if blot is not None and len(blot):
        blot = blot[(blot["ts"] >= pd.Timestamp(start)) & (blot["ts"] <= pd.Timestamp(end))]
    return {"series": series, "blotter": blot, "params": res.get("params", {})}


def config_label(floor, monetise_drawdown):
    """The one naming convention for a combination, used by every table, figure legend and
    output filename. Centralised because a label that differs between a figure and a table is a
    reader silently comparing two different rows."""
    dd = "blind" if monetise_drawdown is None else f"dd{monetise_drawdown:.0%}"
    return f"{floor:.2f}x/{dd}"
