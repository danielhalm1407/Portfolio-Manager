# %% 1. Import libraries

# 1. Import libraries
# Imports

# ============================================================================
# OPTIMISING PUT OPTION MONEYNESS FOR DRAWDOWN-ADJUSTED RETURNS (13-02).
# Run this file CELL BY CELL and look at each figure before moving to the next.
#
# THE QUESTION
# ------------
# How far out of the money should a rolled protective put be struck? Too deep
# and the protection is almost pure vega that never pays; too near and the
# premium drag eats the portfolio in the twenty quiet years between crises.
# The answer is a trade-off, so it has to be read on BOTH kinds of regime.
#
# THE SHAPE OF THE SCRIPT
# -----------------------
#   STEP 1  GFC, four strikes, BLIND ROLL (no monetisation). Stacked panels.
#           This is where the DECAY is visible: panel 2 shows each strike's
#           value as a multiple of the premium paid, rising into the fall and
#           then decaying back towards zero because a blind roll holds to
#           expiry whatever the leg is worth. That is finding 7 drawn.
#   STEP 2  GFC, the same four strikes, monetise_drawdown ARMED. Same panels,
#           now with MONETISE markers and the FLAT bands that follow them.
#   STEP 2b The only blind-vs-armed overlay in the file, and only for TWO
#           strikes. Four blind plus four armed on one axis is eight lines and
#           no argument; two strikes answers "what did arming it change" and
#           stops.
#   STEP 3  Nine strikes, 0.80 to 1.00, ARMED ONLY, over the full history —
#           scored on all six indicative windows by SLICING.
#   GRIDS   The raw floored Calmar, then the same thing relative to SPY's own.
#           Colour normalised WITHIN each window; SPY carried as its own row.
#   PANELS  One stacked figure per window, armed configurations only, with
#           every roll and monetisation marked.
#
# WHY THE BLIND ROLL APPEARS ONLY AT THE GFC. It is a CONTROL, not a candidate:
# nobody is choosing between "monetise" and "never monetise" by this point —
# 13-01 settled that the blind roll gives its gains back. Carrying it through
# every window and into the grid would double the runs and the rows to restate
# a conclusion already reached, and it would halve the resolution of the
# moneyness axis the grid is actually for.
#
# EVERY NUMBER AND EVERY FIGURE BELOW IS IN-SAMPLE. Each threshold is read on
# the same windows it is scored on — the deliberate first stage, not a defect.
# 13-03's CPCV is where it gets tested. Nothing here is a validated, selected
# or recommended parameter set.
#
# NO BID/ASK MODEL. Every roll, monetisation and reopen fills at the model
# mark, which flatters every configuration and MOST of all the ones that trade
# most. So if the busiest line wins, that is a hypothesis, not a result.
# ============================================================================

import importlib

import pandas as pd

from portutils.strategies import scoring
from portutils.strategies.scoring import (CALM, DRAWDOWN, INDICATIVE_WINDOWS, rank_configs,
                                          score_grid, spy_reference_rows)

# The runner and the axis definitions. IMPORTED, never re-derived here: Task 2a's requirement is
# that the full grid is the SAME code path called more times, and a ladder that ran through its
# own private copy of the simulator setup would prove nothing about the grid that follows it.
import pipelines.option_grid_sweep as sweep
import pipelines.option_grid_figures as gridfig
from pipelines.option_grid_sweep import (LADDER_MONETISE_DRAWDOWN, LADDER_REENTRY_IV,
                                         MONEYNESS_GRID, MONEYNESS_LADDER, config_label,
                                         load_full, run_config, slice_result)
from pipelines.option_probe_figures import UNDERLYING

pd.set_option("display.width", 220, "display.max_columns", 60)

# %% Reload custom package

# Reload custom package

# Reload modules during development (re-import names after reload). The scoring metric and the
# figure builders are what actually get iterated on here; the rules and the ledger underneath
# them are stable and 13-02 may not touch them (see the plan's DO NOT CHANGE list).
importlib.reload(scoring)
importlib.reload(sweep)
importlib.reload(gridfig)
from portutils.strategies.scoring import (CALM, DRAWDOWN, INDICATIVE_WINDOWS,  # noqa: E402
                                          rank_configs, score_grid, spy_reference_rows)
from pipelines.option_grid_sweep import config_label, run_config, slice_result  # noqa: E402

# %% 2. Load the window

# 2. Load the window

# SPY closes and REAL implied vol on their intersection: 5,201 bars, 2006-01-09 to 2026-09-18.
# The pre-2006 price history is dropped LOUDLY rather than silently — v2 cannot price it, which
# is a Phase 11 data limit and not a choice. Loaded once; every step below slices this.
SPOT_FULL, IV_FULL = load_full()

# The GFC window, used for steps 1, 2 and 2b. Chosen because it is the one window in which every
# mechanism fires — a deep drawdown to trigger monetisation, a vol spike to hold the re-entry
# gate, and enough bars for several 63-bar rolls — and because it is the first of the indicative
# drawdown windows, so step 3 can be read directly against it.
GFC = INDICATIVE_WINDOWS["GFC 2007-2009"][:2]

# The two strikes step 2b overlays. A deep one and a near one: if arming the trigger helps the
# two ends of the ladder in the same direction and by a similar amount, the monetisation
# decision is roughly separable from the moneyness decision, which is what the two-axis grid
# quietly assumes. If it does not, that assumption is the finding.
COMPARE_STRIKES = (0.85, 0.95)

print(f"\nladder strikes (figures) : {MONEYNESS_LADDER}")
print(f"grid strikes   (heatmaps): {MONEYNESS_GRID}")
print(f"monetise trigger         : drawdown {LADDER_MONETISE_DRAWDOWN:.0%}, "
      f"re-entry IV {LADDER_REENTRY_IV:.2f}")
print(f"GFC window               : {GFC[0]} -> {GFC[1]}")
print(f"indicative windows       : {len(INDICATIVE_WINDOWS)} "
      f"({sum(1 for v in INDICATIVE_WINDOWS.values() if v[2] == DRAWDOWN)} drawdown, "
      f"{sum(1 for v in INDICATIVE_WINDOWS.values() if v[2] == CALM)} calm)")

# %% 3. STEP 1 — the blind roll over the GFC, and the decay it never escapes

# 3. STEP 1 — blind roll, GFC

# ============================================================================
# STEP 1. Hold every leg to expiry, re-strike every 63 bars, never monetise.
# Four strikes, one window.
#
# PANEL 2 IS THE CELL. Each line is that strike's long leg valued as a MULTIPLE
# of the premium paid for it, resetting at every roll. A blind roll's curve
# climbs as SPY falls and then decays back toward zero as expiry approaches,
# because nothing in the rule ever takes the money — the hedge was worth
# several times its cost at some point in the quarter and settled at whatever
# it happened to be worth on the roll bar. The DEEPER the strike, the more
# violent that arc: a 0.80x put is almost entirely vega, so it spikes hardest
# and decays hardest.
#
# PANEL 1 asks the other half: did any of that reach the portfolio? Against a
# -48% index the hedged lines should sit above SPY, and the ORDER of the four
# is the first real reading of the moneyness trade-off.
# ============================================================================

step1 = {}
for floor in MONEYNESS_LADDER:
    step1[config_label(floor, None)] = run_config(floor=floor, monetise_drawdown=None,
                                                  window=GFC)

step1_scores = score_grid({k: v["series"] for k, v in step1.items()}, SPOT_FULL,
                          windows={"GFC 2007-2009": (*GFC, DRAWDOWN)},
                          blotter_by_config={k: v["blotter"] for k, v in step1.items()},
                          underlying=UNDERLYING)
print("\nSTEP 1 — blind roll, GFC window. IN-SAMPLE.")
print(step1_scores[["config", "score", "floor_bound", "ret", "max_dd", "spy_ret", "spy_max_dd",
                    "dd_vs_spy", "premium_pct_spot"]].round(4).to_string(index=False))

gridfig.fig_moneyness_stack(
    step1, SPOT_FULL.loc[GFC[0]:GFC[1]],
    title="STEP 1 — blind roll (no monetisation), four strikes, GFC 2007-2009",
    underlying=UNDERLYING).show()

# %% 4. STEP 2 — the same ladder with the monetisation trigger armed

# 4. STEP 2 — armed, GFC

# ============================================================================
# STEP 2. One thing changes: `monetise_drawdown` is armed at 10%, with the
# re-entry gate and max_flat_bars that necessarily come with it (a trigger with
# no re-entry policy would monetise once and sit unhedged forever — and in
# 2008-09 that is an unhedged book straight through the drawdown).
#
# WHAT TO LOOK FOR, in the panels rather than the table:
#   panel 2  a MONETISE triangle sitting ON the multiple curve at the moment
#            the policy took the money, and the curve BREAKING afterwards —
#            the gap is the unhedged wait, not missing data
#   panel 3  the FLAT band for that strike filling the same gap, under a real
#            IV path far above the 0.18 re-entry level for most of 2008-09
#   panel 1  whether the equity step at each monetisation is visible at all
#
# The 10% depth is ILLUSTRATIVE and hand-picked — the same value 13-01's batch
# used, chosen so this figure is readable against 13-01's own full-history run
# rather than against nothing. It is NOT a chosen parameter.
# ============================================================================

step2 = {}
for floor in MONEYNESS_LADDER:
    step2[config_label(floor, LADDER_MONETISE_DRAWDOWN)] = run_config(
        floor=floor, monetise_drawdown=LADDER_MONETISE_DRAWDOWN, window=GFC)

step2_scores = score_grid({k: v["series"] for k, v in step2.items()}, SPOT_FULL,
                          windows={"GFC 2007-2009": (*GFC, DRAWDOWN)},
                          blotter_by_config={k: v["blotter"] for k, v in step2.items()},
                          underlying=UNDERLYING)

gridfig.fig_moneyness_stack(
    step2, SPOT_FULL.loc[GFC[0]:GFC[1]],
    title=(f"STEP 2 — monetise at {LADDER_MONETISE_DRAWDOWN:.0%} drawdown, re-enter under "
           f"{LADDER_REENTRY_IV:.2f} IV, four strikes, GFC 2007-2009"),
    reentry_iv=LADDER_REENTRY_IV, underlying=UNDERLYING).show()

# %% 5. STEP 2b — blind against armed, TWO strikes only

# 5. STEP 2b — the one blind-vs-armed overlay

# ============================================================================
# THE ONLY PLACE THE TWO POLICIES SHARE AN AXIS, and only for two strikes.
# Eight lines on one panel is not a comparison, it is a thicket — so this takes
# a DEEP strike and a NEAR one and asks whether arming the trigger moved them
# in the same direction by a similar amount.
#
# That question is not cosmetic. The two-axis grid that follows treats
# moneyness and monetisation depth as separable axes of one surface. If arming
# the trigger helps 0.85x and hurts 0.95x, they are not separable, the surface
# has an interaction term, and a 1-D read along either axis would be
# misleading — which is precisely the kind of thing a plan discovers too late
# if it never draws it.
# ============================================================================

compare = {}
for floor in COMPARE_STRIKES:
    compare[config_label(floor, None)] = step1[config_label(floor, None)]
    compare[config_label(floor, LADDER_MONETISE_DRAWDOWN)] = \
        step2[config_label(floor, LADDER_MONETISE_DRAWDOWN)]

gridfig.fig_moneyness_stack(
    compare, SPOT_FULL.loc[GFC[0]:GFC[1]],
    title=(f"STEP 2b — blind roll against monetisation, {COMPARE_STRIKES[0]:.2f}x and "
           f"{COMPARE_STRIKES[1]:.2f}x only, GFC 2007-2009"),
    reentry_iv=LADDER_REENTRY_IV, underlying=UNDERLYING).show()

# The arithmetic behind it. `n_monetisations` is included because a trigger that never fired
# cannot have changed anything, and a configuration whose score moved without any monetisation
# is a bug worth chasing rather than a finding.
both_scores = pd.concat([step1_scores, step2_scores], ignore_index=True)
cmp_rows = []
for label, res in {**step1, **step2}.items():
    row = both_scores[both_scores["config"] == label].iloc[0]
    cmp_rows.append({
        "config": label,
        "floor": res["params"]["floor"],
        "monetise_drawdown": res["params"]["monetise_drawdown"],
        "score": row["score"], "ret": row["ret"], "max_dd": row["max_dd"],
        "premium_pct_spot": row["premium_pct_spot"],
        "n_monetisations": len([e for e in res["rule"].events if e["action"] == "monetise"]),
        "n_events": len(res["rule"].events),
    })
print("\nSTEP 1 vs STEP 2 — GFC window. IN-SAMPLE, illustrative trigger depth, no bid/ask.")
print(pd.DataFrame(cmp_rows).round(4).to_string(index=False))

# %% 6. STEP 3 — nine strikes over the FULL history, armed only

# 6. STEP 3 — the full history

# ============================================================================
# Nine strikes from 0.80 to 1.00, monetisation ARMED on every one, each
# simulated ONCE over the whole 2006-2026 window. All six indicative windows
# are then scored by SLICING those stored series.
#
# WHY ONCE AND THEN SLICED, rather than once per (config, window). Six windows
# x nine configurations is 54 simulator runs; nine full-history runs is nine.
# More importantly it is the property AC-2 exists to protect and the reason the
# per-bar series are persisted at all: it collapses 13-02's and 13-03's cost
# from (grid x origins) runs to (grid), which is what makes daily-stepped
# origins and CPCV affordable later. Scoring is slicing.
#
# THE CONSEQUENCE, STATED RATHER THAN HIDDEN. A sliced window scores the overlay
# as if it had ALREADY BEEN RUNNING when the window opened — carrying whatever
# premium it had paid and whatever position it held — not as if it started flat
# on the window's first bar. That is the right measurement for a standing hedge
# programme and it is NOT the same thing steps 1-2 measured, where each run
# began on the window's own first bar. Expect the GFC numbers here to differ
# from step 1's for exactly that reason; a difference is not a bug.
#
# Each run is seconds over 5,201 bars (13-01.1's narrow ledger). If any single
# run takes MINUTES, `ledger=NarrowLedger()` has come unwired and nothing below
# should be trusted — see run_config.
# ============================================================================

step3 = {}
for floor in MONEYNESS_GRID:
    step3[config_label(floor, LADDER_MONETISE_DRAWDOWN)] = run_config(
        floor=floor, monetise_drawdown=LADDER_MONETISE_DRAWDOWN, window=None)

# The state-leak assertion Task 2a requires. `PortfolioSimulator.run()` resets rules but NOT the
# book, so a reused book would carry dead legs forward and reinstate the quadratic cost 13-01.1
# removed. A constant ledger width across all nine runs is the evidence it did not.
widths = {label: len(res["state"].columns) for label, res in step3.items()}
assert len(set(widths.values())) == 1, f"ledger width drifted across configurations: {widths}"
print(f"\nledger width constant at {next(iter(widths.values()))} columns across "
      f"{len(step3)} configurations — no state leaked between runs")
print(f"total wall-clock: {sum(r['elapsed'] for r in step3.values()):.1f}s for "
      f"{len(step3)} full-history runs")

step3_scores = score_grid({k: v["series"] for k, v in step3.items()}, SPOT_FULL,
                          blotter_by_config={k: v["blotter"] for k, v in step3.items()},
                          underlying=UNDERLYING)
# SPY as its own ROW rather than only as a column on every row. "Which of these actually beat
# just holding the thing" is the first question anyone asks of a hedge grid, and as a column it
# is invisible in a heatmap.
grid_scores = pd.concat([step3_scores, spy_reference_rows(step3_scores)], ignore_index=True)

# %% 7. GRID A — floored Calmar, coloured within each window

# 7. GRID A — the raw score

# ============================================================================
# COLOUR IS NORMALISED WITHIN EACH COLUMN. A single scale across the whole grid
# would compare a GFC cell with a calm-2017 cell: every configuration scores
# around -0.8 through the GFC and around +5 through 2017, so a shared scale
# paints the crisis column one shade and the calm column another and shows
# only which REGIME was benign — which nobody needs a grid to learn. Per
# column, colour answers the question the grid is actually for: which strike
# did better INSIDE this window. The printed number still carries the
# magnitude, and the hover still reports the raw value.
# ============================================================================

# SINCE 13-02.1 `score` is the RELATIVE score (relative wealth over relative drawdown, SPY = 1.0)
# and every run is sized to the 0.95x put's premium (run_config's default rule). 13-02's floored
# Calmar is still on every row as `calmar_floored`.
gridfig.fig_score_grid(
    grid_scores, value_col="score",
    title=(f"Relative score by moneyness and window — monetise at "
           f"{LADDER_MONETISE_DRAWDOWN:.0%} drawdown"),
    trial_count=len(step3), normalise="column").show()

floor_share = step3_scores["floor_bound"].mean()
calm_floor_share = step3_scores.loc[step3_scores["kind"] == CALM, "floor_bound"].mean()
print(f"\nfloor-bound share: {floor_share:.1%} overall, {calm_floor_share:.1%} in the calm "
      f"windows (floor = {scoring.DRAWDOWN_FLOOR:.0%})")

print("\nSTEP 3 — full tidy frame. IN-SAMPLE, no bid/ask.")
print(step3_scores[["config", "window", "kind", "score", "floor_bound", "ret", "max_dd",
                    "spy_score", "score_vs_spy", "dd_vs_spy", "premium_pct_spot"]]
      .round(3).to_string(index=False))

# %% 8. GRID B — the same score as a proportion of SPY's own

# 8. GRID B — relative to the index

# ============================================================================
# The same target variable divided by SPY's floored Calmar on the SAME window.
# This is the grid that answers "did the hedge earn its place", because 1.0 is
# the index and the number is comparable across windows in a way the raw score
# is not.
#
# ONE SHARED COLOUR SCALE HERE, centred on 1.0 — deliberately NOT per-column.
# The whole point of dividing by the benchmark is that a cell means the same
# thing in every column, so normalising per column would throw away exactly
# the comparability the division bought.
#
# READ THE SIGN BEFORE THE NUMBER. On every DRAWDOWN window SPY's own Calmar is
# NEGATIVE, and dividing one negative by another makes a ratio ABOVE 1.0 mean
# the configuration did WORSE than the index, not better. The ratio is only
# "higher is better" on a window where the index itself made money — the three
# calm ones. This is a property of ratios of signed quantities, not a bug, and
# it is the same root cause as the ordering caveat printed in cell 9.
# ============================================================================

# SINCE 13-02.1 this is the same number as Grid A (the score is already relative to SPY, so
# score_vs_spy == score) and the sign caveat above no longer applies to it. Drawn on the old
# floored Calmar instead, so the comparison 13-02 made is still available here.
grid_scores["calmar_vs_spy"] = grid_scores["calmar_floored"] / grid_scores["spy_calmar_floored"]
gridfig.fig_score_grid(
    grid_scores, value_col="calmar_vs_spy",
    title="Floored Calmar as a proportion of SPY's own — 1.0 is the index",
    trial_count=len(step3), normalise=None, midpoint=1.0,
    colourbar_title="score /<br>SPY score").show()

# %% 9. The ranked table, and the caveat that has to be read before it

# 9. Ranking

# ============================================================================
# Ranks are computed WITHIN the drawdown set and WITHIN the calm set, and the
# table is sorted by the WORSE of the two — never by their average. An average
# lets a configuration that tops the crisis set and bottoms the calm set come
# out near the middle and look like a compromise. It is not a compromise; it is
# a hedge that only pays if the crisis arrives on schedule.
#
# This is a RANKING, not a selection. No row here is chosen, validated or
# recommended — 13-03's CPCV is the only thing that can speak to that.
# ============================================================================

ranked = rank_configs(step3_scores)
print("\nRanked by worst-of-two. IN-SAMPLE. Not a selection.")
print(ranked.round(3).to_string())

# ============================================================================
# THE ORDERING CAVEAT — found on this script's first real run, 2026-10-17. On a
# window whose RETURN IS NEGATIVE, floored Calmar is non-monotone in both of
# its own inputs: dividing a negative numerator by a larger drawdown moves the
# ratio towards zero, so a DEEPER drawdown scores BETTER. Measured on the GFC
# blind roll, 0.80x lost more and fell further than 0.95x and the metric ranked
# it first.
#
# Printed rather than fixed: AC-3 fixes the metric, and swapping the formula
# silently would discard a recorded decision. See scoring.calmar's own header.
#
# RESOLVED 2026-10-18 (13-02.1): the score is now scoring.relative_score, which
# cannot invert. The caveat below therefore applies only to the descriptive
# `calmar_floored` column, which is why it is still printed.
# ============================================================================
neg = sorted({w for w, g in step3_scores.groupby("window") if (g["spy_ret"] < 0).all()})
if neg:
    print(f"\nORDERING CAVEAT: {len(neg)} window(s) have a NEGATIVE return "
          f"({', '.join(neg)}). On those, a higher floored Calmar can mean a DEEPER drawdown, "
          f"not a shallower one, and a score/SPY ratio above 1.0 means WORSE than the index. "
          f"Read `ret` and `max_dd` directly for those windows.")

# %% 10. One stacked panel per window — armed configurations only

# 10. Per-window stacked panels

# ============================================================================
# The grids are nine numbers per window. These are the same runs as paths, one
# figure per window, with every roll and monetisation marked — which is the
# form in which "the deep strike collapsed through the calm decade" and "the
# near strike barely dented the GFC" are arguments rather than cells.
#
# Only the four LADDER strikes are drawn, not all nine: nine lines plus their
# markers is unreadable, and the four are grid points too (the grid axis
# interleaves them), so these panels are four columns of the grid above rather
# than a separate experiment.
#
# ONE THING WORTH CHECKING EXPLICITLY — does a CALM window ever monetise at
# all? If a calm panel carries no MONETISE triangle anywhere, the monetisation
# policy is inert there and every difference between strikes in that window is
# pure premium drag. That is a real reading, and it is only visible as an
# absence of markers.
#
# Nothing re-runs the simulator: every panel is a SLICE of the series and the
# blotter cell 6 already produced.
# ============================================================================

for window_name, (start, end, kind) in INDICATIVE_WINDOWS.items():
    sliced = {label: slice_result(step3[label], start, end)
              for label in (config_label(f, LADDER_MONETISE_DRAWDOWN)
                            for f in MONEYNESS_LADDER)}
    # A window with fewer than two bars inside the run's coverage has nothing to draw, and a
    # one-point figure would read as a flat line rather than as missing data.
    if min(len(v["series"]) for v in sliced.values()) < 2:
        print(f"skipping {window_name}: outside the run's coverage")
        continue
    # The event breakdown PRINTED as well as drawn, because "this calm window never monetised
    # once" is an ABSENCE of markers — easy to miss by eye and impossible to miss in a count.
    # Classified through the shared helper, so the number and the triangles cannot disagree.
    counts = []
    for label, v in sliced.items():
        ev = scoring.classify_option_events(v["blotter"], UNDERLYING)
        # slice_result keeps fills from BEFORE the window (13-02.1) so a close is classified
        # against the position it closes; count only the events inside the window.
        if len(ev):
            ev = ev[pd.to_datetime(ev["ts"]) >= pd.Timestamp(start)]
        tally = ev["event"].value_counts() if len(ev) else pd.Series(dtype=int)
        # OPEN is counted separately from ROLL and not folded into it: after a monetisation the
        # position REOPENS, which carries opening fills alone and is therefore an OPEN, not a
        # roll. A window showing monetisations but no reopens is one where the re-entry gate
        # held for the rest of it — a different fact from "it never traded".
        counts.append(f"{label} {int(tally.get(scoring.OPEN, 0))}O/"
                      f"{int(tally.get(scoring.ROLL, 0))}R/"
                      f"{int(tally.get(scoring.MONETISE, 0))}M")
    print(f"{window_name} ({kind}): " + "  ".join(counts)
          + "   [O=opens/reopens, R=rolls, M=monetisations]")
    gridfig.fig_moneyness_stack(
        sliced, SPOT_FULL.loc[start:end],
        title=(f"{window_name} ({kind}) — monetise at {LADDER_MONETISE_DRAWDOWN:.0%} drawdown, "
               f"four strikes"),
        reentry_iv=LADDER_REENTRY_IV, underlying=UNDERLYING).show()

# %% 11. 13-02.1 — the published figures, from the persisted budget-sized sweep

# 11. The published page's figures, read back from disk

# ============================================================================
# Everything the published optimisation page draws, rebuilt here from the
# PERSISTED sweep (outputs/option_grid_budget/) without re-running the grid:
# the GFC mechanism steps, one panel per window, the four build-up grids (MDD,
# R, (1+R)/MDD, the relative score — each with SPY as its own row and the
# 5%-a-year reference run beneath), the three 1-D fit layers at the fixed
# trigger, the surfaces, the snapping slice and the 3-D surface.
#
# The 3-D surface needs WebGL. VS Code's interactive window may not have it
# (the panel then reads "WebGL is not supported"); `.show()` in a browser
# renderer (`pio.renderers.default = "browser"`) opens it in Chrome or Edge,
# where it rotates normally.
# ============================================================================

from portutils.strategies import surface  # noqa: E402

pub_results, pub_scores = sweep.load_sweep()
pub_trials = int(pub_scores["config"].nunique())
pub_fits, pub_optima, pub_bounds, _ = sweep.fit_surfaces(pub_scores)
pub_opt = pub_optima["POOLED_DEMEANED"]
pub_snap = surface.snap_to_strike(pub_opt.point[0], float(SPOT_FULL.iloc[-1]), increment=1.0,
                                  predict=pub_fits["POOLED_DEMEANED"].predict,
                                  other=pub_opt.point[1])
pub_figs = sweep.build_publication_figures(
    pub_scores, pub_fits, pub_optima, pub_bounds, pub_snap, pub_trials, pub_results, SPOT_FULL,
    reference=sweep.load_reference(),
    reference_scores=pd.read_csv(sweep.OUT_DIR / "reference" / "reference_scores.csv"))
for name, fig in pub_figs.items():
    print(name)
    fig.show()

# %%
