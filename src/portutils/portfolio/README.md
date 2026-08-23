# `portfolio/` — accounting, policies, simulation

The engine that answers "what do I own, what did it cost, what is it worth, and what should I
trade". Broker-agnostic on purpose: these modules know about symbols, quantities and prices, and
nothing about IBKR. The single bridge is [`ibkr_sync.py`](ibkr_sync.py).

That separation is what lets the same `Book` serve a live account and a simulated one — and lets
[`tests/test_book_parity.py`](../../../tests/test_book_parity.py) hold both to identical numbers.

## The modules

| module | owns | pure? |
|---|---|---|
| [`book.py`](book.py) | `Book`, `Position` — average-cost accounting, realised and unrealised P&L. The accounting engine. | yes |
| [`fills.py`](fills.py) | `Fill` (the atomic print) and `Order` (the ledger entry above it) | yes |
| [`ledger.py`](ledger.py) | `StateLedger` — the per-bar record of what the book looked like | yes |
| [`rules.py`](rules.py) | the policies: `BuyAndHoldRule`, `ConstantMixRule`, `DrawdownRotationRule`, `TradeListRule`, plus `weights_to_units` | yes |
| [`simulator.py`](simulator.py) | `PortfolioSimulator` — replay a price history against rules and account the result | yes |
| [`execution.py`](execution.py) | `SimExecutionBackend` — turns "trade N units" into a `Fill` | yes |
| [`backcast.py`](backcast.py) | policy-anchored backcast: reconstruct the book further back than TWS will report | yes |
| [`ibkr_sync.py`](ibkr_sync.py) | `book_from_portfolio`, `fills_from_executions`, `rewind_positions`, `reconcile` — broker → book | reads frames only |

## `Book` is the centre of it

Average-cost accounting: a buy raises the position and blends the cost basis; a sell realises P&L
against that basis and leaves the basis unchanged. `book.equity(marks)` values the whole book at a
given set of prices.

**Marks must be in one currency.** `book_from_portfolio` back-solves base equity from `marketPrice`
and `averageCost`, so feeding it unconverted rows makes the book a sum across three currencies —
and `ConstantMixRule` then sizes *every* leg off that wrong equity, not just the mispriced one. The
live pipeline converts first; see [EXECUTION_STACK.md](../../../EXECUTION_STACK.md).

The engine was extracted from `orders/kts.py`, whose simulated-book attributes are now property
shims over `Book`. Parity is pinned by [`test_book_parity.py`](../../../tests/test_book_parity.py)
and [`test_kts_migration.py`](../../../tests/test_kts_migration.py); the accounting rules themselves
by [`test_book.py`](../../../tests/test_book.py) (12 tests).

## `ledger.py` — the per-bar state row

`StateLedger` keeps one row per bar timestamp, indexed by `ts`. It writes the **accounting**
half itself and lets the caller stamp everything else, so no strategy knowledge leaks into the
accounting layer.

**`book`** — a [`Book`](book.py): `{symbol: Position}` plus the `base_equity` the P&L is measured
on top of. Each `Position` holds signed `qty`, `avg_entry` (VWAP of the open units), cumulative
`realised`, and the per-bar `closed_this_bar`. The ledger only *reads* it — through
`Position.snapshot(price)` or `Book.snapshot(prices)` — then clears the per-bar counters.
It never applies a fill; `apply_fill` stays the sole mutator.

**`price` / `prices`** — the **mark**, not a fill price: a scalar on the single-asset path,
a `{symbol: price}` mapping on the multi-asset one. Marks are an argument rather than book state
because unrealised P&L moves on every tick and is never stored as authoritative — it is recomputed
at the moment of marking.

**`extra`** — the strategy/context half: any flat `{column: value}` dict, merged last so it wins on
a key clash. It records *why* the bar looked the way it did:

| caller | what it stamps |
|---|---|
| `orders/kts.py` (`_record_state_row`) | the recommendation block — `r_hat`, `q_low`/`q_high`, `direction`, previous/target/capped/recommended weights, `weight_threshold`, `binding_cap`, `intended`/`filled`/`unfilled`, `portfolio_value`, `portfolio_pnl` |
| [`simulator.py`](simulator.py) | portfolio-level context — `equity`, `gross_exposure`, `n_trades`, `turnover_notional`, `turnover_frac` |
| an offline scenario | nothing; the frame shape is unchanged |

Keys the schema did not anticipate simply widen the frame, so earlier bars show `NaN` for a column
that only starts existing mid-run — a leg first traded halfway through a run is handled, not fatal.

**When a row is written** — at every bar close **and** whenever the book actually moves: the union
of the two, not the lesser. `kts.py` records on the bar-close hook, on each replay bar, and again
immediately after a manual or auto trade so the panel reflects the click instead of waiting for the
next close. That is safe because rows are keyed on the bar timestamp: re-marking a bar *overwrites*
its row rather than appending a second one, so the frame stays one-row-per-bar however many times a
bar is marked. The one genuinely per-bar field, `closed_units`, is why `reset_bar_counters()` fires
immediately after each write.

**`Book.snapshot(prices)`** returns `{symbol: {field: value}}` plus a `"TOTAL"` aggregate. Per-symbol
fields are `position, avg_entry_price, entry_cost, mark_value, unrealised_pnl, realised_pnl,
total_pnl, last_price, closed_units`; `TOTAL` carries `entry_cost, mark_value, unrealised_pnl,
realised_pnl, total_pnl, closed_units, portfolio_value` — `avg_entry_price` and `last_price` are
left out rather than faked, being meaningless across instruments. `record_book` flattens that
nesting into one **wide** row (`"<SYM>_<field>"`, `"TOTAL_<field>"`), because every plotting and
performance helper in `portutils` takes a wide frame of columns-as-series.

## `rules.py` — the policies

Each rule implements `propose(timestamp, marks, book) -> {symbol: units}`. `ConstantMixRule` is the
one the live rebalancer uses, and it is used **unchanged** in production: the live-only adjustments
(whole-share rounding, `min_turnover`, the per-order notional ceiling) are applied afterwards by
`build_orders` in the pipeline, so the studied policy and the traded policy are the same object.

## `backcast.py`

TWS serves roughly seven days of execution history. Anything further back has to be *reconstructed*:
`backcast.py` takes today's positions and a policy, walks them backwards, and produces the fill path
that policy would have generated. Hence [`src/pipelines/log_positions.py`](../../pipelines/log_positions.py),
which snapshots positions daily so future work needs less reconstruction. Tested offline in
[`test_ibkr_sync.py`](../../../tests/test_ibkr_sync.py) (18 tests).
