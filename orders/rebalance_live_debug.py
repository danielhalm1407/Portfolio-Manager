# %% 1. Import libraries
# 1. Import libraries
#
# CELL-BY-CELL DEBUG HARNESS for src/pipelines/rebalance_live.py.
#
# The pipeline is a one-shot CLI: connect, decide, submit, disconnect, all inside one
# try/finally. That is right for a scheduled run and useless when something goes wrong —
# by the time you read the output the socket is closed, the intermediate frames are gone,
# and the only way to look again is to re-run the whole thing against TWS.
#
# This file runs the SAME decision in `# %%` cells, so portfolio_df, marks, book and orders
# stay live in the namespace and the connection stays open while you poke at them. It was
# written after the first armed run reported seven orders "Submitted" into a completely
# empty TWS Orders panel — a failure that is invisible in a script that hangs up
# immediately and obvious in a session that does not.
#
# ══════════════════════════════════════════════════════════════════════════════
# IT IMPORTS THE PIPELINE'S FUNCTIONS. IT DOES NOT REIMPLEMENT THEM.
# base_currency_marks / build_orders / live_settings are imported from
# src/pipelines/rebalance_live.py. A debug harness with its own copy of the
# arithmetic debugs the harness. Importing that module is safe: its main() sits
# behind `if __name__ == "__main__"`, so nothing runs on import.
#
# THIS FILE, BY CONTRAST, MUST NEVER BE IMPORTED. Its cells connect to TWS and
# (once armed) place orders. Compare orders/rebalance_port_basic.py, whose module
# level ends with a dry_run=False rebalance and a stray live market order — which
# is why tests/test_rebalance_live.py::test_does_not_import_the_cell_script exists.
# ══════════════════════════════════════════════════════════════════════════════

import importlib
import pathlib
import sys

import numpy as np
import pandas as pd
from IPython.display import display

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from portutils.ingestion import ibkr_requests as ib
from portutils.portfolio import book_from_portfolio, reconcile
from portutils.utils import config as cfg

import pipelines.rebalance_live as live

# %% Reload custom packages
# Reload custom packages
#
# Re-run this cell after editing the pipeline or the library; the rest of the cells then
# pick up the change without dropping the TWS connection or losing the frames above.

importlib.reload(ib)
importlib.reload(live)

# %% 2. Settings — READ THE ARM_LIVE NOTE
# 2. Settings — READ THE ARM_LIVE NOTE
#
# ══════════════════════════════════════════════════════════════════════════════
# ARM_LIVE IS THE ONLY THING BETWEEN THIS FILE AND A REAL ORDER.
# Cell 11 opens with `assert ARM_LIVE`, so "Run All" stops there with an
# AssertionError instead of trading. Arming is a deliberate one-line edit, made
# with the order table from cell 8 already on screen. Set it back to False when
# you are done — a file left armed is a file that trades the next time someone
# runs it end to end.
# ══════════════════════════════════════════════════════════════════════════════
ARM_LIVE = False

# CLIENT_ID must differ from every other script that might be connected at the same time:
# the pipeline uses 151 and research/check_existing_port.py uses 141. TWS misbehaves
# silently when two connections share one id — it does not raise, it just goes strange.
CLIENT_ID = 161

SETTINGS = live.live_settings()
ACCOUNT = SETTINGS["account"]
WEIGHTS = cfg.portfolio_weights(SETTINGS["portfolio"])

print(f"account {ACCOUNT}  portfolio '{SETTINGS['portfolio']}'")
print(f"weights: {WEIGHTS}")
print(f"sum {sum(WEIGHTS.values()):.4f} (remainder is cash by design)")
print(f"guards: max_order_value {SETTINGS['max_order_value']:,} | "
      f"min_turnover {SETTINGS['min_turnover']:.2%}")
print(f"\nARM_LIVE = {ARM_LIVE}  ->  cell 11 will {'SEND ORDERS' if ARM_LIVE else 'refuse to run'}")

# Is anything even open? The first armed run of the pipeline went out on a Sunday.
looks_open, hours_msg = live.market_hours_note()
print(f"market hours: {hours_msg}")

# %% 3. Connect
# 3. Connect
#
# The connection stays open across every cell below — that is the entire point of this
# file. Do not run cell 15 (disconnect) until you have finished looking.

app = ib.IBApp()
app.start(client_id=CLIENT_ID)
ib.connection_status(app)

# %% 4. Pull the account
# 4. Pull the account
#
# Nothing here computes anything — pure acquisition, same three calls the pipeline makes.

portfolio_df = ib.get_account_updates(app=app, account=ACCOUNT)
acct = ib.get_account_data(app=app)

net_liq_row = acct.loc[acct["tag"] == "NetLiquidation"].iloc[0]
net_liq = float(net_liq_row["value"])
base_ccy = str(net_liq_row.get("currency", "?"))

print(f"NetLiquidation {net_liq:,.2f} {base_ccy}, {len(portfolio_df)} holdings")
display(portfolio_df)

# %% 5. FX and base-currency marks — THE CURRENCY CELL
# 5. FX and base-currency marks — THE CURRENCY CELL
#
# updatePortfolio converts NOTHING: marketPrice, marketValue, averageCost and
# unrealizedPnL all arrive in the POSITION'S own currency, while NetLiquidation is in the
# base currency. Weighing a EUR position against a GBP NetLiq without converting is how a
# 0.69% holding reads as 0.80%.
#
# WHAT TO CHECK HERE: fx_ratio must be 1.0 for base-currency legs and NOT 1.0 for foreign
# ones. A 1.0 on a foreign leg means the conversion silently did not happen.

fx_rates = ib.get_exchange_rates(app)
print(f"FX into {base_ccy}: {fx_rates}")

marks, detail = live.base_currency_marks(portfolio_df, fx_rates=fx_rates, base_ccy=base_ccy)
display(detail.round(4))

unpriced = [s for s in portfolio_df["symbol"] if s not in marks]
if unpriced:
    print(f"NO BASE PRICE (no FX rate — excluded from every weight): {', '.join(unpriced)}")

# %% 6. Build the book and reconcile against IBKR
# 6. Build the book and reconcile against IBKR
#
# BOTH marketPrice and averageCost are converted before the book is built. book_from_portfolio
# back-solves its base equity from those two columns, so an unconverted row makes the book a
# sum across three currencies — and ConstantMixRule sizes EVERY leg off that equity, not just
# the mispriced one.

priced_df = portfolio_df.copy()
priced_df["_fx"] = [1.0 if str(r.get("currency", "")).upper() == base_ccy.upper()
                    else fx_rates.get(str(r.get("currency", "")).upper(), np.nan)
                    for _, r in priced_df.iterrows()]
priced_df["marketPrice"] = priced_df["marketPrice"] * priced_df["_fx"]
priced_df["averageCost"] = priced_df["averageCost"] * priced_df["_fx"]
priced_df = priced_df.loc[priced_df["_fx"].notna()]

book = book_from_portfolio(priced_df.drop(columns=["_fx"]), base_equity=net_liq)

# Sanity check with real teeth: equity(marks) must come back to NetLiq. If it does not, the
# currency handling is wrong and every weight below is wrong with it.
print(f"book.equity(marks) = {book.equity(marks):,.2f} vs NetLiq {net_liq:,.2f}")
display(reconcile(book, priced_df))

# %% 7. Marks for targets we do not hold yet
# 7. Marks for targets we do not hold yet
#
# A target with no position has no marketValue and no conId. Price it off the last close —
# which comes back in the symbol's QUOTE currency (USD for these US-SMART lines) and needs
# the same conversion as anything else.

unheld = [s for s in WEIGHTS if s not in marks]
print(f"targets with no position: {unheld}")
if unheld:
    hist = ib.get_equity_data(symbols=unheld, duration="5 D", bar_size="1 day",
                              output_format="dict", app=app)
    for sym, df in hist.items():
        if df is None or df.empty:
            continue
        last = float(df["close"].iloc[-1])
        rate = 1.0 if base_ccy.upper() == "USD" else fx_rates.get("USD")
        if rate is None:
            print(f"  {sym}: {last:,.4f} USD — NO USD RATE, skipping")
            continue
        marks[sym] = last * rate
        print(f"  {sym}: {last:,.4f} USD -> {marks[sym]:,.4f} {base_ccy}")

# %% 8. The order table — READ THIS BEFORE ARMING ANYTHING
# 8. The order table — READ THIS BEFORE ARMING ANYTHING
#
# Same build_orders the pipeline calls. Check, in order:
#   * every held leg has a real conId, and the London/Xetra lines their own currency
#   * current_weight looks sane (a foreign leg silently unconverted reads high)
#   * status is OK — REJECT means the notional exceeded max_order_value, SKIP means the
#     leg is inside min_turnover or rounds to nothing
#   * notional_base is in the ACCOUNT'S currency, and so is the ceiling it is checked against

orders = live.build_orders(book, marks, WEIGHTS, net_liq, SETTINGS, detail=detail)
display(orders.round(4))

tradeable = orders[orders["units"].fillna(0) != 0]
print(f"\n{len(tradeable)} of {len(orders)} legs tradeable; "
      f"largest notional {orders['notional_base'].abs().max():,.0f} {base_ccy} "
      f"(ceiling {SETTINGS['max_order_value']:,})")

# %% 9. Resolve the contracts
# 9. Resolve the contracts
#
# conId, exchange and currency straight from the account snapshot, so the order names the
# exact listing IBKR told us we hold rather than letting TWS re-resolve a bare ticker. A
# target we do not hold has no spec and falls back to STK/SMART/USD — correct for the US
# listings and only those.

specs = ib.contract_specs_from_portfolio(portfolio_df)
for sym in tradeable["symbol"]:
    print(f"{sym:6s} {specs.get(sym, '(default STK/SMART/USD)')}")

# %% 10. DRY RUN submit — sends nothing
# 10. DRY RUN submit — sends nothing
#
# Prints the fully resolved contract per leg. This is the line that would have exposed the
# hardcoded-USD bug: "BUY 100 BARC (USD)" looks fine right up until you notice BARC is a
# London line in GBP.

order_app = ib.OrderApp(app)
dry = ib.submit_rebalance_orders(order_app, tradeable, symbol_col="symbol",
                                 currency_col="currency", units_col="units",
                                 dry_run=True, contract_specs=specs)
display(dry)

# %% 11. LIVE submit — GATED, edit ARM_LIVE in cell 2 to run
# 11. LIVE submit — GATED, edit ARM_LIVE in cell 2 to run
#
# The assert is what makes "Run All" safe: it stops here rather than trading. Nothing below
# this line is reached unless you deliberately armed the file.

assert ARM_LIVE, (
    "ARM_LIVE is False — refusing to send orders. Set ARM_LIVE = True in cell 2 and "
    "re-run that cell first, with the order table from cell 8 in front of you."
)
if not looks_open:
    print(f"*** WARNING: {hours_msg} ***")

print("submitting for real:")
submitted = ib.submit_rebalance_orders(order_app, tradeable, symbol_col="symbol",
                                       currency_col="currency", units_col="units",
                                       dry_run=False, contract_specs=specs)
display(submitted)

# %% 12. Did TWS actually take them?
# 12. Did TWS actually take them?
#
# ══════════════════════════════════════════════════════════════════════════════
# THE CELL THAT WOULD HAVE CAUGHT THE ORIGINAL FAILURE.
# placeOrder writes to the socket and returns — it neither waits nor confirms, and
# it does NOT raise when TWS ignores the order. "Submitted" in cell 11 only means
# the call did not throw. This cell reads what TWS actually said.
#
# acknowledged=False means TWS never mentioned that order at all: assume it did not
# arrive. Check the TWS Orders panel before re-running, or you may end up sending
# the same basket twice.
# ══════════════════════════════════════════════════════════════════════════════

ack = ib.wait_for_order_ack(app, submitted["orderId"].tolist())
display(ack)

missing = ack.loc[~ack["acknowledged"], "orderId"].tolist() if len(ack) else []
if missing:
    print(f"*** {len(missing)} orders NEVER acknowledged (ids {missing}) — most likely "
          f"not received by TWS. ***")
else:
    print("all orders acknowledged.")

# %% 13. What is working right now
# 13. What is working right now
#
# The broker's own view, independent of anything this session thinks it did. This is the
# authority — if an order is here, it is real.

open_orders = ib.get_open_orders_data(app)
display(open_orders)

# %% 14. THE UNDO — cancel
# 14. THE UNDO — cancel
#
# Two levels. Cancel one id, or cancel everything on the account (reqGlobalCancel covers
# orders placed by ANY client id, not just this session's — which is what you want when
# something has gone wrong and you are not sure what is out there).
#
# Cancellation is a REQUEST, not a guarantee: re-run cell 13 afterwards and confirm the
# orders are actually gone rather than assuming the call did it.

# ib.cancel_order(app, 1)
# ib.cancel_all_orders(app)
print("cancel calls are commented out on purpose — uncomment the one you mean.")

# %% 15. What actually filled
# 15. What actually filled
#
# Real executions, with commissions. Empty means "no fills in the window TWS serves"
# (roughly 7 days), never "no trades ever".

fills = ib.get_executions_data(app, days_back=1)
print(f"{len(fills)} fills in the last day")
if len(fills):
    display(fills[["ts", "symbol", "side", "shares", "price", "commission"]])

# %% 16. Disconnect
# 16. Disconnect
#
# Last, and only when finished — every frame above dies with the session.

ib.disconnect_ib(app)
print("disconnected")

# %%
