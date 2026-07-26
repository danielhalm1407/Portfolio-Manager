# `rebalance_live_debug.py` — cell-by-cell harness for the live rebalancer

**This file connects to TWS and, once armed, places real orders. It must never be imported.**

## Why it exists

[`src/pipelines/rebalance_live.py`](../src/pipelines/rebalance_live.py) is a one-shot CLI: connect, decide, submit, disconnect, all in one `try/finally`. When something goes wrong there is nothing left to inspect — the socket is closed and every intermediate frame is gone.

It was written after the first armed run reported seven orders `Submitted` into a **completely empty TWS Orders panel**. `EClient.placeOrder` writes to the socket and returns; acceptance arrives later via `openOrder`/`orderStatus` on the reader thread. The pipeline disconnected in the next statement, which stopped that thread and discarded everything in flight. That failure is invisible in a script that hangs up immediately and obvious in a session that stays connected.

It **imports** `live_settings`, `base_currency_marks` and `build_orders` from the pipeline rather than restating them. A debug harness with its own copy of the arithmetic debugs the harness. Importing `rebalance_live` is safe — its `main()` is behind `if __name__ == "__main__"`.

## The ARM_LIVE gate

```python
ARM_LIVE = False          # cell 2
...
assert ARM_LIVE, "..."    # cell 11, first line
```

"Run All" therefore stops at cell 11 with an `AssertionError` instead of trading. Arming is a deliberate one-line edit made with cell 8's order table on screen. **Set it back to `False` when you're done** — a file left armed trades the next time anyone runs it end to end.

This is precisely what [`rebalance_port_basic.py`](rebalance_port_basic.py) lacks: its module level ends with a `dry_run=False` rebalance and a stray live market order, which is why importing *that* file transmits orders. Both properties are pinned by `tests/test_rebalance_live.py::test_debug_cell_script_is_disarmed_and_gated` and `::test_debug_cell_script_is_never_imported_by_src`.

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
| 12 | `ack` | **`acknowledged=False` ⇒ TWS never saw it** |
| 13 | `open_orders` | the broker's own view — the authority |
| 14 | — | `cancel_order` / `cancel_all_orders` (commented out) |
| 15 | `fills` | real executions with commissions |
| 16 | — | disconnect, last |

`CLIENT_ID = 161`, distinct from the pipeline's 151 and `check_existing_port`'s 141. TWS misbehaves silently when two connections share an id.

## When something goes wrong

1. **Cell 12** — `acknowledged=False` means TWS never mentioned the order. Assume it did not arrive; check the Orders panel before re-running or you may send the basket twice. A row with an `error` value *did* arrive and was refused — the opposite situation, needing the opposite response.
2. **Cell 13** — `get_open_orders_data` is independent of anything this session believes. If an order is here, it is real.
3. **Cell 14** — `cancel_order(app, id)` for one, `cancel_all_orders(app)` for everything on the account across all client ids. Cancellation is a *request*: re-run cell 13 and confirm rather than assuming.

## Caveats

- Cells 1-10 are read-only plus a dry run, safe to run straight through. Cell 11 onwards is not.
- Market orders cannot execute outside trading hours. Cell 2 prints whether anything is open; the check is a crude weekday/clock test, not a venue calendar — no holidays, no half-days.
- The pipeline remains the thing to run for a real rebalance. This file is for understanding one.
