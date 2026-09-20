"""
Cache a wide daily close-price panel to data/processed/ for offline scenario work.

Why this exists: the accounting/scenario stack (``portutils.portfolio``) takes a wide price
DataFrame and does not care where it came from, but a study that re-pulls from TWS on every
run is neither reproducible nor runnable with the gateway closed. This pipeline does the
one-time pull and lands a parquet that every later run reads.

Two modes:
  * default          — pull from IBKR via ``get_equity_data`` (needs TWS/Gateway running).
  * ``--from-csv``   — normalise an existing raw CSV panel (the one get_equity_data already
                       wrote to data/raw/market/) without touching the network.

Side-effecting by design, hence a pipeline and not library code (see src/CLAUDE.md).

The universe is never hardcoded here: ``--portfolio`` names a weight vector in
``config/asset_universe.yaml`` and its tickers become the fetch list.

Usage:
    python src/pipelines/cache_prices.py --portfolio spy_kmlm --from-csv
    python src/pipelines/cache_prices.py --portfolio hedge_sleeves
"""

import argparse
import pathlib
import sys

import pandas as pd

# Repo root is two levels up from src/pipelines/, so data/ resolves regardless of the
# working directory the script is launched from.
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

RAW_DIR = REPO / "data" / "raw" / "market"
PROCESSED_DIR = REPO / "data" / "processed"

# The universe is NOT defined here. Tickers, their hedge/beta/core roles and the named
# weight vectors all live in config/asset_universe.yaml, reached through
# portutils.utils.config — so adding an instrument to a study is a YAML edit, not a code
# edit, and no two files can disagree about what counts as a hedge.
from portutils.utils import config as cfg   # noqa: E402  (needs the sys.path insert above)


def _tidy(df, ragged=False):
    # get_equity_data's combined output is Date + one column per symbol. Normalise to a
    # DatetimeIndex of bars with float columns — the exact shape PortfolioSimulator wants.
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col])
    out = out.sort_values(date_col).set_index(date_col)
    out.index.name = "ts"
    # Forward-fill isolated gaps (a symbol that did not print on a holiday) but drop any
    # leading rows still missing a quote — the sim must never mark a leg at a made-up price.
    out = out.astype(float).ffill()
    # ============================================================================
    # RAGGED-HISTORY BRANCH — added 2026-09-20 (Phase 11, Task 4, Step 0). The
    # unconditional `dropna(how="any")` below is the IDENTICAL truncation defect as
    # PanelBuilder._load, one layer EARLIER: it runs here, in the code that WRITES the
    # parquet. A SPY-from-1993 series cached beside a QQQ-from-1999 series would be
    # truncated to 1999 in the FILE ITSELF, before PanelBuilder ever runs and before
    # COVERAGE.md is generated — baking the loss into the very file the coverage report
    # is meant to describe.
    #
    # The comment above ("the sim must never mark a leg at a made-up price") is CORRECT
    # for a simulator frame and WRONG for a coverage frame — they are not the same
    # frame, and both needs are real. Unlike panel.py, this does not need to be an
    # opt-in default: cache_prices.py WRITES NEW FILES UNDER NEW TAGS, so a long-history
    # tag has no existing reader to regress. `ragged=False` (the default) keeps the
    # exact current behaviour so the three protected tags (spy_kmlm, hedge_rotation,
    # holdings) reproduce byte-for-byte on a re-run.
    # ============================================================================
    if ragged:
        return out
    return out.dropna(how="any")


def from_csv(symbols, csv_path, ragged=False):
    # Offline path: reuse the panel get_equity_data already persisted.
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} not found — run without --from-csv once with TWS open to create it")
    df = pd.read_csv(csv_path)
    have = [s for s in symbols if s in df.columns]
    missing = sorted(set(symbols) - set(have))
    if missing:
        # Loud rather than silent: a scenario quietly missing its growth leg would still
        # "work" and produce a completely different answer.
        raise KeyError(f"{csv_path.name} is missing {missing}; re-pull with --tag to refresh")
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    return _tidy(df[[date_col] + have], ragged=ragged)


def from_ibkr(symbols, duration, bar_size, ragged=False):
    # Live path: one combined pull for the whole universe. output_dir keeps the raw CSV in
    # sync too, so a later --from-csv run has something to read (data/raw/ is append-only,
    # per the repo convention — transformations land in data/processed/).
    from portutils.ingestion.ibkr_requests import get_equity_data
    df = get_equity_data(
        symbols=list(symbols),
        duration=duration,
        bar_size=bar_size,
        output_dir=str(RAW_DIR),
        output_format="combined",
        merged_filename="portfolio_prices.csv",
        skip_existing=False,
    )
    return _tidy(df, ragged=ragged)


def from_long_history(symbols, bar_size="1 day", what_to_show="TRADES"):
    """Phase 11 long-history path: portutils.ingestion.history, IBKR primary with a
    yfinance fallback (2026-09-20 checkpoint decision), one shared TWS connection
    reused across symbols rather than reconnecting per ticker.

    Returns (prices, provenance): `prices` is the ragged wide close-price frame (NOT
    truncated to the latest first-bar — see history.py's module docstring and
    `_tidy`'s ragged branch); `provenance` is a same-shaped frame of 'ibkr' / 'yfinance'
    per symbol per date, so AC-2's "every row records which source produced it" is
    checkable independently of the price values.
    """
    from portutils.ingestion.history import fetch_long_history
    from portutils.ingestion.ibkr_requests import IBApp, connect_ib

    app = IBApp()
    connect_ib(app, host="127.0.0.1", port=7497, client_id=502)
    try:
        per_symbol = {
            sym: fetch_long_history(sym, app=app, bar_size=bar_size, what_to_show=what_to_show)
            for sym in symbols
        }
    finally:
        app.disconnect()

    price_cols = {sym: df[sym] for sym, df in per_symbol.items()}
    source_cols = {sym: df[f"{sym}_source"] for sym, df in per_symbol.items()}
    # outer join, deliberately: this IS the ragged case Task 4 exists for — a series
    # that starts later must keep its own coverage window rather than being cut to
    # the panel's latest-first-bar. No ffill, no dropna here; `_tidy(..., ragged=True)`
    # only re-sorts/re-types, it does not re-introduce the truncation.
    prices = pd.concat(price_cols, axis=1).sort_index()
    provenance = pd.concat(source_cols, axis=1).sort_index()
    prices.index.name = "ts"
    provenance.index.name = "ts"
    return prices, provenance


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio", default="spy_kmlm",
                    help="named weight vector in config/asset_universe.yaml; its tickers "
                         "become the symbol list to fetch")
    ap.add_argument("--tag", default=None,
                    help="output name: data/processed/prices_<tag>.parquet (default: --portfolio)")
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="explicit ticker override; normally leave unset and let "
                         "--portfolio resolve it from the YAML")
    ap.add_argument("--duration", default="1 Y")
    ap.add_argument("--bar-size", default="1 day")
    ap.add_argument("--from-csv", action="store_true",
                    help="skip IBKR; normalise data/raw/market/portfolio_prices.csv instead")
    ap.add_argument("--csv", default=str(RAW_DIR / "portfolio_prices.csv"))
    ap.add_argument("--ragged", action="store_true",
                    help="retain each symbol's full history instead of truncating the whole "
                         "panel to the latest first-bar across the universe. Only for NEW "
                         "tags — the protected tags (spy_kmlm, hedge_rotation, holdings) must "
                         "never be re-run with this set, or their bytes would change")
    ap.add_argument("--long-history", action="store_true",
                    help="Phase 11 path: portutils.ingestion.history (paginated IBKR fetch, "
                         "yfinance fallback, per-symbol provenance). Implies --ragged; writes "
                         "a companion provenance_<tag>.csv alongside the prices parquet")
    args = ap.parse_args()

    # Symbols come from the named portfolio unless explicitly overridden. Resolving through
    # config (not a literal here) is what keeps this pipeline, the study and the charts
    # agreeing on one universe.
    symbols = args.symbols or list(cfg.portfolio_weights(args.portfolio))
    # Tag defaults to the portfolio name so the parquet is self-describing.
    tag = args.tag or args.portfolio

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / f"prices_{tag}.parquet"

    if args.long_history:
        prices, provenance = from_long_history(symbols, bar_size=args.bar_size)
        provenance.to_csv(PROCESSED_DIR / f"provenance_{tag}.csv")
    else:
        prices = (from_csv(symbols, pathlib.Path(args.csv), ragged=args.ragged) if args.from_csv
                  else from_ibkr(symbols, args.duration, args.bar_size, ragged=args.ragged))

    prices.to_parquet(out)
    # ASCII only in console output — the Windows console here is cp1252 and would raise
    # UnicodeEncodeError on an arrow glyph.
    print(f"wrote {out}  ({len(prices)} bars x {len(prices.columns)} symbols, "
          f"{prices.index[0].date()} to {prices.index[-1].date()})")


if __name__ == "__main__":
    main()
