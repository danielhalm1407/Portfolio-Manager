# `rebalance_port_basic.py` — the original manual rebalancer

The first-generation rebalancing script, kept as-is. Renamed from `rebalance_port.py` when [`rebalance_live.py`](../src/pipelines/rebalance_live.md) took over as the policy-driven rebalancer; "basic" means manual target weights typed into the file, not lesser.

## What it does

A `# %%` cell script walked through by hand:

1. Connect an `IBApp` and wrap it in an `OrderApp`.
2. `get_portfolio_info_and_val()` — pull holdings and both portfolio values (summed `marketValue` from positions, and `NetLiquidation` from the account summary; they differ, and the script prints both).
3. Read `TotalCashValue` and show what adding a slice of idle cash would do to the deployable total.
4. **Type target weights by hand** as a `pd.Series` aligned to the portfolio rows.
5. `calculate_target_positions()` — target value, current weight, weight diff, a `clears_min_turnover` flag, and `units_diff` (the signed share delta).
6. `submit_rebalance_orders()` — loop the table and place market orders. **`dry_run=True` by default.**

## ⚠ Do not import from this file

It is a **cell script with module-level side effects**. Importing it:

1. opens an IBKR connection (`ib_app.start(client_id=125)` at line 32),
2. runs every cell top to bottom, which includes
3. `submit_rebalance_orders(..., dry_run=False)` and a stray `submit_market_order('5mvl', 'BUY', 10, ...)`.

So `import rebalance_port_basic` **transmits real orders**. `rebalance_live.py` originally did exactly that to reuse the submitter, which would have sent orders on a *dry run*, before any of its safety checks ran. Caught by an end-to-end stubbed dry run; pinned by `test_does_not_import_the_cell_script`.

**`submit_rebalance_orders` now lives in [`portutils.ingestion.ibkr_requests`](../src/portutils/ingestion/ibkr_requests.py)**, beside `OrderApp`, where it can be imported safely. The copy in this file is a frozen historical duplicate — left because this script is preserved as-is. Edit the library version.

That docstring is also the clearest explanation in the repo of how order IDs are handled: you never increment them yourself; `OrderApp.place_order` calls `ib_app.reserve_order_id()`, which returns the next id from the last `nextValidId` callback and increments it under a lock.

## Why it is still here

Kept as the record of the original manual workflow, and as a worked example of pulling an account, computing target positions from hand-set weights, and submitting a batch.

## How it differs from `rebalance_live.py`

| | `rebalance_port_basic.py` | `rebalance_live.py` |
|---|---|---|
| target weights | typed into the file by hand | `config/asset_universe.yaml` |
| policy | none — you decide the weights | `ConstantMixRule`, the backtested one |
| run style | cell by cell, interactive | `python …` with CLI flags |
| live switch | `dry_run=` argument in the call | `--live` / `--dry-run` / config default |
| guards | dry-run default only | + paper assert, max order value, audit trail |
| audit trail | none | `outputs/live/orders_<timestamp>.csv` |

Use this one for an ad-hoc rebalance to weights you have decided yourself. Use `rebalance_live.py` to run the studied policy.

## Running it

Cell script; needs TWS on `127.0.0.1:7497`. **Start with `dry_run=True`** (the default) and read the printed orders before flipping it.

The account is hardcoded as `DUP102412` in `get_portfolio_info_and_val`'s signature.

## Caveats

- **Target weights are aligned by position, not by symbol** — `pd.Series([...], index=port_df.index)`. If the holdings change, the weights silently attach to different tickers. Check the displayed table before submitting.
- `calculate_target_positions` computes `clears_min_turnover` but **does not act on it** — the flag is reported, and every non-zero `units_diff` is still submitted.
- The rebalance is sized against whichever portfolio value you pass; the script computes `port_val` (NetLiq) and `mkt_val` (summed positions) and the example call passes `mkt_val`. They are different denominators and give different targets.
- Market orders only.
- The trailing cells contain scratch experiments (a GBP order, an open-orders check, an `orderStatus` call with a wrong keyword). They are exploratory leftovers, not part of the workflow.

## Related

- [`src/pipelines/rebalance_live.md`](../src/pipelines/rebalance_live.md) — the policy-driven successor
- [`research/check_existing_port.md`](../research/check_existing_port.md) — inspect the account before or after rebalancing
