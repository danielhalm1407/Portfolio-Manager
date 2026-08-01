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
