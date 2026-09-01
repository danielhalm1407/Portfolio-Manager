"""
REBALANCING AS THE SOURCE OF REALISED P&L — three books, one price panel.

The question behind this pipeline: `research/hedge_sleeves/hedge_sleeves.py` hands a
CONSTANT weight vector to ``PanelBuilder.attribution``, which computes ``contrib =
weights * rets``. Holding weights fixed on every bar is mathematically a daily-rebalanced
constant-mix portfolio — it already assumes you trade back to target every day, selling
whatever rallied and buying whatever fell. When the hedge sleeve rallies into an equity
drawdown, that model is quietly selling the hedge at the high, every single day.

But a weights-times-returns model has no units and no cost basis, so it cannot show the
consequence: the REALISED P&L that rebalancing crystallises, or the turnover it costs.
This pipeline runs the same idea through the fill-based accounting engine, where both are
visible, and answers a second question at the same time — whether the discretionary
drawdown-rotation overlay adds anything on top of plain rebalancing.

Three books over one panel:
    drift        BuyAndHoldRule            — no rebalancing. Realised P&L is exactly zero.
    mix          ConstantMixRule(every=1)  — the hedge_sleeves.py assumption made explicit.
    mix+overlay  ConstantMix + Drawdown    — does the cutoff rule earn its complexity?

Reads data/processed/prices_<tag>.parquet (see cache_prices.py); universe, weights and
roles come from config/asset_universe.yaml. Writes per-bar state, blotters, a summary and
five HTML figures to outputs/scenarios/.

Usage:
    python src/pipelines/rebalance_study.py
    python src/pipelines/rebalance_study.py --portfolio spy_kmlm --capital 1000000
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

# The below line of code mechanically:
# use pathlib.Path(__file__) to get the path of this file (rebalance_study.py)
# then .resolve() to get the absolute path of this file
# then .parents[2] to go up two directories (from src/pipelines/rebalance
REPO = pathlib.Path(__file__).resolve().parents[2]
# This script is run directly (python src/pipelines/rebalance_study.py), not imported as part
# of an installed package, so `portutils` is NOT on sys.path yet. Adding src/ here is what
# makes the imports below resolve — which is why they cannot be moved to the top of the file.
sys.path.insert(0, str(REPO / "src"))

# ---------------------------------------------------------------------------
# Two things about the import block below that look odd but are deliberate:
#
# 1. `# noqa: E402` — "noqa" is the standard flake8/ruff pragma for "skip the linter check
#    on this line". E402 is the rule "module level import not at top of file". These imports
#    legitimately break that rule: they must come AFTER the sys.path.insert above, or
#    portutils would not be importable at all. The pragma says "this is intentional" so a
#    linter run does not flag every line here as an error.
#
# 2. These names live in portutils/portfolio/RULES.py (and simulator.py), not directly in
#    portfolio/__init__.py — yet we import them from `portutils.portfolio`. That works
#    because __init__.py re-exports them (`from .rules import BuyAndHoldRule, ...`), binding
#    a second name to the SAME class object; `portutils.portfolio.BuyAndHoldRule is
#    portutils.portfolio.rules.BuyAndHoldRule` is True, not a copy.
#
#    Importing from the package (rather than the submodule) is the intended public API: the
#    __all__ list in the portutils/__init__.py defines what is meant to be used from outside, 
#    & the submodule split (rules / book / fills / ledger / simulator / ibkr_sync / backcast) — is
#    internal organisation. Callers should not have to track which file a class lives in, and
#    if one moves between modules later, package-level imports keep working unchanged.
# ---------------------------------------------------------------------------
from portutils.analysis.performance import (                         # noqa: E402
    PerformanceSummary,
    ReturnAttribution,
)
from portutils.portfolio import (                                    # noqa: E402
    BuyAndHoldRule,
    ConstantMixRule,
    DrawdownRotationRule,
    PortfolioSimulator,
)
from portutils.utils import config as cfg                            # noqa: E402
from portutils.viz.dash_timeseries_app import make_level_figure      # noqa: E402
from portutils.viz import theme                                      # noqa: E402

OUT_DIR = REPO / "outputs" / "scenarios"

# Palettes are DEFINED in portutils.viz.theme, not here — a pipeline should not own the
# repo's colour vocabulary, and research/rebalance_realisation.py imports these names from
# this module (`study.TICKER_COLOURS`). Re-exported under the same names so that keeps working.
BOOK_COLOURS = theme.BOOK_COLOURS
TICKER_COLOURS = theme.TICKER_COLOURS
# The slot-based assigner for tickers TICKER_COLOURS does not pin — re-exported alongside the
# dicts so a research script importing `study` gets the whole colour vocabulary from one place.
ticker_colour_map = theme.ticker_colour_map
SPLIT_COLOURS = theme.SPLIT_COLOURS
RESIDUAL_LABEL = theme.RESIDUAL_LABEL
RESIDUAL_COLOUR = theme.RESIDUAL_COLOUR

# Shared figure styling — the hedge_sleeves.py call style, factored out so five figures do
# not repeat eleven keyword arguments each.
FIG_KW = dict(
    reindex=False,
    auto_colour_map=False,
    auto_label_map=False,
    x_tick_label_mode="year_month",
    font_color=theme.INK,
    fig_height=600,
    show_plotly_stack_mode_buttons=False,
    close_hour=99,
)


# ---------------------------------------------------------------------------
# BOOKS
# ---------------------------------------------------------------------------
def run_books(prices, weights, capital, dd_trigger, cut_frac, recovery_frac,
              max_rotations, slippage_k):
    # Every book starts from the same capital and the same target weights; the ONLY thing
    # that differs is the rule set. That is what makes the comparison attributable to the
    # policy rather than to sizing.
    books = {}

    # drift — open the target book once, then never trade again. Weights wander with the
    # market. Realised P&L must come out at exactly zero; it is the control.
    books["drift"] = PortfolioSimulator(
        prices, [BuyAndHoldRule(weights)],
        starting_capital=capital, slippage_k=slippage_k)

    # mix — rebalance to target every bar. Sizing off live equity makes it contrarian:
    # sell what rallied, buy what fell, continuously and without any trigger.
    books["mix"] = PortfolioSimulator(
        prices, [ConstantMixRule(weights, every=1)],
        starting_capital=capital, slippage_k=slippage_k)

    # mix+overlay — the same daily rebalance PLUS the discretionary drawdown cut. Both
    # rules propose deltas into the same book and the simulator merges them, so this is
    # strictly "rebalancing, and also the overlay" — the clean test of what the overlay adds.
    hedge = [t for t in weights if cfg.asset_role(t) == "hedge"]
    beta = [t for t in weights if cfg.asset_role(t) == "beta"]
    overlay = DrawdownRotationRule(
        sleeve_weights={t: weights[t] for t in hedge},
        growth_weights={t: weights[t] for t in beta},
        dd_trigger=dd_trigger, cut_frac=cut_frac,
        recovery_frac=recovery_frac, max_rotations=max_rotations,
    )
    books["mix+overlay"] = PortfolioSimulator(
        prices, [ConstantMixRule(weights, every=1), overlay],
        starting_capital=capital, slippage_k=slippage_k)

    frames = {name: sim.run() for name, sim in books.items()}
    return frames, books, overlay


# ---------------------------------------------------------------------------
# TABLES
# ---------------------------------------------------------------------------
def summarise(frames, capital):
    # Head-to-head on the figures the study exists to compare. Equity says who ended richer;
    # the realised/unrealised split says how much of that is banked rather than still at
    # risk; turnover says what it cost to get there.
    rows = []
    for name, df in frames.items():
        last = df.iloc[-1]
        rows.append({
            "book": name,
            "final_equity": float(last["equity"]),
            "total_pnl": float(last["TOTAL_total_pnl"]),
            "realised_pnl": float(last["TOTAL_realised_pnl"]),
            "unrealised_pnl": float(last["TOTAL_unrealised_pnl"]),
            "return_pct": 100.0 * (float(last["equity"]) / capital - 1.0),
            "max_drawdown_pct": 100.0 * float(df["drawdown"].max()),
            "turnover_cum": float(last["turnover_cum"]),
            # Turnover as a multiple of starting capital — the intuitive "how many times
            # did I trade my whole book" figure.
            "turnover_x_capital": float(last["turnover_cum"]) / capital,
            "n_trades": int(df["n_trades"].sum()),
        })
    return pd.DataFrame(rows).set_index("book")


def realised_by_ticker(frames, weights):
    # WHICH leg did the crystallising. This is the table that answers the original question:
    # if rebalancing works the way the mechanic suggests, the hedge legs should show
    # positive realised P&L (sold into their rallies) even when the book overall is down.
    rows = []
    for name, df in frames.items():
        last = df.iloc[-1]
        for t in weights:
            col = f"{t}_realised_pnl"
            if col in df.columns:
                rows.append({"book": name, "ticker": t, "role": cfg.asset_role(t),
                             "realised_pnl": float(last[col])})
    out = pd.DataFrame(rows)
    return out.pivot_table(index=["role", "ticker"], columns="book",
                           values="realised_pnl", aggfunc="sum")


def slippage_sweep(prices, weights, capital, ks):
    # A daily rebalance trades ~250x a year. At zero cost it looks free; the sweep shows
    # where that stops being true. Reported next to the headline so the realised-P&L result
    # is never quoted without its cost.
    rows = []
    for k in ks:
        df = PortfolioSimulator(prices, [ConstantMixRule(weights, every=1)],
                                starting_capital=capital, slippage_k=k).run()
        last = df.iloc[-1]
        rows.append({
            "slippage_bps": k * 1e4,
            "final_equity": float(last["equity"]),
            "realised_pnl": float(last["TOTAL_realised_pnl"]),
            "turnover_cum": float(last["turnover_cum"]),
            "cost_vs_zero": np.nan,   # filled below once the k=0 row exists
        })
    out = pd.DataFrame(rows).set_index("slippage_bps")
    if 0.0 in out.index:
        out["cost_vs_zero"] = out["final_equity"] - out.loc[0.0, "final_equity"]
    return out


def performance_table(frames):
    # Metrics from the repo's existing stack rather than anything hand-rolled here. Simple
    # returns now that PerformanceSummary's simple-return compounding is fixed.
    eq = pd.DataFrame({name: df["equity"].astype(float) for name, df in frames.items()})
    rets = eq.pct_change().dropna().reset_index()
    rets = rets.rename(columns={rets.columns[0]: "time"})
    return PerformanceSummary(rets, ret_cols=list(frames), kind="simple",
                              include_vol=True, include_drawdown=True).run()


# ---------------------------------------------------------------------------
# WEIGHT PATHS — the input the attribution chart actually deserves
# ---------------------------------------------------------------------------
def actual_weights(df, tickers):
    # Each leg's REAL weight at each bar: mark value over equity, straight out of the
    # accounting. This replaces PanelBuilder.simulate_weights (which hardcodes 1/N and so
    # cannot express a 60/40 target) and it beats a constant vector: it is the weight path
    # the book actually had, including whatever the overlay traded.
    w = pd.DataFrame(index=df.index)
    for t in tickers:
        col = f"{t}_mark_value"
        if col in df.columns:
            w[t] = df[col].astype(float) / df["equity"].astype(float)
    return w


# ---------------------------------------------------------------------------
# FIGURES
# ---------------------------------------------------------------------------
def _plot_df(frame_dict, index):
    # make_level_figure wants a plain frame with a 'time' column, not a DatetimeIndex.
    out = pd.DataFrame(frame_dict, index=index)
    out["time"] = out.index
    return out.reset_index(drop=True)


def fig_attribution(frames, weights, prices):
    # FIGURE 0 — the hedge_sleeves.py chart, but per book and driven by ACTUAL weights.
    # contrib_t = w_t * r_t accumulated in portfolio-value units, so the stacked legs sum to
    # the book's cumulative return. The `mix` book's legs and the `drift` book's legs tell
    # visibly different stories precisely because their weight paths differ.
    #
    # The maths is ReturnAttribution's, not this function's — the same implementation
    # PanelBuilder.attribution calls, so this chart and the constant-weight strawman in
    # research/rebalance_realisation.py are finally on one convention (base-100, scaled by
    # V_{t-1}) and can be read side by side.
    rets = prices.pct_change().fillna(0.0)
    figs = {}
    for name, df in frames.items():
        w = actual_weights(df, list(weights)).reindex(rets.index).fillna(0.0)
        res = ReturnAttribution(
            w, rets,
            # These weights are mark_value/equity out of the accounting frame, i.e.
            # POST-trade for their bar, so they must be shifted forward one bar before
            # meeting the return they earned or the attribution looks ahead.
            shift_weights=True,
            # Anchor the wealth index on the book's OWN equity rather than on the sum of
            # contributions, which is what makes the residual below measurable.
            equity=df["equity"],
            # Slippage and any uninvested cash live outside w*r. Give them a leg instead
            # of letting them show up as the stack quietly missing the total.
            residual_label=RESIDUAL_LABEL,
            # No group_map: one leg per ticker is exactly the ungrouped case, so there is
            # no need for a map of singleton lists.
        ).run()
        cum = res.cum_col.copy()
        # Overall comes from the book's own equity path, NOT from re-summing the legs —
        # with the residual present those agree, and taking it from equity means the line
        # is the truth the stack is being checked against.
        cum["overall"] = res.wealth_change
        # Legs in a fixed order: tickers first, residual last, so the cost bucket reads as
        # the remainder it is.
        leg_cols = list(weights) + [RESIDUAL_LABEL]
        figs[name] = make_level_figure(
            _plot_df({c: cum[c] for c in cum.columns}, cum.index),
            cols_of_interest=leg_cols,
            stack_mode="stack_split_sign",
            show_overall_line=True, overall_col="overall",
            overall_label=f"{name} total", overall_colour="white",
            colour_map={**{t: TICKER_COLOURS.get(t, "#6baed6") for t in weights},
                        RESIDUAL_LABEL: RESIDUAL_COLOUR},
            label_map={**{t: f"{t} ({cfg.asset_role(t)}, {weights[t]:.0%})" for t in weights},
                       RESIDUAL_LABEL: RESIDUAL_LABEL},
            figure_title=f"Return attribution on ACTUAL weights - {name}",
            **FIG_KW,
        )
    return figs


def fig_equity(frames):
    # FIGURE 1 — which book ended richer. Plain levels, no stacking.
    idx = next(iter(frames.values())).index
    return make_level_figure(
        _plot_df({n: d["equity"].astype(float) for n, d in frames.items()}, idx),
        cols_of_interest=list(frames),
        colour_map=BOOK_COLOURS,
        label_map={n: n for n in frames},
        figure_title="Equity path by rebalancing policy",
        **FIG_KW,
    )


def fig_split(frames):
    # FIGURE 2 — realised vs unrealised, stacked, per book. The picture that makes "same
    # P&L, different bucket" obvious: drift is pure unrealised, mix carries a growing
    # realised base underneath it.
    figs = {}
    for name, df in frames.items():
        figs[name] = make_level_figure(
            _plot_df({"realised": df["TOTAL_realised_pnl"].astype(float),
                      "unrealised": df["TOTAL_unrealised_pnl"].astype(float),
                      "total": df["TOTAL_total_pnl"].astype(float)}, df.index),
            cols_of_interest=["realised", "unrealised"],
            stack_mode="stack_split_sign",
            show_overall_line=True, overall_col="total",
            overall_label="total P&L", overall_colour="white",
            colour_map=SPLIT_COLOURS,
            label_map={"realised": "realised (banked)",
                       "unrealised": "unrealised (still at risk)"},
            figure_title=f"Realised vs unrealised P&L - {name}",
            **FIG_KW,
        )
    return figs


def fig_realised_by_ticker(frames, weights, book="mix"):
    # FIGURE 3 — cumulative realised P&L per leg for the rebalanced book. If the mechanic
    # is real, the hedge leg's line climbs during equity drawdowns: it is being sold into
    # its own rally, bar after bar.
    df = frames[book]
    cols = [t for t in weights if f"{t}_realised_pnl" in df.columns]
    data = {t: df[f"{t}_realised_pnl"].astype(float) for t in cols}
    data["overall"] = df["TOTAL_realised_pnl"].astype(float)
    return make_level_figure(
        _plot_df(data, df.index),
        cols_of_interest=cols,
        stack_mode="stack_split_sign",
        show_overall_line=True, overall_col="overall",
        overall_label="total realised", overall_colour="white",
        colour_map={t: TICKER_COLOURS.get(t, "#6baed6") for t in cols},
        label_map={t: f"{t} ({cfg.asset_role(t)})" for t in cols},
        figure_title=f"Cumulative realised P&L by ticker - {book}",
        **FIG_KW,
    )


def fig_weights(frames, weights, ticker=None):
    # FIGURE 4 — actual weight vs target, per book, for one leg. Shows WHY mix trades and
    # drift does not: drift's line wanders off target while mix's is pinned to it.
    ticker = ticker or next(iter(weights))
    idx = next(iter(frames.values())).index
    data = {name: actual_weights(d, [ticker])[ticker]
            for name, d in frames.items() if ticker in actual_weights(d, [ticker])}
    data["target"] = pd.Series(weights[ticker], index=idx)
    return make_level_figure(
        _plot_df(data, idx),
        cols_of_interest=list(data),
        colour_map={**BOOK_COLOURS, "target": "#ffffff"},
        label_map={**{n: n for n in frames}, "target": f"target {weights[ticker]:.0%}"},
        figure_title=f"{ticker} weight vs target by policy",
        **FIG_KW,
    )


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio", default="spy_kmlm")
    ap.add_argument("--tag", default=None, help="parquet tag (default: --portfolio)")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--dd-trigger", type=float, default=0.03)
    ap.add_argument("--cut-frac", type=float, default=0.5)
    ap.add_argument("--recovery-frac", type=float, default=0.5)
    ap.add_argument("--max-rotations", type=int, default=3)
    ap.add_argument("--slippage-k", type=float, default=0.0)
    args = ap.parse_args()

    tag = args.tag or args.portfolio
    path = REPO / "data" / "processed" / f"prices_{tag}.parquet"
    if not path.exists():
        raise SystemExit(f"{path} not found - run: python src/pipelines/cache_prices.py "
                         f"--portfolio {args.portfolio} --from-csv")
    prices = pd.read_parquet(path)
    weights = cfg.portfolio_weights(args.portfolio)
    # Only trade what we actually have marks for; a target weight with no price column would
    # silently never be filled and quietly change the book.
    weights = {t: w for t, w in weights.items() if t in prices.columns}

    frames, sims, overlay = run_books(
        prices, weights, args.capital, args.dd_trigger, args.cut_frac,
        args.recovery_frac, args.max_rotations, args.slippage_k)

    summary = summarise(frames, args.capital)
    by_ticker = realised_by_ticker(frames, weights)
    sweep = slippage_sweep(prices, weights, args.capital, [0.0, 0.0005, 0.001])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_csv(OUT_DIR / f"{tag}_{name.replace('+', '_')}_state.csv")
        sims[name].blotter().to_csv(
            OUT_DIR / f"{tag}_{name.replace('+', '_')}_blotter.csv", index=False)
    summary.to_csv(OUT_DIR / f"{tag}_summary.csv")
    by_ticker.to_csv(OUT_DIR / f"{tag}_realised_by_ticker.csv")
    sweep.to_csv(OUT_DIR / f"{tag}_slippage_sweep.csv")
    performance_table(frames).to_csv(OUT_DIR / f"{tag}_performance.csv")

    # ---- figures ----
    figures = {}
    for name, f in fig_attribution(frames, weights, prices).items():
        figures[f"attribution_{name.replace('+', '_')}"] = f
    figures["equity"] = fig_equity(frames)
    for name, f in fig_split(frames).items():
        figures[f"split_{name.replace('+', '_')}"] = f
    figures["realised_by_ticker"] = fig_realised_by_ticker(frames, weights)
    for t in weights:
        figures[f"weights_{t}"] = fig_weights(frames, weights, t)
    for name, fig in figures.items():
        # The dark palette belongs to the SAVED DOCUMENT, not to the figure. A standalone HTML has
        # no Dash page div behind it, so a transparent figure would land on the browser's default
        # white. Applying the theme here — rather than inside the fig_* builders — is what lets
        # those same builders serve research/rebalance_realisation.py, which shows these figures
        # inline in the interactive window where transparency correctly inherits the VS Code theme.
        theme.apply_export_theme(fig)
        fig.write_html(OUT_DIR / f"{tag}_{name}.html", include_plotlyjs="cdn")

    # ---- console report ----
    print(f"\nprices: {len(prices)} bars, {prices.index[0].date()} to {prices.index[-1].date()}")
    print(f"weights: {weights}  roles: {{{', '.join(f'{t}: {cfg.asset_role(t)}' for t in weights)}}}")
    print("\nhead-to-head:")
    print(summary.round(2).to_string())
    print("\nrealised P&L by ticker (who did the crystallising):")
    print(by_ticker.round(2).to_string())
    print("\nslippage sweep (constant-mix daily):")
    print(sweep.round(2).to_string())
    if overlay.events:
        print(f"\noverlay events ({len(overlay.events)}):")
        print(pd.DataFrame(overlay.events,
                           columns=["ts", "event", "equity", "drawdown"]).to_string(index=False))
    else:
        print("\noverlay events: none - the drawdown trigger never fired.")

    d, m = summary.loc["drift"], summary.loc["mix"]
    print(f"\ndrift banked {d['realised_pnl']:,.0f} realised and carries "
          f"{d['unrealised_pnl']:,.0f} unrealised.")
    print(f"mix banked {m['realised_pnl']:,.0f} realised and carries "
          f"{m['unrealised_pnl']:,.0f} unrealised, having traded "
          f"{m['turnover_x_capital']:.1f}x its capital over the window.")
    print(f"\nwrote {len(figures)} HTML figures + CSVs to {OUT_DIR}")


if __name__ == "__main__":
    main()
