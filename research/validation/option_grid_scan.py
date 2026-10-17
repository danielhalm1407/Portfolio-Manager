# %% 1. Import libraries

# 1. Import libraries
# Imports

# ============================================================================
# READING THE PARAMETER GRID (13-02 Task 5). Run this file CELL BY CELL.
#
# WHAT THIS SCRIPT IS, AND WHAT IT IS NOT
# ---------------------------------------
# The sweep's own four figures answer "what does the surface look like". This
# one answers the question a reader actually asks first: WHAT DID THE BEST AND
# WORST PARAMETER CHOICES DO IN EACH REGIME? A cell in a heatmap is a number;
# the same cell as an equity path next to SPY through the GFC is an argument.
# Both are needed, and this is the one a human reads.
#
# NOTHING HERE RE-RUNS THE SIMULATOR. Every panel is built from the per-bar
# series, the blotters and the tidy score frame that
# `python -m pipelines.option_grid_sweep` already wrote to outputs/option_grid/.
# That is AC-2's whole point: a sweep costs minutes, and a session spent READING
# it — a different window, a different pair of configurations, a different
# colour scale — must not re-pay that cost once per idea.
#
# RUN THE SWEEP FIRST if outputs/option_grid/ is empty:
#     python -m pipelines.option_grid_sweep
#
# THE ORDER OF THE CELLS is the order the questions come in:
#   3   load — and prove nothing is being simulated
#   4   per-window PATHS: best, second-best and worst, three stacked panels each
#   5   per-window score GRIDS: the same windows as numbers, moneyness x depth
#   6   the pooled grid, every observation from every window
#   7   the pooled spline on the SAME axes as that grid
#
# EVERY NUMBER AND EVERY FIGURE IS IN-SAMPLE. Each threshold was read on the
# same windows it was scored on. Nothing here is a validated, selected or
# recommended parameter set — 13-03's CPCV is the only thing that can speak to
# that. NO BID/ASK MODEL: fills are at the model mark, which flatters the
# configurations that trade most.
# ============================================================================

import importlib

import pandas as pd

from portutils.strategies import scoring, surface
from portutils.strategies.scoring import CALM, DRAWDOWN, INDICATIVE_WINDOWS

import pipelines.option_grid_sweep as sweep
import pipelines.option_grid_figures as gridfig
from pipelines.option_grid_sweep import (LADDER_REENTRY_IV, demeaned_scores, fit_surfaces,
                                         load_full, load_sweep)
from pipelines.option_probe_figures import UNDERLYING

pd.set_option("display.width", 220, "display.max_columns", 60)

# %% Reload custom package

# Reload custom package

# Reload modules during development (re-import names after reload). The figure builders and the
# smoother are what get iterated on here; the rules and the ledger underneath them are stable
# and 13-02 may not touch them (see the plan's DO NOT CHANGE list).
importlib.reload(scoring)
importlib.reload(surface)
importlib.reload(sweep)
importlib.reload(gridfig)
from pipelines.option_grid_sweep import demeaned_scores, fit_surfaces, load_sweep  # noqa: E402

# %% 2. Load the persisted sweep — no simulator runs here

# 2. Load the persisted sweep

# ============================================================================
# Everything below reads from disk. `load_sweep` returns the SAME shapes
# `sweep_grid` and `tidy_scores` produce in a live run, so every figure builder
# takes a reloaded sweep and a fresh one interchangeably — which is what stops
# this script and the sweep's own figures drifting into two different pictures
# of one run.
# ============================================================================

results, scores = load_sweep()
# SPY itself, for the path panels. Loaded from the price parquet, not re-simulated: this is the
# only thing in the script that touches data/, and it is a read.
SPOT_FULL, _IV_FULL = load_full()

n_configs = scores["config"].nunique()
print(f"\nreloaded {n_configs} configurations x {scores['window'].nunique()} windows "
      f"= {len(scores)} scored rows")
print(f"moneyness levels      : {sorted(scores['floor'].unique())}")
print(f"monetise depths       : {sorted(scores['monetise_drawdown'].dropna().unique())} "
      f"plus the blind roll")
runtime = pd.read_csv(sweep.OUT_DIR / "trial_count_and_runtime.csv").iloc[-1]
print(f"TRIAL COUNT           : {int(runtime['trial_count'])} "
      f"(projected {runtime['projected_minutes']:.1f} min, "
      f"actual {runtime['actual_minutes']:.1f} min, measured {runtime['measured_at']})")

# %% 3. Per-window paths — best, second-best and worst

# 3. Per-window paths

# ============================================================================
# One figure per indicative window: the BEST, SECOND-BEST and WORST
# configurations for THAT window, drawn against SPY on three stacked panels.
#
# SECOND BEST IS NOT PADDING. The gap between first and second IS the
# plateau-versus-spike question in path form. If the two are indistinguishable,
# the surface is flat there and the choice between them does not matter — which
# is the strongest result this plan can produce, because a recommendation that
# does not need precision survives being rounded to a tradeable strike. If they
# are visibly apart on one window and not another, the optimum is sharp in one
# regime and broad in the other, which a single pooled curvature would average
# away.
#
# WHAT TO CHECK on each panel, in order:
#   panel 1  does the worst one fail in a way that MAKES SENSE — too much carry
#            in a calm window, too little protection in a crisis — or in a way
#            that suggests a bug?
#   panel 2  did the best one's hedge actually reach a multiple of its premium,
#            and was that multiple taken or given back?
#   panel 3  how much of the window did each spend FLAT, and was the vol gate
#            the reason?
# ============================================================================

for window_name in INDICATIVE_WINDOWS:
    start, end, kind = INDICATIVE_WINDOWS[window_name]
    rows = scores[(scores["window"] == window_name) & scores["score"].notna()]
    if len(rows) < 2:
        print(f"skipping {window_name}: {len(rows)} scored configurations")
        continue
    # Sliced to the window — the persisted series are the FULL history, and a path panel of
    # twenty years cannot show what happened in two months of 2020.
    sliced = {label: sweep.slice_result(res, start, end) for label, res in results.items()}
    gridfig.fig_window_paths(window_name, scores, sliced, SPOT_FULL.loc[start:end],
                             reentry_iv=LADDER_REENTRY_IV, underlying=UNDERLYING).show()

    best, worst = rows.loc[rows["score"].idxmax()], rows.loc[rows["score"].idxmin()]
    print(f"{window_name} ({kind}): best {best['config']} {best['score']:.3f} | "
          f"worst {worst['config']} {worst['score']:.3f} | "
          f"SPY {best['spy_score']:.3f} | spread {best['score'] - worst['score']:.3f}")

# %% 4. Per-window score grids — moneyness against monetisation depth

# 4. Per-window score grids

# ============================================================================
# The same windows as NUMBERS: moneyness across, monetisation drawdown down,
# cell colour and cell text the floored Calmar, floor-bound cells starred.
#
# One figure per window rather than one figure with a window axis, because
# these are the grids the SURFACE is fitted to — and a surface is fitted per
# window. Reading them in sequence is reading the same object the per-window
# panel of the sweep's figures draws smoothed.
#
# The blind-roll column appears here (as NaN on the drawdown axis it never sat
# on) and is excluded from every fit — see sweep.tidy_scores.
# ============================================================================

for window_name in INDICATIVE_WINDOWS:
    window_rows = scores[(scores["window"] == window_name) & scores["monetise_drawdown"].notna()]
    if not len(window_rows):
        continue
    kind = INDICATIVE_WINDOWS[window_name][2]
    gridfig.fig_score_grid(
        window_rows, value_col="score", row=gridfig.Y_PARAM, col=gridfig.X_PARAM,
        title=f"{window_name} ({kind}) — floored Calmar by moneyness and monetisation depth",
        trial_count=n_configs, normalise=None).show()

# %% 5. The pooled grid — every observation from every window

# 5. The pooled grid

# ============================================================================
# POOLING THE RAW SCORES IS DOMINATED BY WHICH WINDOW A ROW CAME FROM, and this
# figure is where that becomes visible rather than being a sentence in a
# docstring. Floored Calmar runs around -0.7 through the drawdown windows and
# around +5 through the calm ones, so the pooled mean of each cell is mostly
# "three calm windows averaged with three crisis ones" and the parameter effect
# is a rounding error on top of it.
#
# Measured on this grid: a surface fitted to these pooled values comes back
# with R-squared 0.009 at the MAXIMUM smoothing parameter — a flat plane. That
# is not a failed fit, it is the correct answer to the question as posed, and
# it is why cell 6 fits the window-DEMEANED scores instead.
# ============================================================================

pooled_rows = scores[scores["monetise_drawdown"].notna()]
gridfig.fig_score_grid(
    pooled_rows, value_col="score", row=gridfig.Y_PARAM, col=gridfig.X_PARAM,
    title="Pooled over all six windows — floored Calmar (dominated by window, not by parameters)",
    trial_count=n_configs, normalise=None).show()

by_kind = pooled_rows.groupby("kind")["score"].agg(["mean", "std", "min", "max"])
print("\nWhy the pooled grid says so little — the level gap between the two regimes:")
print(by_kind.round(3).to_string())
print("\nwithin-window spread (max - min across configurations), per window:")
print(pooled_rows.groupby("window")["score"].agg(lambda s: s.max() - s.min())
      .round(3).to_string())

# %% 6. The pooled spline, on the same axes and scale as that grid

# 6. The pooled spline

# ============================================================================
# The fitted surface drawn on the SAME axes and the SAME colour range as the
# pooled grid above, so the smoothing reads as a CONTINUATION of the design
# points rather than as a separate object that happens to be nearby. That
# adjacency is the argument; a smooth surface shown on its own axes is just a
# nice picture.
#
# Fitted on WINDOW-DEMEANED scores, so the height is "better or worse than the
# average configuration in the same window" and NOT a Calmar. Stated in the
# title too, because a reader who takes this height for a Calmar will read the
# whole figure backwards.
#
# AND THE BOUNDARY THAT MATTERS: this smooth is a DISPLAY, not a selection
# rule. Its optimum is an IN-SAMPLE optimum and nothing consumes it. 13-03
# selects per split by DISCRETE GRID ARGMAX. See surface.py's header for why.
# ============================================================================

fits, optima, bounds, one_d = fit_surfaces(scores)

print("\n1-D FITS FIRST — each axis with the other held at its default:")
for name, fit in one_d.items():
    print(f"  {name:20s} lam {fit.lam:>12.4g}  edf {fit.edf:5.2f}  "
          f"GCV {fit.gcv:.4g}  in-sample R2 {fit.r2:.3f}")
print("\n2-D TENSOR SMOOTHS — note how the optimum MOVES between regimes:")
for name, fit in fits.items():
    opt = optima[name]
    print(f"  {name:18s} edf {fit.edf:5.2f}  R2 {fit.r2:5.3f}  "
          f"optimum floor {opt.point[0]:.3f} / dd {opt.point[1]:.3f}  "
          f"curvature {opt.curvature:9.1f}")

gridfig.fig_pooled_spline(
    fits["POOLED_DEMEANED"], demeaned_scores(scores), bounds, trial_count=n_configs,
    title=("Pooled spline, window-demeaned — height is deviation from each window's own mean, "
           "NOT a Calmar")).show()

# ============================================================================
# AND THE CHECK THAT STOPS A SMOOTH PICTURE BEING OVER-READ. These fits sit
# close to interpolation — the simulator is deterministic, so GCV has no noise
# to regularise against and drives the smoothing parameter to its floor. An
# interpolant passes through every design point and can still OVERSHOOT
# between them. Trust a fitted optimum's POSITION where it agrees with the raw
# argmax; never quote its HEIGHT as a score anything achieved.
# ============================================================================
print("\nFITTED OPTIMUM vs RAW GRID ARGMAX:")
print(sweep.fit_vs_raw(scores, fits, optima).round(3).to_string(index=False))

# The snapping cost, against today's SPY level. A continuous optimum nobody can trade is not a
# result; if the gap between it and the nearest listed strike is material, the surface is too
# sharp for its own optimum to mean anything deployable.
spot_now = float(SPOT_FULL.iloc[-1])
opt = optima["POOLED_DEMEANED"]
snap = surface.snap_to_strike(opt.point[0], spot_now, increment=1.0,
                              predict=fits["POOLED_DEMEANED"].predict, other=opt.point[1])
print(f"\nSNAPPING (spot {spot_now:.2f}): continuous {opt.point[0]:.4f} -> "
      f"strike {snap.strike:.0f} ({snap.moneyness:.4f}), cost {snap.cost:.5f}")
print("\nIN-SAMPLE. Nothing above is validated, selected or recommended.")

# %%
