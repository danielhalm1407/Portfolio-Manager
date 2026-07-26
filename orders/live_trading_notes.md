# Going live — what broke, and why

Session notes from pointing [`rebalance_live.py`](../src/pipelines/rebalance_live.md) at a real IBKR account for the first time. **What each file does is documented in its own `.md`** — this file is only about the things that went wrong and what they taught.

Three sticking points, in the order they surfaced. Each looked like a different bug and each turned out to be one wrong assumption.

---

## 1. A ticker is not an instrument

**Symptom.** Price history for the account's own holdings failed: `No security definition has been found` for all five, then a 30-second stall and a `TimeoutError` that killed the whole pull.

**Cause.** Every contract was built as `STK / SMART / USD`. That describes a US listing. The account holds London and Xetra lines, so TWS could not resolve them — and a bare ticker is *ambiguous* anyway, since the same string names different instruments on different venues.

The account snapshot already carried the answer: `updatePortfolio` returns `conId`, `exchange` and `currency` per position, and the code threw all three away.

**Fix.** `contract_specs_from_portfolio()` feeds the broker's own `conId` back into the request. A conId is unambiguous by construction.

**The follow-on.** Routing by the position's `exchange` field (`LSE`, `IBIS2`, `NYSE`) fixed resolution but caused something new: IB warning **10311**, *"this order will be directly routed"*, and TWS silently held every foreign leg in the Pending panel with a blue **Transmit** button. Direct routing trips a precautionary setting.

That field is the *listing venue*, not a routing instruction. It belongs in `primaryExchange`, which disambiguates without dictating a route. With `exchange="SMART"` + `primaryExchange=<venue>` + conId, orders route normally and no longer need a human to press a button.

> **Insight.** The broker already knows the contract. Any time you find yourself reconstructing an instrument from a ticker, check whether the answer is sitting in a field you discarded.

---

## 2. Nothing IBKR returns is in one currency

**Symptom.** Backcast cost bases were off by ~100× for BARC and HSBA. Later, weights for the EUR and USD legs were quietly wrong by their FX rate.

**Cause — two different ones, which is what made it confusing.**

- **Historical bars are quoted in the venue's unit.** LSE quotes in **pence**; `averageCost` on the portfolio row is in **pounds**. A 100× discrepancy between two numbers that are both "the price of BARC".
- **`updatePortfolio` converts nothing.** `marketPrice`, `marketValue`, `averageCost` and `unrealizedPnL` all arrive in the *position's own* currency. Only `NetLiquidation` is in the account base currency. So `qty * marketPrice / net_liq` adds euros to pounds and calls the result a weight.

I initially "fixed" this by deriving a base price as `marketValue / position`, assuming `marketValue` was base-converted. **It is not.** The tell was `fx_ratio == 1.0` on a EUR line — a conversion that had not happened.

**Fix.** `get_exchange_rates()` reads IBKR's own rates from the `$LEDGER:ALL` account summary, and every position is converted before it is weighed. The rate is the broker's, so converted values agree with the NetLiq they are divided by — an independently fetched FX rate would be a *second opinion* on a number IB has already decided.

Two details that matter more than they look:

- **A currency with no rate is never assumed 1:1.** The leg goes unpriced and is skipped. Assuming parity is exactly how 8,156 euros gets valued as 8,156 pounds.
- **Both `marketPrice` and `averageCost` are converted** before `book_from_portfolio` sees them. That function back-solves the book's base equity from those two columns, and `ConstantMixRule` sizes **every** leg off `book.equity()` — so one unconverted row corrupts the targets for the whole book, including the legs that were fine.

> **Insight.** Mixed-currency bugs do not look like currency bugs. They look like one position being weirdly large. The diagnostic that works is a ratio between two numbers that should be identical — printed per leg, so the conversion is visible rather than assumed.

---

## 3. An order is asynchronous, and its errors will lie to you

**Symptom, in three rounds.** Seven orders reported `Submitted` into a completely empty TWS Orders panel. Then seven `Invalid time in force:Empty`. Then five orders reported as *"NEVER ACKNOWLEDGED — probably not received"* while sitting visibly on screen.

**Cause — layered, and each layer hid the next.**

| | what was wrong |
|---|---|
| `market_order()` | never set `tif`. ibapi leaves it `""` and TWS refuses the order outright. `crypto_marketable_limit_order` set `"IOC"` explicitly — the one path that always worked, which is why the gap survived so long |
| the submit loop | `placeOrder` writes to a socket and returns; acceptance arrives later via callbacks. The script disconnected in the next statement, stopping the reader thread before anything could be read |
| `error()` | on ibapi 10.47 the signature is `error(reqId, errorTime, errorCode, ...)`. Taking "the first int" as the code read an **epoch-millisecond timestamp**. Every code-based decision silently stopped working |

That third one is why the diagnosis went wrong twice. Farm-status pings printed as errors (`IB Error -1: 1785082784783 - ...is OK`), the error-200 fail-fast never fired (hence the 30s stall in §1), and real rejections were discarded — so orders TWS had refused *in plain language* were reported as silence.

**Fix.**

- `tif="DAY"` on market orders. Right for a rebalance: an unfilled remainder should expire with the marks that sized it, not fill against tomorrow's prices.
- `wait_for_order_ack()` blocks until TWS answers and reports `acknowledged=False` for genuine silence — the script can no longer claim success it has not observed.
- `error()` separates `errorTime` from `errorCode` **by magnitude** (epoch ≈ 1.7e12; no IB code exceeds five digits), so it survives another argument-order change.
- Advisory codes are **kept, not discarded**. 10311 (held) and 399 (*"will not be placed until Monday 09:00"*) are not failures — but discarding them made a held order indistinguishable from a lost one, and treating 399 as terminal printed `REJECTED` on five healthy queued orders.
- **A live order status overrides any recorded error.** `PreSubmitted` means alive, whatever message came with it. A code list is a maintenance burden that fails silently as IB adds warnings; the order's own status needs no maintenance.
- `cancel_order` / `cancel_all_orders` + the [`cancel_orders`](../src/pipelines/cancel_orders.md) pipeline. There was **no way to cancel an order anywhere in the repo**, and on ibapi 10.47 the obvious call raises `TypeError` (it wants an `OrderCancel` object).
- **Pending-order guard**, on by default: a symbol with a working order is skipped. `build_orders` sizes from the *position*, and a working order is not a position yet — so a re-run sees the same gap, proposes the same trade, and doubles the exposure invisibly.

> **Insight.** "The call returned" is not evidence. Nothing in an async API tells you it worked; you have to go and ask, and you have to be able to say *"I don't know"* when the answer never comes.

---

## Not a bug: PRIIPs

`SPY` and `KMLM` were rejected with `201 — Customer Ineligible; this product does not have a KID`. US-domiciled ETFs publish no Key Information Document, so a UK/EU **retail** client may not buy them. No code change makes this work.

Replaced with **CSPX** (iShares Core S&P 500 UCITS, LSE, USD) — Irish-domiciled, KID published. Note `LNG` trades fine on NYSE: PRIIPs covers *packaged products*, not ordinary shares.

Because CSPX is not held, it has no conId, so `live_trading.contract_overrides` in [`settings.yaml`](../config/settings.yaml) supplies its contract — used for **both** pricing and routing, so the two can never disagree about which instrument they mean.

---

## Where the changes landed

| file | what changed |
|---|---|
| [`ibkr_requests.py`](../src/portutils/ingestion/ibkr_requests.py) | `contract(con_id=)`, `contract_specs_from_portfolio`, `get_exchange_rates`, `wait_for_order_ack`, `cancel_order`, `cancel_all_orders`; `error()` timestamp fix + notices; `market_order` TIF; per-symbol contracts and partial-success in `get_equity_data` |
| [`rebalance_live.py`](../src/pipelines/rebalance_live.md) | base-currency valuation, universe = targets ∪ holdings, pending guard, market-hours warning, ack reporting, full diagnostic table |
| [`cancel_orders.py`](../src/pipelines/cancel_orders.md) | new — the undo |
| [`rebalance_live_debug.py`](rebalance_live_debug.md) | new — cell-by-cell harness with the `ARM_LIVE` gate |
| [`asset_universe.yaml`](../config/asset_universe.yaml) / [`settings.yaml`](../config/settings.yaml) | `live_book`, contract overrides, `min_turnover` sized against the target weights |

Everything above is pinned by tests that run offline, with no TWS.

---

## Two operational gotchas

- **An untransmitted order is not an order.** If TWS holds one for manual Transmit it was never sent to IB's servers: `reqAllOpenOrders` returns nothing for it and the API cannot cancel it. Clear those in the TWS UI. It also cannot fill on its own — but clicking Transmit later will duplicate whatever you sent since.
- **`min_turnover` must be smaller than your smallest target weight.** It gates on the weight *gap*, so a 2% gate against a 1.43% target skips every leg forever, from flat. Pinned by a test.
