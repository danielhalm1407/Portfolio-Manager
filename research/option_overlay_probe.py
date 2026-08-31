# %% 1. Import libraries

# 1. Import libraries
# Imports

import importlib

import numpy as np
import pandas as pd
import plotly.graph_objects as go

import portutils
from portutils.strategies.instruments import pricing, vol
from portutils.strategies.instruments.pricing import black_scholes_put
from portutils.strategies.instruments.vol import synthetic_iv_surface
from portutils.utils.config import PROJECT_ROOT
from portutils.viz import theme

# %% Reload custom package

# Reload custom package

# Reload module during development (re-import names after reload). The two instrument modules are
# the ones actually being iterated on here — everything else in the import block is stable.
importlib.reload(vol)
importlib.reload(pricing)
from portutils.strategies.instruments.pricing import black_scholes_put
from portutils.strategies.instruments.vol import synthetic_iv_surface

# %% 2. Probe parameters — the one place to change the ladder

# 2. Probe parameters — the one place to change the ladder

# ============================================================================
# WHAT THIS SCRIPT IS
# The manual strike-ladder pass that precedes 16-02/16-03. It answers one
# question with the data that already exists: does the ported vol surface plus
# a European put produce sane protection COSTS and sane protection PAYOFFS on
# the real SPY path, before three plans of module-building are committed to.
#
# It deliberately does NOT roll, does not size a hedge, and does not book a
# P&L. A roll calendar is 16-02 (`RollCalendar`) and the structures that use it
# are 16-03. Everything here is a single, fixed-tenor snapshot repriced bar by
# bar — which is exactly why it needs no Phase 11 data and no option chain.
# ============================================================================

# The strike ladder, expressed as moneyness against the PERIOD-START spot rather than as absolute
# strikes, so the ladder is meaningful whatever window the panel happens to cover. 1.00 is the ATM
# reference; 0.80 is roughly where the Obsidian hedge catalogue's collar puts its floor.
MONEYNESS = (0.80, 0.90, 0.95, 1.00)

# Tenor held CONSTANT at 63 trading days — the roll period the collar resets on ("How to roll a
# put hedge"). Constant, not decaying, on purpose: with a fixed tenor every bar shows the value of
# a FRESHLY STRUCK 3-month put, so the series isolates the spot and vol effect. Theta decay within
# a roll period is a property of the roll, and the roll is 16-02's job, not this script's.
TENOR_YEARS = 63 / 252

# Risk-free rate. 0.02 is the value 12-01's worked examples use, kept identical here so any number
# this script prints can be cross-checked against the plan by hand.
RATE = 0.02

# Dividend yield left at zero to match those same worked examples. SPY actually yields ~1.2%,
# which over a 63-day tenor is worth a few tenths of a point on the put — a known, deliberate
# omission at probe grade, and a parameter 16-02's OptionLeg must carry properly.
DIV_YIELD = 0.0

# The dense grid used for the smirk figure only — enough points to make the convexity visible.
SMIRK_MONEYNESS_GRID = np.linspace(0.60, 1.30, 141)

# Tenors shown on the smirk figure, in years. Chosen to straddle the 63-day roll so the term
# structure is visible as separation between the curves rather than asserted in a comment.
SMIRK_TENORS = {"1 month": 21 / 252, "3 months": 63 / 252, "6 months": 126 / 252, "1 year": 1.0}

# The panel the spot path is read from. 250 rows, 2025-07-28 to 2026-07-24 — one regime, which is
# the whole reason Phase 11 exists. Sufficient for a shape check, insufficient for a verdict.
PRICE_PANEL = PROJECT_ROOT / "data" / "processed" / "prices_spy_kmlm.parquet"
UNDERLYING = "SPY"

# %% 3. The smirk at a single spot

# 3. The smirk at a single spot

# The first thing worth seeing is that the surface has the right SHAPE. Fix one spot, sweep the
# strike, and the three terms should be individually visible: the smirk (down and to the right,
# because lower strikes carry higher IV), the term structure (curves separated vertically, longer
# tenors higher) and the convexity (the wings turning up rather than running off linearly).
SMIRK_SPOT = 100.0

fig_smirk = go.Figure()
for i, (label, tenor) in enumerate(SMIRK_TENORS.items()):
    # One trace per tenor. Strikes are absolute (moneyness x spot) because that is what the
    # surface takes; the x-axis stays in moneyness because that is what is readable.
    strikes = SMIRK_MONEYNESS_GRID * SMIRK_SPOT
    ivs = synthetic_iv_surface(strikes, tenor, SMIRK_SPOT)
    fig_smirk.add_trace(go.Scatter(
        x=SMIRK_MONEYNESS_GRID,
        y=ivs,
        mode="lines",
        name=label,
        line=dict(color=theme.CATEGORICAL[i], width=2),
        hovertemplate="K/S %{x:.2f}<br>IV %{y:.1%}<extra>" + label + "</extra>",
    ))

# ATM marker: the one point on the surface whose value should be recognisably `base` plus the
# term term and nothing else, since log-moneyness is zero there and the skew and smile vanish.
fig_smirk.add_vline(x=1.0, line=dict(color=theme.MUTED, width=1, dash="dot"),
                    annotation_text="ATM", annotation_position="top")

# Transparent backgrounds rather than a dark template, per theme.py: the dark palette applies
# where an HTML DOCUMENT is produced. A figure shown in the VS Code interactive window inherits
# VS Code's own theme through a transparent background and already reads correctly — baking
# plotly_dark in here would fix an export case this script does not have by breaking the inline
# case it does. If a figure is ever saved, call theme.apply_export_theme(fig) before write_html.
fig_smirk.update_layout(
    title=f"Synthetic IV surface — the smirk at spot {SMIRK_SPOT:.0f}",
    xaxis_title="moneyness K / S",
    yaxis_title="implied vol",
    yaxis_tickformat=".0%",
    hovermode="x unified",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
)
fig_smirk.show()

# Sanity print: the two anchor values 12-01 quotes, so a mis-ported constant is caught here and
# not three cells later. K=90, tau=0.125, spot_ref=100 must give 0.210.
print(f"IV(K=90, tau=0.125, spot_ref=100) = {synthetic_iv_surface(90, 0.125, 100):.4f}  (plan: 0.2098)")
print(f"IV(K=90, tau=0.125, spot_ref=88)  = {synthetic_iv_surface(90, 0.125, 88):.4f}  (plan: 0.1760)")

# %% 4. Load the SPY price path

# 4. Load the SPY price path

# Straight parquet read rather than PanelBuilder: this needs ONE column and no normalisation, and
# PanelBuilder._load currently truncates ragged history (the defect 11-01 fixes at both sites).
# Nothing here is ragged — one ticker, one file — but going around the loader keeps this script
# independent of how that fix lands.
prices = pd.read_parquet(PRICE_PANEL)
spot_path = prices[UNDERLYING].dropna()

print(f"{UNDERLYING}: {len(spot_path)} bars, "
      f"{spot_path.index[0].date()} to {spot_path.index[-1].date()}")
print(f"spot {spot_path.iloc[0]:.2f} -> {spot_path.iloc[-1]:.2f}  "
      f"({spot_path.iloc[-1] / spot_path.iloc[0] - 1:+.1%})")
# The worst peak-to-trough on this window. If this is shallow, every put below reads as pure cost,
# and that is a fact about the window, not about the hedge.
drawdown = spot_path / spot_path.cummax() - 1.0
print(f"max drawdown on this window: {drawdown.min():.1%} on {drawdown.idxmin().date()}")

# %% 5. The strike ladder

# 5. The strike ladder

# Strikes are struck ONCE, off the period-start spot, and then held. That is the v1 convention
# (`spot_ref` pinned to the period-start spot) and it is also what a real hedge does between
# rolls: the strike does not chase the market intra-period.
SPOT_REF = float(spot_path.iloc[0])
LADDER = {m: m * SPOT_REF for m in MONEYNESS}

for m, k in LADDER.items():
    # IV is a property of the strike and tenor alone under v1, so it can be printed once here
    # rather than recomputed per bar — which is itself the observation cell 6 makes visible.
    iv = float(synthetic_iv_surface(k, TENOR_YEARS, SPOT_REF))
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
#               "frozen reference" means, and it is what ships.
#   v2 (dashed) spot_ref = the current bar's spot -> as the market falls, a
#               fixed strike slides DOWN the smirk and its IV FALLS.
# That falling line is the whole argument recorded on 2026-08-29: under v2 the
# protection gets CHEAPER precisely in the selloff Phase 13 exists to measure,
# unless the vol LEVEL (term A) is made dynamic at the same time. Hence: never
# term B alone.
# ============================================================================

strike_colours = {m: theme.CATEGORICAL[i] for i, m in enumerate(MONEYNESS)}

iv_v1 = pd.DataFrame(
    # Frozen reference: one scalar per strike, broadcast across the index.
    {m: np.full(len(spot_path), float(synthetic_iv_surface(k, TENOR_YEARS, SPOT_REF)))
     for m, k in LADDER.items()},
    index=spot_path.index,
)
iv_v2 = pd.DataFrame(
    # Current-spot reference: the surface is re-evaluated against every bar's spot.
    {m: synthetic_iv_surface(k, TENOR_YEARS, spot_path.to_numpy()) for m, k in LADDER.items()},
    index=spot_path.index,
)

fig_iv = go.Figure()
for m in MONEYNESS:
    colour = strike_colours[m]
    # legendgroup ties the two references for one strike together, so clicking the legend hides
    # a strike entirely rather than leaving its twin behind.
    fig_iv.add_trace(go.Scatter(
        x=iv_v1.index, y=iv_v1[m], mode="lines", name=f"{m:.2f}x  v1 frozen",
        legendgroup=f"{m:.2f}", line=dict(color=colour, width=2),
    ))
    fig_iv.add_trace(go.Scatter(
        x=iv_v2.index, y=iv_v2[m], mode="lines", name=f"{m:.2f}x  v2 current",
        legendgroup=f"{m:.2f}", line=dict(color=colour, width=1.5, dash="dash"),
    ))

fig_iv.update_layout(
    title=f"Implied vol per ladder strike — {TENOR_YEARS * 252:.0f}-day tenor, "
          f"struck off spot {SPOT_REF:.2f}",
    xaxis_title="date", yaxis_title="implied vol", yaxis_tickformat=".0%",
    hovermode="x unified",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
)
fig_iv.show()

# %% 7. Put price through time, one series per strike

# 7. Put price through time, one series per strike

# Same ladder, same two references, now valued. Note that the v1 series MOVE even though their IV
# does not: spot is the dominant term in a put, and holding vol fixed simply means the price is
# responding to the underlying alone. The v1/v2 GAP is therefore the pure vol-reference effect,
# with the spot effect divided out — which is what makes it readable.
put_v1 = pd.DataFrame(
    {m: black_scholes_put(spot_path.to_numpy(), LADDER[m], TENOR_YEARS, iv_v1[m].to_numpy(),
                          rate=RATE, div_yield=DIV_YIELD)
     for m in MONEYNESS},
    index=spot_path.index,
)
put_v2 = pd.DataFrame(
    {m: black_scholes_put(spot_path.to_numpy(), LADDER[m], TENOR_YEARS, iv_v2[m].to_numpy(),
                          rate=RATE, div_yield=DIV_YIELD)
     for m in MONEYNESS},
    index=spot_path.index,
)

fig_px = go.Figure()
for m in MONEYNESS:
    colour = strike_colours[m]
    fig_px.add_trace(go.Scatter(
        x=put_v1.index, y=put_v1[m], mode="lines", name=f"{m:.2f}x  v1 frozen",
        legendgroup=f"{m:.2f}", line=dict(color=colour, width=2),
    ))
    fig_px.add_trace(go.Scatter(
        x=put_v2.index, y=put_v2[m], mode="lines", name=f"{m:.2f}x  v2 current",
        legendgroup=f"{m:.2f}", line=dict(color=colour, width=1.5, dash="dash"),
    ))

# The spot path on a secondary axis: without it the put series are uninterpretable, because every
# feature in them is a feature of the underlying.
fig_px.add_trace(go.Scatter(
    x=spot_path.index, y=spot_path.to_numpy(), mode="lines", name=f"{UNDERLYING} spot (rhs)",
    line=dict(color=theme.MUTED, width=1), yaxis="y2",
))

fig_px.update_layout(
    title=f"European put value per ladder strike — {TENOR_YEARS * 252:.0f}-day tenor, "
          f"re-struck never (single snapshot repriced)",
    xaxis_title="date", yaxis_title="put value (price units)",
    yaxis2=dict(title=f"{UNDERLYING} spot", overlaying="y", side="right", showgrid=False),
    hovermode="x unified",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
)
fig_px.show()

# %% 8. What the ladder costs, and what the reference choice is worth

# 8. What the ladder costs, and what the reference choice is worth

# The numbers the figures are asserting, printed so they can be quoted into the write-up. The
# last column is the one that settles the 2026-08-29 decision: at the deepest point of the
# drawdown, how much CHEAPER does the current-spot reference make the protection?
trough = drawdown.idxmin()
print(f"at the trough ({trough.date()}, spot {spot_path.loc[trough]:.2f}, "
      f"{drawdown.min():.1%} off the high):\n")
print(f"{'strike':>10} {'K/S then':>9} {'IV v1':>8} {'IV v2':>8} "
      f"{'put v1':>9} {'put v2':>9} {'v2 vs v1':>10}")
for m in MONEYNESS:
    p1, p2 = put_v1.loc[trough, m], put_v2.loc[trough, m]
    # K/S at the trough, NOT the moneyness the strike was struck at. The two differ by however far
    # spot has moved since inception, and the v1/v2 gap is a function of the FORMER — which is why
    # a ladder struck once and never rolled understates the effect this table exists to show.
    print(f"{LADDER[m]:10.2f} {LADDER[m] / spot_path.loc[trough]:9.2f} "
          f"{iv_v1.loc[trough, m]:8.1%} {iv_v2.loc[trough, m]:8.1%} "
          f"{p1:9.2f} {p2:9.2f} {(p2 / p1 - 1) if p1 else float('nan'):10.1%}")

# Cost of carrying the ladder as an outright, expressed against spot — the number that decides
# whether a structure is worth building before Phase 13 measures the drag properly.
print("\ncost at inception, as a share of spot:")
for m in MONEYNESS:
    print(f"  {m:.2f}x  {put_v1.iloc[0][m] / SPOT_REF:6.2%}")
