# %% 1. Import libraries

# 1. Import libraries
# Imports

import importlib

import numpy as np
import pandas as pd

import pipelines.option_probe_figures as opf
from pipelines.option_probe_figures import (
    BASE_VOL, DIV_YIELD, MONEYNESS, RATE, TENOR_YEARS, UNDERLYING, VOL_BETA,
    build_ladder, episode_paths, fig_drawdown_episode, fig_iv_paths, fig_put_paths, fig_smirk,
    iv_paths, load_spot_path, put_paths, worst_drawdown_episode,
)
from portutils.strategies.instruments.pricing import black_scholes_put
from portutils.strategies.instruments.vol import synthetic_iv_surface

# %% Reload custom package

# Reload custom package

# Reload module during development (re-import names after reload). The figure builders are what
# actually get iterated on in a session; the two instrument modules underneath them are stable.
importlib.reload(opf)
from pipelines.option_probe_figures import (
    build_ladder, episode_paths, fig_drawdown_episode, fig_iv_paths, fig_put_paths, fig_smirk,
    iv_paths, load_spot_path, put_paths, worst_drawdown_episode,
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

# %% 9. Export the figures for the static site

# 9. Export the figures for the static site

# Renders every figure above to standalone interactive HTML under docs/figures/, which is what
# GitHub Pages serves. Same builders, so the published page and the inline figure cannot
# disagree. Also runnable from a terminal as:  python -m pipelines.option_probe_figures
opf.main()
