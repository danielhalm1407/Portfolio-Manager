# %% 1. Import libraries

# 1. Import libraries
#
# Interactive companion to src/pipelines/rebalance_study.py. Structured as a cell script in
# the same style as research/hedge_sleeves/hedge_sleeves.py: same imports, same reload block,
# same PanelBuilder -> attribution -> make_level_figure flow, same explicit colour/label maps.
#
# What this file is FOR: hedge_sleeves.py models the portfolio as `contrib = weights * rets`
# with a CONSTANT weight vector. Holding weights fixed every bar is mathematically a
# daily-rebalanced constant-mix portfolio — it already assumes you sell whatever rallied and
# buy whatever fell, every single day. But a weights-times-returns model has no units and no
# cost basis, so the REALISED P&L that rebalancing crystallises, and the turnover it costs,
# are both invisible there. Here they are the subject.
#
# Two deliberate differences from hedge_sleeves.py:
#   * data comes from a cached parquet, not a live get_equity_data call — runs with TWS
#     closed and gives the same bars every time;
#   * tickers and weights come from config/asset_universe.yaml, not literals in this file.
#
# hedge_sleeves.py itself is left untouched.

import importlib
import pathlib
import sys

import numpy as np
import pandas as pd
from IPython.display import display

# research/ is not on the package path (it is not part of the installed portutils), so add
# the repo's src/ before importing anything from portutils or pipelines.
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import portutils
# Import the submodules by name (not just names out of them) so the importlib.reload calls
# in the next cell have something bound to reload — same import shape as hedge_sleeves.py.
from portutils.viz import panel, dash_timeseries_app
from portutils.viz.panel import PanelBuilder
from portutils.utils import config as cfg

# %% Reload custom packages
# Reload custom packages
#
# Reload modules during development so edits to the library land without restarting the
# kernel — same pattern as hedge_sleeves.py.
importlib.reload(portutils.viz.dash_timeseries_app)
from portutils.viz.dash_timeseries_app import make_level_figure

import pipelines.rebalance_study as study
importlib.reload(study)

# %% 2. Load the cached price panel
# 2. Load the cached price panel
# Built by: python src/pipelines/cache_prices.py --portfolio spy_kmlm --from-csv
# Real IBKR daily closes, persisted to parquet so this study is reproducible offline.
PORTFOLIO = "spy_kmlm"
CAPITAL = 1_000_000.0

prices = pd.read_parquet(REPO / "data" / "processed" / f"prices_{PORTFOLIO}.parquet")

# Target weights + roles from the YAML catalogue — the single source of truth for what
# counts as a hedge and what counts as beta.
weights = cfg.portfolio_weights(PORTFOLIO)
weights = {t: w for t, w in weights.items() if t in prices.columns}
roles = {t: cfg.asset_role(t) for t in weights}

print(f"{len(prices)} bars, {prices.index[0].date()} to {prices.index[-1].date()}")
print("weights:", weights)
print("roles:  ", roles)
display(prices.head())

# %% 3. The hedge_sleeves.py view — constant weights x returns
# 3. The hedge_sleeves.py view — constant weights x returns
# This reproduces exactly what hedge_sleeves.py does, on this two-asset book: a constant
# weight vector handed to PanelBuilder.attribution. Note what it CAN show (each leg's
# contribution to return) and what it cannot: there is no realised/unrealised split here,
# because there are no units and no cost basis anywhere in this calculation.

panel = PanelBuilder(df_all=prices)
rets_index = prices.pct_change().dropna().index
const_weights = pd.DataFrame({t: w for t, w in weights.items()}, index=rets_index)

# Each ticker is its own "sector" so attribution rolls up per-ticker with no grouping —
# the singleton-list trick from hedge_sleeves.py.
STRATEGY_MAP = {t: [t] for t in weights}

sector_contrib, portfolio_daily, sector_cum, portfolio_series_change, portfolio_series = \
    panel.attribution(const_weights, STRATEGY_MAP)

plot_df = sector_cum.copy()
plot_df["overall"] = portfolio_series_change
plot_df["time"] = plot_df.index
plot_df = plot_df.reset_index(drop=True)
display(plot_df.tail())

# %% Colour and label maps
# Colour and label maps
# KMLM keeps its hedge_sleeves.py yellow and SPY its steel blue, so charts read the same
# across the repo. Book colours are shared with the pipeline for the same reason.
TICKER_COLOURS = study.TICKER_COLOURS
BOOK_COLOURS = study.BOOK_COLOURS
SPLIT_COLOURS = study.SPLIT_COLOURS

colour_map = {t: TICKER_COLOURS.get(t, "#6baed6") for t in weights}
label_map = {t: f"{t} - {roles[t]} ({weights[t]:.0%})" for t in weights}

# %% 4. Plot the constant-weight return attribution (the hedge_sleeves.py chart)
# 4. Plot the constant-weight return attribution (the hedge_sleeves.py chart)
fig_const = make_level_figure(
    plot_df,
    cols_of_interest=list(weights),
    reindex=False,
    stack_mode="stack_split_sign",
    show_overall_line=True,
    overall_col="overall",
    overall_label="Portfolio (constant weights = implicit daily rebalance)",
    overall_colour="white",
    colour_map=colour_map,
    label_map=label_map,
    auto_colour_map=False,
    figure_title="Return attribution - constant weights (the hedge_sleeves.py model)",
    fig_height=600,
    font_color="#e0e0e0",
    show_plotly_stack_mode_buttons=False,
    x_tick_label_mode="year_month",
    close_hour=99,
)
fig_const.show()

# %% 5. Run the three books through the fill-based accounting engine
# 5. Run the three books through the fill-based accounting engine
# Three books, three policies:
#   drift        buy the target book once, never trade again -> realised P&L exactly zero
#   mix          rebalance to target every bar -> the constant-weight assumption made real
#   mix+overlay  the same daily rebalance PLUS the discretionary drawdown-cut rule
#
# Same starting capital and same target weights for all three; only the rule set differs,
# so any difference in the result is attributable to policy rather than to sizing.

frames, sims, overlay = study.run_books(
    prices, weights, CAPITAL,
    dd_trigger=0.03, cut_frac=0.5, recovery_frac=0.5, max_rotations=3,
    slippage_k=0.0,
)

summary = study.summarise(frames, CAPITAL)
display(summary.round(2))

# %% 6. Who did the crystallising?
# 6. Who did the crystallising?
#
# The table the whole exercise is for. If the rebalancing mechanic works the way the
# arithmetic says, the hedge leg banks real money by being sold into its own rallies —
# without any drawdown trigger, threshold or discretion.
display(study.realised_by_ticker(frames, weights).round(2))

# %% Does the hedge really bank gains when beta falls?
# Does the hedge really bank gains when beta falls?
#
# Direct test rather than assumption: isolate the bars where SPY fell AND KMLM rose, and
# check how much of KMLM's realised P&L was booked on exactly those bars.
r = prices.pct_change()
mix = frames["mix"]
kmlm_booked = mix["KMLM_realised_pnl"].astype(float).diff()
mask = (r["SPY"] < 0) & (r["KMLM"] > 0)

print(f"bars where SPY fell and KMLM rose: {int(mask.sum())} of {len(r)}")
print(f"KMLM realised booked on those bars: {kmlm_booked[mask].sum():,.0f}")
print(f"KMLM realised booked over the whole window: {kmlm_booked.sum():,.0f}")
print(f"share of the hedge's realised P&L earned on those bars: "
      f"{100 * kmlm_booked[mask].sum() / kmlm_booked.sum():.0f}%")

# %% 7. Return attribution on ACTUAL weights
# 7. Return attribution on ACTUAL weights
#
# The upgrade over cell 4. Rather than assuming a constant weight vector (or using
# PanelBuilder.simulate_weights, which hardcodes 1/N and so cannot express 60/40), feed the
# attribution the weight path the book REALLY had: mark value over equity, straight out of
# the accounting. drift's weights visibly wander; mix's stay pinned to target.
for name, fig in study.fig_attribution(frames, weights, prices).items():
    fig.show()

# %% 8. Equity paths
# 8. Equity paths

study.fig_equity(frames).show()

# %% 9. Realised vs unrealised, per book
# 9. Realised vs unrealised, per book
#
# The picture that makes "same total P&L, different bucket" obvious: drift is pure
# unrealised, while mix carries a growing realised base underneath it. Green is banked and
# no longer at risk; amber is still exposed to the next drawdown.
for name, fig in study.fig_split(frames).items():
    fig.show()

# %% 10. Cumulative realised P&L by ticker (the rebalanced book)
# 10. Cumulative realised P&L by ticker (the rebalanced book)

study.fig_realised_by_ticker(frames, weights, book="mix").show()

# %% 11. Weight drift vs target
# 11. Weight drift vs target
#
# WHY mix trades and drift does not, in one chart per leg.
for t in weights:
    study.fig_weights(frames, weights, t).show()

# %% 12. What the rebalancing costs
# 12. What the rebalancing costs
#
# A daily rebalance trades roughly 250 times a year. At zero slippage it looks free; this is
# where that stops being true. Always read the realised-P&L figure next to this one.
display(study.slippage_sweep(prices, weights, CAPITAL, [0.0, 0.0005, 0.001]).round(2))

print(f"\nturnover as a multiple of starting capital:")
print(summary["turnover_x_capital"].round(2).to_string())

# %% 13. Did the discretionary overlay earn its complexity?
# 13. Did the discretionary overlay earn its complexity?
#
# mix+overlay is the daily rebalance PLUS the drawdown-cut rule. Compare it against mix on
# its own — and read the turnover column while doing so.
if overlay.events:
    display(pd.DataFrame(overlay.events, columns=["ts", "event", "equity", "drawdown"]))
else:
    print("the drawdown trigger never fired on this window")

display(summary.loc[["mix", "mix+overlay"]].round(2))

# %%
