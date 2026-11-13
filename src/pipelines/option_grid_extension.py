"""
13-02.2 — MORE OBSERVATIONS FOR THE MONEYNESS OPTIMISATION, AND A 2026 ILLUSTRATION.

WHAT THIS ADDS TO 13-02.1 (approved by the user 2026-11-12, a subset of 13-02.2-PLAN.md)
-------------------------------------------------------------------------------------------
* ROUTE B — the strike axis extended below 0.80x, down to 0.70x. Only the 28 new combinations
  (four strikes x seven triggers) are simulated; the 63 persisted runs in
  ``outputs/option_grid_budget_guarded/`` are reused unchanged.
* ROUTE A — twenty RANDOM windows, overlap allowed, scored on all 91 combinations by slicing the
  persisted full-history runs. No window is re-simulated. The six indicative windows stay as the
  narrative set of the published page; the random set is its own section and its own fits.
* A 2026 ILLUSTRATIVE WINDOW, from the first trading day of 2026 to the last cached bar, which
  covers the drawdown after the Iran war began in February. Run FRESH from the window's first bar
  (a hedge opened on day one), for SPY and for QQQ as the tech underlying, at three strikes only
  (0.95x, 0.80x, 0.70x) so the figure stays legible.
* AN ENERGY DIVERSIFIER on the same window: the premium the put programme spends at each opening
  is instead used to buy XLE (the S&P 500 energy sector ETF). Same budget, spent on a diversifying
  leg rather than on insurance. A refiners ETF is the intended diversifier and is parked in the plan.

EVERYTHING HERE IS IN-SAMPLE, and every fill is at the model mark (no bid/ask), exactly as in
option_grid_sweep.py. The diversifier comparison is illustrative over one window, not a result.

Run as a script:  ``python -m pipelines.option_grid_extension``  (simulate, score, persist)
The published page reads what this persists:  ``python -m pipelines.option_grid_sweep --publish-only``
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from portutils.strategies import scoring, surface
from portutils.viz import theme

import pipelines.option_grid_figures as gridfig
import pipelines.option_grid_sweep as sweep
from pipelines.option_monetisation_batch import load_window

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ============================================================================
# THE DESIGN
# ============================================================================

# Route B: the four strikes below the published grid's 0.80x edge, a 0.025 step like the rest of it.
EXTENDED_STRIKES = (0.700, 0.725, 0.750, 0.775)
# The full 13-strike axis the random windows are scored on.
EXTENDED_GRID = EXTENDED_STRIKES + tuple(sweep.MONEYNESS_GRID)

# Route A: twenty windows, overlap allowed (the user's choice for this first pass).
N_RANDOM_WINDOWS = 20

# The 2026 window. The end is the last cached bar rather than a fixed date, so a later data refresh
# extends it without an edit here.
WINDOW_2026_START = "2026-01-02"
# Three strikes at most on an illustrative figure, so a reader is not faced with a wall of lines:
# the budget strike, the old edge of the grid and the new one.
ILLUSTRATIVE_STRIKES = (0.95, 0.80, 0.70)
# The tech underlying. QQQ prices and IBKR QQQ implied vol are already cached beside SPY's.
TECH_UNDERLYING = "QQQ"

# The diversifier. XLE is the S&P 500 energy sector ETF, the most widely held energy benchmark.
# A refiners ETF (CRAK) is the intended comparison and is parked in 13-02.2-PLAN.md.
DIVERSIFIER = "XLE"
ENERGY_RAW = PROJECT_ROOT / "data" / "raw" / "market" / "yfinance_XLE_daily.csv"
ENERGY_PATH = PROJECT_ROOT / "data" / "processed" / "prices_energy.parquet"

# Where this plan's runs land. Its OWN directory, so 13-02.1's guarded sweep stays byte-identical.
EXT_DIR = PROJECT_ROOT / "outputs" / "option_grid_extended"


# ============================================================================
# DATA — the energy series
# ============================================================================

def cache_energy(start="2005-12-01", end="2026-09-19"):
    """Download XLE daily closes from Yahoo Finance and cache them.

    Total-return closes (``auto_adjust=True``), because the diversifier is held through dividends
    and a price-only series would understate it by roughly the ETF's yield. The raw download is
    written to ``data/raw/`` as received and the tidy series to ``data/processed/``, keeping raw
    data append-only.
    """
    import yfinance as yf
    raw = yf.download(DIVERSIFIER, start=start, end=end, auto_adjust=True, progress=False)
    ENERGY_RAW.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(ENERGY_RAW)
    close = raw["Close"]
    # yfinance returns a one-column frame keyed by ticker; reduce it to a named series.
    close = close[DIVERSIFIER] if isinstance(close, pd.DataFrame) else close
    out = close.rename(DIVERSIFIER).to_frame()
    out.index = pd.to_datetime(out.index).tz_localize(None)
    ENERGY_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(ENERGY_PATH)
    return out[DIVERSIFIER]


def load_energy():
    """The cached XLE series, downloading it once if it is not there yet."""
    if not ENERGY_PATH.exists():
        return cache_energy()
    return pd.read_parquet(ENERGY_PATH)[DIVERSIFIER]


# ============================================================================
# ROUTE B — the 28 new strikes, simulated once over the full history
# ============================================================================

def sweep_new_strikes(persist=True):
    """Run the four new strikes against every trigger, persisting the same three artefacts as
    13-02.1. Returns ``{label: run_config result}``."""
    combos = sweep.grid_combinations(moneyness=EXTENDED_STRIKES)
    results = {}
    for d in ("series", "blotter", "rule_history"):
        (EXT_DIR / d).mkdir(parents=True, exist_ok=True)
    for floor, dd in combos:
        res = sweep.run_config(floor=floor, monetise_drawdown=dd, quiet=False)
        label = sweep.config_label(floor, dd)
        results[label] = res
        if persist:
            slug = sweep.config_slug(floor, dd)
            res["series"].to_parquet(EXT_DIR / "series" / f"{slug}.parquet")
            res["blotter"].to_csv(EXT_DIR / "blotter" / f"{slug}.csv", index=False)
            res["history"].to_csv(EXT_DIR / "rule_history" / f"{slug}.csv")
    if persist:
        # The config index, so a reload needs no filename parsing (same rule as load_sweep).
        pd.DataFrame([{"config": sweep.config_label(f, d), "floor": f, "monetise_drawdown": d}
                      for f, d in combos]).to_csv(EXT_DIR / "configs.csv", index=False)
    return results


def load_extended():
    """All 91 configurations from disk: 13-02.1's 63 plus this plan's 28. No simulator."""
    results, _ = sweep.load_sweep()
    configs = pd.read_csv(EXT_DIR / "configs.csv")
    for _, row in configs.iterrows():
        dd = None if pd.isna(row["monetise_drawdown"]) else float(row["monetise_drawdown"])
        slug = sweep.config_slug(float(row["floor"]), dd)
        results[row["config"]] = {
            "series": pd.read_parquet(EXT_DIR / "series" / f"{slug}.parquet"),
            "blotter": pd.read_csv(EXT_DIR / "blotter" / f"{slug}.csv", parse_dates=["ts"]),
            "params": {"floor": float(row["floor"]), "monetise_drawdown": dd}}
    return results


# ============================================================================
# ROUTE A — twenty random windows, scored by slicing
# ============================================================================

def score_random(results, spot, n=N_RANDOM_WINDOWS):
    """Draw the random windows and score every configuration on them. Returns
    ``(windows, scores)``."""
    windows = scoring.random_windows(spot, n=n)
    scores = sweep.tidy_scores(results, spot, windows=windows)
    return windows, scores


def fit_random(scores, trigger=sweep.LADDER_MONETISE_DRAWDOWN):
    """The pooled fits on the random set: the window-demeaned 2-D surface and the 1-D strike layers
    at a fixed trigger.

    Only the POOLED surface is fitted, not one per window: a two-month window can leave a strike's
    score flat across the trigger axis, and twenty per-window surfaces would add nothing the pooled
    demeaned surface does not already say. Blind-roll rows are dropped, as in fit_surfaces.
    """
    df = scores.dropna(subset=["score"])
    df = df[~df["blind_roll"]]
    bounds = ((float(df["floor"].min()), float(df["floor"].max())),
              (float(df["monetise_drawdown"].min()), float(df["monetise_drawdown"].max())))
    demeaned = df["score"] - df.groupby("window")["score"].transform("mean")
    fit = surface.fit_2d(df["floor"], df["monetise_drawdown"], demeaned)
    opt = surface.optimum(fit, bounds)
    points, layers = sweep.fit_strike_layers(scores, trigger)
    return fit, opt, bounds, points, layers


# ============================================================================
# THE 2026 WINDOW — fresh runs, SPY and QQQ, three strikes
# ============================================================================

def window_2026(symbol):
    """``(spot, iv)`` for ``symbol`` from the first 2026 bar to the last cached bar."""
    spot, iv = load_window(symbol)
    return spot.loc[WINDOW_2026_START:], iv.loc[WINDOW_2026_START:]


def illustrative_runs(symbol, trigger=sweep.LADDER_MONETISE_DRAWDOWN):
    """The three illustrative strikes over the 2026 window, opened on its first bar."""
    spot, iv = window_2026(symbol)
    return {sweep.config_label(f, trigger): sweep.run_config(
                floor=f, monetise_drawdown=trigger, spot=spot, iv=iv, underlying=symbol,
                quiet=True)
            for f in ILLUSTRATIVE_STRIKES}


def premium_spend(res, symbol):
    """Premium paid on each OPENING option fill of a run, summed per bar, as a positive amount in
    the run's price units. This is the hedging budget the diversifier spends instead."""
    fills = scoring.classify_option_fills(res["blotter"], symbol)
    opens = fills[fills["opening"]]
    return opens.groupby("ts")["notional"].sum().abs()


def diversifier_equity(spot, energy, spend):
    """The underlying held one-for-one, plus XLE bought with each premium payment on its date.

    Funded exactly as the put programme is: the same cash leaves on the same bars. The XLE bought
    is then held to the end of the window, because a diversifier is a position, not a policy with
    an exit. Returns the equity series in the underlying's price units, comparable to a put run's.
    """
    xle = energy.reindex(spot.index).ffill()
    spend = spend.reindex(spot.index).fillna(0.0)
    units = (spend / xle).cumsum()
    cash = -spend.cumsum()
    return spot + units * xle + cash


def _drawdown(series):
    # Fall from the running peak, as a negative fraction, for the drawdown panel.
    s = pd.Series(series).astype(float)
    return s / s.cummax() - 1.0


def fig_diversifier(symbol, spot, energy, runs, div_equity, budget_label, title=""):
    """Three stacked panels: equity, drawdown, and the diversifier's own path.

    Panel 1 compares holding the index alone, the put programme at each illustrative strike and the
    index plus the energy leg bought with the same budget. Only the budget strike is visible by
    default; the deeper two are a legend click away, so the figure opens on three lines.
    Panel 2 is each visible line's fall from its own running peak, which is the quantity the hedge
    exists to reduce. Panel 3 is XLE against the index, both rebased, which is what drives the
    diversifier line.
    """
    labels = list(runs)
    colours = gridfig.moneyness_colours(labels)
    div_colour = theme.CATEGORICAL[6]
    div_name = f"{symbol} + {DIVERSIFIER} (same budget)"
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25],
                        vertical_spacing=0.05,
                        subplot_titles=("Equity, Rebased to the First Bar",
                                        "Drawdown from Running Peak",
                                        f"{DIVERSIFIER} against {symbol}, Rebased"))
    lines = [(f"{symbol} only", spot, theme.CATEGORICAL[0], True)]
    lines += [(lab, runs[lab]["series"]["equity"], colours[lab],
               True if lab == budget_label else "legendonly") for lab in labels]
    lines += [(div_name, div_equity, div_colour, True)]
    for name, series, colour, visible in lines:
        eq = gridfig._rebase(series)
        fig.add_trace(go.Scatter(x=eq.index, y=eq.to_numpy(), name=name, legendgroup=name,
                                 visible=visible, line=dict(color=colour, width=1.8),
                                 hovertemplate="%{x|%Y-%m-%d}<br>" + name +
                                 " %{y:.3f}<extra></extra>"), row=1, col=1)
        dd = _drawdown(series)
        fig.add_trace(go.Scatter(x=dd.index, y=dd.to_numpy(), name=name, legendgroup=name,
                                 visible=visible, showlegend=False,
                                 line=dict(color=colour, width=1.4),
                                 hovertemplate="%{x|%Y-%m-%d}<br>" + name +
                                 " drawdown %{y:.1%}<extra></extra>"), row=2, col=1)
    seg = energy.reindex(spot.index).ffill()
    for name, series, colour in ((symbol, spot, theme.CATEGORICAL[0]),
                                 (DIVERSIFIER, seg, div_colour)):
        eq = gridfig._rebase(series)
        fig.add_trace(go.Scatter(x=eq.index, y=eq.to_numpy(), name=f"{name} (rebased)",
                                 showlegend=False, line=dict(color=colour, width=1.4,
                                                             dash="dot"),
                                 hovertemplate="%{x|%Y-%m-%d}<br>" + name +
                                 " %{y:.3f}<extra></extra>"), row=3, col=1)
    fig.update_yaxes(tickformat=".0%", row=2, col=1)
    gridfig._set_title(fig, title or f"Put Hedge against an Energy Diversifier, {symbol}, 2026",
                       f"Same premium budget each time the hedge opens. Deeper strikes are a "
                       f"legend click away. IN-SAMPLE, one window; {gridfig.MARK_CAVEAT}.")
    fig.update_layout(height=820, hovermode="x unified")
    gridfig.apply_bottom_legend(fig, gap=0.08, margin_b=140)
    return gridfig._inline_theme(fig)


# ============================================================================
# FIGURES FOR THE PUBLISHED PAGE
# ============================================================================

# Heading phrase -> figure stem(s), merged into option_grid_sweep.FIGURE_AT. Each phrase must be
# unique to one h2 of the write-up, or the build fails (build_report._split_sections).
FIGURE_AT = {
    "twenty random windows": ["ext_random_heatmap", "ext_random_fit", "ext_random_contour"],
    "Iran war": ["y2026_spy", "y2026_qqq"],
    "energy diversifier": ["y2026_energy_spy", "y2026_energy_qqq"],
}


def build_extension_figures(trigger=sweep.LADDER_MONETISE_DRAWDOWN):
    """Every figure this plan adds to the page, from persisted runs plus six short fresh ones."""
    scores = pd.read_csv(EXT_DIR / "scores_random.csv")
    trials = int(scores["config"].nunique())
    fit, opt, bounds, points, layers = fit_random(scores, trigger)
    n = scores["window"].nunique()
    figs = {
        "ext_random_heatmap": gridfig.fig_grid_heatmap(
            scores, trial_count=trials,
            title=f"Mean Relative Score over {n} Random Windows, by Strike and Trigger"),
        "ext_random_fit": gridfig.fig_strike_fit_layers(
            points, layers, active=[list(layers)[2]], trial_count=trials,
            subtitle=f"Trigger fixed at {trigger:.0%}; {n} random windows, overlap allowed.",
            title="Relative Score against Moneyness over Random Windows, with the P-spline"),
        "ext_random_contour": gridfig.fig_pooled_contour(
            fit, sweep.demeaned_scores(scores), opt, bounds, trial_count=trials,
            colourbar_title="Fitted<br>deviation",
            title="Pooled Fitted Surface over Random Windows, Window-Demeaned",
            subtitle=("Height is each configuration's deviation from its own window's mean "
                      "score.")),
    }
    energy = load_energy()
    budget_label = sweep.config_label(sweep.BUDGET_MONEYNESS, trigger)
    for symbol, key in (("SPY", "spy"), (TECH_UNDERLYING, "qqq")):
        runs = illustrative_runs(symbol, trigger)
        spot = next(iter(runs.values()))["spot"]
        figs[f"y2026_{key}"] = gridfig.fig_moneyness_stack(
            runs, spot, reentry_iv=sweep.LADDER_REENTRY_IV, underlying=symbol,
            title=(f"{symbol}, 2026 to Date: Monetise at a {trigger:.0%} Drawdown, Three Strikes "
                   f"Sized to the {sweep.BUDGET_MONEYNESS:.2f}x Premium"))
        div = diversifier_equity(spot, energy, premium_spend(runs[budget_label], symbol))
        figs[f"y2026_energy_{key}"] = fig_diversifier(symbol, spot, energy, runs, div,
                                                      budget_label)
    return figs


def window_stats(trigger=sweep.LADDER_MONETISE_DRAWDOWN):
    """Return and maximum drawdown for every line on the 2026 figures, for the write-up."""
    energy = load_energy()
    budget_label = sweep.config_label(sweep.BUDGET_MONEYNESS, trigger)
    rows = []
    for symbol in ("SPY", TECH_UNDERLYING):
        runs = illustrative_runs(symbol, trigger)
        spot = next(iter(runs.values()))["spot"]
        lines = {f"{symbol} only": spot}
        lines.update({lab: r["series"]["equity"] for lab, r in runs.items()})
        spend = premium_spend(runs[budget_label], symbol)
        lines[f"{symbol} + {DIVERSIFIER}"] = diversifier_equity(spot, energy, spend)
        for name, s in lines.items():
            rows.append({"underlying": symbol, "line": name,
                         "return": scoring.window_return(s), "max_dd": scoring.max_drawdown(s),
                         "trough": pd.Series(s).div(pd.Series(s).cummax()).idxmin().date()})
        rows.append({"underlying": symbol, "line": "premium spent / start",
                     "return": float(spend.sum() / spot.iloc[0]), "max_dd": np.nan,
                     "trough": None})
    xle = energy.loc[WINDOW_2026_START:]
    rows.append({"underlying": DIVERSIFIER, "line": f"{DIVERSIFIER} only",
                 "return": scoring.window_return(xle), "max_dd": scoring.max_drawdown(xle),
                 "trough": None})
    return pd.DataFrame(rows)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 78)
    print("13-02.2 — deeper strikes, random windows, the 2026 window.  IN-SAMPLE.")
    print("=" * 78)
    load_energy()
    sweep_new_strikes(persist=True)
    results = load_extended()
    spot, _iv = sweep.load_full()
    windows, scores = score_random(results, spot)
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    scores.to_csv(EXT_DIR / "scores_random.csv", index=False)
    pd.DataFrame([{"window": k, "start": s, "end": e, "kind": kd}
                  for k, (s, e, kd) in windows.items()]).to_csv(EXT_DIR / "random_windows.csv",
                                                                index=False)

    # The read-outs the write-up quotes, printed and persisted.
    kinds = pd.Series([k for _, _, k in windows.values()]).value_counts().to_dict()
    eff = scoring.effective_window_count(windows)
    fit, opt, bounds, points, layers = fit_random(scores)
    armed = scores[~scores["blind_roll"]].dropna(subset=["score"])
    mean_grid = armed.groupby(["floor", "monetise_drawdown"])["score"].mean()
    best = mean_grid.idxmax()
    at10 = armed[np.isclose(armed["monetise_drawdown"], sweep.LADDER_MONETISE_DRAWDOWN)]
    by_strike = at10.groupby(["floor", "kind"])["score"].mean().unstack()
    summary = {
        "n_windows": len(windows), "effective_non_overlapping": eff, "kinds": kinds,
        "trials": int(scores["config"].nunique()),
        "pooled_demeaned_optimum": {"floor": float(opt.point[0]), "dd": float(opt.point[1])},
        "raw_mean_argmax": {"floor": float(best[0]), "dd": float(best[1]),
                            "score": float(mean_grid.max())},
        "pspline_r2": float(list(layers.values())[2]["fit"].r2),
        "surface_r2": float(fit.r2),
    }
    (EXT_DIR / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))
    print("\nwindows:")
    for k, (s, e, kd) in windows.items():
        print(f"  {k:28s} {s} -> {e}  {kd}")
    print("\nmean score at the 10% trigger, by strike and window kind:")
    print(by_strike.round(3).to_string())
    stats = window_stats()
    stats.to_csv(EXT_DIR / "window_2026.csv", index=False)
    print("\n2026 window:")
    print(stats.round(4).to_string(index=False))
    return scores, summary, stats


if __name__ == "__main__":
    main()
