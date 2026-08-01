"""
CANCEL OPEN ORDERS — the undo for src/pipelines/rebalance_live.py.

A rebalance that goes wrong leaves working (or held) orders behind, and until this file
existed there was no way to clear them except by hand in TWS. It is deliberately a separate,
tiny pipeline rather than a flag on the rebalancer: the thing you reach for when something
has gone wrong must not share a code path with the thing that went wrong.

═══════════════════════════════════════════════════════════════════════════════
WHAT "OPEN" MEANS HERE, AND WHY --all-clients IS THE DEFAULT
═══════════════════════════════════════════════════════════════════════════════
``reqOpenOrders`` returns only orders placed by THIS client id. An order submitted by
rebalance_live (client 151) is invisible to a session connecting as 152, which is exactly
how you end up believing you cancelled something you did not. ``reqAllOpenOrders`` covers
every client on the account, and ``reqGlobalCancel`` likewise cancels across all of them —
so both defaults here are the wide ones.

Held orders count. An order sitting in the TWS Pending panel with a blue Transmit button
(IB warning 10311, direct-routed precaution) is NOT working, but it is still an order and
still needs cancelling before it can be replaced.

═══════════════════════════════════════════════════════════════════════════════
SAFETY MODEL — INVERTED RELATIVE TO THE REBALANCER
═══════════════════════════════════════════════════════════════════════════════
For rebalance_live the dangerous act is trading, so the default is a dry run. Here the
dangerous act is cancelling something you wanted to keep, so:

    default              LIST the open orders, cancel nothing
    --cancel             cancel them, one id at a time, and verify
    --global             use reqGlobalCancel instead of per-id cancels
    --ids 3,4            cancel only these

Cancellation is a REQUEST, not a guarantee. Every path re-reads the open orders afterwards
and reports what actually survived, rather than assuming the call worked.

Usage:
    python src/pipelines/cancel_orders.py                # list only
    python src/pipelines/cancel_orders.py --cancel       # cancel every open order
    python src/pipelines/cancel_orders.py --cancel --ids 3,4
"""

import argparse
import pathlib
import sys
import time

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from portutils.utils import config as cfg   # noqa: E402


def open_orders_table(ib, app, all_clients=True):
    """Current open orders, with the columns that matter for deciding what to kill."""
    df = ib.get_open_orders_data(app, all_clients=all_clients)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    cols = [c for c in ("orderId", "symbol", "action", "totalQuantity", "orderType",
                        "exchange", "currency", "status", "filled", "remaining")
            if c in df.columns]
    return df[cols].sort_values("orderId") if cols else df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cancel", action="store_true",
                    help="actually cancel; without this the orders are only listed")
    ap.add_argument("--global", dest="use_global", action="store_true",
                    help="use reqGlobalCancel (every open order, every client) instead of "
                         "cancelling id by id")
    ap.add_argument("--ids", default=None,
                    help="comma-separated order ids to cancel instead of all of them")
    ap.add_argument("--this-client-only", action="store_true",
                    help="only list/cancel orders placed by this client id (default is "
                         "every client on the account)")
    # 152 sits beside the rebalancer's 151 so the two can never collide; TWS misbehaves
    # silently when two connections share an id.
    ap.add_argument("--client-id", type=int, default=152)
    args = ap.parse_args()

    settings = dict(cfg.SETTINGS.get("live_trading") or {})
    account = settings.get("account", "DUP102412")

    from portutils.ingestion import ibkr_requests as ib
    app = ib.IBApp()
    app.start(client_id=args.client_id)
    try:
        before = open_orders_table(ib, app, all_clients=not args.this_client_only)
        if before.empty:
            print(f"account {account}: no open orders.")
            return
        print(f"account {account}: {len(before)} open order(s)")
        print(before.to_string(index=False))

        if not args.cancel:
            print("\nlisted only — pass --cancel to actually cancel them.")
            return

        # --- Which ones -----------------------------------------------------
        if args.ids:
            wanted = [int(x) for x in args.ids.split(",") if x.strip()]
            missing = [i for i in wanted if i not in set(before["orderId"])]
            if missing:
                print(f"WARNING: ids {missing} are not in the open-order list; skipping them.")
            wanted = [i for i in wanted if i not in missing]
        else:
            wanted = [int(i) for i in before["orderId"]]

        if not wanted and not args.use_global:
            print("nothing to cancel.")
            return

        # --- Cancel ---------------------------------------------------------
        if args.use_global:
            # The blunt instrument: every open order on the account, whoever placed it.
            ib.cancel_all_orders(app)
        else:
            # One at a time, so a single unknown/stale id cannot abort the rest.
            for order_id in wanted:
                try:
                    ib.cancel_order(app, order_id)
                except Exception as exc:
                    print(f"  cancel failed for orderId={order_id}: {exc}")

        # --- Verify ---------------------------------------------------------
        # Cancellation is asynchronous. Re-reading the book is the ONLY way to know it
        # happened; the call returning proves nothing, which is the same lesson placeOrder
        # taught in rebalance_live.
        time.sleep(2.0)
        after = open_orders_table(ib, app, all_clients=not args.this_client_only)
        if after.empty:
            print("\nall open orders are gone.")
        else:
            print(f"\n{len(after)} order(s) STILL OPEN after the cancel request:")
            print(after.to_string(index=False))
            print("Re-run to try again, or clear them by hand in TWS.")
    finally:
        # Same drain as the rebalancer: disconnect() stops the reader thread outright, so
        # a cancel acknowledgement still in flight would be lost.
        time.sleep(1.0)
        ib.disconnect_ib(app)


if __name__ == "__main__":
    main()
