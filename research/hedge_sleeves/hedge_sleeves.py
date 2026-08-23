# %% 1. Import libraries

# 1. Import libraries
# Imports

import importlib
import os
import pathlib
import pandas as pd
from IPython.display import display
import portutils
from portutils.viz import panel, dash_timeseries_app
from portutils.viz.panel import PanelBuilder, SECTOR_MAP, SECTOR_COLORS, LINE_STYLES
# from portutils.ingestion import ibkr_requests
# from portutils.ingestion.ibkr_requests import IBApp, get_account_data, OrderApp
from portutils.ingestion.ibkr_requests import get_equity_data


# %% Reload custom packages

# Reload custom package

# Reload module during development (re-import names after reload)
# importlib.reload(ibkr_requests)
# from portutils.ingestion.ibkr_requests import IBApp, get_account_data, OrderApp, connection_status

# Reload the dash_timeseries_app module and import the relevant functions and classes for building the figure ---
importlib.reload(portutils.viz.dash_timeseries_app)
from portutils.viz.dash_timeseries_app import _build_level_figure, _normalize_config, BaseFigureConfig, TimeSeriesFigureConfig

importlib.reload(portutils.ingestion.ibkr_requests)
from portutils.ingestion.ibkr_requests import get_equity_data
# %% Load in the data for a combined diversified portfolio

# Load in the data for a combined diversified portfolio

SYMBOLS = (
    "SPY",   # SPDR S&P 500 ETF — broad market benchmark / beta reference
    "ACWX",  # iShares MSCI ACWI ex US ETF — core broad global equities outside the US (all-cap, 47 countries); largest single position
    "FTXL",  # First Trust Nasdaq Semiconductor ETF — smart-beta semi: weights on value+vol+growth,
             # underweights mega-cap → mid-cap tilt; satellite growth engine
    "KMLM",  # KFA Mount Lucas Index Strategy ETF — managed futures / trend-following across commodities, fixed income, currencies
    "MNA",   # IQ Merger Arbitrage ETF — long announced-merger targets, short acquirers
    "RNR",   # RenaissanceRe Holdings Ltd — specialty reinsurance, property cat / casualty; a stock, not an ETF
    "JPM",   # JPMorgan Chase & Co. — largest US bank; financials sleeve proxy alongside RNR
    "FLSP",  # Franklin Liberty Systematic Style Premia ETF — multi-factor alternative risk premia (value, momentum, carry, low-vol)
    "CPER",  # United States Copper Index Fund — pure copper futures price exposure, no equity wrapper
    'DBB',  # iPath Bloomberg Aluminium Subindex Total Return ETN — aluminium futures exposure; replaces JJU which timed out on historical data fetch
    "DGRE",  # WisdomTree Emerging Markets Quality Dividend Growth Fund — EM quality dividend growth factor
    "EQLT",  # Xtrackers MSCI USA Quality ESG ETF — quality factor screen, low-volatility / high-ROIC tilt
    "DVYE",  # iShares Emerging Markets Dividend ETF — EM high-dividend income, broad EM exposure
    "QDIV",  # Global X S&P 500 Quality Dividend ETF — quality income blend, dividend screen on S&P 500
)



# Resolve the canonical data directory relative to this file — keeps the path
# portable regardless of the working directory when the script is executed.
current_file_path = pathlib.Path(__file__).parent
data_dir = str(current_file_path / "../../data/raw/market")

# Fetch combined close-price panel from IBKR TWS.
# output_format='combined' merges all symbol close series into one wide
# DataFrame (columns: Date + one per symbol) — same shape as the legacy CSV.
# output_dir is provided so the merged result is also persisted to
# portfolio_prices.csv, keeping the CSV in sync for offline/fallback use.
df = get_equity_data(
    symbols=list(SYMBOLS),
    skip_existing=False,
    duration="1 Y",
    output_dir=data_dir,
    output_format="combined",
    merged_filename="portfolio_prices.csv",
)



df.head()



# %% convert this prices data into returns data

# convert this prices data into returns data
#
# initialise an instance of our built-in panel builder class with this df as df_all

# panel = PanelBuilder(df_all=df)

df = df.sort_values("Date").reset_index(drop=True)

# Require the six core tickers to be non-null; metals, quality sleeves, and JPM
# may have shorter histories and are allowed to backfill with NaN (pct_change → 0.0).
df = df.dropna(subset=["SPY", "ACWX", "FTXL", "KMLM", "MNA", "RNR"]).reset_index(drop=True)

dates = df["Date"].reset_index(drop=True)
# load in data for:
# the broad market (SPY)
spy_prices = df["SPY"].astype(float).reset_index(drop=True)
# then the ETFs/stocks we will use in the portfolio:
# - ACWX: iShares MSCI ACWI ex US ETF (broad global equities ex-US, 47 countries) — core position, largest weight
acwx_prices = df["ACWX"].astype(float).reset_index(drop=True)
# - FTXL: First Trust Nasdaq Semiconductor ETF (smart-beta semi, mid-cap tilt) — satellite growth leg
ftxl_prices = df["FTXL"].astype(float).reset_index(drop=True)
# - KMLM: KFA Mount Lucas Index Strategy ETF (managed futures / trend-following across commodities, fixed income, currencies)
kmlm_prices = df["KMLM"].astype(float).reset_index(drop=True)
# - MNA: IQ Merger Arbitrage ETF (long announced-merger targets, short acquirers)
mna_prices = df["MNA"].astype(float).reset_index(drop=True)
# - RNR: RenaissanceRe Holdings Ltd (specialty reinsurance — property cat / casualty; a stock, not an ETF)
rnr_prices = df["RNR"].astype(float).reset_index(drop=True)
# - JPM: JPMorgan Chase & Co. (largest US bank; financials sleeve)
jpm_prices = df["JPM"].astype(float).reset_index(drop=True)
# --- Metals sleeve (20% gross, split evenly) ---
# - CPER: United States Copper Index Fund (pure copper futures, no equity)
cper_prices = df["CPER"].astype(float).reset_index(drop=True)
# - DBB: Invesco DB Base Metals Fund (aluminium + copper + zinc futures basket — replaces thin ALUM/JJU ETNs)
dbb_prices = df["DBB"].astype(float).reset_index(drop=True)
# --- Quality / EM income sleeve (20% gross, split evenly across four tickers) ---
# - DGRE: WisdomTree EM Quality Dividend Growth
dgre_prices = df["DGRE"].astype(float).reset_index(drop=True)
# - EQLT: Xtrackers MSCI USA Quality ESG ETF
eqlt_prices = df["EQLT"].astype(float).reset_index(drop=True)
# - DVYE: iShares EM Dividend ETF (high dividend, EM)
dvye_prices = df["DVYE"].astype(float).reset_index(drop=True)
# - QDIV: Global X S&P 500 Quality Dividend ETF (quality income)
qdiv_prices = df["QDIV"].astype(float).reset_index(drop=True)


asset_returns = pd.DataFrame(
    {
        "ACWX": acwx_prices.pct_change(),
        "FTXL": ftxl_prices.pct_change(),
        "KMLM": kmlm_prices.pct_change(),
        "MNA":  mna_prices.pct_change(),
        "RNR":  rnr_prices.pct_change(),
        "JPM": jpm_prices.pct_change(),
        # Metals sleeve — use fillna(0) so gaps in thin ETNs don't poison the portfolio return
        "CPER": cper_prices.pct_change(),
        "DBB":  dbb_prices.pct_change(),
        # Quality / EM income sleeve
        "DGRE": dgre_prices.pct_change(),
        "EQLT": eqlt_prices.pct_change(),
        "DVYE": dvye_prices.pct_change(),
        # "QDIV": qdiv_prices.pct_change(),
    }
).fillna(0.0)

asset_returns.head()

# %% build a capital efficient portfolio with these ETFs, with return contributions

# build a capital efficient portfolio using the following weights:
# Core broad global equities ex-US — largest single position, anchors the portfolio
ACWX_WEIGHT = 0.30
# Semi growth satellite — equal weight to ACWX but higher-beta, higher-conviction tilt
FTXL_WEIGHT = 0.30
# Managed futures / trend-following — reduced to 10% to free room for new sleeves
KMLM_WEIGHT = 0.50
MNA_WEIGHT  = 0.20
# RNR reduced to 10%; remaining 10% allocated to JPMorgan financials (same thematic sleeve)
RNR_WEIGHT  = 0.10
JPM_WEIGHT = 0.10
# Metals sleeve — 10% gross split evenly between copper futures and aluminium
CPER_WEIGHT = 0.05
DBB_WEIGHT  = 0.05
# Quality / EM income sleeve — 20% gross split evenly across four tickers
DGRE_WEIGHT = 0.05
EQLT_WEIGHT = 0.05
DVYE_WEIGHT = 0.05
QDIV_WEIGHT = 0.05

return_contributions_df = pd.DataFrame(
    {
        "ACWX": ACWX_WEIGHT * asset_returns["ACWX"],
        "FTXL": FTXL_WEIGHT * asset_returns["FTXL"],
        "KMLM": KMLM_WEIGHT * asset_returns["KMLM"],
        "MNA":  MNA_WEIGHT  * asset_returns["MNA"],
        "RNR":  RNR_WEIGHT  * asset_returns["RNR"],
        "JPM": JPM_WEIGHT * asset_returns["JPM"],
        "CPER": CPER_WEIGHT * asset_returns["CPER"],
        "DBB":  DBB_WEIGHT  * asset_returns["DBB"],
        "DGRE": DGRE_WEIGHT * asset_returns["DGRE"],
        "EQLT": EQLT_WEIGHT * asset_returns["EQLT"],
        "DVYE": DVYE_WEIGHT * asset_returns["DVYE"],
        "QDIV": QDIV_WEIGHT * asset_returns["QDIV"],
    }
).fillna(0.0)

return_contributions_df.head()

# %% convert to cumulative return contributions via panel attribution API

# convert to cumulative return contributions via panel attribution API

# Build a prices DataFrame with DatetimeIndex, restricted to the analysis window
# so that cumulative contributions start at zero at the window open date.
prices_df = df.set_index("Date")[["ACWX", "FTXL", "KMLM", "MNA", "RNR", "JPM", "CPER", "DBB", "DGRE", "EQLT", "DVYE", "QDIV"]]
prices_df = prices_df[prices_df.index >= '2026-01-01']

# Instantiate PanelBuilder with the twelve strategy price series.
# PanelBuilder._daily_returns calls pct_change() internally, so we pass prices not returns.
# (That method is now a thin wrapper over portutils.analysis.returns.daily_returns — the
# definition moved out of the plotting module, but the behaviour here is unchanged.)
panel_hedge = PanelBuilder(df_all=prices_df)

# Build a constant-weight DataFrame aligned to the daily-returns index.
# We bypass simulate_weights (which produces equal 1/N drifting weights) because
# our strategy weights are fixed and asymmetric — and may exceed 1.0 in gross
# exposure (levered portfolio).
rets_index = prices_df.pct_change().dropna().index
weights = pd.DataFrame(
    {
        "ACWX": ACWX_WEIGHT,
        "FTXL": FTXL_WEIGHT,
        "KMLM": KMLM_WEIGHT,
        "MNA":  MNA_WEIGHT,
        "RNR":  RNR_WEIGHT,
        "JPM": JPM_WEIGHT,
        "CPER": CPER_WEIGHT,
        "DBB":  DBB_WEIGHT,
        "DGRE": DGRE_WEIGHT,
        "EQLT": EQLT_WEIGHT,
        "DVYE": DVYE_WEIGHT,
        "QDIV": QDIV_WEIGHT,
    },
    index=rets_index,
)

# Each strategy is its own "sector" — map each column to a singleton list so that
# attribution rolls up per-strategy contributions independently (no grouping).
STRATEGY_MAP = {
    "ACWX": ["ACWX"],
    "FTXL": ["FTXL"],
    "KMLM": ["KMLM"],
    "MNA":  ["MNA"],
    "RNR":  ["RNR"],
    "JPM": ["JPM"],
    "CPER": ["CPER"],
    "DBB":  ["DBB"],
    "DGRE": ["DGRE"],
    "EQLT": ["EQLT"],
    "DVYE": ["DVYE"],
    "QDIV": ["QDIV"],
}

# Run attribution: returns cumulative wealth-index-style contribution per strategy
# (sector_cum) and the overall portfolio excess return over base-100 (portfolio_series_change).
sector_contrib, portfolio_daily, sector_cum, portfolio_series_change, portfolio_series = \
    panel_hedge.attribution(weights, STRATEGY_MAP)

# Shape for make_level_figure: add 'overall' and 'time' columns, then drop the DatetimeIndex.
plot_df = sector_cum.copy()
plot_df['overall'] = portfolio_series_change
plot_df['time'] = plot_df.index
plot_df = plot_df.reset_index(drop=True)

plot_df.head()

# %% Plot the return attribution for the combined diversified portfolio

# Plot the return attribution for the combined diversified portfolio
#
# Color scheme:
#   ACWX         — steel blue      (core position — darkest/most prominent blue)
#   FTXL         — sky blue        (satellite semi growth — lighter blue, same family)
#   KMLM         — yellow          (managed futures — grouped visually with metals sleeve)
#   CPER, DBB    — burnt/amber orange (metals sleeve: copper futures + base metals)
#   MNA          — crimson red     (merger arbitrage)
#   RNR, JPM     — purples         (reinsurance + JPMorgan financials — same thematic sleeve)
#   DGRE, EQLT, DVYE, QDIV — greens (quality / EM income sleeve)

chosen_colour_map = {
    # Core global ex-US position — steel blue (dominant, most prominent)
    "ACWX": "#1f77b4",
    # Semi growth satellite — sky / cornflower blue (same family, lighter)
    "FTXL": "#6baed6",
    # Managed futures — yellow (visually adjacent to metals sleeve)
    "KMLM": "#f0c040",
    # Metals sleeve — burnt orange + golden amber
    "CPER": "#e06c1a",   # burnt orange  (copper futures)
    "DBB":  "#f5a623",   # golden amber  (aluminium ETN)
    # Merger arbitrage — crimson red
    "MNA":  "#d62728",
    # Financials / reinsurance sleeve — two purples
    "RNR":  "#9467bd",   # medium purple (RenaissanceRe)
    "JPM": "#c5b0d5",   # light lavender (JPMorgan Financials)
    # Quality / EM income sleeve — four shades of green
    "DGRE": "#2ca02c",   # forest green      (EM quality dividend growth)
    "EQLT": "#3cb371",   # medium sea green  (quality factor)
    "DVYE": "#228b22",   # dark green        (EM high dividend)
    "QDIV": "#8fbc8f",   # light sage green  (quality income)
}

# Human-readable legend labels for each ticker — shown in the plot legend and tooltips
chosen_label_map = {
    "ACWX": "ACWX — Global Eq ex-US (30%)",
    "FTXL": "FTXL — Nasdaq Smart Semi (30%)",
    "KMLM": "KMLM — Managed Futures (10%)",
    "MNA":  "MNA — Merger Arbitrage (20%)",
    "RNR":  "RNR — RenaissanceRe Reinsurance (10%)",
    "JPM": "JPM — JPMorgan Financials (10%)",
    "CPER": "CPER — Copper Futures (10%)",
    "DBB":  "DBB — Aluminium ETN (10%)",
    "DGRE": "DGRE — EM Quality Div Growth (5%)",
    "EQLT": "EQLT — Quality Factor (5%)",
    "DVYE": "DVYE — EM High Dividend (5%)",
    "QDIV": "QDIV — Quality Income (5%)",
}

from portutils.viz.dash_timeseries_app import make_level_figure

fig = make_level_figure(
      plot_df,
      cols_of_interest=["ACWX", "FTXL", "KMLM", "MNA", "RNR", "JPM", "CPER", "DBB", "DGRE", "EQLT", "DVYE", "QDIV"],
      reindex=False,
      stack_mode='stack_split_sign',
      show_overall_line=True,
      overall_col='overall',
      overall_label='Portfolio (EW, Quarterly Rebal)',
      overall_colour='white',
      colour_map=chosen_colour_map,
      label_map=chosen_label_map,
      auto_colour_map=False,
      figure_title='Strategies Return Attribution',
      fig_height=700,
      font_color='#e0e0e0',
      show_plotly_stack_mode_buttons=False,
      x_tick_label_mode='year_month',
      close_hour=99,
  )
fig.show()

# %%