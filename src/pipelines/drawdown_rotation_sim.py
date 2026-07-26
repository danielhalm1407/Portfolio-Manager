"""
DRAWDOWN ROTATION SCENARIO — what happens to realised vs unrealised P&L if I cut the hedge
sleeve during a drawdown and rotate into high beta to ride the recovery?

Runs two books over the same cached price panel:

  * BASELINE  — buy the hedge sleeve on day one and hold. Nothing is ever sold, so realised
                P&L stays at zero and the entire result sits in unrealised.
  * ROTATION  — same opening sleeve, but when the book draws down past `--dd-trigger` it
                sells `--cut-frac` of the sleeve and buys the high-beta basket with the
                proceeds, rotating back once `--recovery-frac` of the loss is clawed back.

The comparison is the point: both books can end at a similar equity while having a
completely different realised/unrealised split, and the rotation's P&L is CRYSTALLISED —
it is booked, taxable and no longer at risk, where the baseline's is still mark-to-market.
That distinction is invisible to a returns-compounding backtester, which is exactly why this
runs on the fill/cost-basis engine in portutils.portfolio instead.

Reads data/processed/prices_<tag>.parquet (see cache_prices.py). Writes the per-bar state
frames, the trade blotters and a summary to outputs/scenarios/.

Usage:
    python src/pipelines/drawdown_rotation_sim.py
    python src/pipelines/drawdown_rotation_sim.py --dd-trigger 0.05 --cut-frac 0.6
"""

import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from portutils.analysis.performance import PerformanceSummary   # noqa: E402
from portutils.portfolio import (                               # noqa: E402
    BuyAndHoldRule,
    DrawdownRotationRule,
    PortfolioSimulator,
)
# The hedge/beta split comes from config/asset_universe.yaml via portutils.utils.config —
# one catalogue for the whole repo, so no two files can disagree about which tickers are
# the hedge and which are the beta. Intersected with the cached panel at load time (below)
# so a role list wider than the parquet does not break the run.
from portutils.utils import config as cfg                       # noqa: E402

OUT_DIR = REPO / "outputs" / "scenarios"


def equal_weights(symbols, total=1.0):
    # Equal-weight the legs within a basket. Deliberately naive: this study is about the
    # ACCOUNTING consequences of the rotation, not about optimal sizing — bringing a
    # weighting model in here would confound the two questions.
    return {s: total / len(symbols) for s in symbols}


def roles_in_panel(prices):
    # Resolve the hedge and beta legs for THIS panel: the catalogue's role lists intersected
    # with the columns actually present. Intersecting matters because the YAML describes the
    # whole investable universe while a cached parquet holds only one study's slice.
    hedge = [t for t in cfg.tickers_by_role("hedge") if t in prices.columns]
    beta = [t for t in cfg.tickers_by_role("beta") if t in prices.columns]
    return hedge, beta


def run_scenarios(prices, capital, dd_trigger, cut_frac, recovery_frac, max_rotations,
                  sleeve_weight, slippage_k):
    hedge, beta = roles_in_panel(prices)
    sleeve = equal_weights(hedge, total=sleeve_weight)
    growth = equal_weights(beta)

    # BASELINE: identical opening book, no policy. Without this the rotation's numbers have
    # nothing to be judged against.
    hold_sim = PortfolioSimulator(prices, [BuyAndHoldRule(sleeve)],
                                  starting_capital=capital, slippage_k=slippage_k)
    hold = hold_sim.run()

    rule = DrawdownRotationRule(
        sleeve_weights=sleeve, growth_weights=growth,
        dd_trigger=dd_trigger, cut_frac=cut_frac,
        recovery_frac=recovery_frac, max_rotations=max_rotations,
    )
    rot_sim = PortfolioSimulator(prices, [rule], starting_capital=capital,
                                 slippage_k=slippage_k)
    rot = rot_sim.run()
    return (hold, hold_sim, rot, rot_sim, rule)


def summarise(hold, rot, capital):
    # Head-to-head on the figures this study exists to compare. Equity and total P&L say
    # who ended richer; the realised/unrealised split says how much of that is banked.
    def tail(df, col):
        return float(df.iloc[-1][col])

    rows = []
    for name, df in (("hold", hold), ("rotate", rot)):
        rows.append({
            "book": name,
            "final_equity": tail(df, "equity"),
            "total_pnl": tail(df, "TOTAL_total_pnl"),
            "realised_pnl": tail(df, "TOTAL_realised_pnl"),
            "unrealised_pnl": tail(df, "TOTAL_unrealised_pnl"),
            "return_pct": 100.0 * (tail(df, "equity") / capital - 1.0),
            "max_drawdown_pct": 100.0 * float(df["drawdown"].max()),
        })
    return pd.DataFrame(rows).set_index("book")


def performance_table(hold, rot):
    # Reuse the repo's existing metric stack rather than hand-rolling ratios: convert each
    # book's equity path to simple returns and hand it to PerformanceSummary, which expects
    # a `time` column plus one column per return series.
    eq = pd.DataFrame({
        "hold": hold["equity"].astype(float),
        "rotate": rot["equity"].astype(float),
    })
    # LOG returns. PerformanceSummary now handles kind="simple" correctly too (its resampled
    # compounding was fixed), and the two agree exactly — this path stays on logs simply
    # because it already did and there is nothing to gain from churning it.
    rets = np.log(eq).diff().dropna().reset_index()
    # PerformanceSummary keys off a column literally named `time`; the ledger's index is
    # named "ts", so rename whatever came out of reset_index() (position 0) to match.
    rets = rets.rename(columns={rets.columns[0]: "time"})
    return PerformanceSummary(rets, ret_cols=["hold", "rotate"], kind="log",
                              include_vol=True, include_drawdown=True).run()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", default="hedge_rotation")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    # A 3% trigger is deliberate on a 1-year ETF panel: a 10% threshold may simply never
    # fire in a calm sample, and a policy that never triggers teaches nothing.
    ap.add_argument("--dd-trigger", type=float, default=0.03)
    ap.add_argument("--cut-frac", type=float, default=0.5)
    ap.add_argument("--recovery-frac", type=float, default=0.5)
    ap.add_argument("--max-rotations", type=int, default=3)
    ap.add_argument("--sleeve-weight", type=float, default=1.0,
                    help="fraction of starting capital deployed into the sleeve on day one")
    ap.add_argument("--slippage-k", type=float, default=0.0,
                    help="proportional slippage per fill (0.0005 = 5bps against you)")
    args = ap.parse_args()

    path = REPO / "data" / "processed" / f"prices_{args.tag}.parquet"
    if not path.exists():
        raise SystemExit(f"{path} not found — run: python src/pipelines/cache_prices.py "
                         f"--tag {args.tag} --from-csv")
    prices = pd.read_parquet(path)

    hold, hold_sim, rot, rot_sim, rule = run_scenarios(
        prices, args.capital, args.dd_trigger, args.cut_frac, args.recovery_frac,
        args.max_rotations, args.sleeve_weight, args.slippage_k)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hold.to_csv(OUT_DIR / f"{args.tag}_hold_state.csv")
    rot.to_csv(OUT_DIR / f"{args.tag}_rotate_state.csv")
    rot_sim.blotter().to_csv(OUT_DIR / f"{args.tag}_rotate_blotter.csv", index=False)
    events = pd.DataFrame(rule.events, columns=["ts", "event", "equity", "drawdown"])
    events.to_csv(OUT_DIR / f"{args.tag}_rotate_events.csv", index=False)

    summary = summarise(hold, rot, args.capital)
    summary.to_csv(OUT_DIR / f"{args.tag}_summary.csv")
    # Risk/return metrics come from the repo's existing stack, not from anything hand-rolled
    # here — same numbers the rest of the research uses. Saved rather than printed: it is a
    # wide multi-section frame that does not read well in a console.
    performance_table(hold, rot).to_csv(OUT_DIR / f"{args.tag}_performance.csv")

    print(f"\nprices: {len(prices)} bars, {prices.index[0].date()} to {prices.index[-1].date()}")
    _hedge, _beta = roles_in_panel(prices)
    print(f"sleeve: {sorted(_hedge)}   growth: {sorted(_beta)}")
    print(f"\npolicy events ({len(events)}):")
    print(events.to_string(index=False))
    print("\nhead-to-head:")
    print(summary.round(2).to_string())

    # The single sentence this whole pipeline exists to produce.
    banked = summary.loc["rotate", "realised_pnl"]
    carried = summary.loc["rotate", "unrealised_pnl"]
    print(f"\nrotation booked {banked:,.0f} of realised P&L and still carries "
          f"{carried:,.0f} unrealised; the hold book carries "
          f"{summary.loc['hold', 'unrealised_pnl']:,.0f} with nothing banked.")
    print(f"\nwrote state/blotter/summary CSVs to {OUT_DIR}")


if __name__ == "__main__":
    main()
