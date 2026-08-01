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


def _tidy(df):
    # get_equity_data's combined output is Date + one column per symbol. Normalise to a
    # DatetimeIndex of bars with float columns — the exact shape PortfolioSimulator wants.
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col])
    out = out.sort_values(date_col).set_index(date_col)
    out.index.name = "ts"
    # Forward-fill isolated gaps (a symbol that did not print on a holiday) but drop any
    # leading rows still missing a quote — the sim must never mark a leg at a made-up price.
    out = out.astype(float).ffill().dropna(how="any")
    return out


def from_csv(symbols, csv_path):
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
    return _tidy(df[[date_col] + have])


def from_ibkr(symbols, duration, bar_size):
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
    return _tidy(df)


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
    args = ap.parse_args()

    # Symbols come from the named portfolio unless explicitly overridden. Resolving through
    # config (not a literal here) is what keeps this pipeline, the study and the charts
    # agreeing on one universe.
    symbols = args.symbols or list(cfg.portfolio_weights(args.portfolio))
    # Tag defaults to the portfolio name so the parquet is self-describing.
    tag = args.tag or args.portfolio

    prices = (from_csv(symbols, pathlib.Path(args.csv)) if args.from_csv
              else from_ibkr(symbols, args.duration, args.bar_size))

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / f"prices_{tag}.parquet"
    prices.to_parquet(out)
    # ASCII only in console output — the Windows console here is cp1252 and would raise
    # UnicodeEncodeError on an arrow glyph.
    print(f"wrote {out}  ({len(prices)} bars x {len(prices.columns)} symbols, "
          f"{prices.index[0].date()} to {prices.index[-1].date()})")


if __name__ == "__main__":
    main()
