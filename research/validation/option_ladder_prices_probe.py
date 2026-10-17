# %% 1. Import libraries

# 1. Import libraries
# Imports

import importlib

import numpy as np
import pandas as pd

import pipelines.option_probe_figures as opf
from pipelines.option_probe_figures import (
    BASE_VOL, DIV_YIELD, MONEYNESS, OVERLAY_ORDER, RATE, TENOR_YEARS, UNDERLYING, VOL_BETA,
    align_real_iv, build_ladder, episode_paths, fig_drawdown_episode, fig_iv_paths,
    fig_overlay_values, fig_put_paths, fig_put_paths_market, fig_real_iv_history, fig_smirk,
    iv_paths, iv_paths_market, load_real_iv, load_spot_path, overlay_runs, overlay_table,
    put_paths, worst_drawdown_episode,
)
from portutils.strategies.instruments.pricing import black_scholes_put
from portutils.strategies.instruments.vol import synthetic_iv_surface

# %% Reload custom package

# Reload custom package

# Reload module during development (re-import names after reload). The figure builders are what
# actually get iterated on in a session; the two instrument modules underneath them are stable.
importlib.reload(opf)
from pipelines.option_probe_figures import (
    align_real_iv, build_ladder, episode_paths, fig_drawdown_episode, fig_iv_paths,
    fig_overlay_values, fig_put_paths, fig_put_paths_market, fig_real_iv_history, fig_smirk,
    iv_paths, iv_paths_market, load_real_iv, load_spot_path, overlay_runs, overlay_table,
    put_paths, worst_drawdown_episode,
)

# %% 2. What this script is

# 2. What this script is

# ============================================================================
# THE MANUAL STRIKE-LADDER PASS that precedes 16-02/16-03. It answers one
# question with the data that already exists: does the ported vol surface plus
# a European put produce sane protection COSTS and sane protection PAYOFFS on
# the real SPY path, before three plans of module-building are committed to.
#
# Cells 3-7 carry a ladder struck ONCE at the start of the window. Cell 8 is
# the correction to that: a ladder struck at the drawdown's PEAK, which is
# where a rolled hedge would actually have been standing. The two answer
# different questions and cell 8 is the one about payoff.
#
# Cell 9 (added by plan 16-03, 2026-09-19) goes one step further: the three
# hedge structures as simulator rules, ROLLED every 63 bars over the window,
# valued as a whole book next to SPY alone.
#
# Cells 10-11 (added 2026-09-20, following 11-01) replace assumption with
# measurement: cell 10 plots SPY's REAL implied vol over this same window
# (IBKR's OPTION_IMPLIED_VOLATILITY, not synthetic, not VIX), and cell 11
# reprices the ladder with BOTH the vol level and the moneyness reference
# dynamic together, sourced from that real series — the "never term B alone"
# combination the 2026-08-29 decision required, landing here with real
# numbers instead of Finding 6's illustrative VOL_BETA stand-in.
#
# The figures themselves are built by pipelines/option_probe_figures.py, which
# also renders them to docs/figures/ for the static site. One implementation
# serves both, so what is shown here and what is published cannot diverge.
# ============================================================================

# %% 3. The smirk at a single spot

# 3. The smirk at a single spot

# The first thing worth seeing is that the surface has the right SHAPE. Fix one spot, sweep the
# strike, and the three terms should be individually visible: the smirk (down and to the right,
# because lower strikes carry higher IV), the term structure (curves separated vertically, longer
# tenors higher) and the convexity (the wings turning up rather than running off linearly).
fig_smirk().show()

# Sanity print: the two anchor values 12-01 quotes, so a mis-ported constant is caught here and
# not five cells later.
print(f"IV(K=90, tau=0.125, spot_ref=100) = {synthetic_iv_surface(90, 0.125, 100):.4f}  (plan: 0.2098)")
print(f"IV(K=90, tau=0.125, spot_ref=88)  = {synthetic_iv_surface(90, 0.125, 88):.4f}  (plan: 0.1760)")

# %% 4. Load the SPY price path

# 4. Load the SPY price path

spot_path = load_spot_path()

print(f"{UNDERLYING}: {len(spot_path)} bars, "
      f"{spot_path.index[0].date()} to {spot_path.index[-1].date()}")
print(f"spot {spot_path.iloc[0]:.2f} -> {spot_path.iloc[-1]:.2f}  "
      f"({spot_path.iloc[-1] / spot_path.iloc[0] - 1:+.1%})")

# The window's deepest fall, and — crucially — WHERE it sits relative to the start. A window that
# rallies first and then gives the rally back ends up near where it began, which is exactly the
# trap cell 8 exists to escape.
peak, trough, drawdown = worst_drawdown_episode(spot_path)
print(f"worst drawdown: {drawdown.min():.1%}, peak {peak.date()} ({spot_path[peak]:.2f}) "
      f"-> trough {trough.date()} ({spot_path[trough]:.2f})")
print(f"spot at the trough is {spot_path[trough] / spot_path.iloc[0] - 1:+.1%} vs the WINDOW START "
      f"— which is what a ladder struck at the start actually experiences")

# %% 5. The strike ladder

# 5. The strike ladder

# Strikes are struck ONCE, off the period-start spot, and then held. That is the v1 convention
# (`spot_ref` pinned to the period-start spot) and it is also what a real hedge does between
# rolls: the strike does not chase the market intra-period.
SPOT_REF = float(spot_path.iloc[0])
LADDER = build_ladder(SPOT_REF)

for m, k in LADDER.items():
    # IV is a property of the strike and tenor alone under v1, so it can be printed once here
    # rather than recomputed per bar — which is itself the observation cell 6 makes visible.
    iv = float(synthetic_iv_surface(k, TENOR_YEARS, SPOT_REF, base=BASE_VOL))
    px = float(black_scholes_put(SPOT_REF, k, TENOR_YEARS, iv, rate=RATE, div_yield=DIV_YIELD))
    print(f"{m:.2f} x spot -> K = {k:7.2f}   IV = {iv:.1%}   put = {px:6.2f}  "
          f"({px / SPOT_REF:.2%} of spot)")

# %% 6. IV through time, one series per strike

# 6. IV through time, one series per strike

# ============================================================================
# THE FIGURE THAT MAKES THE v1/v2 DECISION VISIBLE
# Two references for moneyness, plotted together for the same ladder:
#   v1 (solid)  spot_ref pinned to the period-start spot -> log(K/S0) is fixed,
#               so IV is FLAT for a given strike. Flat is not a bug; it is what
#               "frozen reference" means, and it is what ships. The put still
#               moves — through the PRICER's spot argument, never through the
#               surface, which is not consulted again after the strike is set.
#   v2 (dashed) spot_ref = the current bar's spot -> as the market falls, a
#               fixed strike slides DOWN the smirk and its IV FALLS.
# That falling line is the whole argument recorded on 2026-08-29: under v2 the
# protection gets CHEAPER precisely in the selloff Phase 13 exists to measure,
# unless the vol LEVEL (term A) is made dynamic at the same time. Hence: never
# term B alone.
# ============================================================================

iv_v1, iv_v2 = iv_paths(spot_path, LADDER, spot_ref=SPOT_REF)
fig_iv_paths(iv_v1, iv_v2, SPOT_REF).show()

# %% 7. Put value through time, one series per strike

# 7. Put value through time, one series per strike

# Same ladder, same two references, now valued. The v1 series MOVE even though their IV does not:
# spot is the dominant term in a put, so holding vol fixed means the price is responding to the
# underlying alone. The v1/v2 GAP is the pure vol-reference effect, spot divided out.
put_v1 = put_paths(spot_path, LADDER, iv_v1)
put_v2 = put_paths(spot_path, LADDER, iv_v2)
fig_put_paths(put_v1, put_v2, spot_path).show()

print(f"at the trough ({trough.date()}, spot {spot_path.loc[trough]:.2f}, "
      f"{drawdown.min():.1%} off the high):\n")
print(f"{'strike':>10} {'K/S then':>9} {'IV v1':>8} {'IV v2':>8} "
      f"{'put v1':>9} {'put v2':>9} {'v2 vs v1':>10}")
for m in MONEYNESS:
    p1, p2 = put_v1.loc[trough, m], put_v2.loc[trough, m]
    # K/S at the trough, NOT the moneyness the strike was struck at. The two differ by however far
    # spot has moved since inception — and here they barely differ at all, because the market
    # rallied and gave it all back. That is why the v1/v2 gap below reads so small.
    print(f"{LADDER[m]:10.2f} {LADDER[m] / spot_path.loc[trough]:9.2f} "
          f"{iv_v1.loc[trough, m]:8.1%} {iv_v2.loc[trough, m]:8.1%} "
          f"{p1:9.2f} {p2:9.2f} {(p2 / p1 - 1) if p1 else float('nan'):10.1%}")

print("\ncost at inception, as a share of spot:")
for m in MONEYNESS:
    print(f"  {m:.2f}x  {put_v1.iloc[0][m] / SPOT_REF:6.2%}")

# %% 8. The drawdown episode — a ladder struck where a rolled hedge would stand

# 8. The drawdown episode — a ladder struck where a rolled hedge would stand

# ============================================================================
# WHY THIS CELL EXISTS
# Cells 5-7 strike the ladder at the WINDOW START and never restrike. The
# market then rallied +9.2% and gave all of it back, so at the trough spot was
# within 1% of where the ladder was struck: the fall happened entirely ABOVE
# the strikes, and the ladder never had the drawdown in front of it. Those
# cells therefore measure CARRY, and reading them as a payoff result — as an
# earlier version of the write-up did — is wrong.
#
# Strike instead at the drawdown's PEAK. Now the fall is in front of the
# strike, which is what a 63-day roll produces automatically: every roll
# restrikes at the then-current spot, so whatever comes next is measured from
# there rather than from wherever the market happened to be a year ago.
#
# Unlike cells 5-7 this carries ONE option through its whole life, so tau
# decays bar by bar and theta is part of what is measured.
# ============================================================================

idx, ep_strikes, mtm_flat, mtm_spike = episode_paths(spot_path, peak)
fig_drawdown_episode(idx, ep_strikes, mtm_flat, mtm_spike, spot_path, peak, trough).show()

print(f"struck {peak.date()} at spot {spot_path[peak]:.2f}, "
      f"held to the trough {trough.date()} at {spot_path[trough]:.2f} "
      f"({spot_path[trough] / spot_path[peak] - 1:+.1%}, "
      f"{len(spot_path.loc[peak:trough]) - 1} bars)\n")
print(f"{'strike':>10} {'premium':>9} {'% of spot':>10} {'MTM':>9} {'return':>9} "
      f"{'+vol spike':>11} {'return':>9}")
for m in MONEYNESS:
    prem = float(mtm_flat[m].iloc[0])
    mtm = float(mtm_flat[m].loc[trough])
    mtm_s = float(mtm_spike[m].loc[trough])
    print(f"{ep_strikes[m]:10.2f} {prem:9.2f} {prem / spot_path[peak]:10.2%} "
          f"{mtm:9.2f} {mtm / prem - 1:+9.1%} {mtm_s:11.2f} {mtm_s / prem - 1:+9.1%}")

# The "+vol spike" columns are an ILLUSTRATIVE term-A response — the ATM level rising by
# VOL_BETA vol points per 1.0 of drawdown, so 0.16 -> 0.34 at the trough. Not calibrated; that
# is what Phase 11's vol history is for. It is here to bound what v1's frozen base leaves out,
# and the gap is largest on the CHEAP OTM strikes — the ones actually worth buying.
print(f"\n(vol spike model: base = {BASE_VOL:.2f} + {VOL_BETA:.1f} x drawdown, "
      f"illustrative only — Phase 11 calibrates it)")

# %% 9. The three hedge structures, rolled, through the real simulator

# 9. The three hedge structures, rolled, through the real simulator

# ============================================================================
# FROM A SINGLE LEG TO A HEDGED BOOK (plan 16-03)
# Cells 5-8 price legs; nothing there holds a portfolio or rolls. Here each
# structure is a rule in PortfolioSimulator: one SPY unit bought on bar 0, its
# hedge opened on bar 1 (the first bar SPY is held) and rolled every 63 bars
# off the then-current spot — the restriking that finding 4 said was missing.
#
# Reading the figure:
#   top    — total value, 1 SPY unit plus its hedge, against SPY alone
#   middle — each hedge's value as a multiple of what it cost, RESETTING at every
#            roll; above 1.0 the hedge has gained since it was bought, and the
#            last point of each segment is what it actually settled at
#   bottom — cumulative net premium paid
# Hover the triangles on the top panel for each roll's per-leg detail.
#
# v1 surface throughout (base 0.16 frozen), so every hedge payoff is a FLOOR
# (finding 6), and this is ONE window — one observation, not a result.
# ============================================================================

runs = overlay_runs(spot_path)
fig_overlay_values(runs, spot_path).show()

# The summary the figure is read against. "premium %/yr" is net premium paid per year as a share
# of the average spot — the measured version of finding 3's single-put estimate.
table = overlay_table(runs, spot_path)
print(table.to_string(formatters={
    "final value": "{:.2f}".format, "return": "{:+.1%}".format,
    "max drawdown": "{:.1%}".format, "premium %/yr": "{:.2%}".format,
}))

# The roll log per structure — what each rule actually did, straight from its events.
for name in OVERLAY_ORDER[1:]:
    ev = runs[name]["rule"].events_frame()
    print(f"\n{name}")
    print(ev[["ts", "action", "symbol", "qty", "price", "pnl_per_unit"]]
          .to_string(index=False, float_format=lambda v: f"{v:.3f}"))


# %% 10. Real implied vol over the probe window (Finding 8)

# 10. Real implied vol over the probe window (Finding 8)

# IBKR's OPTION_IMPLIED_VOLATILITY for SPY itself — NOT VIX (an SPX-derived, 30-day-constant-
# maturity index IBKR would only serve as its own contract) and not synthetic. Cached by 11-01's
# probe path, reaching back to 2006-01-09; sliced here to this probe's own short window.
real_iv = align_real_iv(load_real_iv(), spot_path)
fig_real_iv_history(real_iv).show()

print(f"real IV over the window: {real_iv.min():.1%} to {real_iv.max():.1%}, "
      f"mean {real_iv.mean():.1%}  (vol.py base = {BASE_VOL:.0%})")
print(f"at the drawdown peak ({peak.date()}): {real_iv.loc[peak]:.1%}")
print(f"at the drawdown trough ({trough.date()}): {real_iv.loc[trough]:.1%}  "
      f"({real_iv.loc[trough] / real_iv.loc[peak] - 1:+.0%} vs the peak)")

# %% 11. The ladder repriced with real vol level AND current spot, both dynamic (Finding 9)

# 11. The ladder repriced with real vol level AND current spot, both dynamic (Finding 9)

# ============================================================================
# BOTH TERMS DYNAMIC TOGETHER, FOR REAL — the 2026-08-29 decision was "ship v1,
# and never enable term B (current-spot moneyness) without term A (a dynamic
# vol level)". Cells 5-7 show term B alone (v2) and Finding 6 illustrates term
# A alone (the VOL_BETA stand-in). This is the first time both run together
# off REAL data rather than an assumption: `iv_paths_market` feeds cell 10's
# real IV series into `synthetic_iv_surface` as `base`, bar by bar, while the
# moneyness reference is each bar's own spot — exactly v2's mechanism, just
# no longer paired with a frozen level.
# ============================================================================

market_iv = iv_paths_market(spot_path, LADDER, real_iv)
put_market = put_paths(spot_path, LADDER, market_iv)
# Cell 10's IV series is STACKED underneath this figure's put panel on a shared x-axis, so the
# vol spike lines up vertically with the jump it caused in the dashed lines. Cell 10 keeps its
# standalone figure too — that one is about the vol level itself, this one about its consequence.
fig_put_paths_market(put_v1, put_market, spot_path, real_iv).show()

print(f"at the trough ({trough.date()}):\n")
print(f"{'strike':>10} {'IV v1':>8} {'IV market':>10} {'put v1':>9} {'put market':>11} {'vs v1':>9}")
for m in MONEYNESS:
    p1, pm = put_v1.loc[trough, m], put_market.loc[trough, m]
    print(f"{LADDER[m]:10.2f} {iv_v1.loc[trough, m]:8.1%} {market_iv.loc[trough, m]:10.1%} "
          f"{p1:9.2f} {pm:11.2f} {(pm / p1 - 1):+9.1%}")

# This is a probe-window result, not the walk-forward: it confirms the MECHANISM (a real vol spike
# repriced the cheap strikes far more than the ATM one) on one window. The full 2006-2026 real
# series calibrating vol.py's v2 across many regimes is Phase 13's job, not this probe's.

# %% 12. Export the figures AND the report page for the static site

# 12. Export the figures AND the report page for the static site

# LAST CELL ON PURPOSE. main() rebuilds every figure from scratch and writes docs/figures/*.html
# plus the narrative report at docs/index.html, so it has to run AFTER the cells whose figures it
# publishes — otherwise a figure change made in an earlier cell would not reach the page until the
# next run. Also runnable from a terminal as:  python -m pipelines.option_probe_figures
opf.main()
