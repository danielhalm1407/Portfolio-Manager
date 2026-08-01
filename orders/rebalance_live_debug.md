# `rebalance_live_debug.py` — cell-by-cell harness for the live rebalancer

**This file connects to TWS and, once armed, places real orders. It must never be imported.**

## Why it exists

[`src/pipelines/rebalance_live.py`](../src/pipelines/rebalance_live.py) is a one-shot CLI: connect, decide, submit, disconnect, all in one `try/finally`. When something goes wrong there is nothing left to inspect — the socket is closed and every intermediate frame is gone.

It was written after the first armed run reported seven orders `Submitted` into a **completely empty TWS Orders panel**. `EClient.placeOrder` writes to the socket and returns; acceptance arrives later via `openOrder`/`orderStatus` on the reader thread. The pipeline disconnected in the next statement, which stopped that thread and discarded everything in flight. That failure is invisible in a script that hangs up immediately and obvious in a session that stays connected.

It **imports** `live_settings`, `base_currency_marks` and `build_orders` from the pipeline rather than restating them. A debug harness with its own copy of the arithmetic debugs the harness. Importing `rebalance_live` is safe — its `main()` is behind `if __name__ == "__main__"`.

## The ARM_LIVE gate

```python
ARM_LIVE = True           # cell 2 — as committed on this branch
...
assert ARM_LIVE, "..."    # cell 11, first line
```

Both ends in the file: [`ARM_LIVE` in cell 2](rebalance_live_debug.py#L67) and [the gate itself at the top of cell 11](rebalance_live_debug.py#L262), whose [`# %% 11.` header](rebalance_live_debug.py#L224) carries the long-form note on `assert` semantics summarised below.

"Run All" therefore stops at cell 11 with an `AssertionError` instead of trading. Arming is a deliberate one-line edit made with cell 8's order table on screen. **Set it back to `False` when you're done** — a file left armed trades the next time anyone runs it end to end.

> **This branch ships the file ARMED (`ARM_LIVE = True`).** That is a deliberate working state, not the safe default, and [`test_debug_cell_script_is_disarmed_and_gated`](../tests/test_rebalance_live.py#L1158) fails while it holds — [the assertion `"ARM_LIVE = False" in src`](../tests/test_rebalance_live.py#L1168) is exactly what that test pins. Treat the failure as the reminder it is.

This is precisely what [`rebalance_port_basic.py`](rebalance_port_basic.py) lacks: its module level ends with a `dry_run=False` rebalance and a stray live market order, which is why importing *that* file transmits orders. Both properties are pinned by [`test_debug_cell_script_is_disarmed_and_gated`](../tests/test_rebalance_live.py#L1158) and [`test_debug_cell_script_is_never_imported_by_src`](../tests/test_rebalance_live.py#L1143).

### What `assert` actually does, and why it is the right tool here

```
assert <expression>[, <message>]
```

Evaluate `<expression>`. **Truthy → nothing happens**, execution continues to the next statement. **Falsy → `AssertionError(<message>)`** is raised. That is the entire construct.

- **It is a statement, not a function.** `assert (x, "msg")` — comma *inside* the parentheses — asserts a two-element **tuple**, which is always truthy, so the check can never fire. `assert x, ("msg")` — comma *outside* — is fine; those parentheses only let a long message span lines via adjacent-string concatenation, which is exactly how cell 11 is written. CPython emits `SyntaxWarning: assertion is always true, perhaps remove parentheses?` for the broken form.
- **The expression need not be a bool.** Normal truthiness applies: `assert orders` fails on an empty list, `assert n` fails on `0`, and `assert df` on a DataFrame *raises `ValueError`* because pandas refuses to guess — write `assert len(df)` there. `ARM_LIVE` is a plain bool, the unambiguous case.
- **The message is lazily evaluated**, built only on failure, so a long explanatory string costs nothing on the happy path. Its job is to say what to *do*, not merely that something was `False`.
- **Use asserts for invariants** that should be impossible to violate if the program is correct — a guard against the programmer. For expected runtime conditions (bad input, missing file, closed market) raise a real exception instead, because **asserts are erased under `python -O`**. That erasure is the one caveat on this gate: `python -O rebalance_live_debug.py` would strip it. Acceptable because the file is run cell-by-cell in an interactive session that never passes `-O`, and `ARM_LIVE` is itself a second, human gate.

Not to be confused with `dict.setdefault`, which appears a few lines away in the submit path. `d.setdefault(k, v)` returns `d[k]` if `k` is present, otherwise **inserts** `k=v` and returns `v`. It mutates, never raises, never rejects. `submit_rebalance_orders` uses `spec.setdefault('currency', ...)` so a currency from the broker's own contract record is left alone and only a *missing* one is filled from the table's column. **`assert` = stop if this is not true; `setdefault` = fill this in if it is not there.**

## The cells

| # | leaves behind | what to look at |
|---|---|---|
| 1-2 | `SETTINGS`, `WEIGHTS`, `ARM_LIVE`, `CLIENT_ID` | weights sum, guards, market-hours line |
| 3 | `app` | connection stays open until cell 16 |
| 4 | `portfolio_df`, `net_liq`, `base_ccy` | NetLiq **and its currency** |
| 5 | `fx_rates`, `marks`, `detail` | **`fx_ratio` must be 1.0 only for base-currency legs** |
| 6 | `priced_df`, `book` | `book.equity(marks)` must return to NetLiq |
| 7 | marks for unheld targets | last close, converted from USD |
| 8 | `orders`, `tradeable` | conIds, weights, `status`, notional vs ceiling |
| 9 | `specs` | resolved contract per leg |
| 10 | `dry` | dry-run submit — sends nothing |
| 11 | `submitted` | **gated live submit** |
| 11b | `smoke_spec`, `smoke_px`, `smoke_units` | smoke test **resolve** — read-only, dry run only |
| 11c | `smoke_sent`, `sent_ids` | smoke test **send** — the cell that trades, own `ARM_SMOKE` gate |
| 11d | — | smoke test **verdict** — `check_orders`, re-run at will |
| 12 | `ack` | **read `verdict` first**; `NO_ANSWER` ⇒ TWS never saw it |
| 13 | `open_orders` | the broker's own view — the authority |
| 14 | — | `cancel_order` / `cancel_all_orders` (commented out) |
| 15 | `fills` | real executions with commissions |
| 16 | — | disconnect, last |

`CLIENT_ID = 161`, distinct from the pipeline's 151 and `check_existing_port`'s 141. TWS misbehaves silently when two connections share an id.

## What one `submit_rebalance_orders` call unrolls into

[Cell 10's dry run](rebalance_live_debug.py#L219), [cell 11's live submit](rebalance_live_debug.py#L293) and [cell 11b's smoke leg](rebalance_live_debug.py#L399) all funnel through [`submit_rebalance_orders`](../src/portutils/ingestion/ibkr_requests.py#L1824), which is a **loop over the rows** of the table you hand it, not a single broker call. Per row:

1. read symbol / currency / units from the named columns; skip `NaN` rows and `units == 0`
2. `int(units)` → `action = BUY` if positive else `SELL`, `quantity = abs(units)`
3. `spec = dict(contract_specs.get(symbol, {}))`, then `spec.setdefault('currency', ...)` — the account snapshot's `conId` / `exchange` / `primary_exchange` / `currency` win, and the table's currency column only fills a gap
4. `dry_run=True` → print the resolved line and move on. `dry_run=False` → the chain below.

Every link below points at the defining line, so the chain can be read by clicking through it:

- [`order_app.submit_market_order(symbol, action, quantity, **spec)`](../src/portutils/ingestion/ibkr_requests.py#L2714) — the one call the loop makes per row
  - [`contract(symbol, sec_type, exchange, currency, primary_exchange, con_id)`](../src/portutils/ingestion/ibkr_requests.py#L124) — builds the ibapi `Contract`. `conId` pins the exact listing; `exchange` stays `SMART` so IB routes rather than direct-routing (direct routing trips precautionary warning 10311 and TWS *holds* the order)
  - [`market_order(action, quantity)`](../src/portutils/ingestion/ibkr_requests.py#L232) — builds the ibapi `Order`: `MKT`, the side, the size
  - [`OrderApp.place_order(contract_obj, order)`](../src/portutils/ingestion/ibkr_requests.py#L2696) — the only place an order reaches the socket
    - [`app.reserve_order_id()`](../src/portutils/ingestion/ibkr_requests.py#L697) — next id from the last [`nextValidId`](../src/portutils/ingestion/ibkr_requests.py#L764) callback, incremented under a lock, so you never manage order ids yourself
    - `EClient.placeOrder(order_id, contract_obj, order)` — ibapi itself: writes to the socket and returns

`placeOrder` returns immediately and **never raises on rejection**, so the `Submitted: orderId=…` prints only prove the calls did not throw. Cell 12 is what reads TWS's actual answer.

## Cell 11b — the single-leg smoke test

[Jump to the cell.](rebalance_live_debug.py#L298) The smallest thing that exercises the *entire* live path (contract resolution → `Order` → `reserve_order_id` → `placeOrder` → ack) without touching the rebalance table. Use it to see how a leg breaks and what TWS says when it does: wrong currency, unknown ticker, closed market, no market-data entitlement. It builds a **one-row DataFrame** and hands it to the same `submit_rebalance_orders` every other cell uses — testing a different code path would test nothing.

```python
ARM_SMOKE    = False    # separate gate from ARM_LIVE, on purpose
SMOKE_SYMBOL = "SPY"    # or "KMLM" — both US-listed, so the USD defaults hold
SMOKE_CASH   = 200.0    # notional to spend; units derive from the last close
SMOKE_SIDE   = 1        # +1 BUY, -1 SELL
```

**You do not need to know the currency, the conId or the units.** `resolve_spec(symbol, portfolio_df, app)` finds them:

- **held symbols** → the broker's own record (`conId` / `secType` / `currency` / `primaryExchange`) straight out of the cell-4 snapshot, via [`contract_specs_from_portfolio`](../src/portutils/ingestion/ibkr_requests.py#L156)
- **anything else** → `contract()`'s `STK/SMART/USD` defaults, correct for US listings like SPY and KMLM and wrong for everything else

There is no `reqContractDetails` helper in `ibkr_requests.py`, so the confirmation that the contract resolves at all is a **historical-bar request on that same spec**: if bars come back, TWS matched the contract — an error 200 here means the order would have failed identically — and the last close also gives the price, which is how the cell sizes by **cash** instead of a guessed unit count. `int()` truncates toward zero, so cash below one share gives `0` units, which `submit_rebalance_orders` skips rather than sending a zero-quantity order.

### The four steps, and where each one lives

| step | in the cell | what it calls in the library |
|---|---|---|
| **1. look the ticker up** | [`resolve_spec(...)`](rebalance_live_debug.py#L336), called at [line 371](rebalance_live_debug.py#L371); the spec lookup and the `setdefault` fallback are [lines 348-351](rebalance_live_debug.py#L348-L351) | [`contract_specs_from_portfolio`](../src/portutils/ingestion/ibkr_requests.py#L156) — pure, reads the cell-4 snapshot, no I/O |
| **2. prove it resolves, and print it** | [the bar probe](rebalance_live_debug.py#L357), then [the "resolved … last close" print](rebalance_live_debug.py#L367) or [the NO BARS branch](rebalance_live_debug.py#L360-L365) | [`get_equity_data`](../src/portutils/ingestion/ibkr_requests.py#L1247) → [`contract()`](../src/portutils/ingestion/ibkr_requests.py#L124) → `reqHistoricalData`, answered by [`historicalData`](../src/portutils/ingestion/ibkr_requests.py#L955) |
| **3. size it and dry-run it** | [units from cash](rebalance_live_debug.py#L376), [the one-row frame](rebalance_live_debug.py#L377), [the always-on dry run](rebalance_live_debug.py#L382) | [`submit_rebalance_orders(dry_run=True)`](../src/portutils/ingestion/ibkr_requests.py#L1939) — prints, sends nothing |
| **4. send it** ([cell 11c](rebalance_live_debug.py#L386)) | [the `ARM_SMOKE` branch](rebalance_live_debug.py#L394), [the live submit](rebalance_live_debug.py#L399), which sets `sent_ids` | [`submit_rebalance_orders(dry_run=False)`](../src/portutils/ingestion/ibkr_requests.py#L1969) |
| **5. read the answer** ([cell 11d](rebalance_live_debug.py#L411)) | [`check_orders(app, sent_ids)`](rebalance_live_debug.py#L441) | [`check_orders`](../src/portutils/ingestion/ibkr_requests.py#L2516) → [`wait_for_order_ack`](../src/portutils/ingestion/ibkr_requests.py#L1986) → [`get_open_orders_data`](../src/portutils/ingestion/ibkr_requests.py#L2430) |

### Reading the answer — the `verdict` column

[`check_orders`](../src/portutils/ingestion/ibkr_requests.py#L2516) runs three stages, announcing each: acknowledgement (with verdicts) → the broker's own view → the two joined on `orderId`. It holds no logic itself. **The order is mandatory, not stylistic**: `get_open_orders_data` [clears `app.open_orders`](../src/portutils/ingestion/ibkr_requests.py#L2435-L2437), which `wait_for_order_ack` reads for `symbol` / `action` / `quantity` — reversed, those columns come back blank with nothing to explain why.

The merged frame is **paired, not appended**: [`_pair_columns`](../src/portutils/ingestion/ibkr_requests.py#L2479) puts `status` next to `status_broker` and `symbol` next to `symbol_broker`, keeping the ack frame's own reading order and leaving broker-only columns (`conId`, `tif`, `lmtPrice`) at the end. A plain `pd.merge` appends the broker's columns to the far right, which puts a dozen columns between the two values you are trying to compare — and spotting disagreement is the whole reason for joining.

[`_order_verdict`](../src/portutils/ingestion/ibkr_requests.py#L2215) reduces each order's state to one word, **status first and message codes second** — IB adds warning codes faster than anyone updates a constant, but an order's own status is authoritative:

| verdict | meaning | the real example |
|---|---|---|
| `REJECTED` | refused; `reason` carries the text verbatim | `201 … This product does not have a KID in English …` — a SPY smoke leg |
| `PENDING_OPEN` | **healthy**, accepted and parked until the venue opens | `399 … will not be placed at the exchange until 2026-07-31 09:00:00 MET` |
| `HELD` | alive, waiting on a human — `whyHeld`, or the 10311 direct-routing warning | order sitting in Pending with a Transmit button |
| `WORKING` | live at IB, nothing attached | a normal in-hours order |
| `FILLED` | done | — |
| `NO_ANSWER` | TWS never mentioned this id at all | the empty-panel failure this harness exists for |

`PENDING_OPEN` earns its own verdict because five such orders once read as failures.

**Every message gets its own column.** Alongside the counts `n_errors` / `n_notices`, the frame carries `error_1 … error_N` and `notice_1 … notice_N`, widened by [`_message_columns`](../src/portutils/ingestion/ibkr_requests.py#L2174) from [the append-only logs](../src/portutils/ingestion/ibkr_requests.py#L578) — one message per cell, so "what was the *second* error?" is a column lookup rather than string-splitting a blob. The column count is set by the busiest order, and a run in which nothing drew a message of a kind gains **no** columns of that kind at all. Shorter orders are `None`-padded; an id TWS never mentioned keeps its row with every message cell empty.

The older `req_errors` / `req_notices` dicts hold only the **last of each kind** per id — a summary, never a record, which is why the logs exist. A third structure, `req_messages`, keeps both kinds interleaved in arrival order; it is [deliberately without a consumer](../src/portutils/ingestion/ibkr_requests.py#L590), a raw audit trail for the debugging session where the *sequence* of a warning and a refusal is the question.

**Where the messages come from, and why one seemed to vanish.** Nothing above raises. Messages arrive asynchronously on the reader thread and land in [`IBApp.error`](../src/portutils/ingestion/ibkr_requests.py#L780); acceptance and state changes arrive the same way via [`openOrder`](../src/portutils/ingestion/ibkr_requests.py#L1097) and [`orderStatus`](../src/portutils/ingestion/ibkr_requests.py#L1120). `error()` *prints* each message, but a notebook only routes background-thread stdout into a cell **while that cell is running** — a rejection landing after the submit cell finished had nowhere to print. **The state was never lost, only the print.** Re-run cell 11d and it is there. The [poll loop](../src/portutils/ingestion/ibkr_requests.py#L2068) also now exits on a *quiet period* rather than at first mention, so a refusal arriving a few hundred milliseconds after the first `PreSubmitted` makes it into the same frame.

`ARM_SMOKE` is deliberately separate from `ARM_LIVE` — arming the rebalance must not also fire a test order, and vice versa. The cells sit **after** cell 11's `assert ARM_LIVE`, which is what keeps the repo rule "no `dry_run=False` above the gate" true ([the loop at `test_rebalance_live.py:1174-1180`](../tests/test_rebalance_live.py#L1174-L1180) walks the file's non-comment lines and checks exactly that). Consequence: under "Run All" it is only reached once the file is armed, and running it *standalone* in an interactive session executes no assert at all — `ARM_SMOKE` is then the only gate. Set it back to `False` when you are done.

## When something goes wrong

1. **Cell 12** — `verdict=NO_ANSWER` means TWS never mentioned the order. Assume it did not arrive; check the Orders panel before re-running or you may send the basket twice. `verdict=REJECTED` is the opposite situation needing the opposite response: it *did* arrive and was refused, and `reason` says why. `PENDING_OPEN` is neither — the order is fine and queued.
2. **Cell 13** — [`get_open_orders_data`](../src/portutils/ingestion/ibkr_requests.py#L2430) is independent of anything this session believes. If an order is here, it is real.
3. **Cell 14** — [`cancel_order(app, id)`](../src/portutils/ingestion/ibkr_requests.py#L2306) for one, [`cancel_all_orders(app)`](../src/portutils/ingestion/ibkr_requests.py#L2319) for everything on the account across all client ids. Cancellation is a *request*: re-run cell 13 and confirm rather than assuming.

## Caveats

- Cells 1-10 are read-only plus a dry run, safe to run straight through. Cell 11 onwards is not; cell 11b is read-only only *while `ARM_SMOKE` is `False`*.
- Market orders cannot execute outside trading hours. Cell 2 prints whether anything is open; the check is a crude weekday/clock test, not a venue calendar — no holidays, no half-days.
- The pipeline remains the thing to run for a real rebalance. This file is for understanding one.
