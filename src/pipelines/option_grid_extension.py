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
  (a hedge opened on day one), for SPY and for QQQ as the tech underlying, at four strikes only
  (0.95x, 0.90x, 0.80x, 0.70x) so the figure stays legible.
* AN ENERGY DIVERSIFIER on the same window: the premium the put programme spends at each opening
  is instead used to buy XLE (the S&P 500 energy sector ETF). Same budget, spent on a diversifying
  leg rather than on insurance. A refiners ETF is the intended diversifier and is parked in the plan.

EVERYTHING HERE IS IN-SAMPLE, and every fill is at the model mark (no bid/ask), exactly as in
option_grid_sweep.py. The diversifier comparison is illustrative over one window, not a result.

Run as a script:  ``python -m pipelines.option_grid_extension``  (simulate, score, persist)
The published page reads what this persists:  ``python -m pipelines.option_grid_sweep --publish-only``
"""

import functools
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from portutils.strategies import attribution, scoring, surface
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
# Four strikes at most on an illustrative figure, so a reader is not faced with a wall of lines:
# the budget strike, the old edge of the grid and the new one, plus 0.90x (added 2026-11-13 at the
# user's request) so the step from the one strike that paid in March to the ones that decayed to
# nothing is visible, rather than a jump from 0.95x straight to 0.80x.
ILLUSTRATIVE_STRIKES = (0.95, 0.90, 0.80, 0.70)
# The fixed energy weight compared against the budget-funded diversifier (2026-11-13). The put
# budget only ever moves about 5% of the book into XLE, so a fixed 20% shows what a deliberate
# allocation to the diversifier does, rather than one sized by what a put happens to cost.
MIX_WEIGHT = 0.20
# The tech underlying. QQQ prices and IBKR QQQ implied vol are already cached beside SPY's.
TECH_UNDERLYING = "QQQ"

# The diversifier. XLE is the S&P 500 energy sector ETF, the most widely held energy benchmark.
# A refiners ETF (CRAK) is the intended comparison and is parked in 13-02.2-PLAN.md.
DIVERSIFIER = "XLE"
ENERGY_RAW = PROJECT_ROOT / "data" / "raw" / "market" / "yfinance_XLE_daily.csv"
ENERGY_PATH = PROJECT_ROOT / "data" / "processed" / "prices_energy.parquet"

# Where this plan's runs land. Its OWN directory, so 13-02.1's guarded sweep stays byte-identical.
EXT_DIR = PROJECT_ROOT / "outputs" / "option_grid_extended"
# Dated events drawn on the 2026 figures (2026-11-13): reference data with a source per row.
EVENTS_PATH = PROJECT_ROOT / "data" / "reference" / "events_2026_iran_war.csv"
# The two triggers compared over 2026. 8% is the trigger the six-window analysis carries
# forward; 10% is the page's long-standing illustration, kept as the comparison.
TRIGGERS_2026 = (0.08, 0.10)
# The trigger the robustness analysis concludes on: the best median score over the random windows
# (0.85x / 8%, median 1.22) and the trigger the six-window analysis carries forward.
ROBUST_TRIGGER = 0.08
# The two strikes the trigger comparison draws: the budget strike and the next one down.
TRIGGER_STRIKES = (0.95, 0.90)
# The three bars on which IBKR's SPY implied vol prints about twelve points out of line with the
# VIX (9-10 Feb: 0.257 / 0.262 against 17.4 / 17.8; 17 Aug: 0.244 against 15.2; checked against
# Yahoo's ^VIX 2026-11-13). Not removed from the data; the 2026 table reports drawdown with and
# without them, because 9 February is every hedged programme's running peak.
IV_ANOMALY_DAYS = ("2026-02-09", "2026-02-10", "2026-08-17")


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


def fit_median_surface(scores):
    """The pooled surface fitted to the MEDIAN score of each configuration across the random
    windows (2026-11-14), replacing the window-demeaned mean surface on the page.

    The mean surface was pulled to the 0.70x / 20% corner by one window's aftermath (March 2020).
    The median is the robust centre the section ranks on, so the surface is fitted to it: one
    observation per (strike, trigger), blind rolls excluded. Because the medians are a fixed grid
    with no noise, cross-validation fits close to interpolation; the optimum's position is the
    reading, as with the per-window surfaces.
    """
    df = scores.dropna(subset=["score"])
    df = df[~df["blind_roll"]]
    med = (df.groupby(["floor", "monetise_drawdown"])["score"].median()
           .rename("score").reset_index())
    bounds = ((float(med["floor"].min()), float(med["floor"].max())),
              (float(med["monetise_drawdown"].min()), float(med["monetise_drawdown"].max())))
    fit = surface.fit_2d(med["floor"], med["monetise_drawdown"], med["score"])
    return fit, surface.optimum(fit, bounds), bounds, med


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
# THE 2026 WINDOW — fresh runs, SPY and QQQ, four strikes
# ============================================================================

def window_2026(symbol):
    """``(spot, iv)`` for ``symbol`` from the first 2026 bar to the last cached bar."""
    spot, iv = load_window(symbol)
    return spot.loc[WINDOW_2026_START:], iv.loc[WINDOW_2026_START:]


@functools.lru_cache(maxsize=None)
def _run_2026(symbol, floor, trigger):
    # One fresh 2026 run per (underlying, strike, trigger), cached for the life of the process:
    # the figures, the persisted tables and the write-up's generated values all read the same
    # runs, so a publish simulates each one once and the three can never disagree.
    spot, iv = window_2026(symbol)
    return sweep.run_config(floor=floor, monetise_drawdown=trigger, spot=spot, iv=iv,
                            underlying=symbol, quiet=True)


def illustrative_runs(symbol, trigger=sweep.LADDER_MONETISE_DRAWDOWN, strikes=None):
    """The illustrative strikes over the 2026 window, opened on its first bar."""
    strikes = ILLUSTRATIVE_STRIKES if strikes is None else strikes
    return {sweep.config_label(f, trigger): _run_2026(symbol, f, trigger) for f in strikes}


def load_events():
    """The dated 2026 events drawn as vertical lines, with their sources (data/reference)."""
    if not EVENTS_PATH.exists():
        return None
    return pd.read_csv(EVENTS_PATH, parse_dates=["date"])


def monetisations(res, symbol):
    """``[(date, multiple)]`` for every monetisation in a run: the bar the policy sold the hedge
    and the rule's own value-over-premium multiple on that bar."""
    events = scoring.classify_option_events(res["blotter"], symbol)
    if not len(events):
        return []
    mon = events[events["event"] == scoring.MONETISE]
    mult = pd.to_numeric(res["series"]["multiple"], errors="coerce")
    # The multiple is recorded on the bar BEFORE the sale goes flat, so read the last value at or
    # before the monetisation bar.
    return [(pd.Timestamp(ts), float(mult.loc[:ts].dropna().iloc[-1])) for ts in mon["ts"]]


def first_leg_attribution(symbol, floor, trigger=sweep.LADDER_MONETISE_DRAWDOWN):
    """Per-bar attribution of the FIRST put leg a 2026 run bought (opened in January), through
    to the bar before its expiry, whether or not the policy sold it earlier: the question is what
    the leg was worth, not what the policy did with it."""
    res = _run_2026(symbol, floor, trigger)
    spot, iv = window_2026(symbol)
    blot = res["blotter"]
    leg = blot[blot["symbol"].str.contains(" P ")].iloc[0]
    strike = float(re.search(r" P ([\d.]+)", leg["symbol"]).group(1))
    expiry_bar = int(re.search(r"@b(\d+)", leg["symbol"]).group(1))
    out = attribution.attribute_leg(spot, iv, strike, pd.Timestamp(leg["ts"]), expiry_bar,
                                    rate=sweep.RATE, div_yield=sweep.DIV_YIELD)
    out.attrs.update(strike=strike, expiry=spot.index[min(expiry_bar, len(spot) - 1)],
                     opened=pd.Timestamp(leg["ts"]))
    return out


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


def diversifier_blotter(spot, energy, spend, symbol):
    """One row per XLE purchase made with the put budget: what was spent and what it bought.

    Added 2026-11-13 at the user's request, because the diversifier line on the 2026 figure barely
    moves off the index and the only way to see why is to see the trades. Each row states the
    premium as a share of the index's price on that bar (what one 0.95x put costs relative to one
    unit of the underlying), the XLE units that cash bought, and XLE's resulting weight in the
    portfolio. Same funding arithmetic as ``diversifier_equity``, so the two cannot disagree.
    """
    xle = energy.reindex(spot.index).ffill()
    equity = diversifier_equity(spot, energy, spend)
    # Cumulative XLE units on every bar, so a row can report the weight AFTER that day's purchase.
    units = (spend.reindex(spot.index).fillna(0.0) / xle).cumsum()
    rows = []
    for ts, amount in spend.items():
        held = float(units.loc[ts] * xle.loc[ts])
        rows.append({"underlying": symbol, "date": ts.date(), "spot": float(spot.loc[ts]),
                     "premium": float(amount),
                     "premium_pct_of_spot": float(amount / spot.loc[ts]),
                     "xle_price": float(xle.loc[ts]),
                     "xle_units_bought": float(amount / xle.loc[ts]),
                     "xle_value_held": held,
                     "xle_weight_after": held / float(equity.loc[ts])})
    return pd.DataFrame(rows)


def fixed_mix_equity(spot, energy, weight=MIX_WEIGHT):
    """The index and XLE bought in a fixed split on the first bar and then held, in the index's
    price units so the line is comparable to the put runs and the budget diversifier.

    Buy-and-hold rather than rebalanced: over the windows on the page the two differ by under a
    percentage point (measured 2026-11-13), and holding needs no rebalancing rule to describe.
    """
    xle = energy.reindex(spot.index).ffill()
    start = float(spot.iloc[0])
    return start * ((1.0 - weight) * spot / spot.iloc[0] + weight * xle / xle.iloc[0])


def _drawdown(series):
    # Fall from the running peak, as a negative fraction, for the drawdown panel.
    s = pd.Series(series).astype(float)
    return s / s.cummax() - 1.0


def fig_diversifier(symbol, spot, energy, runs, div_equity, budget_label, title="",
                    blotter=None, mix_equity=None, events=None):
    """Three stacked panels: equity, drawdown, and the diversifier's own path.

    Panel 1 compares holding the index alone, the put programme at each illustrative strike and the
    index plus the energy leg bought with the same budget. Only the budget strike is visible by
    default; the deeper ones are a legend click away, so the figure opens on three lines.
    Panel 2 is each visible line's fall from its own running peak, which is the quantity the hedge
    exists to reduce. Panel 3 is XLE against the index, both rebased, which is what drives the
    diversifier line.

    ``blotter`` (from ``diversifier_blotter``) marks each XLE purchase on the diversifier line,
    with the premium as a share of the index and XLE's weight after the buy in the hover, so a
    reader can see how little energy the put budget actually buys. ``mix_equity`` adds the fixed
    split from ``fixed_mix_equity`` as a further line, for a deliberate allocation to compare.
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
    # The fixed split, in aqua: no put strike uses it now that strikes sit on the orange ramp.
    if mix_equity is not None:
        mix_name = (f"{1 - MIX_WEIGHT:.0%} {symbol} / {MIX_WEIGHT:.0%} {DIVERSIFIER} "
                    f"(fixed, held)")
        lines += [(mix_name, mix_equity, theme.CATEGORICAL[2], True)]
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

    # THE PUT PROGRAMMES' BLOTTERS (user request, 2026-11-14), as markers on each strike's equity
    # line, classified OPEN / ROLL / MONETISE by the SHARED scoring.classify_option_events and
    # carrying the SAME hover as the stacked figures (legs traded, cost or proceeds, contracts,
    # and the rule's state, drawdown, multiple and iv on that bar), so a trade reads the same on
    # every figure. Each set shares its strike's legend group and visibility, so a strike that is
    # a legend click away brings its trades with it.
    for lab in labels:
        res = runs[lab]
        # Named put_events, not events: `events` is this function's dated-event-lines argument.
        put_events = scoring.classify_option_events(res.get("blotter"), symbol)
        if not len(put_events):
            continue
        eq = gridfig._rebase(res["series"]["equity"])
        for kind, marker in gridfig.EVENT_SYMBOL.items():
            rows = put_events[put_events["event"] == kind]
            if not len(rows):
                continue
            ts = pd.DatetimeIndex(rows["ts"])
            fig.add_trace(go.Scatter(
                x=ts, y=eq.reindex(ts).to_numpy(), mode="markers", name=f"{lab} {kind}",
                legendgroup=lab, showlegend=False,
                visible=True if lab == budget_label else "legendonly",
                marker=dict(color=colours[lab], symbol=marker, size=10,
                            line=dict(color=theme.INK, width=0.8)),
                customdata=gridfig.event_customdata(rows, res["series"], res.get("blotter"),
                                                    symbol),
                hovertemplate=("<b>" + kind + "</b> " + lab + "  %{x|%Y-%m-%d}<br>"
                               + gridfig.EVENT_HOVER + "<extra></extra>")), row=1, col=1)
    # The three shape keys, once, in neutral ink, as on the stacked figures.
    for kind, marker in gridfig.EVENT_SYMBOL.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name=f"Put {kind}",
                                 legendgroup="put-events",
                                 marker=dict(color=theme.INK, symbol=marker, size=9),
                                 hoverinfo="skip"), row=1, col=1)

    # The fixed split is bought once, on the first bar, and never traded again: one marker says so.
    if mix_equity is not None:
        first = mix_equity.index[0]
        fig.add_trace(go.Scatter(
            x=[first], y=[1.0], mode="markers", name=f"{mix_name} buy", legendgroup=mix_name,
            showlegend=False, marker=dict(color=theme.CATEGORICAL[2], symbol="triangle-up",
                                          size=11, line=dict(color=theme.INK, width=1)),
            hovertemplate=("%{x|%Y-%m-%d}<br>bought " + f"{1 - MIX_WEIGHT:.0%} {symbol} and "
                           f"{MIX_WEIGHT:.0%} {DIVERSIFIER}" + ", held to the end of the window"
                           "<extra></extra>")), row=1, col=1)

    # THE DIVERSIFIER'S BLOTTER, as markers on its own equity line. Same legend group as the line,
    # so hiding the diversifier hides its trades too. customdata carries the row so the hover can
    # state the purchase in full rather than only its date.
    if blotter is not None and len(blotter):
        eq = gridfig._rebase(div_equity)
        dates = pd.to_datetime(blotter["date"])
        custom = blotter[["premium", "premium_pct_of_spot", "xle_units_bought",
                          "xle_weight_after"]].to_numpy()
        fig.add_trace(go.Scatter(
            x=dates, y=eq.reindex(dates).to_numpy(), mode="markers", name=f"{DIVERSIFIER} buys",
            legendgroup=div_name, showlegend=False,
            marker=dict(color=div_colour, symbol="triangle-up", size=11,
                        line=dict(color=theme.INK, width=1)),
            customdata=custom,
            hovertemplate=("%{x|%Y-%m-%d}<br>" + DIVERSIFIER + " bought with the put budget"
                           "<br>spent %{customdata[0]:.2f} (%{customdata[1]:.2%} of " + symbol +
                           ")<br>units %{customdata[2]:.3f}<br>" + DIVERSIFIER +
                           " weight after %{customdata[3]:.1%}<extra></extra>")), row=1, col=1)
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
    gridfig.add_event_lines(fig, events)
    gridfig._set_title(fig, title or f"Put Hedge against an Energy Diversifier, {symbol}, 2026",
                       f"Same premium budget each time the hedge opens; triangles mark each "
                       f"{DIVERSIFIER} purchase. Deeper strikes are a legend click away. "
                       f"IN-SAMPLE, one window; {gridfig.MARK_CAVEAT}.")
    fig.update_layout(height=820, hovermode="x unified")
    gridfig.apply_bottom_legend(fig, gap=0.08, margin_b=140)
    return gridfig._inline_theme(fig)


def mix_window_stats(energy, weight=MIX_WEIGHT):
    """Return and maximum drawdown for SPY alone, XLE alone and the fixed split, over each of the
    six indicative windows and the 2026 window.

    Added 2026-11-13 because the 2026 window is an oil shock, which is the one kind of fall an
    energy position is most likely to offset. The indicative windows include crashes with other
    causes, so the same split over them shows whether the 2026 result travels.
    """
    spot_full, _iv = sweep.load_full()
    windows = {name: (start, end, kind)
               for name, (start, end, kind) in scoring.INDICATIVE_WINDOWS.items()}
    windows["2026 to date"] = (WINDOW_2026_START, str(spot_full.index[-1].date()), "illustrative")
    xle_full = energy.reindex(spot_full.index).ffill()
    rows = []
    for name, (start, end, kind) in windows.items():
        spot = spot_full.loc[start:end]
        lines = {"SPY only": spot, f"{DIVERSIFIER} only": xle_full.loc[start:end],
                 f"{1 - weight:.0%} SPY / {weight:.0%} {DIVERSIFIER}":
                     fixed_mix_equity(spot, energy, weight)}
        for line, s in lines.items():
            rows.append({"window": name, "kind": kind, "line": line,
                         "return": scoring.window_return(s), "max_dd": scoring.max_drawdown(s)})
    return pd.DataFrame(rows)


def fig_mix_windows(stats, title=""):
    """Two panels of grouped bars, one group per window: maximum drawdown above, return below.

    SPY keeps its blue, XLE the diversifier's violet and the fixed split the aqua it has on the
    2026 figure, so the three read as the same lines across both figures.
    """
    colours = {"SPY only": theme.CATEGORICAL[0], f"{DIVERSIFIER} only": theme.CATEGORICAL[6]}
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=("Maximum Drawdown", "Return over the Window"))
    windows = list(dict.fromkeys(stats["window"]))
    for line in dict.fromkeys(stats["line"]):
        sub = stats[stats["line"] == line].set_index("window").loc[windows]
        colour = colours.get(line, theme.CATEGORICAL[2])
        for row, col in ((1, "max_dd"), (2, "return")):
            fig.add_trace(go.Bar(
                x=windows, y=sub[col].to_numpy(), name=line, legendgroup=line,
                showlegend=(row == 1), marker=dict(color=colour),
                hovertemplate="%{x}<br>" + line + (" max drawdown " if row == 1 else " return ")
                              + "%{y:.1%}<extra></extra>"), row=row, col=1)
    fig.update_yaxes(tickformat=".0%")
    fig.update_layout(barmode="group", height=640)
    gridfig._set_title(fig, title or f"SPY, {DIVERSIFIER} and a Fixed "
                                     f"{1 - MIX_WEIGHT:.0%}/{MIX_WEIGHT:.0%} Split, by Window",
                       f"Each split is bought on the window's first day and held. IN-SAMPLE.")
    gridfig.apply_bottom_legend(fig, gap=0.08, margin_b=120)
    return gridfig._inline_theme(fig)


# Factor colours for the attribution figure. Spot keeps the underlying's blue; the other three
# take categorical slots no put line uses now that strikes sit on the orange ramp.
FACTOR_STYLE = {
    "spot": ("Spot falling", theme.CATEGORICAL[0]),
    "vol_level": ("Implied vol level", theme.CATEGORICAL[6]),
    "skew_slide": ("Skew slide", theme.CATEGORICAL[4]),
    "time": ("Time decay", theme.CATEGORICAL[7]),
}


def fig_attribution(symbol="SPY", strikes=ILLUSTRATIVE_STRIKES, events=None, title=""):
    """One panel per strike: the January leg's value split into its four drivers, bar by bar.

    Every line is a multiple of the leg's entry price. The four driver lines are Shapley
    contributions (``portutils.strategies.attribution``), so on every bar 1 plus their sum is
    the leg's value multiple, drawn in the strike's own colour. The dashed line reprices the same
    spot and vol with the leg's full original time to expiry, which shows what the fall would
    have been worth to a leg that was not about to expire.
    """
    legs = {f: first_leg_attribution(symbol, f) for f in strikes}
    fig = make_subplots(rows=len(strikes), cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=[f"{f:.2f}x put, strike {legs[f].attrs['strike']:.2f}"
                                        for f in strikes])
    for row, f in enumerate(strikes, start=1):
        a = legs[f]
        colour = theme.put_strike_colour(f)
        for factor, (name, fcol) in FACTOR_STYLE.items():
            fig.add_trace(go.Scatter(
                x=a.index, y=a[factor].to_numpy(), name=name, legendgroup=factor,
                showlegend=(row == 1), line=dict(color=fcol, width=1.4),
                hovertemplate="%{x|%Y-%m-%d}<br>" + name + " %{y:+.2f}x<extra></extra>"),
                row=row, col=1)
        fig.add_trace(go.Scatter(
            x=a.index, y=a["multiple"].to_numpy(), name="Value multiple", legendgroup="mult",
            showlegend=(row == 1), line=dict(color=colour, width=2.6),
            customdata=a[["bars_left", "base_iv", "strike_iv"]].to_numpy(),
            hovertemplate=("%{x|%Y-%m-%d}<br>value %{y:.2f}x premium<br>%{customdata[0]} bars "
                           "left<br>ATM iv %{customdata[1]:.3f}, strike iv %{customdata[2]:.3f}"
                           "<extra>" + f"{f:.2f}x" + "</extra>")), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=a.index, y=a["multiple_full_tenor"].to_numpy(),
            name="Value with the full tenor left", legendgroup="full",
            showlegend=(row == 1), line=dict(color=colour, width=1.6, dash="dash"),
            hovertemplate=("%{x|%Y-%m-%d}<br>with full tenor %{y:.2f}x premium"
                           "<extra>" + f"{f:.2f}x" + "</extra>")), row=row, col=1)
        fig.add_hline(y=0.0, row=row, col=1, line=dict(color=theme.GRID, width=1))
        fig.update_yaxes(title_text="x premium", row=row, col=1)
    gridfig.add_event_lines(fig, events)
    first = next(iter(legs.values()))
    gridfig._set_title(
        fig, title or f"What Moved the Value of the January Put, {symbol}, 2026",
        f"Each leg opened {first.attrs['opened']:%d %b} and expired {first.attrs['expiry']:%d %b}. "
        f"Driver lines are Shapley contributions in multiples of the entry price; value = 1 + "
        f"their sum. IN-SAMPLE, model marks.")
    fig.update_layout(height=260 * len(strikes) + 200, hovermode="closest")
    gridfig.apply_bottom_legend(fig, gap=0.06, margin_b=150)
    return gridfig._inline_theme(fig)


# ============================================================================
# FIGURES FOR THE PUBLISHED PAGE
# ============================================================================

# Heading phrase -> figure stem(s), merged into option_grid_sweep.FIGURE_AT. Each phrase must be
# unique to one h2 of the write-up, or the build fails (build_report._split_sections).
FIGURE_AT = {
    # Order set 2026-11-14: mean grid, median grid, the strike scatter, then the pooled surface.
    "twenty random windows": ["ext_random_heatmap", "ext_random_median", "ext_random_fit",
                              "ext_random_contour"],
    # Not "Iran war": since 2026-11-14 that phrase is also in the parent section's heading.
    "only the tech hedge reached its trigger": ["y2026_spy", "y2026_qqq"],
    "energy diversifier": ["y2026_energy_spy", "y2026_energy_qqq"],
    "fixed allocation to energy": ["energy_mix_windows"],
    "time left on the leg": ["y2026_attribution"],
    # Not "8% trigger": the random-window heading also carries that phrase, which inserted these
    # two figures a second time with duplicate ids and left two empty boxes (fixed 2026-11-14).
    "would have monetised the SPY hedge": ["y2026_trigger_spy", "y2026_trigger_qqq"],
}


def _median_contour_args(scores):
    # (fit, design points, optimum, bounds) in the order fig_pooled_contour takes them.
    fit, opt, bounds, med = fit_median_surface(scores)
    return fit, med, opt, bounds


def build_extension_figures(trigger=sweep.LADDER_MONETISE_DRAWDOWN):
    """Every figure this plan adds to the page, from persisted runs plus six short fresh ones."""
    scores = pd.read_csv(EXT_DIR / "scores_random.csv")
    trials = int(scores["config"].nunique())
    fit, opt, bounds, _points, _layers = fit_random(scores, trigger)
    # The strike scatter is drawn at the ROBUST trigger (8%), the depth the section concludes on,
    # rather than the 10% illustration trigger (2026-11-14).
    points, layers = sweep.fit_strike_layers(scores, ROBUST_TRIGGER)
    n = scores["window"].nunique()
    figs = {
        "ext_random_heatmap": gridfig.fig_grid_heatmap(
            scores, trial_count=trials,
            title=f"Mean Relative Score over {n} Random Windows, by Strike and Trigger"),
        "ext_random_median": gridfig.fig_grid_heatmap(
            scores, trial_count=trials, aggfunc="median",
            title=f"Median Relative Score over {n} Random Windows, by Strike and Trigger"),
        "ext_random_fit": gridfig.fig_strike_fit_layers(
            points, layers, active=[list(layers)[2]], trial_count=trials,
            colour_by="kind", show_median=True,
            subtitle=(f"Trigger fixed at {ROBUST_TRIGGER:.0%}; {n} random windows, overlap "
                      f"allowed. Crosses mark the median across windows."),
            title="Relative Score against Moneyness over Random Windows, with the P-spline"),
        "ext_random_contour": gridfig.fig_pooled_contour(
            *_median_contour_args(scores), trial_count=trials,
            colourbar_title="Fitted<br>median score",
            title="Pooled Fitted Surface of the Median Score over Random Windows",
            subtitle=("Height is the fitted median relative score across the random windows "
                      "(SPY = 1.0).")),
    }
    energy = load_energy()
    events = load_events()
    budget_label = sweep.config_label(sweep.BUDGET_MONEYNESS, trigger)
    for symbol, key in (("SPY", "spy"), (TECH_UNDERLYING, "qqq")):
        runs = illustrative_runs(symbol, trigger)
        spot = next(iter(runs.values()))["spot"]
        figs[f"y2026_{key}"] = gridfig.fig_moneyness_stack(
            runs, spot, reentry_iv=sweep.LADDER_REENTRY_IV, underlying=symbol, events=events,
            title=(f"{symbol}, 2026 to Date: Monetise at a {trigger:.0%} Drawdown, Four Strikes "
                   f"Sized to the {sweep.BUDGET_MONEYNESS:.2f}x Premium"))
        # The 8% against the 10% trigger on the two nearest strikes. Same strike, same colour;
        # the shallower trigger is dashed (option_grid_figures.moneyness_styles).
        pair = {}
        for f in TRIGGER_STRIKES:
            for trig in sorted(TRIGGERS_2026, reverse=True):
                pair[sweep.config_label(f, trig)] = _run_2026(symbol, f, trig)
        figs[f"y2026_trigger_{key}"] = gridfig.fig_moneyness_stack(
            pair, spot, reentry_iv=sweep.LADDER_REENTRY_IV, underlying=symbol, events=events,
            title=(f"{symbol}, 2026 to Date: an {min(TRIGGERS_2026):.0%} against a "
                   f"{max(TRIGGERS_2026):.0%} Drawdown Trigger"))
        spend = premium_spend(runs[budget_label], symbol)
        div = diversifier_equity(spot, energy, spend)
        figs[f"y2026_energy_{key}"] = fig_diversifier(
            symbol, spot, energy, runs, div, budget_label,
            blotter=diversifier_blotter(spot, energy, spend, symbol),
            mix_equity=fixed_mix_equity(spot, energy), events=events)
    figs["y2026_attribution"] = fig_attribution("SPY", events=events)
    figs["energy_mix_windows"] = fig_mix_windows(mix_window_stats(energy))
    return figs


# ============================================================================
# GENERATED VALUES FOR THE WRITE-UP (2026-11-13)
# Every number and table the 2026 sections quote, computed from the same cached runs the figures
# draw. build_report substitutes them for {{name}} tokens in the Markdown and raises on any token
# left unresolved, so the commentary cannot fall behind the results it describes.
# ============================================================================

def _pct(x, signed=False, places=1):
    # Percent with a typographic minus, the form the write-up's tables already use.
    s = f"{x:+.{places}%}" if signed else f"{x:.{places}%}"
    return s.replace("-", "−")


def _day(ts):
    # "27 March": the write-up's date style, no year (every date here is 2026).
    ts = pd.Timestamp(ts)
    return f"{ts.day} {ts:%B}"


def _sgn(x, places=1):
    # A signed number with a typographic minus, for the attribution's contributions.
    return f"{x:+.{places}f}".replace("-", "−")


def _mult(x):
    return f"{x:.2f}"


def _md_table(header, rows):
    # A Markdown table from a header list and row lists, for a {{token}} that stands for a table.
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _peak_trough(series):
    # The deepest fall from a running peak: (peak date, trough date, fall, recovery date or None).
    s = pd.Series(series).astype(float)
    trough = (s / s.cummax()).idxmin()
    peak = s.loc[:trough].idxmax()
    after = s.loc[trough:]
    recovered = after[after >= s.loc[peak]]
    return peak, trough, float(s.loc[trough] / s.loc[peak] - 1.0), (
        recovered.index[0] if len(recovered) else None)


def page_values(trigger=sweep.LADDER_MONETISE_DRAWDOWN):
    """``{token: text}`` for every ``{{token}}`` in the write-up's 2026 sections."""
    v = {}
    energy = load_energy()
    budget_label = sweep.config_label(sweep.BUDGET_MONEYNESS, trigger)
    v["trigger"] = f"{trigger:.0%}"

    # ---- the window and the fall ----
    spot_by = {}
    for symbol in ("SPY", TECH_UNDERLYING):
        spot, _iv = window_2026(symbol)
        spot_by[symbol] = spot
        peak, trough, fall, rec = _peak_trough(spot)
        k = symbol.lower()
        v[f"{k}_peak"], v[f"{k}_trough"], v[f"{k}_fall"] = _day(peak), _day(trough), _pct(-fall)
        v[f"{k}_recovered"] = _day(rec) if rec is not None else "the end of the window"
    v["window_end"] = _day(spot_by["SPY"].index[-1])

    # ---- the four-strike results table at the illustration trigger ----
    # The last column drops the two anomalous IV bars before measuring the drawdown, since both
    # are the hedged equity's running peak and would otherwise set the depth of every fall after.
    def _dd_excl(s):
        s = pd.Series(s).astype(float)
        return scoring.max_drawdown(s.drop(pd.DatetimeIndex(IV_ANOMALY_DAYS), errors="ignore"))
    rows = []
    for symbol in ("SPY", TECH_UNDERLYING):
        spot = spot_by[symbol]
        rows.append([symbol, "unhedged", _pct(scoring.window_return(spot), True),
                     _pct(scoring.max_drawdown(spot)), _pct(_dd_excl(spot))])
        for lab, res in illustrative_runs(symbol, trigger).items():
            eq = res["series"]["equity"]
            rows.append([symbol, f"{res['params']['floor']:.2f}×",
                         _pct(scoring.window_return(eq), True), _pct(scoring.max_drawdown(eq)),
                         _pct(_dd_excl(eq))])
            if symbol == "SPY":
                key = f"spy_{int(round(res['params']['floor'] * 100)):03d}"
                v[f"{key}_dd_all"] = _pct(scoring.max_drawdown(eq))
                v[f"{key}_dd_excl"] = _pct(_dd_excl(eq))
    v["y2026_table"] = _md_table(["underlying", "strategy", "return", "max drawdown",
                                  "max drawdown, excluding anomalous IV days"], rows)

    # ---- premium spent, and each strike's monetisations, by underlying ----
    for symbol in ("SPY", TECH_UNDERLYING):
        k = symbol.lower()
        runs = illustrative_runs(symbol, trigger)
        spend = premium_spend(runs[budget_label], symbol)
        v[f"{k}_premium_pct"] = _pct(float(spend.sum() / spot_by[symbol].iloc[0]))
        for f in ILLUSTRATIVE_STRIKES:
            mons = monetisations(runs[sweep.config_label(f, trigger)], symbol)
            key = f"{k}_{int(round(f * 100)):03d}"
            v[f"{key}_mon"] = ("was not monetised" if not mons else
                               "was monetised on " + " and ".join(
                                   f"{_day(d)} at {_mult(m)} times its premium" for d, m in mons))

    # ---- attribution of the January SPY leg at the SPY trough ----
    rows = []
    trough = pd.Timestamp(_peak_trough(spot_by["SPY"])[1])
    for f in ILLUSTRATIVE_STRIKES:
        a = first_leg_attribution("SPY", f, trigger)
        at = a.loc[:trough].iloc[-1]
        rows.append([f"{f:.2f}×", f"{at['multiple']:.2f}", _sgn(at['spot'], 2),
                     _sgn(at['vol_level'], 2), _sgn(at['skew_slide'], 2), _sgn(at['time'], 2),
                     f"{at['multiple_full_tenor']:.2f}"])
        if f == ILLUSTRATIVE_STRIKES[0]:
            v["att_opened"], v["att_expiry"] = _day(a.attrs["opened"]), _day(a.attrs["expiry"])
            v["att_bars_left"] = str(int(at["bars_left"]))
            v["att_iv_open"] = f"{a['base_iv'].iloc[0]:.2f}"
            v["att_iv_trough"] = f"{at['base_iv']:.2f}"
        v[f"att_{int(round(f * 100)):03d}_mult"] = f"{at['multiple']:.2f}"
        v[f"att_{int(round(f * 100)):03d}_full"] = f"{at['multiple_full_tenor']:.1f}"
        v[f"att_{int(round(f * 100)):03d}_spot"] = _sgn(at['spot'], 1)
        v[f"att_{int(round(f * 100)):03d}_vol"] = _sgn(at['vol_level'], 1)
        v[f"att_{int(round(f * 100)):03d}_skew"] = _sgn(at['skew_slide'], 1)
        v[f"att_{int(round(f * 100)):03d}_time"] = _sgn(at['time'], 1)
    v["attribution_table"] = _md_table(
        ["strike", "value, × premium", "spot", "vol level", "skew slide", "time",
         "value with full tenor"], rows)

    # ---- the 8% against the 10% trigger ----
    rows = []
    for symbol in ("SPY", TECH_UNDERLYING):
        k = symbol.lower()
        for f in TRIGGER_STRIKES:
            for trig in sorted(TRIGGERS_2026):
                res = _run_2026(symbol, f, trig)
                eq = res["series"]["equity"]
                mons = monetisations(res, symbol)
                fired = ("not fired" if not mons else
                         ", ".join(f"{_day(d)} at {_mult(m)}×" for d, m in mons))
                rows.append([symbol, f"{f:.2f}×", f"{trig:.0%}",
                             _pct(scoring.window_return(eq), True),
                             _pct(scoring.max_drawdown(eq)), fired])
                key = f"{k}_{int(round(f * 100)):03d}_t{int(round(trig * 100)):02d}"
                v[f"{key}_ret"] = _pct(scoring.window_return(eq), True)
                v[f"{key}_dd"] = _pct(scoring.max_drawdown(eq))
                v[f"{key}_fired"] = fired
    v["trigger_table"] = _md_table(["underlying", "strike", "trigger", "return",
                                    "max drawdown", "monetised"], rows)
    # How often the shallower trigger outscored the deeper one in the persisted grids, per
    # (window, strike) cell: the six indicative windows and the twenty random ones.
    lo, hi = sorted(TRIGGERS_2026)
    for key, path in (("ind", sweep.OUT_DIR / "scores_tidy.csv"),
                      ("rnd", EXT_DIR / "scores_random.csv")):
        if not path.exists():
            v[f"t_beats_{key}"], v[f"t_cells_{key}"] = "n/a", "n/a"
            continue
        sc = pd.read_csv(path)
        sc = sc[np.isclose(sc["monetise_drawdown"].fillna(-1), lo)
                | np.isclose(sc["monetise_drawdown"].fillna(-1), hi)]
        piv = sc.pivot_table(index=["window", "floor"], columns="monetise_drawdown",
                             values="score").dropna()
        wins = int((piv.iloc[:, 0] > piv.iloc[:, 1]).sum())
        v[f"t_beats_{key}"], v[f"t_cells_{key}"] = str(wins), str(len(piv))

    # ---- the energy diversifier ----
    rows, brows = [], []
    for symbol in ("SPY", TECH_UNDERLYING):
        k = symbol.lower()
        runs = illustrative_runs(symbol, trigger)
        spot = spot_by[symbol]
        spend = premium_spend(runs[budget_label], symbol)
        div = diversifier_equity(spot, energy, spend)
        mix = fixed_mix_equity(spot, energy)
        put = runs[budget_label]["series"]["equity"]
        for name, s in (("unhedged", spot), (f"{sweep.BUDGET_MONEYNESS:.2f}× put hedge", put),
                        ("energy diversifier", div),
                        (f"{1 - MIX_WEIGHT:.0%} {symbol} / {MIX_WEIGHT:.0%} {DIVERSIFIER}, fixed",
                         mix)):
            rows.append([symbol, name, _pct(scoring.window_return(s), True),
                         _pct(scoring.max_drawdown(s))])
        v[f"{k}_div_ret"] = _pct(scoring.window_return(div), True)
        v[f"{k}_div_dd"] = _pct(scoring.max_drawdown(div))
        v[f"{k}_mix_ret"] = _pct(scoring.window_return(mix), True)
        v[f"{k}_mix_dd"] = _pct(scoring.max_drawdown(mix))
        v[f"{k}_ret"] = _pct(scoring.window_return(spot), True)
        v[f"{k}_dd"] = _pct(scoring.max_drawdown(spot))
        b = diversifier_blotter(spot, energy, spend, symbol)
        for _, r in b.iterrows():
            brows.append([symbol, _day(r["date"]), _pct(r["premium_pct_of_spot"], places=2),
                          f"{r['xle_units_bought']:.3f}", _pct(r["xle_weight_after"])])
        if symbol == "SPY":
            at_trough = b[pd.to_datetime(b["date"]) <= trough]
            v["xle_weight_at_trough"] = _pct(at_trough["xle_weight_after"].iloc[-1]
                                             if len(at_trough) else 0.0)
            v["xle_weight_max"] = _pct(b["xle_weight_after"].max())
            v["premium_pct_lo"] = _pct(b["premium_pct_of_spot"].min())
            v["premium_pct_hi"] = _pct(b["premium_pct_of_spot"].max())
    v["energy_table"] = _md_table(["underlying", "strategy", "return", "max drawdown"], rows)
    v["blotter_table"] = _md_table(["underlying", "date", "premium, % of index",
                                    "XLE units bought", "XLE weight after"], brows)
    xle = energy.reindex(spot_by["SPY"].index).ffill()
    spy_peak = pd.Timestamp(_peak_trough(spot_by["SPY"])[0])
    v["xle_to_trough"] = _pct(float(xle.loc[trough] / xle.iloc[0] - 1.0))
    v["xle_peak_to_trough"] = _pct(float(xle.loc[trough] / xle.loc[spy_peak] - 1.0), True)
    v["xle_window"] = _pct(scoring.window_return(xle))
    v["mix_weight"] = f"{MIX_WEIGHT:.0%}"
    v["mix_core"] = f"{1 - MIX_WEIGHT:.0%}"

    # ---- the fixed split across the indicative windows ----
    mix = mix_window_stats(energy)
    mrows = []
    for window, grp in mix.groupby("window", sort=False):
        g = grp.set_index("line")
        spy_l, xle_l = "SPY only", f"{DIVERSIFIER} only"
        mix_l = [x for x in g.index if x not in (spy_l, xle_l)][0]
        mrows.append([window, _pct(g.loc[xle_l, "return"], True),
                      _pct(g.loc[spy_l, "return"], True), _pct(g.loc[mix_l, "return"], True),
                      _pct(g.loc[spy_l, "max_dd"]), _pct(g.loc[mix_l, "max_dd"])])
    v["mix_windows_table"] = _md_table(
        ["window", "XLE return", "SPY return", "split return", "SPY max drawdown",
         "split max drawdown"], mrows)
    return v


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
        lines[f"{1 - MIX_WEIGHT:.0%}/{MIX_WEIGHT:.0%} fixed"] = fixed_mix_equity(spot, energy)
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
    write_diversifier_tables()
    return scores, summary, stats


def write_diversifier_tables(trigger=sweep.LADDER_MONETISE_DRAWDOWN):
    """Persist the XLE blotter for both underlyings and the fixed-split table across windows, the
    numbers the write-up's diversifier sections quote. Cheap (two short runs and some slicing), so
    callable on its own without re-running the sweep."""
    energy = load_energy()
    budget_label = sweep.config_label(sweep.BUDGET_MONEYNESS, trigger)
    blotters = []
    for symbol in ("SPY", TECH_UNDERLYING):
        runs = illustrative_runs(symbol, trigger)
        spot = runs[budget_label]["spot"]
        blotters.append(diversifier_blotter(spot, energy,
                                            premium_spend(runs[budget_label], symbol), symbol))
    blotter = pd.concat(blotters, ignore_index=True)
    mix = mix_window_stats(energy)
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    blotter.to_csv(EXT_DIR / "diversifier_blotter_2026.csv", index=False)
    mix.to_csv(EXT_DIR / "energy_mix_windows.csv", index=False)
    print("\ndiversifier blotter:")
    print(blotter.round(4).to_string(index=False))
    print("\nfixed split by window:")
    print(mix.round(4).to_string(index=False))
    return blotter, mix


if __name__ == "__main__":
    main()
