# The execution stack — how a target weight becomes a real order

Everything from a number in a YAML file to a fill at Interactive Brokers, and back again as an
audit trail. This document exists because the stack **spans folders** — `config/`, `src/portutils/`,
`src/pipelines/`, `orders/` — so it is invisible from inside any one of them.

If you only read one thing: **`placeOrder` writes to a socket and returns.** It does not wait, it
does not confirm, and it does not raise when TWS ignores the order. Almost every design decision
below follows from that sentence.

---

## The path

```
config/settings.yaml + config/asset_universe.yaml    target weights, guards, account
  │        portfolio_weights()  ── src/portutils/utils/config.py
  ▼
IBKR account snapshot                                positions, NetLiquidation, FX rates
  │        get_account_updates / get_account_data / get_exchange_rates
  ▼
base_currency_marks()                                every price into the account's currency
  │        ── src/pipelines/rebalance_live.py
  ▼
book_from_portfolio() → Book                         positions + average cost + P&L
  │        ── src/portutils/portfolio/ibkr_sync.py, book.py
  ▼
ConstantMixRule.propose()                            the STUDIED policy, unchanged
  │        ── src/portutils/portfolio/rules.py
  ▼
build_orders()                                       live-only guards → the order table
  │        ── src/pipelines/rebalance_live.py
  ▼
submit_rebalance_orders()                            per row: contract() + market_order() + place_order()
  │        ── src/portutils/ingestion/ibkr_requests.py
  ▼
TWS / IB Gateway                                     placeOrder writes to the socket and RETURNS
  │
  ▼
check_orders()                                       what we sent → verdict → broker view → combined
  │        ── src/portutils/ingestion/ibkr_requests.py
  ▼
outputs/live/orders_*.csv                            the audit trail
```

Two entry points drive that path:

| | [`src/pipelines/rebalance_live.py`](src/pipelines/rebalance_live.py) | [`orders/rebalance_live_debug.py`](orders/rebalance_live_debug.py) |
|---|---|---|
| shape | one-shot CLI, `try/finally`, disconnects at the end | `# %%` cells, connection stays open |
| use | the real rebalance, schedulable | understanding or debugging one |
| safety | `--live` flag; dry run by default | `ARM_LIVE` / `ARM_SMOKE`, both default `False` |
| docs | [rebalance_live.md](src/pipelines/rebalance_live.md) | [rebalance_live_debug.md](orders/rebalance_live_debug.md) |

The harness **imports** `live_settings`, `base_currency_marks` and `build_orders` from the pipeline
rather than restating them. A debug harness with its own copy of the arithmetic debugs the harness.

---

## The three things that went wrong, and what they taught

Recorded in full in [`orders/live_trading_notes.md`](orders/live_trading_notes.md). Summarised here
because each one is now a structural feature of the code.

### 1. A ticker is not an instrument

The same ticker can name a London line and a US one. A bare symbol on `STK/SMART/USD` is correct for
a US ETF and is either a rejection or *a different instrument in a different currency* for anything
else. The account snapshot already carries IBKR's own `conId` for every position, so
`contract_specs_from_portfolio()` maps it back into contract kwargs and every order names the exact
listing the broker told us we hold.

The venue goes in `primaryExchange`, **not** `exchange`. Putting it in `exchange` means direct
routing, which trips precautionary warning 10311 and makes TWS *hold* the order for manual
confirmation — indistinguishable, from the API side, from an order that was lost.

### 2. Nothing IBKR returns is in one currency

`updatePortfolio` converts nothing: `marketPrice`, `marketValue`, `averageCost` and `unrealizedPnL`
all arrive in the **position's own** currency, while `NetLiquidation` is in the **account's base**
currency. Weighing a EUR position against a GBP NetLiq without converting is how a 0.69% holding
reads as 0.80%.

`base_currency_marks()` converts everything first, using the `ExchangeRate` rows from the account
ledger. Its companion `detail` frame carries an `fx_ratio` column, and the check that matters is:
**1.0 on a base-currency leg, never 1.0 on a foreign one.** The `Book` is built from converted prices
so `book.equity(marks)` returns to NetLiq — if it does not, every weight below it is wrong.

### 3. An order is asynchronous, and its errors lie

TWS answers on the ibapi **reader thread**, long after `placeOrder` returned. Consequences, each
of which cost a debugging session:

- A pipeline that disconnects immediately destroys its own evidence — `disconnect()` stops the reader
  thread, so callbacks still in flight are discarded. Seven orders once reported "Submitted" into a
  completely empty TWS Orders panel.
- `error()` *prints* every message, but a notebook only routes background-thread output into a cell
  **while that cell is running**. A rejection arriving after the submit cell finished had nowhere to
  print. The state was never lost — only the print.
- The `errorTime` trap: ibapi ≥10.47 passes `error(reqId, errorTime, errorCode, errorString)`, so
  "the first int" is an epoch-millisecond timestamp, not the error code. Every code-based decision
  silently stopped working. `IBApp.error` now locates fields by magnitude, since no IB error code
  exceeds five digits.
- An empty `tif` is rejected outright (`Invalid time in force:Empty`), which is why `market_order()`
  sets `DAY` explicitly.

---

## Reading the answer: the verdict vocabulary

`check_orders()` runs the sequence and returns one table. With `sent=` it has four stages: **what we
sent** → acknowledgement → the broker's own view → the two joined. The order is mandatory:
`get_open_orders_data` *clears* `app.open_orders`, which the acknowledgement step reads.

| verdict | meaning | what to do |
|---|---|---|
| `FILLED` | done | nothing |
| `WORKING` | live at IB, nothing attached | nothing |
| `PENDING_OPEN` | accepted, parked until the venue opens | **nothing — this is healthy.** Five of these once read as failures |
| `HELD` | alive but waiting on a human (`whyHeld`, or 10311 direct routing) | click Transmit in TWS, or cancel |
| `REJECTED` | refused; `reason` carries the text verbatim | read the reason — e.g. `201 … no KID in English …` |
| `NO_ANSWER` | TWS never mentioned this id at all | assume it did not arrive; check the panel before re-running |

Two subtleties worth knowing:

- **What we sent is the spine.** `symbol`, `action`, `quantity` are *inputs* to `placeOrder`; TWS
  only echoes them back through `openOrder`, and it sends no `openOrder` for an order it refuses. So
  a rejected order has no symbol unless the table is built outwards from our own submission record.
- **Status outranks message codes.** IB adds warning codes faster than anyone updates a constant, but
  an order's own status is authoritative and needs no maintenance.

Every message TWS sends about an order is kept in append-only per-order logs on `IBApp`
(`req_error_log`, `req_notice_log`), surfaced as `error_1 … error_N` / `notice_1 … notice_N`. They
live as long as the connection, which is why re-running the check cell after the fact still shows a
message whose print was lost.

---

## The safety model

Five independent gates, deliberately not one:

| gate | where | what it stops |
|---|---|---|
| `--live` flag | [rebalance_live.py](src/pipelines/rebalance_live.py) | dry run is the default; transmitting is opt-in per invocation |
| `ARM_LIVE` / `ARM_SMOKE` | [rebalance_live_debug.py](orders/rebalance_live_debug.py) | "Run All" stops at an `assert` instead of trading |
| `max_order_value` | `build_orders` | a single order larger than the ceiling is **REJECTED, not clipped** — an outsized order means something upstream is wrong |
| `min_turnover` | `build_orders` | paying the spread to correct noise |
| pending guard | `pending_symbols` | re-running before the first order fills would see the same gap, propose the same trade, and **double the exposure** |

Plus `market_hours_note()`, a crude weekday/clock check — the first armed run went out on a Sunday.
It is not a venue calendar: no holidays, no half-days.

The undo is [`src/pipelines/cancel_orders.py`](src/pipelines/cancel_orders.py)
([doc](src/pipelines/cancel_orders.md)). Cancellation is a *request*, not a guarantee: confirm
afterwards rather than assuming.

---

## How the library got its shape

Two consolidations, both complete, both worth recording because the result looks arbitrary without
the history.

**One `IBApp`, no base class.** `ibkr_conn.py` used to define `IBKRApp(EWrapper, EClient)` with
`ibkr_requests.py` subclassing it as `IBApp(IBKRApp)`. The base earned its keep only if several
subclasses shared it, and there was exactly one. It also carried two flags (`data`, `finished`) that
nothing ever read. It was folded in: `IBApp` now inherits `EWrapper, EClient` directly — the client
and the wrapper are the same object, which is why `EClient.__init__(self, self)` passes `self`
twice. The old file is preserved at `sandbox/ibkr_conn.py`.

**The library owns all TWS plumbing.** [`orders/kts.py`](orders/kts.py) — the Kalman/OU trading GUI —
used to ship its *own* `IBApp`, contract builders and order logic. It now imports them from
`portutils.ingestion.ibkr_requests` and `portutils.portfolio`, and holds only the Tk state and the
model. The market-data smarts that existed only in kts (delayed tick tiers 66/67/68 for
crypto/FX/off-hours, mid synthesis, sectype gating) were ported *into* the library rather than
dropped. Pinned by [`tests/test_kts_migration.py`](tests/test_kts_migration.py) and
[`tests/test_book_parity.py`](tests/test_book_parity.py), which requires the extracted `Book` to
reproduce kts's numbers exactly.

One consequence to remember when working on either: request ids and order ids are **separate
counters sharing one numeric space**, and they do collide. Reading `req_errors[15]` is safe only
because we ask about ids we just submitted, in a window where no other request is in flight.

---

## Where each piece lives

| concern | file | doc |
|---|---|---|
| All TWS plumbing — connection, `IBApp`, builders, request helpers, `OrderApp` | [`src/portutils/ingestion/ibkr_requests.py`](src/portutils/ingestion/ibkr_requests.py) | [ingestion/README.md](src/portutils/ingestion/README.md) |
| Accounting — `Book`, `Position`, average cost, realised/unrealised P&L | [`src/portutils/portfolio/book.py`](src/portutils/portfolio/book.py) | [portfolio/README.md](src/portutils/portfolio/README.md) |
| Broker → book bridge, reconciliation | [`src/portutils/portfolio/ibkr_sync.py`](src/portutils/portfolio/ibkr_sync.py) | ” |
| Rebalance policies | [`src/portutils/portfolio/rules.py`](src/portutils/portfolio/rules.py) | ” |
| The live rebalancer | [`src/pipelines/rebalance_live.py`](src/pipelines/rebalance_live.py) | [rebalance_live.md](src/pipelines/rebalance_live.md) |
| The cell-by-cell harness | [`orders/rebalance_live_debug.py`](orders/rebalance_live_debug.py) | [rebalance_live_debug.md](orders/rebalance_live_debug.md) |
| Cancelling | [`src/pipelines/cancel_orders.py`](src/pipelines/cancel_orders.py) | [cancel_orders.md](src/pipelines/cancel_orders.md) |
| Daily position snapshots (7-day history ceiling) | [`src/pipelines/log_positions.py`](src/pipelines/log_positions.py) | [log_positions.md](src/pipelines/log_positions.md) |
| Hard-won operational notes | — | [live_trading_notes.md](orders/live_trading_notes.md), [notes_on_orders.md](orders/notes_on_orders.md) |

Offline tests for all of it: [`tests/test_rebalance_live.py`](tests/test_rebalance_live.py) (86
tests), [`tests/test_book.py`](tests/test_book.py), [`tests/test_ibkr_sync.py`](tests/test_ibkr_sync.py).
None of them need a TWS connection.
