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
from pathlib import Path

import numpy as np
import pandas as pd

from portutils.portfolio.book import Book
from portutils.portfolio.narrow_ledger import NarrowLedger
from portutils.portfolio.rules import BuyAndHoldRule
from portutils.portfolio.simulator import PortfolioSimulator
from portutils.strategies import scoring, surface
from portutils.strategies.rules.options import (BudgetMatchedPutRule, PortfolioBudgetPutRule,
                                                ProtectivePutRule)
from portutils.viz import theme

# The figure builders. Imported at module scope and not deferred: there is no cycle —
# option_grid_figures imports option_probe_figures and this module, never the other way round.
import pipelines.option_grid_figures as gridfig

# The report renderer the other two published sections already use. One implementation, so the
# heading -> figure guard, the link rewriting and the page CSS cannot drift between the three.
# No cycle: build_report imports option_probe_figures' build_all lazily and only when it is given
# no figures, which this module never does.
from pipelines.build_report import build_report

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
# SIZING (13-02.1, decisions D2 and D3, 2026-10-18). 13-02 bought ONE put per
# unit of SPY at every strike, so a 0.80x put was bought for a small fraction
# of what a 0.95x put cost, and the moneyness axis was really a SPEND axis in
# disguise. Every run here is now sized to an EQUAL PREMIUM BUDGET: as many
# contracts at strike K as the premium of one BUDGET_MONEYNESS put buys, per
# unit held (BudgetMatchedPutRule). Spend is then constant across strikes and
# the grid asks the real question: for the same money, which strike protects
# best. 13-02's one-per-unit results are kept in outputs/option_grid/.
# ============================================================================
BUDGET_MONEYNESS = 0.95

# The second sizing class, run ONCE as a reference rather than swept: a fixed share of portfolio
# value spent on puts each year (PortfolioBudgetPutRule), at the ladder's default strike and
# trigger. It answers the allocation question as a holder would state it ("I will spend 5% a
# year on insurance") beside the grid's budget-matched answer.
PORTFOLIO_BUDGET_PCT = 0.05
REFERENCE_FLOOR = 0.90

# THE PROFITABLE-MONETISE GUARD (13-02.1 round 2, 2026-10-19). The drawdown trigger is not
# allowed to sell a hedge worth less than this multiple of its premium; it holds and lets the
# ordinary roll replace it instead. Found on the GFC: the standing 0.80x programme sold a put
# struck at the October 2007 peak for 0.009x premium four bars before expiry, then sat unhedged
# for four months. See _ProfitableMonetiseMixin in rules/options.py for the measurement.
MONETISE_MIN_MULTIPLE = 1.0


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

def run_config(floor=0.90, monetise_drawdown=None, window=None, rule_cls=BudgetMatchedPutRule,
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
    # The budget strike travels with the budget-matched rule unless a caller overrides it, so
    # the default call is sized exactly as decision D2 says without every caller repeating it.
    if rule_cls is BudgetMatchedPutRule:
        kw.setdefault("budget_moneyness", BUDGET_MONEYNESS)
    # The guard travels with the trigger on both budget rules, never on the plain rule.
    if monetise_drawdown is not None and rule_cls in (BudgetMatchedPutRule,
                                                      PortfolioBudgetPutRule):
        kw.setdefault("monetise_min_multiple", MONETISE_MIN_MULTIPLE)
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
                   "reset_bars": RESET_BARS, "rule": rule_cls.__name__,
                   "budget_moneyness": getattr(rule, "budget_moneyness", None),
                   "budget_pct": getattr(rule, "budget_pct", None)},
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
        # Cut at the END only, NOT at the start (fixed 13-02.1). Lifecycle classification
        # (classify_option_events) follows the running position from the first fill it is
        # given, so a blotter cut at the window start turns the close of a leg opened BEFORE the
        # window into an apparent "opening" fill: the COVID monetisation of 2020-02-27 was
        # being drawn and counted as an OPEN. Keeping the earlier fills lets the classifier see
        # the position it is closing; the figure drops events outside the series' own index.
        blot = blot[blot["ts"] <= pd.Timestamp(end)]
    return {"series": series, "blotter": blot, "params": res.get("params", {})}


def config_label(floor, monetise_drawdown):
    """The one naming convention for a combination, used by every table and figure legend.
    Centralised because a label that differs between a figure and a table is a reader silently
    comparing two different rows."""
    dd = "blind" if monetise_drawdown is None else f"dd{monetise_drawdown:.0%}"
    return f"{floor:.2f}x/{dd}"


def config_slug(floor, monetise_drawdown):
    """The FILENAME form of the same combination.

    Separate from ``config_label`` because the label carries ``/`` and ``%`` — fine in a legend,
    and on Windows a ``/`` silently turns one file into a directory that is not there. The slug
    keeps the parameters readable (``m0900_dd10``) rather than hashing them, so a parquet in
    ``outputs/`` can still be matched to a row of the score table by eye.
    """
    dd = "blind" if monetise_drawdown is None else f"dd{monetise_drawdown * 100:.0f}"
    return f"m{floor * 1000:.0f}_{dd}"


# ============================================================================
# TASK 2b — THE FULL GRID.
#
# Everything above is the runner. Everything below runs it once per combination
# of the two axes, scores every window by SLICING the stored series, fits the
# response surfaces and writes the lot to outputs/option_grid/.
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 13-02.1: budget-sized results land in their OWN directory. 13-02's one-put-per-unit sweep stays
# untouched in outputs/option_grid/ (UNIT_OUT_DIR) so the two sizings can be compared directly.
UNIT_OUT_DIR = PROJECT_ROOT / "outputs" / "option_grid"
# Round 2 (2026-10-19): the guarded rerun lands in its own directory too, leaving the first
# budget-sized run (no guard, pro-rata 5% reference) in UNGUARDED_OUT_DIR for comparison.
UNGUARDED_OUT_DIR = PROJECT_ROOT / "outputs" / "option_grid_budget"
OUT_DIR = PROJECT_ROOT / "outputs" / "option_grid_budget_guarded"
SERIES_DIR = OUT_DIR / "series"
BLOTTER_DIR = OUT_DIR / "blotter"
HISTORY_DIR = OUT_DIR / "rule_history"

# THE SINGLE-RUN CONSTANT THE PROJECTION MULTIPLIES. 13-01.1's SUMMARY records 6.30 s over 5,201
# bars (1.21 ms/bar) for a protective put with monetisation armed — the measurement this plan's
# AC-1 is required to cite, and NOT 13-01's superseded ~6.5 MINUTE figure, which 13-01.1
# invalidates by roughly 25x.
#
# Corroborated twice since, on the same structure this sweep varies: 1.0-1.8 s per full-history
# run in the ladder probe, and 2-6 s per run measured across the nine-strike ladder in
# research/validation/option_moneyness_optimisation.py on 2026-10-17. The LARGER 13-01.1 number
# is kept deliberately — a projection that comes in under budget and then overruns is worse than
# one that is pessimistic and beaten.
SINGLE_RUN_SECONDS = 6.3

# If the projection exceeds this, the sweep STOPS and says so rather than starting. 13-01 wrote a
# seven-configuration script and measured it second, which is how a 45-minute run got started
# before anyone knew it was 45 minutes (13-01 implementation context, section 9). The budget is
# the structural fix for that, not a comment asking the next person to be careful.
RUNTIME_BUDGET_MINUTES = 30.0


def grid_combinations(moneyness=None, drawdowns=None):
    """The explicit Cartesian product of the two axes, as a list of ``(floor, drawdown)`` pairs.

    A function rather than a module constant so the count can be printed and written out BEFORE
    anything runs — that count is the TRIAL COUNT, the number a probability-of-backtest-
    overfitting figure needs in 13-03, and it has to be recorded at the moment the design is
    fixed rather than reconstructed afterwards from however many files happen to exist.
    """
    moneyness = MONEYNESS_GRID if moneyness is None else moneyness
    drawdowns = MONETISE_DRAWDOWN_GRID if drawdowns is None else drawdowns
    return [(f, d) for f in moneyness for d in drawdowns]


def sweep_grid(combinations=None, persist=True, quiet=True):
    """Run every combination ONCE over the full history, persisting AC-2's three artefacts.

    Returns ``(results, elapsed)`` where ``results`` maps the config label to ``run_config``'s
    dict. Scoring is NEVER done here: it is a slice of what this returns, which is the property
    that collapses 13-02's and 13-03's cost from (grid x origins) simulations to (grid).
    """
    combinations = grid_combinations() if combinations is None else combinations
    if persist:
        for d in (SERIES_DIR, BLOTTER_DIR, HISTORY_DIR):
            d.mkdir(parents=True, exist_ok=True)

    results = {}
    t0 = time.perf_counter()
    for floor, dd in combinations:
        # A FRESH Book and a FRESH NarrowLedger per call — see run_config. This loop is exactly
        # where a reused book would compound, which is why the ledger width is asserted below.
        res = run_config(floor=floor, monetise_drawdown=dd, window=None, quiet=quiet)
        results[config_label(floor, dd)] = res
        if persist:
            slug = config_slug(floor, dd)
            # AC-2's THREE artefacts. All three, because under NarrowLedger the equity series
            # alone can no longer explain itself: the per-leg columns that used to carry the
            # story are gone, so the blotter and the rule's own per-bar history ARE the
            # explanation. A caller that persists only equity gets a fast run it cannot debug.
            res["series"].to_parquet(SERIES_DIR / f"{slug}.parquet")
            res["blotter"].to_csv(BLOTTER_DIR / f"{slug}.csv", index=False)
            res["history"].to_csv(HISTORY_DIR / f"{slug}.csv")
    elapsed = time.perf_counter() - t0

    # THE STATE-LEAK ASSERTION. `PortfolioSimulator.run()` resets rules but NOT the book
    # (simulator.py:48-50), so a sweep that reused a Book would carry each combination's dead
    # legs into the next and the quadratic cost 13-01.1 removed would come back compounded. A
    # constant ledger width across every run is the evidence it did not happen — asserted rather
    # than hoped for, because the symptom is a slow run and not a wrong number.
    widths = {k: len(v["state"].columns) for k, v in results.items()}
    if len(set(widths.values())) != 1:
        raise RuntimeError(f"ledger width drifted across the sweep — state leaked: {widths}")
    return results, elapsed


def load_sweep(out_dir=None):
    """Reload a completed sweep from disk. NO SIMULATOR, no live session state.

    Returns ``(results, scores)`` in exactly the shapes ``sweep_grid`` and ``tidy_scores``
    produced, so every figure builder takes a reloaded run and a fresh one interchangeably.

    This is what makes Task 5's reading script possible at all, and the reason AC-2 insists the
    artefacts be persisted rather than held in memory: a sweep costs minutes, and a session
    spent READING it — trying a different window, a different pair of configurations, a
    different colour scale — must not re-pay that cost once, let alone once per idea.

    The config LABEL is recovered from ``scores_tidy.csv`` rather than parsed back out of the
    filename. The slug is a one-way function by design (see ``config_slug``); round-tripping it
    would be a second, independent definition of the naming convention, free to disagree with
    the first the moment either changes.
    """
    out_dir = OUT_DIR if out_dir is None else Path(out_dir)
    scores_path = out_dir / "scores_tidy.csv"
    if not scores_path.exists():
        raise FileNotFoundError(
            f"no sweep found at {scores_path} — run `python -m pipelines.option_grid_sweep` first")
    scores = pd.read_csv(scores_path)

    # One row per configuration, carrying the parameters that name its files.
    params = (scores[["config", "floor", "monetise_drawdown"]]
              .drop_duplicates(subset=["config"]).set_index("config"))
    results = {}
    for label, row in params.iterrows():
        dd = None if pd.isna(row["monetise_drawdown"]) else float(row["monetise_drawdown"])
        slug = config_slug(float(row["floor"]), dd)
        series = pd.read_parquet(out_dir / "series" / f"{slug}.parquet")
        blot = pd.read_csv(out_dir / "blotter" / f"{slug}.csv", parse_dates=["ts"])
        hist = pd.read_csv(out_dir / "rule_history" / f"{slug}.csv", index_col=0,
                           parse_dates=[0])
        results[label] = {"series": series, "blotter": blot, "history": hist,
                          "params": {"floor": float(row["floor"]), "monetise_drawdown": dd}}
    return results, scores


def tidy_scores(results, spy, underlying=UNDERLYING):
    """``scoring.score_grid`` over every combination, with the two swept PARAMETERS joined back on.

    The parameters have to travel with the scores or the surface cannot be fitted: a tidy frame
    keyed only by a config LABEL would need the label parsed back into numbers, and a parser is
    a second definition of the naming convention waiting to disagree with the first.
    """
    scores = scoring.score_grid({k: v["series"] for k, v in results.items()}, spy,
                                blotter_by_config={k: v["blotter"] for k, v in results.items()},
                                underlying=underlying)
    params = {k: v["params"] for k, v in results.items()}
    scores["floor"] = scores["config"].map(lambda c: params[c]["floor"])
    # The blind roll's depth is `None`, which cannot sit on a numeric axis. Left as NaN and
    # FLAGGED rather than mapped to 0.0: "monetise at a 0% drawdown" is not what a blind roll
    # is, and a stand-in zero would become a design point the spline fits through — anchoring
    # the surface at a place the grid never ran.
    scores["monetise_drawdown"] = scores["config"].map(
        lambda c: params[c]["monetise_drawdown"] if params[c]["monetise_drawdown"] is not None
        else np.nan)
    scores["blind_roll"] = scores["monetise_drawdown"].isna()
    return scores


def demeaned_scores(scores):
    """``scores`` with ``score`` replaced by its deviation from that WINDOW's mean.

    The design-point overlay on a pooled figure has to be on the SAME vertical scale as the
    surface drawn under it, or the open circles float above or below a contour they are meant to
    sit on and the figure argues against itself. Since the pooled surface is fitted on demeaned
    scores (see ``fit_surfaces``), its design points must be demeaned the same way.
    """
    out = scores.dropna(subset=["score"]).copy()
    # Blind-roll rows dropped here too, matching `fit_surfaces`. They were never on the drawdown
    # axis, so including them would shift each window's mean away from the one the surface was
    # actually fitted against — a half-centimetre offset between the contour and its own points.
    if "blind_roll" in out.columns:
        out = out[~out["blind_roll"]]
    out["score"] = out["score"] - out.groupby("window")["score"].transform("mean")
    return out


def fit_surfaces(scores, drop_blind=True):
    """AC-5's staged fits: 1-D along each axis FIRST, then the 2-D tensor smooth, per window AND
    pooled.

    THE STAGING IS THE POINT, not a formality. A 2-D smooth has two smoothing parameters and an
    interaction, so when it looks wrong there are three candidate causes. Fitting each axis alone
    first means a one-dimensional answer is already in hand — "the score rises to about 0.90 and
    then flattens" — against which the surface either agrees or raises a question that can be
    named.

    The BLIND-ROLL rows are dropped from every fit by default; see `tidy_scores` for why they
    are not a point on the drawdown axis at all.

    Returns ``(fits, optima, bounds, one_d)``.
    """
    df = scores.dropna(subset=["score"])
    if drop_blind:
        df = df[~df["blind_roll"]]
    bounds = ((float(df["floor"].min()), float(df["floor"].max())),
              (float(df["monetise_drawdown"].min()), float(df["monetise_drawdown"].max())))

    # ---- 1-D FIRST, each axis with the other held at its DEFAULT ------------
    # "Held at its default" rather than averaged over: averaging across the other axis is
    # already a two-dimensional claim, and the purpose of the 1-D stage is to have one that
    # is not.
    one_d = {}
    slice_dd = df[np.isclose(df["monetise_drawdown"], LADDER_MONETISE_DRAWDOWN)]
    if len(slice_dd):
        by_floor = slice_dd.groupby("floor")["score"].mean()
        one_d["moneyness"] = surface.fit_1d(by_floor.index.to_numpy(), by_floor.to_numpy())
    slice_floor = df[np.isclose(df["floor"], 0.90)]
    if len(slice_floor):
        by_dd = slice_floor.groupby("monetise_drawdown")["score"].mean()
        one_d["monetise_drawdown"] = surface.fit_1d(by_dd.index.to_numpy(), by_dd.to_numpy())

    # ---- THEN 2-D, per window and pooled ------------------------------------
    fits, optima = {}, {}
    for window, grp in df.groupby("window", sort=False):
        fits[window] = surface.fit_2d(grp["floor"], grp["monetise_drawdown"], grp["score"])
        optima[window] = surface.optimum(fits[window], bounds)
    # POOLED over every observation from every window. Four windows are four REGIMES, not four
    # replicates, so pooling AVERAGES AWAY the spread between them — which is why the per-window
    # fits above exist and are produced first. The pooled fit is reported beside them, never
    # instead of them.
    fits["POOLED"] = surface.fit_2d(df["floor"], df["monetise_drawdown"], df["score"])
    optima["POOLED"] = surface.optimum(fits["POOLED"], bounds)

    # ========================================================================
    # AND A SECOND POOLED FIT, ON WINDOW-DEMEANED SCORES — because the first
    # one is empty. MEASURED 2026-10-17 on the 63-combination run: pooling the
    # RAW scores gives R-squared 0.009 at the maximum smoothing parameter, edf
    # 4.0, and a near-flat plane whose "optimum" is simply the corner of the
    # axis. That is not a failed fit; it is the correct answer to the question
    # as posed. Floored Calmar runs around -0.7 through the drawdown windows
    # and around +5 through the calm ones, so almost all the variance in the
    # pooled column is WHICH WINDOW A ROW CAME FROM and almost none of it is
    # the two parameters. GCV sees no parameter signal and returns a plane.
    #
    # Demeaning by window removes that fixed effect and leaves exactly what the
    # pooled surface was wanted for: the parameter effect that is COMMON across
    # regimes, measured as a deviation from each window's own mean. It is a
    # DIFFERENT QUANTITY and is labelled as one everywhere it appears — the
    # height is no longer a Calmar, it is "better or worse than the average
    # configuration in the same window".
    #
    # Both are kept. The raw pooled fit is what AC-5 literally asks for and its
    # emptiness is itself a reportable result; the demeaned one is the only
    # pooled object with anything in it.
    # ========================================================================
    demeaned = df["score"] - df.groupby("window")["score"].transform("mean")
    fits["POOLED_DEMEANED"] = surface.fit_2d(df["floor"], df["monetise_drawdown"], demeaned)
    optima["POOLED_DEMEANED"] = surface.optimum(fits["POOLED_DEMEANED"], bounds)
    return fits, optima, bounds, one_d


def fit_vs_raw(scores, fits, optima):
    """Every fitted optimum next to the RAW GRID ARGMAX for the same window.

    ==========================================================================
    THE CHECK THAT CATCHES AN UNDER-SMOOTHED FIT, and it is needed here rather
    than being belt-and-braces. The simulator is DETERMINISTIC at a fixed
    window, so there is no noise for GCV to regularise against and it drives
    the smoothing parameter towards its floor — these fits sit close to
    interpolation (edf around 30 of 36 coefficients). An interpolant passes
    through every design point and can still overshoot BETWEEN them.
    Measured on the 63-combination run: five of six windows put the fitted
    optimum within one grid step of the raw argmax, but Rates 2022 reported a
    fitted VALUE of +0.520 where the best simulated cell scored +0.161. The
    LOCATION was right and the HEIGHT was invented.
    So: trust a fitted optimum's position when it agrees with the argmax, and
    never quote its height as an achievable score. ``value_overshoot`` below is
    that gap, printed every run rather than rediscovered.
    ==========================================================================
    """
    df = scores.dropna(subset=["score"])
    df = df[~df["blind_roll"]]
    rows = []
    for window, grp in df.groupby("window", sort=False):
        if window not in optima:
            continue
        best = grp.loc[grp["score"].idxmax()]
        opt = optima[window]
        rows.append({
            "window": window,
            "raw_floor": best["floor"], "raw_dd": best["monetise_drawdown"],
            "raw_score": best["score"],
            "fit_floor": opt.point[0], "fit_dd": opt.point[1], "fit_value": opt.value,
            # Positive means the smoother claims a score no simulated cell achieved.
            "value_overshoot": opt.value - best["score"],
            # How far the fitted location sits from the best cell, in moneyness units.
            "floor_gap": abs(opt.point[0] - best["floor"]),
            # The whole window's spread across configurations. A window whose spread is smaller
            # than the overshoot has nothing to say about parameters at all.
            "window_spread": grp["score"].max() - grp["score"].min(),
            "curvature": opt.curvature,
        })
    return pd.DataFrame(rows)


# ============================================================================
# PUBLICATION — the third section of the static site.
#
# Same shape as the other two sections: a write-up rendered to HTML with each
# figure spliced in at the heading that discusses it, plus one standalone page
# per figure beside it. The prose is RENDERED from the Markdown, never retyped,
# so the page and the write-up cannot drift — the same rule the other two
# sections follow, through the same `build_report`.
# ============================================================================

DOCS_DIR = PROJECT_ROOT / "docs" / "optimisation"
DOCS_FIGURES_DIR = DOCS_DIR / "figures"
WRITE_UP = PROJECT_ROOT / "research" / "validation" / "option_moneyness_optimisation.md"

# The ONE vendored plotly.min.js at docs/assets/, spelled relative to each page that loads it. A
# page in docs/optimisation/figures/ is two levels below docs/; the section index is one. Getting
# this wrong points the script tag at a directory that does not exist and the figures silently
# never draw — which is why it is two constants rather than one.
PLOTLY_SRC_FIGURE = "../../assets/plotly.min.js"
PLOTLY_SRC_INDEX = "../assets/plotly.min.js"

# ============================================================================
# 13-02.1 — THE MECHANISM RUNS, THE REFERENCE RUN AND THE 1-D FIT LAYERS.
#
# The page now argues in the order a reader needs: HOW a strike wins (the GFC
# ladder, then every window as paths), then the score built up one ingredient
# at a time, then the 1-D fits at a fixed trigger, and only then the surfaces.
# Everything below feeds that order. None of it changes the grid itself.
# ============================================================================

# The GFC window for the mechanism figures (steps 1, 2 and 2b), and the two strikes step 2b
# overlays. Same choices as the cell script, which imports them from here so there is one copy.
GFC_WINDOW = scoring.INDICATIVE_WINDOWS["GFC 2007-2009"][:2]
COMPARE_STRIKES = (0.85, 0.95)

# The interior knots of the layer-(ii) regression spline. Three, at the ladder's own interior
# strikes, so each cubic piece spans a 0.05 stretch of moneyness. Fixed by hand on purpose: the
# point that layer makes is that SOMEONE has to choose them, which is what layer (iii) removes.
REGRESSION_SPLINE_KNOTS = (0.85, 0.90, 0.95)


def mechanism_runs(window=None):
    """Steps 1 and 2 over the GFC: the four ladder strikes, blind and armed, budget-sized.

    Eight short runs (about 375 bars each), seconds in total. Run fresh rather than sliced from
    the full-history sweep because these start FLAT on the window's first bar, the cleanest way
    to watch one crisis from the first hedge onwards; the per-window panels below are slices of
    the standing programme instead, and the write-up says which is which.
    """
    window = GFC_WINDOW if window is None else window
    blind = {config_label(f, None): run_config(floor=f, monetise_drawdown=None, window=window,
                                               quiet=True)
             for f in MONEYNESS_LADDER}
    armed = {config_label(f, LADDER_MONETISE_DRAWDOWN): run_config(
                 floor=f, monetise_drawdown=LADDER_MONETISE_DRAWDOWN, window=window, quiet=True)
             for f in MONEYNESS_LADDER}
    return blind, armed


def reference_run(persist=True, out_dir=None):
    """THE 5%-A-YEAR PORTFOLIO-BUDGET RUN (decision D3) over the full history, persisted.

    One configuration, at the ladder's default strike and trigger, sized by
    ``PortfolioBudgetPutRule``. It is NOT a design point: it never enters the grid, the ranking
    or a fit, because its sizing is a different axis from the grid's. It is scored on the same
    windows and shown as an extra row beside the grid so the two sizing answers can be read
    side by side.
    """
    res = run_config(floor=REFERENCE_FLOOR, monetise_drawdown=LADDER_MONETISE_DRAWDOWN,
                     rule_cls=PortfolioBudgetPutRule, budget_pct=PORTFOLIO_BUDGET_PCT, quiet=True)
    if persist:
        out_dir = OUT_DIR if out_dir is None else Path(out_dir)
        ref_dir = out_dir / "reference"
        ref_dir.mkdir(parents=True, exist_ok=True)
        res["series"].to_parquet(ref_dir / "portfolio_budget_series.parquet")
        res["blotter"].to_csv(ref_dir / "portfolio_budget_blotter.csv", index=False)
        res["history"].to_csv(ref_dir / "portfolio_budget_rule_history.csv")
    return res


def reference_label():
    """The display label of the reference run, used as its grid row."""
    return (f"{PORTFOLIO_BUDGET_PCT:.0%}/yr budget, {REFERENCE_FLOOR:.2f}x/"
            f"dd{LADDER_MONETISE_DRAWDOWN:.0%}")


def load_reference(out_dir=None):
    """Reload the persisted reference run — no simulator."""
    out_dir = OUT_DIR if out_dir is None else Path(out_dir)
    ref_dir = out_dir / "reference"
    return {"series": pd.read_parquet(ref_dir / "portfolio_budget_series.parquet"),
            "blotter": pd.read_csv(ref_dir / "portfolio_budget_blotter.csv", parse_dates=["ts"]),
            "params": {"floor": REFERENCE_FLOOR, "monetise_drawdown": LADDER_MONETISE_DRAWDOWN}}


def build_up_rows(scores, reference_scores=None, trigger=LADDER_MONETISE_DRAWDOWN):
    """The rows the four build-up grids draw: every strike at ONE trigger depth, then SPY as its
    own row, then the reference run. Each grid then shows, column by column, whether every
    configuration improved on the underlying, and by how much compared with the 5% budget."""
    at = scores[np.isclose(scores["monetise_drawdown"].fillna(-1), trigger)].copy()
    at = at.sort_values("floor")
    parts = [at, scoring.spy_reference_rows(at)]
    if reference_scores is not None and len(reference_scores):
        parts.append(reference_scores)
    return pd.concat(parts, ignore_index=True)


def fit_strike_layers(scores, trigger=LADDER_MONETISE_DRAWDOWN):
    """The three 1-D fitting layers at a FIXED trigger, over every (strike, window) observation.

    Fitted to ALL the points (nine strikes x six windows), not to per-strike means: the score is
    relative to SPY on every window, so the observations share a scale and the spread above each
    strike is part of what the curve has to summarise. Returns ``(points, layers)`` in the shape
    ``gridfig.fig_strike_fit_layers`` takes.
    """
    pts = scores[np.isclose(scores["monetise_drawdown"].fillna(-1), trigger)]
    pts = pts.dropna(subset=["score"])[["floor", "window", "kind", "score"]].copy()
    x, y = pts["floor"].to_numpy(), pts["score"].to_numpy()
    p_fit = surface.fit_1d(x, y)
    layers = {
        "(i) global quadratic": {"fit": surface.fit_poly(x, y, degree=2), "knots": None,
                                 "colour": theme.CATEGORICAL[7], "dash": "dash"},
        "(ii) cubic regression spline": {
            "fit": surface.fit_regression_spline(x, y, REGRESSION_SPLINE_KNOTS),
            "knots": np.asarray(REGRESSION_SPLINE_KNOTS),
            "colour": theme.CATEGORICAL[5], "dash": "dashdot"},
        "(iii) penalised B-spline (P-spline, GCV)": {
            "fit": p_fit, "knots": surface.pspline_knots(x),
            "colour": theme.INK, "dash": "solid"},
    }
    return pts, layers


# Heading -> figure(s). The headings are the INTERFACE: renaming one in the Markdown without
# updating this map fails the build (build_report._split_sections), deliberately, because a
# figure published under the wrong heading is worse than no figure. Since 13-02.1 a value may be a
# LIST of stems, published in order after that section.
WINDOW_PANEL_STEMS = [f"panel_{i}" for i in range(len(scoring.INDICATIVE_WINDOWS))]
FIGURE_AT = {
    # Headings revised 2026-10-19 to formal action titles; each key is a phrase unique to one h2.
    "blind roll of deep": "step1_blind",
    "reverses the ordering of strikes": "step2_armed",
    "rescues deep strikes from time decay": "step2b_compare",
    "across multiple windows": WINDOW_PANEL_STEMS,
    "reduces maximum drawdown": "grid_mdd",
    "costs return in calm markets": "grid_ret",
    "single ratio": "grid_wod",
    "common scale": "grid_score",
    "matters most in crises": "grid_heatmap",
    "Observations at a fixed trigger": "fit_scatter",
    "global polynomial": "fit_poly",
    "regression spline": "fit_regspline",
    "penalised spline": "fit_pspline",
    "optimum differs between regimes": "window_surfaces",
    "Pooling across windows": "pooled_contour",
    "tradeable strike": "snap_slice",
    "three dimensions": "pooled_surface_3d",
    "share of portfolio value": "reference_gfc",
}


def build_publication_figures(scores, fits, optima, bounds, snap, trials, results, spot,
                              reference=None, reference_scores=None, mechanism=None,
                              default_trigger=LADDER_MONETISE_DRAWDOWN):
    """Every figure the published section carries, keyed by the stems FIGURE_AT names."""
    figs = {}
    blind, armed = mechanism if mechanism is not None else mechanism_runs()
    gfc_spot = spot.loc[GFC_WINDOW[0]:GFC_WINDOW[1]]
    budget = f"sized to the {BUDGET_MONEYNESS:.2f}x premium"

    # ---- THE MECHANISM: GFC steps 1, 2, 2b ----
    figs["step1_blind"] = gridfig.fig_moneyness_stack(
        blind, gfc_spot, underlying=UNDERLYING,
        title=f"Step 1 — blind roll, four strikes {budget}, GFC 2007-2009")
    figs["step2_armed"] = gridfig.fig_moneyness_stack(
        armed, gfc_spot, reentry_iv=LADDER_REENTRY_IV, underlying=UNDERLYING,
        title=(f"Step 2 — monetise at {default_trigger:.0%} drawdown, re-enter under "
               f"{LADDER_REENTRY_IV:.2f} IV, four strikes {budget}, GFC 2007-2009"))
    compare = {}
    for f in COMPARE_STRIKES:
        compare[config_label(f, None)] = blind[config_label(f, None)]
        compare[config_label(f, default_trigger)] = armed[config_label(f, default_trigger)]
    figs["step2b_compare"] = gridfig.fig_moneyness_stack(
        compare, gfc_spot, reentry_iv=LADDER_REENTRY_IV, underlying=UNDERLYING,
        title=(f"Step 2b — blind roll against monetisation, {COMPARE_STRIKES[0]:.2f}x and "
               f"{COMPARE_STRIKES[1]:.2f}x only, GFC 2007-2009"))

    # ---- THE MECHANISM, WINDOW BY WINDOW: slices of the standing full-history programme ----
    for stem, (window, (start, end, kind)) in zip(WINDOW_PANEL_STEMS,
                                                 scoring.INDICATIVE_WINDOWS.items()):
        sliced = {lab: slice_result(results[lab], start, end)
                  for lab in (config_label(f, default_trigger) for f in MONEYNESS_LADDER)}
        figs[stem] = gridfig.fig_moneyness_stack(
            sliced, spot.loc[start:end], reentry_iv=LADDER_REENTRY_IV, underlying=UNDERLYING,
            title=(f"{window} ({kind}) — monetise at {default_trigger:.0%} drawdown, four "
                   f"strikes {budget}"))

    # ---- THE SCORE, BUILT UP ONE INGREDIENT AT A TIME ----
    # STRIKE against WINDOW, at the default trigger depth, colour normalised per window. This is
    # the readable cross-section of the grid: the full 63-row config axis would be a heatmap
    # taller than the page, and the narrative at that heading is about how a strike reads
    # differently in each regime rather than about the trigger axis. (13-02's comment on its
    # single `window_grids` figure, which these four build-up grids replace.)
    rows = build_up_rows(scores, reference_scores, default_trigger)
    common = dict(trial_count=trials, normalise="column", note="SPY is its own row.")
    figs["grid_mdd"] = gridfig.fig_score_grid(
        rows, value_col="max_dd", higher_is_better=False, text_format="{:.1%}",
        title=f"Maximum drawdown by strike and window, at a {default_trigger:.0%} trigger",
        **common)
    figs["grid_ret"] = gridfig.fig_score_grid(
        rows, value_col="ret", text_format="{:+.1%}",
        title=f"Total return by strike and window, at a {default_trigger:.0%} trigger", **common)
    figs["grid_wod"] = gridfig.fig_score_grid(
        rows, value_col="wealth_over_dd", text_format="{:.2f}",
        title=(f"(1 + R) / MDD by strike and window, at a {default_trigger:.0%} trigger"),
        **common)
    figs["grid_score"] = gridfig.fig_score_grid(
        rows, value_col="score", text_format="{:.3f}", normalise=None, midpoint=1.0,
        trial_count=trials, colourbar_title="Score<br>(SPY = 1)",
        note="One shared scale centred on 1.0, the index; SPY is its own row.",
        title=(f"The optimisation metric — relative wealth over relative drawdown, at a "
               f"{default_trigger:.0%} trigger"))
    figs["grid_heatmap"] = gridfig.fig_grid_heatmap(scores, trial_count=trials)

    # ---- THE 1-D FIT LAYERS at the fixed trigger ----
    points, layers = fit_strike_layers(scores, default_trigger)
    names = list(layers)
    sub = f"Trigger fixed at {default_trigger:.0%}."
    figs["fit_scatter"] = gridfig.fig_strike_fit_layers(
        points, layers, active=[], trial_count=trials, subtitle=sub,
        title="Every observation at a fixed trigger: one point per strike per window")
    figs["fit_poly"] = gridfig.fig_strike_fit_layers(
        points, layers, active=[names[0]], trial_count=trials, subtitle=sub,
        title="Layer 1 — a global quadratic through the observations")
    figs["fit_regspline"] = gridfig.fig_strike_fit_layers(
        points, layers, active=[names[1]], trial_count=trials, subtitle=sub,
        title="Layer 2 — a cubic regression spline, knots fixed by hand")
    figs["fit_pspline"] = gridfig.fig_strike_fit_layers(
        points, layers, active=[names[2]], trial_count=trials, subtitle=sub,
        title="Layer 3 — the penalised B-spline, smoothing chosen by GCV")

    # ---- THE SURFACES ----
    per_window = {k: v for k, v in fits.items() if not k.startswith("POOLED")}
    pooled_key = "POOLED_DEMEANED"
    figs["window_surfaces"] = gridfig.fig_window_surfaces(per_window, optima, bounds,
                                                          trial_count=trials)
    figs["pooled_contour"] = gridfig.fig_pooled_contour(
        fits[pooled_key], demeaned_scores(scores), optima[pooled_key], bounds,
        trial_count=trials, colourbar_title="Fitted<br>deviation",
        title="Pooled fitted surface, window-demeaned",
        subtitle=("Height is each configuration's deviation from its own window's mean score, "
                  "not a score — pooling the raw scores fits a nearly flat plane because which "
                  "window a row came from dominates the variance."))
    figs["snap_slice"] = gridfig.fig_snap_slice(fits[pooled_key], optima[pooled_key], snap,
                                                bounds, trial_count=trials)
    figs["pooled_surface_3d"] = gridfig.fig_pooled_surface_3d(
        fits[pooled_key], optima[pooled_key], snap, bounds, trial_count=trials,
        title="Pooled window-demeaned surface in three dimensions")

    # ---- THE REFERENCE: 5%-a-year portfolio budget against the budget-matched 0.90x ----
    if reference is not None:
        lab = config_label(REFERENCE_FLOOR, default_trigger)
        figs["reference_gfc"] = gridfig.fig_moneyness_stack(
            {f"{lab} ({budget})": slice_result(results[lab], *GFC_WINDOW),
             reference_label(): slice_result(reference, *GFC_WINDOW)},
            gfc_spot, reentry_iv=LADDER_REENTRY_IV, underlying=UNDERLYING,
            title="Two ways to size the same put: an equal premium budget against 5% of the "
                  "portfolio a year, GFC 2007-2009")
    return figs


def export_optimisation_analysis(out_dir=DOCS_DIR, figures_dir=None):
    """Publish the section from the PERSISTED sweep — no grid re-simulation.

    Reads back what ``main()`` wrote, refits the surfaces (seconds, pure arithmetic) and renders
    the page. Deliberately decoupled from the sweep itself: republishing after an edit to the
    prose must not cost minutes of simulation, and must not risk the published numbers silently
    changing because something upstream moved. The only simulation here is the eight short GFC
    mechanism runs, which are seconds.
    """
    figures_dir = DOCS_FIGURES_DIR if figures_dir is None else figures_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    results, scores = load_sweep()
    spot, _iv = load_full()
    trials = int(scores["config"].nunique())
    fits, optima, bounds, one_d = fit_surfaces(scores)
    pooled_opt = optima["POOLED_DEMEANED"]
    snap = surface.snap_to_strike(pooled_opt.point[0], float(spot.iloc[-1]), increment=1.0,
                                  predict=fits["POOLED_DEMEANED"].predict,
                                  other=pooled_opt.point[1])
    reference = load_reference()
    ref_scores = pd.read_csv(OUT_DIR / "reference" / "reference_scores.csv")

    figs = build_publication_figures(scores, fits, optima, bounds, snap, trials, results, spot,
                                     reference=reference, reference_scores=ref_scores)

    # One standalone page per figure, so a chart can be opened by double-clicking a file with no
    # kernel and no report around it. The SAME figure objects are then handed to build_report, so
    # nothing is rendered twice and the two cannot disagree.
    for name, fig in figs.items():
        theme.apply_export_theme(fig)
        theme.apply_export_spacing(fig)
        path = figures_dir / f"{name}.html"
        fig.write_html(path, include_plotlyjs=PLOTLY_SRC_FIGURE)
        # Plotly's <body> carries no background of its own, so the margin around a dark figure is
        # browser white until this injects the shared page rule.
        path.write_text(theme.darken_page(path.read_text(encoding="utf-8")), encoding="utf-8")

    # THE 3-D SURFACE AS A STATIC PNG TOO (decision D4). Plotly's Surface renders only where the
    # viewer's browser has WebGL, and a browser with graphics acceleration switched off shows
    # "WebGL is not supported" and nothing else — measured on this machine's Chrome, where all
    # three WebGL contexts are unavailable. kaleido renders the SAME figure object headless, so
    # the image cannot drift from the interactive version; the write-up shows it only when the
    # visitor's browser has no WebGL. A failed render is reported, never fatal: the page is
    # still correct for every visitor who has WebGL.
    try:
        figs["pooled_surface_3d"].write_image(figures_dir / "pooled_surface_3d.png",
                                              width=1400, height=820)
    except Exception as exc:  # kaleido missing or its headless browser failing to start
        print(f"WARNING: static 3-D fallback not written ({exc!r})")

    build_report(
        figures=figs,
        write_up=WRITE_UP,
        out_path=out_dir / "index.html",
        figure_at=FIGURE_AT,
        plotly_src=PLOTLY_SRC_INDEX,
        title="Optimising put option moneyness for drawdown-adjusted returns — Portfolio Manager",
        heading="Optimising put option moneyness for drawdown-adjusted returns",
        header_note=("Figures are fully interactive — hover, zoom and legend toggles all work, "
                     "with no server. Generated from {source_link} by "
                     "<code>python -m pipelines.option_grid_sweep</code> on {built}."),
        footer_note=("Portfolio Manager — thematic-fundamental research engine. "
                     "Priced with measured implied volatility, not a synthetic surface. "
                     f"IN-SAMPLE across {trials} trials on the windows the parameters were read "
                     "on — see the section on out-of-sample validation above."),
    )
    print(f"\nwritten to {out_dir}:")
    for name in figs:
        print(f"  figures/{name}.html")
    print("  index.html")
    return out_dir / "index.html"


def main(persist=True):
    """Task 2b and Task 4 in one command:  python -m pipelines.option_grid_sweep"""
    print("=" * 78)
    print("13-02 — THE OPTION MONEYNESS x MONETISATION GRID.  IN-SAMPLE. NOT a selection.")
    print("=" * 78)

    # ---- AC-1: the design, the trial count and the projection, BEFORE anything runs ----
    combos = grid_combinations()
    trials = len(combos)
    projected_s = trials * SINGLE_RUN_SECONDS
    print(f"moneyness axis        : {MONEYNESS_GRID}")
    print(f"monetise-drawdown axis: {MONETISE_DRAWDOWN_GRID}  (None = the blind-roll reference)")
    print(f"tenor                 : FIXED at {RESET_BARS} bars — not an axis in this plan")
    print(f"TRIAL COUNT           : {trials} combinations")
    print(f"PROJECTED RUNTIME     : {projected_s / 60:.1f} min "
          f"({trials} x {SINGLE_RUN_SECONDS:.2f}s, the constant recorded in 13-01.1's SUMMARY)")
    if projected_s / 60 > RUNTIME_BUDGET_MINUTES:
        raise RuntimeError(
            f"projected {projected_s / 60:.1f} min exceeds the {RUNTIME_BUDGET_MINUTES:.0f} min "
            f"budget — shrink an axis rather than starting a run nobody sized")

    spot, _iv = load_full()
    results, elapsed = sweep_grid(combos, persist=persist)
    print(f"\nACTUAL RUNTIME        : {elapsed / 60:.1f} min "
          f"({elapsed / trials:.2f}s per combination, projected {SINGLE_RUN_SECONDS:.2f}s)")
    print(f"ledger width constant across all {trials} runs — no state leaked between them")

    # ---- Scoring, by SLICING. The simulator is never re-run per window. ----
    scores = tidy_scores(results, spot)
    ranked = scoring.rank_configs(scores)
    pivot = scores.pivot_table(index="monetise_drawdown", columns="floor", values="score",
                               aggfunc="mean")

    floor_share = float(scores["floor_bound"].mean())
    calm = scores[scores["kind"] == scoring.CALM]
    print(f"\nfloor-bound share     : {floor_share:.1%} overall, "
          f"{float(calm['floor_bound'].mean()):.1%} in the calm windows "
          f"(floor = {scoring.DRAWDOWN_FLOOR:.0%})")
    print("\nRANKED BY WORST-OF-TWO — IN-SAMPLE, not a selection (top 12):")
    print(ranked.round(3).head(12).to_string())

    # ---- 13-02.1: the regime trade-off and the within-window dispersion, printed every run ----
    # The two headline numbers of the write-up, recomputed rather than carried over from 13-02:
    # under a different metric and a different sizing they are new measurements, not updates.
    both = ranked.dropna(subset=["rank_drawdown", "rank_calm"])
    rho = float(both["rank_drawdown"].corr(both["rank_calm"], method="spearman"))
    top_both = set(both.nsmallest(10, "rank_drawdown").index) & set(
        both.nsmallest(10, "rank_calm").index)
    print(f"\nrank correlation, crisis set vs calm set (Spearman): {rho:+.3f}; "
          f"configurations in the top ten of both: {len(top_both)} {sorted(top_both)}")
    armed_only = scores[~scores["blind_roll"]]
    spread = armed_only.groupby("window", sort=False)["score"].agg(lambda s: s.max() - s.min())
    print("within-window spread of the score across the armed configurations:")
    print(spread.round(4).to_string())

    # ---- 13-02.1: the 5%-a-year portfolio-budget reference run (decision D3) ----
    # Scored on the same windows, persisted beside the grid, and NEVER part of the ranking or a
    # fit — its sizing is a different axis from the grid's.
    reference = reference_run(persist=persist)
    ref_scores = scoring.score_grid({reference_label(): reference["series"]}, spot,
                                    blotter_by_config={reference_label(): reference["blotter"]},
                                    underlying=UNDERLYING)
    print("\nREFERENCE — 5%/yr portfolio budget, 0.90x / dd10%:")
    print(ref_scores[["window", "score", "ret", "max_dd", "spy_ret", "spy_max_dd"]]
          .round(3).to_string(index=False))
    if persist:
        (OUT_DIR / "reference").mkdir(parents=True, exist_ok=True)
        ref_scores.to_csv(OUT_DIR / "reference" / "reference_scores.csv", index=False)

    # ---- AC-5: the surfaces ----
    fits, optima, bounds, one_d = fit_surfaces(scores)
    print("\n1-D FITS FIRST (the other axis held at its default):")
    for name, fit in one_d.items():
        print(f"  {name:20s} lam {fit.lam:>12.4g}  edf {fit.edf:5.2f}  "
              f"GCV {fit.gcv:.4g}  in-sample R2 {fit.r2:.3f}")
    print("\n2-D TENSOR SMOOTHS, per window and pooled:")
    for name, fit in fits.items():
        opt = optima[name]
        print(f"  {name:18s} lam ({fit.lam[0]:.3g}, {fit.lam[1]:.3g})  edf {fit.edf:5.2f}  "
              f"R2 {fit.r2:5.3f}  optimum floor {opt.point[0]:.3f} / dd {opt.point[1]:.3f}  "
              f"curvature {opt.curvature:9.1f}")

    # THE FITTED-VERSUS-RAW CHECK. See fit_vs_raw: these smooths sit close to interpolation
    # because the simulator is deterministic, so a fitted optimum's POSITION is trustworthy only
    # where it agrees with the raw argmax, and its HEIGHT is never an achievable score.
    check = fit_vs_raw(scores, fits, optima)
    print("\nFITTED OPTIMUM vs RAW GRID ARGMAX — position trustworthy, height not:")
    print(check.round(3).to_string(index=False))
    overshot = check[check["value_overshoot"] > 0.05 * check["window_spread"].clip(lower=1e-9)]
    if len(overshot):
        print(f"  WARNING: {len(overshot)} window(s) where the fit claims a score no simulated "
              f"cell reached: {', '.join(overshot['window'])}")

    # The snapping cost, against a real SPY level. A continuous optimum nobody can trade is not
    # a result, so the gap between it and the nearest listed strike is reported beside it.
    #
    # Taken off the WINDOW-DEMEANED pooled fit, not the raw one: see fit_surfaces for the
    # measurement, but in short the raw pooled surface is a near-flat plane whose argmax is the
    # corner of the axis, so snapping it would report a cost of zero and mean nothing by it.
    spot_now = float(spot.iloc[-1])
    pooled_key = "POOLED_DEMEANED"
    pooled_opt = optima[pooled_key]
    snap = surface.snap_to_strike(pooled_opt.point[0], spot_now, increment=1.0,
                                  predict=fits[pooled_key].predict, other=pooled_opt.point[1])
    print(f"\nSNAPPING (spot {spot_now:.2f}): continuous {pooled_opt.point[0]:.4f} -> "
          f"strike {snap.strike:.0f} ({snap.moneyness:.4f}), cost {snap.cost:.4f} "
          f"in score units")

    if persist:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        scores.to_csv(OUT_DIR / "scores_tidy.csv", index=False)
        pivot.to_csv(OUT_DIR / "scores_pivot.csv")
        ranked.to_csv(OUT_DIR / "ranked.csv")
        # The trial count and BOTH wall-clock numbers, so the next plan sizing a THIRD axis has
        # a real constant to size from rather than this plan's projection.
        pd.DataFrame([{
            "trial_count": trials,
            "projected_minutes": round(projected_s / 60, 3),
            "actual_minutes": round(elapsed / 60, 3),
            "seconds_per_combination": round(elapsed / trials, 3),
            "single_run_constant_used": SINGLE_RUN_SECONDS,
            "floor_bound_share": round(floor_share, 4),
            "measured_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        }]).to_csv(OUT_DIR / "trial_count_and_runtime.csv", index=False)
        # The fitted-surface read-outs as a TABLE, because a figure cannot be grepped and AC-5
        # requires the smoothing parameter, the edf and the curvature for every fit.
        pd.DataFrame([{
            "fit": name, "lam_x": fits[name].lam[0], "lam_y": fits[name].lam[1],
            "edf": fits[name].edf, "gcv": fits[name].gcv, "r2_in_sample": fits[name].r2,
            "opt_floor": optima[name].point[0], "opt_monetise_drawdown": optima[name].point[1],
            "opt_value": optima[name].value, "curvature": optima[name].curvature,
        } for name in fits]).to_csv(OUT_DIR / "surface_fits.csv", index=False)
        check.to_csv(OUT_DIR / "fit_vs_raw.csv", index=False)

        # ---- AC-6: the four figures ----
        per_window = {k: v for k, v in fits.items() if not k.startswith("POOLED")}
        # The two pooled figures are drawn from the DEMEANED fit, and say so in their titles.
        # The raw pooled fit stays in surface_fits.csv — it is a reportable null result, not a
        # picture worth four hundred kilobytes of a flat plane.
        demeaned_note = ("height is the deviation from each window's own mean score, NOT a "
                         "score — pooling the raw scores fits a near-flat plane because window "
                         "identity dominates the variance (13-02 measured R2 0.009 under "
                         "floored Calmar)")
        figs = {
            "grid_heatmap": gridfig.fig_grid_heatmap(scores, trial_count=trials),
            "window_surfaces": gridfig.fig_window_surfaces(
                per_window, optima, bounds, trial_count=trials),
            "pooled_contour": gridfig.fig_pooled_contour(
                fits[pooled_key], demeaned_scores(scores), pooled_opt, bounds,
                trial_count=trials,
                title="Pooled fitted surface, window-demeaned", subtitle=demeaned_note),
            "pooled_surface_3d": gridfig.fig_pooled_surface_3d(
                fits[pooled_key], pooled_opt, snap, bounds, trial_count=trials,
                title="Pooled window-demeaned surface — optimum and nearest tradeable strike"),
        }
        for name, fig in figs.items():
            # apply_export_theme is NOT optional: a saved page has no dark div behind it, so a
            # transparent background lets the browser paint its own white and a light-ink figure
            # becomes unreadable. The same call every export path in this repo makes.
            theme.apply_export_theme(fig)
            theme.apply_export_spacing(fig)
            path = OUT_DIR / f"{name}.html"
            # CDN here rather than the vendored docs/assets/plotly.min.js: outputs/ is gitignored
            # scratch and is not served, so a relative path up into docs/ would break the moment
            # anyone moved the file. Nothing under docs/ is written by this plan.
            fig.write_html(path, include_plotlyjs="cdn")
            path.write_text(theme.darken_page(path.read_text(encoding="utf-8")), encoding="utf-8")

        print(f"\nwritten: {OUT_DIR}")
        print("  scores_tidy.csv, scores_pivot.csv, ranked.csv, surface_fits.csv, "
              "trial_count_and_runtime.csv")
        print(f"  {len(figs)} figures; series/, blotter/ and rule_history/ ({trials} each)")

    print("\nREMINDER: every number above is IN-SAMPLE — every threshold was read on the same "
          "windows it was scored on.")
    print("No parameter set here is validated, selected or recommended. That is 13-03's CPCV.")
    return scores, ranked, fits, optima


if __name__ == "__main__":
    import sys
    # `--publish-only` rebuilds docs/optimisation/ from the persisted sweep without re-running the
    # 63 combinations — the path to take after editing the write-up's prose. With no flag the
    # sweep runs and the page is then published from what it just wrote.
    if "--publish-only" not in sys.argv:
        main()
    export_optimisation_analysis()
