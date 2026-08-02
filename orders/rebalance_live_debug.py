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
ARM_LIVE = True

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
#
# The table now carries MONEY next to the weights — current_qty, base_price, current_value,
# target_value, then current_weight / target_weight / weight_diff. target_value -
# current_value is the trade in cash terms and should agree with notional_base.
#
# print_order_table is the PIPELINE'S printer, imported rather than re-written here: the
# totals above the table (portfolio value, non-cash value, implied cash) are what every
# weight in it is measured against, and a harness that formats them differently from the
# pipeline teaches the wrong thing about the pipeline.

orders = live.build_orders(book, marks, WEIGHTS, net_liq, SETTINGS, detail=detail)
live.print_order_table(orders, net_liq, base_ccy=base_ccy, weights=WEIGHTS)

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
#
# ── WHAT `assert` ACTUALLY IS ─────────────────────────────────────────────────
# Syntax:   assert <expression>[, <message>]
# Semantics: evaluate <expression>; if it is TRUTHY, do nothing at all and carry on to
# the next statement. If it is FALSY, raise AssertionError(<message>). That is the whole
# construct — it is a statement, not a function, so `assert(x, "msg")` is a BUG: that
# asserts a 2-tuple, which is always truthy, and the check silently never fires.
#
# The expression does NOT have to be a bool. Python applies its normal truthiness rules,
# so `assert orders` passes for a non-empty list and fails for an empty one; `assert n`
# fails for 0; `assert df` on a DataFrame RAISES ValueError (pandas refuses to guess), so
# write `assert len(df)` there. Here ARM_LIVE is a plain bool, which is the clearest case.
#
# The message is optional and lazily evaluated — it is only built when the check fails, so
# a long explanatory string like the one below costs nothing on the happy path. Its job is
# to tell the reader what to DO, not merely that something was False.
#
# WHEN WE USE IT: for invariants that should be impossible to violate if the program is
# correct — a guard against the programmer, not against the user or the outside world.
# For expected runtime conditions (bad input, missing file, market closed) raise a real
# exception instead (ValueError / RuntimeError), because asserts are ERASED when Python
# runs with -O. That erasure is the one caveat for this line: `python -O rebalance_live_debug.py`
# would strip the gate entirely. It is acceptable here because this file is run cell-by-cell
# in an interactive session that never passes -O, and ARM_LIVE is a second, human gate.
#
# ── assert vs `setdefault` — unrelated things that only look adjacent ─────────
# `setdefault` is a dict METHOD, not a check: `d.setdefault(k, v)` returns d[k] if k is
# already present, otherwise INSERTS k=v and returns v. It mutates, it never raises, and it
# never rejects anything. Line 1867 of ibkr_requests.py uses it — `spec.setdefault('currency',
# ...)` — precisely so a currency coming from the broker's own contract spec is left alone
# and only a MISSING one is filled from the table's currency column. So: assert = "stop if
# this is not true"; setdefault = "fill this in if it is not there".

assert ARM_LIVE, (
    "ARM_LIVE is False — refusing to send orders. Set ARM_LIVE = True in cell 2 and "
    "re-run that cell first, with the order table from cell 8 in front of you."
)
if not looks_open:
    print(f"*** WARNING: {hours_msg} ***")

# ── WHAT THIS ONE CALL UNROLLS INTO ──────────────────────────────────────────
# submit_rebalance_orders (ibkr_requests.py:1761) is a LOOP over the rows of `tradeable`,
# not a single broker call. Per row it does:
#   1. read symbol / currency / units from the named columns; skip NaN rows and units==0
#   2. int(units) -> action = BUY if positive else SELL, quantity = abs(units)
#   3. spec = dict(contract_specs.get(symbol, {})) then spec.setdefault('currency', ...)
#      — the account snapshot's conId/exchange/primary_exchange/currency win; the table's
#      currency column only fills a gap
#   4. dry_run=True -> print the line and move on. dry_run=False -> the chain below:
#
#   order_app.submit_market_order(symbol, action, quantity, **spec)   [OrderApp:2320]
#     ├── contract(symbol, sec_type, exchange, currency, primary_exchange, con_id) [:124]
#     │     builds the ibapi Contract — conId pins the exact listing, exchange stays SMART
#     ├── market_order(action, quantity)                              [:232]
#     │     builds the ibapi Order — MKT, the side, the size
#     └── OrderApp.place_order(contract_obj, order)                   [:2302]
#           ├── app.reserve_order_id()  — next id from the last nextValidId, incremented
#           │                             under a lock, so you never manage ids yourself
#           └── EClient.placeOrder(order_id, contract_obj, order) — writes to the socket
#
# placeOrder RETURNS IMMEDIATELY and never raises on rejection, so the "Submitted:" prints
# below prove only that the calls did not throw. Cell 12 is what reads TWS's actual answer.

print("submitting for real:")
submitted = ib.submit_rebalance_orders(order_app, tradeable, symbol_col="symbol",
                                       currency_col="currency", units_col="units",
                                       dry_run=False, contract_specs=specs)
sent_ids = submitted["orderId"].tolist()
display(submitted)

# %% 11b. SINGLE-LEG SMOKE TEST — one tiny order, own gate
# 11b. SINGLE-LEG SMOKE TEST — one tiny order, own gate
#
# The smallest thing that exercises the ENTIRE live path (contract resolution -> Order ->
# reserve_order_id -> placeOrder -> ack) without touching the rebalance table. Use it to see
# how a leg breaks and what TWS says when it does: wrong currency, unknown ticker, closed
# market, no market-data entitlement. It builds a one-row DataFrame and hands it to the same
# submit_rebalance_orders every other cell uses — testing a different code path would test
# nothing.
#
# YOU DO NOT NEED TO KNOW THE CURRENCY OR THE SPEC. resolve_spec below finds them:
#   * held symbols -> the broker's own record (conId / secType / currency / primaryExchange)
#     straight out of the cell-4 snapshot, via contract_specs_from_portfolio
#   * anything else -> contract()'s STK/SMART/USD defaults, which are correct for US listings
#     like SPY and KMLM and wrong for everything else
# There is no reqContractDetails helper in ibkr_requests.py, so the CONFIRMATION that the
# contract resolves at all is a historical-bar request on that same spec: if bars come back,
# TWS matched the contract, and the last close also gives you the price — which is how you
# size by CASH instead of guessing units.
#
# ══════════════════════════════════════════════════════════════════════════════
# ARM_SMOKE is a SEPARATE gate from ARM_LIVE, on purpose: arming the rebalance must
# not also fire a test order, and vice versa. Leave it False when you are done.
#
# WHY THIS CELL SITS BELOW CELL 11 rather than next to the dry run it resembles: no
# `dry_run=False` may appear above the `assert ARM_LIVE` line — tests/test_rebalance_live.py
# walks the file's non-comment lines and fails the build if one does, because anything
# above the gate is reachable by "Run All" on an unarmed file. The price of that placement
# is that running this cell ON ITS OWN executes no assert at all, so ARM_SMOKE is then the
# only thing standing between you and a live order.
# ══════════════════════════════════════════════════════════════════════════════
ARM_SMOKE = True

SMOKE_SYMBOL = "SPY"        # or "KMLM" — both US-listed, so the USD defaults hold
SMOKE_CASH = 2000.0          # base-currency notional to spend; units derive from last close
SMOKE_SIDE = 1              # +1 BUY, -1 SELL


def resolve_spec(symbol, portfolio_df, app):
    """Return (spec, last_close, currency) for a symbol we may or may not already hold.

    # ========================================================================
    # The minimum viable "look the ticker up" step. Two sources, in priority order:
    # the account snapshot (authoritative — it is the broker's own contract) and,
    # failing that, the STK/SMART/USD defaults plus a historical probe to prove the
    # contract resolves. Read-only: it places nothing.
    # ========================================================================
    """
    # Reuse the same mapping the rebalance uses; a symbol we hold comes back fully
    # specified (conId pins the exact listing), a symbol we do not returns {}.
    spec = ib.contract_specs_from_portfolio(portfolio_df).get(symbol, {})
    # Fill only what is missing — setdefault never overwrites the broker's answer
    # for the 'currency' key, which is the one that matters for sizing by cash.
    spec = dict(spec)
    spec.setdefault("currency", "USD")

    # The probe. A bar request is the cheapest round trip that proves TWS can match this
    # contract: error 200 ("no security definition") here means the order would have failed
    # the same way, and it costs nothing to find that out before arming.
    bars = ib.get_equity_data(symbols=[symbol], duration="5 D", bar_size="1 day",
                              output_format="dict", app=app, contract_specs={symbol: spec})
    df = bars.get(symbol)
    if df is None or df.empty:
        # No bars: either the contract did not resolve or there is no data entitlement for
        # it. Either way, do not guess a size — return None and let the caller stop.
        print(f"{symbol}: NO BARS — contract did not resolve, or no entitlement. spec={spec}")
        return spec, None, spec["currency"]

    last = float(df["close"].iloc[-1])
    print(f"{symbol}: resolved {spec} | last close {last:,.4f} {spec['currency']}")
    return spec, last, spec["currency"]


smoke_spec, smoke_px, smoke_ccy = resolve_spec(SMOKE_SYMBOL, portfolio_df, app)

# Size from cash, not from a number you typed. int() truncates toward zero, so a cash amount
# below one share gives 0 units — which submit_rebalance_orders skips rather than sending a
# zero-quantity order.
smoke_units = 0 if smoke_px is None else SMOKE_SIDE * int(SMOKE_CASH / smoke_px)
smoke_row = pd.DataFrame([{"symbol": SMOKE_SYMBOL, "currency": smoke_ccy, "units": smoke_units}])
print(f"smoke leg: {smoke_units:+d} {SMOKE_SYMBOL} "
      f"(~{abs(smoke_units) * (smoke_px or 0):,.0f} {smoke_ccy})")

# Dry run always — free, and it prints the fully resolved contract line.
display(ib.submit_rebalance_orders(order_app, smoke_row, symbol_col="symbol",
                                   currency_col="currency", units_col="units",
                                   dry_run=True, contract_specs={SMOKE_SYMBOL: smoke_spec}))

# %% 11c. SMOKE TEST — SEND IT
# 11c. SMOKE TEST — SEND IT
#
# Split out from the resolve step above so each half can be re-run on its own: the
# resolve cell is read-only and can be run as often as you like, while THIS cell is the
# one that trades. Nothing here recomputes a spec or a size — it sends exactly what 11b
# printed, so what you reviewed is what goes out.

if ARM_SMOKE and smoke_units != 0:
    # Live, but one leg and a couple of hundred of notional. Everything after the send is
    # about reading the answer: wait_for_order_ack turns "the call did not throw" into
    # "TWS said something about this id", and any rejection text arrives through IBApp.error
    # on the reader thread and prints itself in this session.
    smoke_sent = ib.submit_rebalance_orders(order_app, smoke_row, symbol_col="symbol",
                                            currency_col="currency", units_col="units",
                                            dry_run=False,
                                            contract_specs={SMOKE_SYMBOL: smoke_spec})
    display(smoke_sent)
    # ══════════════════════════════════════════════════════════════════════════
    # KEEP THE WHOLE FRAME, NOT JUST THE IDS. This is the only record that ties an
    # orderId to the symbol it was sent for, and cell 11d needs it.
    #
    # Neither of check_orders' two sources can supply the symbol for a REJECTED
    # order. wait_for_order_ack reads symbol/action/quantity out of app.open_orders,
    # which the openOrder callback fills — and a refused order never becomes an open
    # order, so there is nothing to read. get_open_orders_data then CLEARS that dict
    # and refills it with only what is currently working, so the broker columns are
    # blank for the same rows. Result: orderId 18 comes back REJECTED with the KID
    # reason attached and no clue which ticker it was.
    #
    # submit_rebalance_orders already returns symbol / currency / exchange / con_id /
    # action / quantity alongside orderId. Keeping that frame costs nothing and is
    # authoritative — it is what WE sent, independent of what TWS did with it.
    # ══════════════════════════════════════════════════════════════════════════
    sent_ids = smoke_sent["orderId"].tolist()
else:
    smoke_sent = None
    sent_ids = []
    print("ARM_SMOKE is False (or units rounded to 0) — dry run only, nothing sent.")

# %% 11d. SMOKE TEST — WHAT HAPPENED, AND WHY
# 11d. SMOKE TEST — WHAT HAPPENED, AND WHY
#
# One call, three stages, each announced as it runs:
#   1. wait_for_order_ack — what TWS said about each id, reduced to a verdict
#   2. get_open_orders_data — the broker's own view, independent of this session
#   3. the two joined on orderId
#
# Only AFTER we have ingested and picked up all the callback messsages do
# we ask TWS for its own view of the open orders.
# That is the only authority on whether the order is real, yet this first
# order ack step helps us to debug this situation
#
# READ THE `verdict` COLUMN FIRST:
#   REJECTED      refused outright — `reason` carries the text verbatim, e.g. the
#                 201 "no KID in English" refusal that a SPY smoke test drew
#   PENDING_OPEN  accepted and parked until the venue opens (the 399 "will not be
#                 placed at the exchange until 09:00 MET" legs) — healthy, not a failure
#   HELD          alive but waiting on a human (whyHeld, or the 10311 direct-routing warning)
#   WORKING       live at IB with nothing attached
#   FILLED        done
#   NO_ANSWER     TWS never mentioned this id at all — the empty-panel failure
#
# SAFE TO RE-RUN, AND WORTH RE-RUNNING. The messages live in append-only logs on the
# app for as long as the connection does, so this cell shows everything collected up to
# the moment you run it. That is the fix for messages that "vanished": IBApp.error prints
# on the reader thread, and a notebook only routes that into a cell WHILE THE CELL IS
# RUNNING — a rejection arriving after the submit cell ended had nowhere to print. The
# state was never lost, only the print. Pull it again here.
# Every id sent in this session: cell 11's basket AND cell 11c's smoke leg, checked
# together so one table covers everything that went out.
#
# NOT list.append() — that NESTS the second list ([1, 2, [3, 4]]), and wait_for_order_ack
# calls int() on each element, so the nested list raises TypeError before a single order
# is looked up. `+=` (list.extend) concatenates, which is what is meant here.
#

_frames = []
for _frame_name in ("submitted", "smoke_sent"):
    # globals().get() rather than naming the frames directly: this cell must stay runnable
    # when only one of the two submits has been run, which is the normal case — a bare
    # `submitted` would raise NameError and cost you the whole verdict table.
    _frame = globals().get(_frame_name)
    if _frame is not None and len(_frame):
        # Drop the DRY-RUN rows: they carry orderId=None, and int(None) raises.
        _frames.append(_frame.loc[_frame["orderId"].notna()])

# OUR OWN RECORD of what went out — orderId alongside symbol / action / quantity /
# currency. Needed because a REJECTED order never reaches app.open_orders (TWS sends no
# openOrder callback for one), so neither wait_for_order_ack nor get_open_orders_data can
# name its ticker: orderId 18 came back REJECTED with the KID reason and no symbol.
sent_orders = (pd.concat(_frames, ignore_index=True) if _frames
               else pd.DataFrame(columns=["orderId", "symbol", "action", "quantity"]))
if len(sent_orders):
    sent_orders["orderId"] = sent_orders["orderId"].astype(int)
    # The same id submitted twice (a re-run of a cell) keeps the LAST record — the live one.
    sent_orders = sent_orders.drop_duplicates("orderId", keep="last")

# De-duplicate while preserving the order the orders went out in. dict.fromkeys keeps
# first-seen order; set() would scramble it, and reading a verdict table in submission
# order is how you find the leg that broke.
sent_ids = list(dict.fromkeys(sent_orders["orderId"].tolist()))
print(f"checking {len(sent_ids)} order ids: {sent_ids}")

# sent=sent_orders makes OUR record the spine: one row per order transmitted, named
# whatever TWS did or did not say about it. The local merge this cell used to do now
# lives in check_orders, so the pipeline gets it too — a rejected leg is nameless in a
# scheduled run as well, and that is the run nobody is watching.
verdicts = ib.check_orders(app, sent_ids, sent=sent_orders)

#display(verdicts)

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
#
# Same one call as cell 11d, over cell 11's whole basket instead of the smoke leg:
# acknowledgement (with verdicts) -> the broker's own view -> the two joined. Read the
# `verdict` column first — REJECTED / PENDING_OPEN / HELD / WORKING / FILLED / NO_ANSWER,
# with `reason` carrying the message behind it. PENDING_OPEN is a HEALTHY order queued
# for the venue's open, and the reason it needs its own verdict is that five of them once
# read as failures.
#
# Re-runnable: the message logs on the app persist for the life of the connection, so
# running this again later picks up anything that has landed since.

ack = ib.check_orders(app, sent_ids, sent=sent_orders)

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
ib.cancel_all_orders(app)
print("cancel calls are commented out on purpose — uncomment the one you mean.")

# %% 15. What actually filled
# 15. What actually filled
#
# Real executions, with commissions. Empty means "no fills in the window TWS serves",
# never "no trades ever".
#
# That window is SINCE MIDNIGHT TODAY — not the 7 days this comment used to claim. Verified
# against live TWS on 2026-08-02: an unfiltered ExecutionFilter() returned 0 fills with
# execDetailsEnd received, while the TWS Trade Log showed JUL 27-31. `days_back` is a floor on
# the filter, not a reach back through that ceiling. Real history needs Flex Web Service.
#
# `client_id=161` NARROWS to fills placed by this harness's own client. The default of 0 is the
# broader ask and also catches GUI-placed fills; neither returned anything on a Sunday.
days_back = 7
fills = ib.get_executions_data(app, client_id = 161, days_back=7)
print(f"{len(fills)} fills in the last {days_back} days")
if len(fills):
    display(fills[["ts", "symbol", "side", "shares", "price"]])
    # print(fills.columns)
    # had to get rid of "comission"

# %% 16. Disconnect
# 16. Disconnect
#
# Last, and only when finished — every frame above dies with the session.

ib.disconnect_ib(app)
print("disconnected")

# %%
