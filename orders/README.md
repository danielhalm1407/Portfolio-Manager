# `orders/` — interactive harnesses and GUIs

Runnable, human-driven tools that talk to Interactive Brokers. Everything here is meant to be
**executed cell by cell or as an app**, with a person watching.

> **Nothing in this folder may be imported.** These files have module-level side effects: they open
> TWS connections and, once armed, place orders. `orders/rebalance_port_basic.py` ends its module
> level with a `dry_run=False` rebalance and a stray market order — importing it *transmits those
> orders* before any caller's own checks run. That is why `submit_rebalance_orders` was moved into
> the library, and why `tests/test_rebalance_live.py` pins that no `src/` module imports these files.

For how this fits the wider picture, see [EXECUTION_STACK.md](../EXECUTION_STACK.md).

## The files

| file | what it is | doc |
|---|---|---|
| [`rebalance_live_debug.py`](rebalance_live_debug.py) | Cell-by-cell harness for the live rebalancer. Runs the *same* decision as the pipeline but keeps the connection and every intermediate frame alive so you can look at them. Gated by `ARM_LIVE` / `ARM_SMOKE`. | [rebalance_live_debug.md](rebalance_live_debug.md) |
| [`kts.py`](kts.py) | Kalman Trading System — a Tkinter GUI that estimates the mean level of an Ornstein–Uhlenbeck process and trades against it. Live ticks, calibration, manual and auto orders. | [notes_on_orders.md](notes_on_orders.md) |
| [`rebalance_port_basic.py`](rebalance_port_basic.py) | The original rebalance cell script. Superseded by the pipeline; kept for reference. **The cautionary example above.** | [rebalance_port_basic.md](rebalance_port_basic.md) |

## The notes worth reading

- [**live_trading_notes.md**](live_trading_notes.md) — the three sticking points from taking the
  rebalancer live: a ticker is not an instrument, nothing IBKR returns is in one currency, and an
  order is async with errors that lie. Written as failures and reasoning, not as file descriptions.
- [**notes_on_orders.md**](notes_on_orders.md) — order types, TIF, and IB order mechanics.
- [**dummy_orders.md**](dummy_orders.md) — worked examples.
- [**plan.md**](plan.md) — the original design notes for this folder.

## `kts.py` and the library

`kts.py` used to ship its **own** `IBApp`, contract builders and order-placing logic, duplicating
what `portutils.ingestion.ibkr_requests` already did better (thread-safe order-id allocation,
per-request events). That duplication is gone: it now imports `IBApp`, `contract`, `market_order`,
`crypto_marketable_limit_order` and `OrderApp` from the library, plus the accounting types from
`portutils.portfolio`, and holds only the Tk state and the OU/Kalman model.

The market-data logic that existed *only* in kts was ported **into** the library rather than dropped:

| ported | why it mattered |
|---|---|
| Delayed tick tiers (`66`/`67`/`68` alongside live `1`/`2`/`4`) | without them crypto, FX and off-hours streams simply never tick |
| Mid synthesis (`_maybe_emit_mid`) | a usable price when neither side prints a LAST |
| `sectype` gating | STK and CRYPTO need different order treatment (PAXOS wants marketable IOC limits) |

Pinned by [`tests/test_kts_migration.py`](../tests/test_kts_migration.py) — kts's accounting path
must behave identically after delegating — and [`tests/test_book_parity.py`](../tests/test_book_parity.py),
which requires the extracted `Book` to reproduce kts's numbers exactly.

Both consolidations are **complete**: the library carries `get_historical_bars`,
`crypto_marketable_limit_order` and the `hasattr` guard on `eTradeOnly` / `firmQuoteOnly` that the
migration required, and `kts.py` defines no `IBApp` of its own.

**One trap if you work on it:** request ids and order ids share one numeric space. kts historically
used literal `reqId=1` for its market-data stream and `2` for historical pulls; anything drawing ids
from `next_req_id()` (which starts at 1) will collide with a hardcoded stream id and TWS will
mis-route ticks. Allocate the stream id from `next_req_id()` too.

## Conventions

- Comments here are **dense and explanatory by design** — see the project
  [`CLAUDE.md`](../CLAUDE.md). `kts.py` sets the house style: a header block per function stating its
  role, threading model and when it is called, then a short rationale above each meaningful step.
- Tk widgets are not thread-safe. Anything reaching the GUI from the IB reader thread must be
  dispatched with `root.after(0, ...)`.
