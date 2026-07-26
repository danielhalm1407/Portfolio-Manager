# Orders vanish on submit — wait for acknowledgement, and be able to cancel

Repo copy (canonical): `.claude/plans/order-acknowledgement-and-debug-cells.md` — rename the mirrored file to that.

## Context

The first armed run printed seven `Submitted: orderId=N ...` lines and **nothing reached TWS** — the Orders panel is empty. Those lines were never evidence of acceptance: they print because `placeOrder` did not raise.

**The cause is a race between submitting and hanging up.** `OrderApp.place_order` calls `EClient.placeOrder`, which writes the message to the socket and returns immediately — the API is asynchronous, and acceptance arrives later via the `openOrder` / `orderStatus` callbacks. [rebalance_live.py](../../src/pipelines/rebalance_live.py) submits all seven and then falls straight into `finally: ib.disconnect_ib(app)`, which calls `EClient.disconnect()` and stops the reader thread. Anything still buffered is discarded, and any callback still in flight is never read. Elapsed time between the last `placeOrder` and the socket closing is under a millisecond.

Everything needed to fix it already exists and is simply never consulted: `IBApp.orderStatus` fills `app.order_status` keyed by order id and even sets an `isTerminal` flag ([ibkr_requests.py L986-L1004](../../src/portutils/ingestion/ibkr_requests.py#L986-L1004)); `openOrder` fills `app.open_orders` ([L947](../../src/portutils/ingestion/ibkr_requests.py#L947)). No code waits on either after a submit.

Two things compound it:

- **It was a Sunday.** `2026-07-26` is a Sunday and every venue in the basket (LSE, IBIS2, NYSE, SMART) was shut, so even a successfully transmitted MKT order could only have queued or been refused. The run gave no way to tell which.
- **There is still no way to cancel anything.** No `cancelOrder`, no `reqGlobalCancel`, no helper anywhere in the repo. On the installed **ibapi 10.47.1** both take an `OrderCancel` object (`cancelOrder(orderId, orderCancel)`), so the obvious call raises `TypeError`. If orders had landed, there was no way to pull them.

Outcome wanted: the script cannot report success it has not observed, the connection stays open until TWS has answered, and a bad order can be cancelled.

## Changes

### 1. `ibkr_requests.py` — acknowledgement

`wait_for_order_ack(app, order_ids, timeout=15, settle=1.0)`:

- Poll `app.order_status` / `app.open_orders` under the lock until every id has answered or the timeout expires; short `settle` sleep first so the reader thread gets a scheduling slot.
- Return a DataFrame: `orderId, symbol, status, filled, remaining, avgFillPrice, whyHeld`, plus **`acknowledged` False** for any id TWS never mentioned.
- Merge in `app.req_errors` for the id, since IB reports order rejections through `error()` with the **order id** as `reqId` — which the terminal-error recording added earlier already captures.
  - Caveat to comment: order ids and request ids are separate spaces that can collide (this very run had historical reqIds 2/3/4 alongside orderIds 1-7). Read `req_errors` for order reporting only, never to infer that a request failed.

### 2. `ibkr_requests.py` — the cancel path

- `cancel_order(app, order_id)` and `cancel_all_orders(app)` (`reqGlobalCancel`). Both pick the call shape with `inspect.signature` rather than a version string — 10.47.1 needs `OrderCancel()`, older builds do not.
- Exported from `portutils.ingestion`.

### 3. `rebalance_live.py` — do not report what was not observed

- After `submit_rebalance_orders`, call `wait_for_order_ack` on the returned `orderId`s **before** the `finally` disconnect, and print the status table.
- If any leg is unacknowledged, say so at the top in plain terms: *N of M orders were never acknowledged by TWS — they were probably not received.* Write the ack status into the audit CSV so the record shows what TWS said, not what we hoped.
- **Market-hours warning.** Before submitting live, check the local weekday/time and print a loud warning if every venue is likely shut (weekend, or outside 08:00-21:00 UK for this basket). A warning, not a block — the user may deliberately be queueing for the open.
- The `finally` gains a brief drain so the disconnect never truncates a live socket.

### 4. `orders/rebalance_live_debug.py` — the cell harness (the tool for exactly this)

Requested earlier and now clearly the right instrument: this failure is invisible in a one-shot CLI and obvious in a session that stays connected. Cells mirror `main()`, importing its functions — `live_settings`, `base_currency_marks`, `build_orders` — never restating the logic. `rebalance_live` is import-safe (`main()` is behind `if __name__ == "__main__"`).

Cells: imports → settings (`CLIENT_ID = 161`, distinct from the pipeline's 151 and `check_existing_port`'s 141) → connect → account pull → FX + marks → book → marks for unheld targets → `build_orders` → specs → **dry-run submit** → **live submit** → `wait_for_order_ack` → `get_open_orders_data` → `cancel_order` / `cancel_all_orders` → `get_executions_data` → disconnect.

**Live cell gate:** module constant `ARM_LIVE = False`, and the live cell opens `assert ARM_LIVE, "..."`. A "run all" therefore stops there instead of trading. This is precisely the defect of `orders/rebalance_port_basic.py`, whose module level ends with a `dry_run=False` submit and a stray live market order — which is why `tests/test_rebalance_live.py::test_does_not_import_the_cell_script` exists. The new file must be equally un-importable.

### 5. Noise fix

`get_equity_data` calls `cancelHistoricalData` on requests that already ended, producing `IB Error N: No historical data query found for ticker id:N` in the middle of a safety-critical printout. Only cancel while the request is still pending.

## Verification

1. `python -m pytest tests/ -q` — 63 passing now. New: `wait_for_order_ack` against a stub app (acknowledged, unacknowledged, and rejected-via-`req_errors` cases); `cancel_order` builds the `OrderCancel` form on the installed ibapi, asserted with a recording stub; the `ARM_LIVE` / no-import guards on the new cell script.
2. Offline: `python -c "from portutils.ingestion import cancel_order, cancel_all_orders, wait_for_order_ack"`.
3. **User re-runs the dry run** — unchanged output plus the market-hours warning.
4. **User re-runs armed, during market hours** (LSE 08:00-16:30 UK, US 14:30-21:00 UK — Monday 2026-07-27 at the earliest). Expect a real status table: `Submitted`/`PreSubmitted`/`Filled` per leg, `acknowledged` True throughout. If any row is unacknowledged the fix did not work and the run says so.
5. **User steps the debug script** cells 1-10 (read-only + dry run), then arms it, then uses the status and cancel cells.

I will not run steps 3-5.

## Progress Log

| # | Step | Status |
|---|------|--------|
| 1 | `wait_for_order_ack` | pending |
| 2 | `cancel_order` / `cancel_all_orders` + exports | pending |
| 3 | `rebalance_live.py`: ack wait, unacknowledged warning, CSV columns | pending |
| 4 | Market-hours warning | pending |
| 5 | `orders/rebalance_live_debug.py` + `ARM_LIVE` gate | pending |
| 6 | `cancelHistoricalData` noise fix | pending |
| 7 | Tests + `orders/rebalance_live_debug.md` | pending |
| 8 | User's re-run, in market hours | pending — user-driven |

## Decisions

- 2026-07-26 — **"Submitted" will no longer be printed for an unobserved order.** `placeOrder` not raising says nothing about acceptance; the script must report what TWS answered, and say plainly when it answered nothing.
- 2026-07-26 — Cancel helpers go in the **library**, chosen by `inspect.signature` rather than ibapi version. A kill switch that only exists in a debug file is unavailable when it is needed.
- 2026-07-26 — The market-hours check **warns, never blocks**. Queueing for the open is a legitimate thing to want; doing it unknowingly is not.
- 2026-07-26 — `req_errors` is read for **order reporting only**. Order ids and request ids share a numeric space and did collide in this very run, so it must never be used to infer that a data request failed.
