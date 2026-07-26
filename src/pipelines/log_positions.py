"""
DAILY POSITION SNAPSHOT — the only permanent fix for the 7-day history ceiling.

TWS serves roughly the last 7 days of executions and nothing before that (see
``get_executions_data``). Everything earlier has to be reconstructed under an assumption,
with all the caveats in ``portutils.portfolio.backcast``. That problem is unavoidable for
the past — but entirely avoidable for the future, and it costs one small parquet a day.

Run this on a schedule (or just whenever TWS is up). Each run appends one row per holding
plus the account totals. After a few weeks the reconstruction question becomes moot for
anything going forward: the position path is simply *recorded* rather than inferred.

**Every day this does not run is a day of history that cannot be recovered later.** That is
the entire argument for it, and the reason it was built before the more interesting scripts.

Writes:
    data/raw/ibkr/positions_YYYY-MM-DD.parquet   one file per day (idempotent per date)
    data/raw/ibkr/positions_history.parquet      the accumulated append-only panel

`data/raw/` is append-only by repo convention, so a re-run for a date that already exists
replaces only that date's rows and leaves every other day untouched.

Usage:
    python src/pipelines/log_positions.py
    python src/pipelines/log_positions.py --account DUP102412 --client-id 131
"""

import argparse
import datetime as dt
import pathlib
import sys

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from portutils.ingestion import ibkr_requests as ib   # noqa: E402

OUT_DIR = REPO / "data" / "raw" / "ibkr"
HISTORY = OUT_DIR / "positions_history.parquet"


def snapshot(app, account):
    """One dated snapshot: every holding plus the account-level totals."""
    # Per-holding rows. updatePortfolio carries position, averageCost and IB's own P&L
    # figures — everything needed to rebuild the book later via book_from_portfolio.
    portfolio = ib.get_account_updates(app=app, account=account)

    # Account totals. NetLiquidation is the equity the weights were sized against, and
    # without it a historical weight cannot be reconstructed from positions alone.
    acct = ib.get_account_data(app=app)
    def tag(name):
        row = acct.loc[acct["tag"] == name, "value"]
        return float(row.iloc[0]) if len(row) else float("nan")

    if len(portfolio) == 0:
        # An empty account is a legitimate observation, not an error: record the date with
        # no holdings rather than skipping it and leaving a hole in the panel.
        portfolio = pd.DataFrame(columns=["symbol", "position", "averageCost"])

    portfolio = portfolio.copy()
    # Stamp the observation date on every row — this is what makes the accumulated file a
    # panel rather than a pile of snapshots.
    portfolio["as_of"] = pd.Timestamp(dt.date.today())
    portfolio["account"] = account
    portfolio["net_liquidation"] = tag("NetLiquidation")
    portfolio["total_cash"] = tag("TotalCashValue")
    return portfolio


def append_history(day_df, path=HISTORY):
    """Merge one day into the accumulated panel, replacing that date if it is already there.

    Replace-by-date rather than blind append: re-running on the same day (a common thing to
    do after opening TWS late) must not double-count the holdings.
    """
    if path.exists():
        hist = pd.read_parquet(path)
        if "as_of" in hist.columns and len(day_df):
            hist = hist[hist["as_of"] != day_df["as_of"].iloc[0]]
        out = pd.concat([hist, day_df], ignore_index=True)
    else:
        out = day_df
    out = out.sort_values(["as_of", "symbol"]).reset_index(drop=True)
    out.to_parquet(path)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", default="DUP102412")
    # A distinct client id per script: TWS silently misbehaves when two connections share
    # one, and this runs alongside the research scripts.
    ap.add_argument("--client-id", type=int, default=131)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    args = ap.parse_args()

    app = ib.IBApp()
    app.start(host=args.host, port=args.port, client_id=args.client_id)
    try:
        day = snapshot(app, args.account)
    finally:
        # Always disconnect, even if the snapshot raised — a dangling connection blocks the
        # next run from claiming the same client id.
        ib.disconnect_ib(app)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    day_path = OUT_DIR / f"positions_{stamp}.parquet"
    day.to_parquet(day_path)
    hist = append_history(day)

    print(f"wrote {day_path}  ({len(day)} holdings)")
    print(f"history now {len(hist)} rows across "
          f"{hist['as_of'].nunique() if 'as_of' in hist else 0} dates")
    if len(day):
        print(day[["symbol", "position", "averageCost", "net_liquidation"]].to_string(index=False))


if __name__ == "__main__":
    main()
