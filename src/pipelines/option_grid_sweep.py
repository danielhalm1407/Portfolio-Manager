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
from portutils.strategies.rules.options import ProtectivePutRule
from portutils.viz import theme

# The figure builders. Imported at module scope and not deferred: there is no cycle —
# option_grid_figures imports option_probe_figures and this module, never the other way round.
import pipelines.option_grid_figures as gridfig

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
OUT_DIR = PROJECT_ROOT / "outputs" / "option_grid"
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
          f"in floored-Calmar units")

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
                         "Calmar — pooling the raw scores fits a flat plane (R2 0.009) because "
                         "window identity dominates the variance")
        figs = {
            "grid_heatmap": gridfig.fig_grid_heatmap(scores, trial_count=trials),
            "window_surfaces": gridfig.fig_window_surfaces(
                per_window, optima, bounds, trial_count=trials),
            "pooled_contour": gridfig.fig_pooled_contour(
                fits[pooled_key], demeaned_scores(scores), pooled_opt, bounds,
                trial_count=trials,
                title=f"Pooled fitted surface, window-demeaned — {demeaned_note}"),
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
    main()
