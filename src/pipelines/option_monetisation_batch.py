"""
13-01 TASK 3 — THE FULL-HISTORY BATCH RUN.

WHAT THIS SCRIPT IS
-------------------
One command that prices the option overlays over the whole real implied-vol history
(2006-01-09 to 2026-09-18, 5,204 bars) and prints a comparison table. It is the ENGINE's
demonstration run, not a parameter search:

    python -m pipelines.option_monetisation_batch

WHY A SCRIPT AND NOT MORE PROBE CELLS
-------------------------------------
``research/option_overlay_probe.py`` is twelve cells written to EXPLAIN the pricer one window at
a time, and its mechanism work is finished (findings 1-9). A 5,204-bar run across several
configurations is the wrong shape for that format. The data loading is IMPORTED from
``option_probe_figures`` rather than re-derived — one implementation of "what is the real IV
history", so the two cannot drift.

A NAMED SET, NOT A GRID — AND WHY THAT MATTERS
-----------------------------------------------
The configurations below are ILLUSTRATIVE. They exercise the mechanism and time one run; they
are NOT a search, and the best-looking row here is NOT a finding. Every threshold in this file
was picked by hand, and reading a winner off a table of hand-picked thresholds scored on the
same 20 years is exactly the in-sample claim 13-02 and 13-03 exist to avoid. 13-02 does the
Cartesian product properly, on a stated metric, with the trial count recorded.

WHAT THIS RUN HANDS FORWARD
---------------------------
1. A per-bar equity series per configuration, written under ``outputs/option_monetisation/
   series/``. Max drawdown is NOT recoverable from an aggregate, so the stored primitive has to
   be the series: 13-02 and 13-03 score any window by SLICING these rather than re-running the
   simulator per origin, which is what collapses their cost from (grid x origins) runs to (grid).
2. The measured wall-clock time of ONE run, so 13-02 can size its grid from a number.

NO BID/ASK MODEL
----------------
Every monetisation, roll and reopen fills at the model mark. There is no spread, no slippage and
no market impact, which flatters every configuration — most of all the ones that trade most.
Stated here once and repeated beside the table, because a policy that trades more looks better
than it is under this assumption.
"""

import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from portutils.portfolio.rules import BuyAndHoldRule
from portutils.portfolio.simulator import PortfolioSimulator
from portutils.strategies.rules.options import (
    ProtectivePutRule,
    PutSpreadRule,
    RollingCollarRule,
)
from portutils.viz import theme

from pipelines.option_probe_figures import (
    DIV_YIELD,
    RATE,
    UNDERLYING,
    align_real_iv,
    load_real_iv,
)

# ============================================================================
# PATHS AND WINDOW
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRICE_HISTORY = PROJECT_ROOT / "data" / "processed" / "prices_long_history.parquet"
OUT_DIR = PROJECT_ROOT / "outputs" / "option_monetisation"
SERIES_DIR = OUT_DIR / "series"

# 63 bars, the same quarterly cadence every prior plan used. Held FIXED here: tenor is a third
# sweep axis and it belongs to a later plan, not to the engine's demonstration run.
RESET_BARS = 63

# Named drawdown episodes, for the "what did the hedge actually capture" columns. Chosen because
# they are the three the milestone's claim rests on and the ones 11-01's COVERAGE.md confirms.
DRAWDOWN_WINDOWS = {
    "GFC 2007-2009": ("2007-10-01", "2009-03-31"),
    "COVID 2020": ("2020-02-01", "2020-03-31"),
    "Rates 2022": ("2022-01-01", "2022-10-31"),
}

# Illustrative policy levels. See the module docstring: hand-picked to exercise the mechanism.
ILLUSTRATIVE_DRAWDOWN = 0.10      # monetise once the underlying is 10% below its peak
ILLUSTRATIVE_MULTIPLE = 2.0       # or once the long put is worth twice what it cost
ILLUSTRATIVE_REENTRY_IV = 0.18    # buy again once implied vol is back to 18%
ILLUSTRATIVE_MAX_FLAT = 126       # and never sit unhedged for more than about six months


# ============================================================================
# DATA
# ============================================================================

def load_window():
    """SPY closes and real IV on their INTERSECTION, with the truncation printed.

    The price history reaches 1993 and the IV history only 2006, so v2 cannot price the first
    thirteen years at all. That loss is printed rather than silently absorbed: it is a measured
    Phase 11 finding (real ``OPTION_IMPLIED_VOLATILITY`` begins 2006-01-09), and a run that
    quietly started in 2006 would look like a choice rather than a constraint.
    """
    spot_all = pd.read_parquet(PRICE_HISTORY)[UNDERLYING].dropna()
    iv_all = load_real_iv()

    common = spot_all.index.intersection(iv_all.index)
    if len(common) == 0:
        raise ValueError("price history and IV history do not overlap at all")
    spot = spot_all.loc[common].sort_index()
    iv = align_real_iv(iv_all, spot)

    dropped = len(spot_all) - len(spot)
    print(f"price history : {len(spot_all):,} bars, {spot_all.index.min().date()} -> "
          f"{spot_all.index.max().date()}")
    print(f"IV history    : {len(iv_all):,} bars, {iv_all.index.min().date()} -> "
          f"{iv_all.index.max().date()}")
    print(f"run window    : {len(spot):,} bars, {spot.index.min().date()} -> "
          f"{spot.index.max().date()}")
    print(f"DROPPED       : {dropped:,} price bars before the IV history begins — v2 cannot "
          f"price them, and this is a data limit, not a choice")
    return spot, iv


# ============================================================================
# CONFIGURATIONS — a named set. See the module docstring for why not a grid.
# ============================================================================

def build_configs(iv):
    """One fresh rule per configuration. Rules carry state, so instances are never shared."""
    shared = dict(underlying=UNDERLYING, reset_bars=RESET_BARS, rate=RATE, div_yield=DIV_YIELD)
    v2 = dict(vol_mode="v2", iv_series=iv, **shared)
    policy = dict(reentry_iv=ILLUSTRATIVE_REENTRY_IV, max_flat_bars=ILLUSTRATIVE_MAX_FLAT)
    return {
        # The benchmark every hedge is read against.
        "unhedged": None,
        # Today's behaviour — hold to expiry, roll every 63 bars — under each pricing mode. The
        # pair isolates what v2 alone changes, before any policy is armed.
        "blind_roll_v1": ProtectivePutRule(floor=0.90, **shared),
        "blind_roll_v2": ProtectivePutRule(floor=0.90, **v2),
        # The two triggers, one each, so every monetisation is attributable to one policy.
        "monetise_drawdown": ProtectivePutRule(
            floor=0.90, monetise_drawdown=ILLUSTRATIVE_DRAWDOWN, **policy, **v2),
        "monetise_multiple": ProtectivePutRule(
            floor=0.90, monetise_multiple=ILLUSTRATIVE_MULTIPLE, **policy, **v2),
        # Reference rows only: the other two structures at their 12-01 defaults, so the table
        # shows where they sit without claiming anything about their parameters.
        "put_spread_v2_ref": PutSpreadRule(floor=0.90, spread_width=0.80, **v2),
        "collar_v2_ref": RollingCollarRule(floor=0.90, cap=1.28, **v2),
    }


def run_one(name, rule, spot):
    """One configuration over the whole window. Starting capital = first close, so every value
    reads in SPY price units and the table is directly comparable to 16-03's figures."""
    prices = spot.to_frame(UNDERLYING)
    rules = [BuyAndHoldRule({UNDERLYING: 1.0})] + ([rule] if rule is not None else [])
    sim = PortfolioSimulator(prices, rules, starting_capital=float(spot.iloc[0]))
    t0 = time.perf_counter()
    state = sim.run()
    elapsed = time.perf_counter() - t0

    # The same loud guard overlay_runs uses: legs proposed but never filled means the simulator
    # has no synthetic-marks hook, and the hedged line would silently equal the unhedged one.
    if rule is not None and rule.events and not any(
            f.symbol.startswith(f"{UNDERLYING} ") and f.symbol != UNDERLYING for f in sim.fills):
        raise RuntimeError(f"{name}: option legs were proposed but never filled")
    return state, sim, elapsed


# ============================================================================
# SCORING — every drawdown figure is recomputed from the stored series, never
# cached as a scalar, because a window's drawdown is not recoverable from the
# whole path's drawdown. 13-02 relies on exactly this property.
# ============================================================================

def max_drawdown(equity):
    # Running peak from the series' OWN first bar, so a sliced window resets its peak at the
    # slice start. The carry-in alternative makes a window that opens mid-decline read as calm.
    eq = pd.Series(equity).astype(float)
    if eq.empty:
        return float("nan")
    peak = eq.cummax()
    return float(((peak - eq) / peak.replace(0, np.nan)).max())


def window_pnl(equity, start, end):
    # Change in equity across a named episode, in SPY price units.
    seg = pd.Series(equity).loc[start:end]
    if len(seg) < 2:
        return float("nan")
    return float(seg.iloc[-1] - seg.iloc[0])


def premium_paid(rule):
    # Gross premium paid for LONG legs, net of credits taken on short legs. Settlements are not
    # netted in — this is the drag, not the P&L.
    if rule is None:
        return 0.0
    return float(sum(e["qty"] * e["price"] for e in rule.events if e["action"] == "open"))


def flat_share(rule, n_bars):
    # Share of bars spent UNHEDGED. MANDATORY: a policy can score well simply by sitting out of
    # the market, and without this column that reads as skill rather than absence.
    if rule is None or not rule.history:
        return float("nan")
    states = [h.get("state") for h in rule.history.values()]
    hedged = sum(1 for s in states if s == "HEDGED")
    return float(1.0 - hedged / n_bars)


def summarise(name, rule, state, spot, elapsed):
    eq = state["equity"]
    row = {
        "configuration": name,
        "final_value": float(eq.iloc[-1]),
        "max_drawdown": max_drawdown(eq),
        # Premium as an annualised % of average spot — the figure that makes a hedge look
        # affordable or not, and the one 12-01's economics turn on.
        "premium_pct_spot_pa": (premium_paid(rule) / float(spot.mean())
                                / (len(spot) / 252.0) * 100.0) if rule is not None else 0.0,
        "n_monetisations": (len([e for e in rule.events if e["action"] == "monetise"])
                            if rule is not None else 0),
        "flat_share": flat_share(rule, len(spot)),
        "runtime_s": round(elapsed, 2),
    }
    for label, (start, end) in DRAWDOWN_WINDOWS.items():
        row[f"pnl {label}"] = window_pnl(eq, start, end)
    return row


def trigger_comparison(configs):
    """AC-3b: do the drawdown and multiple triggers fire at the same times?

    Every monetisation logs BOTH quantities whichever one fired, so the two policies can be
    compared after the fact. Agreement is the hypothesis the phase has been assuming — that a
    put paying a multiple of its premium and a market in drawdown are the same event.
    """
    rows = []
    for name in ("monetise_drawdown", "monetise_multiple"):
        rule = configs.get(name)
        if rule is None:
            continue
        for e in rule.events:
            if e["action"] == "monetise":
                rows.append({"configuration": name, "bar": e["bar"], "ts": e["ts"],
                             "trigger": e["trigger"], "drawdown": e["drawdown"],
                             "multiple": e["multiple"], "iv": e["iv"]})
    if not rows:
        return pd.DataFrame(rows), None

    df = pd.DataFrame(rows)
    dd_bars = sorted(df.loc[df["configuration"] == "monetise_drawdown", "bar"])
    mu_bars = sorted(df.loc[df["configuration"] == "monetise_multiple", "bar"])
    if not dd_bars or not mu_bars:
        return df, None
    # For each drawdown-trigger firing, how far away was the NEAREST multiple-trigger firing?
    gaps = [min(abs(b - m) for m in mu_bars) for b in dd_bars]
    agree = sum(1 for g in gaps if g <= 5) / len(gaps)
    return df, {"n_drawdown": len(dd_bars), "n_multiple": len(mu_bars),
                "median_gap_bars": float(np.median(gaps)),
                "share_within_5_bars": float(agree)}


# ============================================================================
# FIGURE
# ============================================================================

def build_figure(name, rule, state, spot, iv):
    """Four stacked panels for one configuration: value, the HEDGED/FLAT ribbon, IV with the
    re-entry gate, and the drawdown with the monetise threshold."""
    hist = pd.DataFrame(rule.history).T if rule is not None and rule.history else pd.DataFrame()
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        row_heights=[0.4, 0.1, 0.25, 0.25],
                        subplot_titles=("Total value vs unhedged SPY", "State",
                                        "Implied vol and the re-entry gate",
                                        "Drawdown and the monetise threshold"))
    c = theme.CATEGORICAL
    fig.add_trace(go.Scatter(x=spot.index, y=spot.to_numpy(), name="SPY only",
                             line=dict(color=c[0])), row=1, col=1)
    fig.add_trace(go.Scatter(x=state.index, y=state["equity"], name=name,
                             line=dict(color=c[1])), row=1, col=1)

    if not hist.empty:
        ts = pd.to_datetime(hist["ts"])
        fig.add_trace(go.Scatter(x=ts, y=(hist["state"] == "HEDGED").astype(int),
                                 name="HEDGED", mode="lines", line=dict(color=c[2], shape="hv"),
                                 fill="tozeroy"), row=2, col=1)
    fig.add_trace(go.Scatter(x=iv.index, y=iv.to_numpy(), name="real IV",
                             line=dict(color=c[3])), row=3, col=1)
    fig.add_hline(y=ILLUSTRATIVE_REENTRY_IV, line_dash="dash", line_color=c[4], row=3, col=1)

    peak = spot.cummax()
    fig.add_trace(go.Scatter(x=spot.index, y=((peak - spot) / peak).to_numpy(),
                             name="drawdown", line=dict(color=c[5])), row=4, col=1)
    fig.add_hline(y=ILLUSTRATIVE_DRAWDOWN, line_dash="dash", line_color=c[4], row=4, col=1)

    fig.update_layout(
        title=(f"{name} — IN-SAMPLE, illustrative parameters, no bid/ask model<br>"
               f"<sub>{spot.index.min().date()} to {spot.index.max().date()}, "
               f"{len(spot):,} bars. Not a parameter search — see 13-02.</sub>"),
        height=1000, hovermode="x unified")
    return theme.apply_export_theme(fig)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 78)
    print("13-01 — OPTION MONETISATION BATCH.  IN-SAMPLE. Illustrative parameters, NOT a sweep.")
    print("=" * 78)
    spot, iv = load_window()

    configs = build_configs(iv)
    print(f"\nconfigurations: {len(configs)} named (not a grid) — {', '.join(configs)}")
    print(f"policy levels : drawdown {ILLUSTRATIVE_DRAWDOWN:.0%}, multiple "
          f"{ILLUSTRATIVE_MULTIPLE:.1f}x, re-entry IV {ILLUSTRATIVE_REENTRY_IV:.0%}, "
          f"max flat {ILLUSTRATIVE_MAX_FLAT} bars\n")

    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    rows, states, first_elapsed = [], {}, None
    for name, rule in configs.items():
        state, _sim, elapsed = run_one(name, rule, spot)
        if first_elapsed is None:
            first_elapsed = elapsed
            # Printed FIRST and recorded in the SUMMARY: 13-02 sizes its grid from this number
            # rather than from a guess, so it is a deliverable of this plan.
            print(f"MEASURED RUNTIME, one configuration over {len(spot):,} bars: "
                  f"{elapsed:.2f}s  ->  a 100-point grid would cost about "
                  f"{elapsed * 100 / 60:.1f} min")
        states[name] = state
        rows.append(summarise(name, rule, state, spot, elapsed))

        # The artefact 13-02 and 13-03 consume. A time series, not a summary row: window
        # drawdowns are recomputed from this by slicing, never recovered from an aggregate.
        out = state[["equity"]].copy()
        out["spot"] = spot.reindex(out.index)
        if rule is not None and rule.history:
            hist = pd.DataFrame(rule.history).T.set_index(pd.to_datetime(
                [h["ts"] for h in rule.history.values()]))
            for col in ("state", "drawdown", "multiple", "iv", "net_value", "net_premium"):
                if col in hist:
                    out[col] = hist[col].reindex(out.index)
        out.to_parquet(SERIES_DIR / f"{name}.parquet")

    table = pd.DataFrame(rows).set_index("configuration")
    pd.set_option("display.width", 200, "display.max_columns", 50)
    print("\n" + "=" * 78)
    print("COMPARISON TABLE — IN-SAMPLE. Fills at the model mark; no bid/ask, no slippage.")
    print("=" * 78)
    print(table.round(4).to_string())

    trig, agreement = trigger_comparison(configs)
    print("\n" + "-" * 78)
    print("AC-3b — DO THE TWO TRIGGERS FIRE TOGETHER?")
    print("-" * 78)
    if agreement is None:
        print("not enough monetisations under both triggers to compare")
    else:
        print(f"drawdown trigger fired {agreement['n_drawdown']} times, "
              f"multiple trigger {agreement['n_multiple']} times")
        print(f"median gap to the nearest counterpart firing: "
              f"{agreement['median_gap_bars']:.0f} bars")
        print(f"share of drawdown firings within 5 bars of a multiple firing: "
              f"{agreement['share_within_5_bars']:.1%}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT_DIR / "comparison_table.csv")
    trig.to_csv(OUT_DIR / "trigger_events.csv", index=False)
    fig_name = "monetise_drawdown"
    fig = build_figure(fig_name, configs[fig_name], states[fig_name], spot, iv)
    fig.write_html(OUT_DIR / f"{fig_name}.html", include_plotlyjs="cdn")

    print(f"\nwritten: {OUT_DIR}")
    print("  comparison_table.csv, trigger_events.csv, "
          f"{fig_name}.html, series/*.parquet ({len(configs)} files)")
    print("\nREMINDER: every number above is IN-SAMPLE and every threshold was hand-picked.")
    print("The best-looking row is NOT a finding. 13-02 runs the grid properly.")
    return table


if __name__ == "__main__":
    main()
