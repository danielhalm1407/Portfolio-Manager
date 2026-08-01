# `ingestion/` — everything that talks to the outside world

Two modules, two very different worlds: Interactive Brokers, and the web.

| module | what |
|---|---|
| [`ibkr_requests.py`](ibkr_requests.py) | **all** TWS/IB Gateway plumbing — connection, callbacks, request helpers, order placement |
| [`reuters.py`](reuters.py) | Playwright-based scraper: section headlines and article bodies |

## `ibkr_requests.py`

The largest module in the repo, and deliberately so: it is the *only* place that speaks to TWS.
Structure, top to bottom:

1. **Connection lifecycle** — `connect_ib`, `disconnect_ib`, `reconnect`, `connection_status`.
   `connect_ib` starts `app.run()` on a **daemon** reader thread so a forgotten disconnect cannot
   hang the interpreter.
2. **Pure builders** — `contract()`, `market_order()`, `limit_order()`,
   `crypto_marketable_limit_order()`, `contract_specs_from_portfolio()`. No I/O; testable offline.
3. **`IBApp(EWrapper, EClient)`** — one class, both roles. Holds every piece of callback state.
4. **Request helpers** — `get_equity_data`, `get_historical_bars`, `get_account_data`,
   `get_account_updates`, `get_positions_data`, `get_exchange_rates`, `get_open_orders_data`,
   `get_executions_data`, `get_pnl_data`.
5. **Orders** — `submit_rebalance_orders`, `wait_for_order_ack`, `check_orders`, `cancel_order`,
   `cancel_all_orders`, and the `OrderApp` wrapper.

### The threading model, which explains most of the design

`IBApp` is simultaneously the **EClient** (outgoing: it sends requests) and the **EWrapper**
(incoming: it receives callbacks) — hence `EClient.__init__(self, self)`, passing `self` twice. The
callbacks arrive on a **background reader thread**, not the one you called from. Therefore:

- every piece of shared state is written under `self.lock` (an `RLock`);
- completion is signalled with per-request `threading.Event`s (`_hist_events`, `_account_events`,
  `_pnl_events`) rather than one global flag, so concurrent requests cannot unblock each other;
- helpers block on `_wait_for(event, timeout, label)` and **report** a timeout rather than hanging;
- `reserve_order_id()` hands out order ids under the lock — you never manage them yourself.

There was once a base class, `IBKRApp`, with `IBApp` subclassing it. It earned its keep only if
several subclasses shared it, and there was exactly one, so it was folded in. See
[EXECUTION_STACK.md](../../../EXECUTION_STACK.md) for that history and for the failures that shaped
the order-handling code.

### Traps that are now structural

- **`error()` is `*args`.** ibapi ≥10.47 passes `error(reqId, errorTime, errorCode, errorString)` —
  taking "the first int" reads an epoch-ms timestamp as the error code. Fields are located by
  magnitude instead, since no IB error code exceeds five digits.
- **A refusal is the only callback you get.** TWS sends no terminating callback for a request it
  rejects, so `error()` records the failure and sets the waiting event itself; otherwise a
  1-second "no such contract" becomes a 30-second stall.
- **Request ids and order ids share one numeric space** and do collide. `req_errors[15]` is safe to
  read only for ids you just submitted.
- **`exchange` vs `primaryExchange`.** The listing venue belongs in `primaryExchange`; putting it in
  `exchange` means direct routing, warning 10311, and TWS holding the order for manual confirmation.
- **Empty `tif` is rejected** (`Invalid time in force:Empty`), so the builders set `DAY` explicitly.
- **`eTradeOnly` / `firmQuoteOnly` were removed from ibapi's `Order`.** Setting them unconditionally
  raises `AttributeError` on newer versions; setting them nowhere makes older TWS builds reject the
  order. The builders guard with `hasattr` so both work.

### Message logs

Every message TWS sends about an order is appended to per-order logs on the app — `req_error_log`,
`req_notice_log` (split by kind) and `req_messages` (both, interleaved, arrival order). They persist
for the life of the connection, which is why re-reading after the fact still finds a message whose
print was lost to a finished notebook cell. `req_errors` / `req_notices` remain as
last-of-each-kind summaries for older callers.

## `reuters.py`

`fetch_section_headline()` and `fetch_article()` return `ArticleStub` / `Article` dataclasses. Driven
by Playwright, so it needs a browser binary once per machine:

```bash
playwright install chromium
```

Used by [`src/pipelines/scrape_commentary.py`](../../pipelines/scrape_commentary.py), which writes
into `data/raw/commentary/`.
