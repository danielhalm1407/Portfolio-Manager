# %% 1. Import libraries

# 1. Import libraries
# Imports

import pathlib
import sys

import pandas as pd
import plotly.graph_objects as go

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from pipelines.coverage_report import build_coverage_report          # noqa: E402
from portutils.analysis import returns                                # noqa: E402
from portutils.ingestion.history import fetch_ibkr_long_history       # noqa: E402
from portutils.strategies.instruments.vol import synthetic_iv_surface  # noqa: E402
from portutils.viz.dash_timeseries_app import LevelDashApp, TimeSeriesAppConfig  # noqa: E402
from portutils.viz.panel import PanelBuilder                          # noqa: E402

PROCESSED = REPO / "data" / "processed"

# %% 2. What this script is

# 2. What this script is

# ============================================================================
# PHASE 11 DATA INSPECTION — piecewise cells over what 11-01 actually cached.
# Run cells top to bottom in an interactive Python session (VS Code / Jupyter's
# "# %%" cell markers). Each cell is independent enough to re-run on its own
# once the imports above have run once.
#
# What's here:
#   Cell 3 — the coverage report, printed inline (no need to open COVERAGE.md)
#   Cell 4 — fetch + cache the SPY/QQQ IMPLIED-VOL history (needs TWS open,
#            ONE-TIME network cost — cached to parquet after the first run)
#   Cell 5 — compare that real IV level against vol.py's `base=0.16` assumption
#   Cell 6 — a quick static reindexed comparison of SPY vs QQQ (no Dash server)
#   Cell 7 — the actual Dash app: a live, browser-based, re-indexable comparison
#            of the long-history series (this is PanelBuilder's data, served
#            through LevelDashApp rather than a notebook plot)
#
# IMPORTANT — what "IV" means here. Cell 4 pulls IBKR's OPTION_IMPLIED_VOLATILITY
# for the SPY and QQQ TICKERS THEMSELVES, not the VIX index. IBKR computes this
# per-underlying from its own option chain; it is NOT VIX (which is specifically
# an SPX-option-derived index IBKR would only serve as its own contract, e.g.
# `contract('VIX', sec_type='IND', exchange='CBOE')` — untested here, out of
# scope for this pass). For a "how does the vol level compare" question this is
# actually the MORE relevant series: it is SPY's own priced vol, and QQQ's own,
# not a levered/differently-weighted SPX proxy.
# ============================================================================

# %% 3. Coverage report, printed inline

# 3. Coverage report, printed inline

# Same two files 11-01 wrote to disk — this cell just avoids having to alt-tab to
# COVERAGE.md. If this cell errors with FileNotFoundError, run
# `python src/pipelines/cache_prices.py --portfolio long_history --long-history`
# first (needs TWS open).
prices = pd.read_parquet(PROCESSED / "prices_long_history.parquet")
provenance = pd.read_csv(PROCESSED / "provenance_long_history.csv", index_col=0, parse_dates=True)
print(build_coverage_report(prices, provenance))

# %% 4. Fetch (once) and cache SPY / QQQ implied-vol history

# 4. Fetch (once) and cache SPY / QQQ implied-vol history

# Needs TWS open on 7497 ONLY the first time this cell runs for a given machine —
# after that, the cached parquet is read straight off disk. Uses the SAME paginated
# fetcher as the price history (history.fetch_ibkr_long_history), just pointed at
# whatToShow='OPTION_IMPLIED_VOLATILITY' instead of 'TRADES'. This is exactly the
# probe path 11-01 Task 1 used to find IV reaches back to 2006-01-09.
iv_path = PROCESSED / "iv_long_history.parquet"
if iv_path.exists():
    iv = pd.read_parquet(iv_path)
    print(f"loaded cached {iv_path.name}")
else:
    from portutils.ingestion.ibkr_requests import IBApp, connect_ib

    app = IBApp()
    connect_ib(app, host="127.0.0.1", port=7497, client_id=601)
    try:
        spy_iv = fetch_ibkr_long_history("SPY", app, what_to_show="OPTION_IMPLIED_VOLATILITY")
        qqq_iv = fetch_ibkr_long_history("QQQ", app, what_to_show="OPTION_IMPLIED_VOLATILITY")
    finally:
        app.disconnect()
    # IBKR's OPTION_IMPLIED_VOLATILITY bars are ALREADY a decimal (checked directly against
    # a live request: 2026-09-18's SPY close was 0.117471) — i.e. the exact same convention
    # as vol.py's `base=0.16` (0.16 = 16%). No /100 needed; an earlier draft of this cell
    # divided by 100 and produced nonsense (~0.0017), which is what a raw eyeball of the
    # numbers against `base` immediately caught.
    iv = pd.concat({"SPY": spy_iv["SPY"], "QQQ": qqq_iv["QQQ"]}, axis=1).sort_index()
    iv.index.name = "ts"
    iv.to_parquet(iv_path)
    print(f"wrote {iv_path}")

print(iv.describe())

# %% 5. Compare real IV against vol.py's `base=0.16`

# 5. Compare real IV against vol.py's `base=0.16`

# `base` in synthetic_iv_surface is the ATM vol LEVEL — at strike == spot_ref (log_moneyness=0)
# and tau -> 0, the skew/smile/term terms all vanish and the function returns exactly `base`.
# So the fairest real-world comparison is SPY/QQQ's OWN implied vol, not VIX (which is an
# SPX-derived, 30-day-CONSTANT-MATURITY index — a related but different number).
last_5y_cutoff = iv.index.max() - pd.Timedelta(days=5 * 365)
spy_iv_stats = {
    "current": float(iv["SPY"].dropna().iloc[-1]),
    "mean_full_history": float(iv["SPY"].mean()),
    "mean_last_5y": float(iv.loc[iv.index >= last_5y_cutoff, "SPY"].mean()),
}
qqq_iv_stats = {
    "current": float(iv["QQQ"].dropna().iloc[-1]),
    "mean_full_history": float(iv["QQQ"].mean()),
    "mean_last_5y": float(iv.loc[iv.index >= last_5y_cutoff, "QQQ"].mean()),
}
print("SPY implied vol vs. vol.py base=0.16:", spy_iv_stats)
print("QQQ implied vol vs. vol.py base=0.16:", qqq_iv_stats)
# A synthetic_iv_surface() call at strike == spot (ATM) reproduces `base` exactly, confirming
# the two numbers are directly comparable rather than measuring different things.
print("synthetic_iv_surface ATM check:", float(synthetic_iv_surface(100, 1e-6, 100)))

# %% 5b. Implied-vol history, plotted (SPY vs QQQ, own separate figure)

# 5b. Implied-vol history, plotted (SPY vs QQQ, own separate figure)

# Raw levels, not reindexed — vol is already on a common 0-1 scale across symbols, unlike
# price. The 0.16 reference line is vol.py's `base` default, so the eye can compare
# directly against what the pricer currently assumes.
iv_fig = go.Figure()
for col in iv.columns:
    iv_fig.add_trace(go.Scatter(x=iv.index, y=iv[col], mode="lines", name=col))
iv_fig.add_hline(y=0.16, line_dash="dash", line_color="white",
                  annotation_text="vol.py base=0.16", annotation_position="top left")
iv_fig.update_layout(title="SPY / QQQ implied vol history (IBKR OPTION_IMPLIED_VOLATILITY)",
                      yaxis_title="implied vol", yaxis_tickformat=".0%",
                      xaxis_tickformat="%b %Y", template="plotly_dark")
iv_fig.show()

# %% 6. Quick static reindexed comparison (no Dash server)

# 6. Quick static reindexed comparison (no Dash server)

# Ragged load (SPY predates QQQ by ~6 years) + anchor="common" so both series read exactly
# 100 on the first date BOTH have data, rather than one series silently going all-NaN (the
# bug anchor="first_row" — the old default — would have hit here).
pb = PanelBuilder(df_all=prices, tickers=list(prices.columns), ragged=True)
common_reindexed = returns.normalise(pb.df_all, base=100.0, anchor="common")

fig = go.Figure()
for col in common_reindexed.columns:
    fig.add_trace(go.Scatter(x=common_reindexed.index, y=common_reindexed[col], mode="lines", name=col))
fig.update_layout(title="SPY vs QQQ — reindexed to 100 at their common start date",
                   xaxis_tickformat="%b %Y", template="plotly_dark")
fig.show()

# %% 7. Live Dash app — interactive re-indexable comparison

# 7. Live Dash app — interactive re-indexable comparison

# Blocks the terminal and opens http://127.0.0.1:8050 . Ctrl+C to stop.
# To compare a DIFFERENT reindex policy, change `reindex=` below and re-run this cell —
# accepts True / False / "common" / "self" (see TimeSeriesFigureConfig's docstring / the
# analysis.returns anchor-policy comment block for what each means).
dash_df = prices.reset_index().rename(columns={"ts": "time"})
cfg = TimeSeriesAppConfig(
    cols_of_interest=["SPY", "QQQ"],
    reindex="common",
    figure_title="Long-history SPY vs QQQ (reindexed at common start)",
    title="Phase 11 data inspection",
    # Matches the "%b %Y" look of the two static figures above. This is the escape-hatch
    # param, not a real fix: the Dash app's x-axis is an integer row-position axis with
    # hand-rendered tick text (not a native Plotly date axis, unlike the two figures
    # above), so it won't re-adapt its format on zoom the way a real date axis would —
    # see the 2026-09-20 Deferred Issue in STATE.md.
    x_tick_label_format="%b %Y",
)
app_obj = LevelDashApp(dash_df, cfg)
app_obj.run()
