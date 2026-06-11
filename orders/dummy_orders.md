# `dummy_orders.json` — schema & PnL conventions

Reference for the order-book mock. JSON cannot hold inline comments, so all
rationale and the "what an alternative encoding would look like" examples live
here.

## Top-level shape

`{ "<order_id>": { "metadata": {...}, "history": [...] } }`

- **`metadata`** — static order facts (contract, action, sizing, rationale) plus
  a **`state`** cache (see below). The **`rationale`** block holds the allocator's
  sizing decision (see below).
- **`history`** — the single append-only event stream. Every timestamped change
  to the order lands here: status transitions, fills, *and* mark-to-market
  snapshots. There is **no** separate `pnl_contribution` block — PnL is carried
  on each history entry's `pnl` object. History is the single source of truth.

## `metadata.rationale` — the sizing decision

Why the allocator placed this order and how big. Forecast fields plus the
weight/value sizing trail:

- `expected_return_h` / `q_low_h` / `q_high_h` — model return forecast over the
  horizon and its lower/upper quantile band.
- `previous_weight` — portfolio weight in this name **before** the order.
- `target_weight` — raw target weight the model wants.
- `recommended_weight` — actual target **after** caps/constraints are applied
  (equals `target_weight` when nothing binds; differs when a cap clips it).
- `weight_change` — `recommended_weight - previous_weight`; the tilt this order
  implements. Sign matches the trade direction.
- `previous_value` — position market value before the order (`previous_weight × NAV`).
- `target_value` — intended position value after the order (`recommended_weight × NAV`).
- `reference_price` — price used to convert the value delta into share quantity.
- `signed_qty` — order size as a **signed** share count: `+` buy, `-` sell
  (e.g. `+100` / `-70`). Magnitude equals `metadata.total_qty`.

These can be partly redundant with `total_qty` / `action` / `market_price_at_submit`
by design — `rationale` is the self-contained record of *why this size*, kept
even if the execution fields are later reconciled against the broker.

## History events

Each entry has a `ts`, an `event` kind, the cumulative order state, an optional
`fill`, and a `pnl` block.

### Event kinds

- **`ORDER_UPDATE`** — a status and/or fill change (PendingSubmit, Submitted,
  PartiallyFilled, Filled, Cancelled, ...). Carries the *new* fill only in
  `fill` (never re-lists prior fills — those already live in earlier entries).
- **`MARK`** — a pure mark-to-market re-valuation. No status/fill change; only
  `pnl.unrealised` and `pnl.mark_price` move.

### When does a MARK occur?

Marks are **sparse and event-driven**, not a heartbeat:

- On each incremental fill, to re-value the open lots at the new mark.
- At wind-down (position fully closed) for the final book.
- The schema *permits* a standalone MARK at any time (e.g. an EOD revaluation)
  but nothing requires a regular cadence.

### The `fill` object

- `role: "OPEN"` — adds to the position. Carries `remaining_open_qty` and a
  `closed_by` list (populated later as closing fills consume this lot).
- `role: "CLOSE"` — reduces/closes the position. Carries a `closes` list
  (each entry: the `order_id`/`exec_id` of the open lot it closes, the `qty`,
  the lot's `entry_price`, and the `realised` PnL for that slice) plus a
  `realised_total`.

### The `pnl` block

`{ "realised_cum": <float>, "unrealised": <float>, "mark_price": <float|null> }`

- `realised_cum` — cumulative realised PnL for this order as of this event.
- `unrealised` — total open-lot mark-to-market PnL at `mark_price` (total only,
  no per-lot breakdown — see below).
- `mark_price` — the price used for `unrealised`; `null` before any fill.

## `metadata.state` — the cache

A denormalised snapshot that **always mirrors the last history entry**:

```jsonc
"state": {
  "as_of": "<ts of last history event>",
  "status": "Filled",
  "qty_filled": 100,
  "qty_outstanding": 0,
  "lifecycle": "LIVE_OPEN",
  "realised_cum": 0.00,
  "unrealised": 160.60
}
```

Convenience for fast reads (no need to scan history). **Invariant:** whatever
appends a history entry must overwrite `metadata.state` in the same write, so it
equals the history tail. History remains the source of truth; `state` is a cache.

`lifecycle` is a coarse label derived from the order's position
(`LIVE_OPEN` = still holds open lots, `CLOSED_BOOK_DONE` = flat and booked).

---

## Per-lot unrealised: recompute on demand (chosen design)

We store **only the total** `unrealised` on each MARK, not a per-lot breakdown.
Per-lot unrealised is pure arithmetic from data already in the history, so
storing it would duplicate state that can drift.

To recompute the per-lot breakdown at a given `mark_price`:

1. Walk `history` and collect every `fill` with `role == "OPEN"`.
2. For each open lot, its still-open quantity = `qty` minus the sum of all
   `qty` slices in later `CLOSE` fills' `closes[]` that reference its `exec_id`.
3. Per-lot unrealised = `remaining_qty * (mark_price - entry_price)`.
   (For a short position the sign flips: `remaining_qty * (entry_price - mark_price)`.)
4. The totals must reconcile: `sum(per-lot unrealised) == pnl.unrealised`.

Worked example — order 1001 at the `2026-05-22T15:00` MARK, `mark_price = 247.10`:

```
lot ...01.01: 60 @ 245.48  ->  60 * (247.10 - 245.48) = 97.20
lot ...01.02: 40 @ 245.515 ->  40 * (247.10 - 245.515) = 63.40
total unrealised                                       = 160.60  ✓ matches pnl.unrealised
```

Realised per-lot is likewise **not** duplicated: it already lives in each CLOSE
fill's `closes[].realised` (and `realised_total`).

## Alternative: store per-lot (NOT used here)

If you ever prefer to persist the breakdown instead of recomputing, a MARK's
`pnl` would nest an `open_fills` array:

```jsonc
{
  "ts": "2026-05-22T15:00:00.000Z",
  "event": "MARK",
  "status": "Filled",
  "qty_filled": 100,
  "qty_outstanding": 0,
  "avg_fill_price": 245.494,
  "fill": null,
  "pnl": {
    "realised_cum": 0.00,
    "unrealised": 160.60,
    "mark_price": 247.10,
    "open_fills": [
      { "exec_id": "0001f4e8.6649abc1.01.01", "remaining_qty": 60, "entry_price": 245.48,  "unrealised": 97.20 },
      { "exec_id": "0001f4e8.6649abc1.01.02", "remaining_qty": 40, "entry_price": 245.515, "unrealised": 63.40 }
    ]
  }
}
```

Trade-off: faster per-lot reads, but the array can drift from the fills it's
derived from and must be kept consistent on every write. We chose
recompute-on-demand to keep history the single non-redundant source of truth.
